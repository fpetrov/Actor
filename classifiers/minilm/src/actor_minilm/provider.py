"""Optional local provider; installed lifecycle rules remain authoritative."""
import asyncio
import hashlib
import os
from pathlib import Path
import time

from actor_runtime.classifiers import EventAssessment
from .capture import Capture
from .model import Classifier, DEFAULT_RUN
from .projection import project


class MiniLMClassifier(Capture):
    def __init__(self, rules, *, model_run=DEFAULT_RUN, capture_root=None, timeout=1., model=None):
        self.model = model if model is not None else Classifier(model_run)
        self.inputs = {}
        self._inference = None
        self.timeout = timeout
        self.neural_calls = self.fallbacks = 0
        super().__init__(rules, capture_root)

    async def classify(self, context, event):
        if event.hints.potentially_critical and self.delegate.rules.lookup(event) is not None:
            assessment = await self.delegate.classify(context, event)
            self.observe_rule_assessment(context, event, assessment)
            return assessment
        identity = self.before(context, event, "configured_provider")
        baseline = await self.delegate.classify(context, event)
        started = time.perf_counter()
        detail = {}
        try:
            if self._inference is not None and not self._inference.done():
                raise TimeoutError("Previous local inference is still draining")
            if self._inference is not None and not self._inference.cancelled():
                # A timed-out worker can finish with an error after its caller left.
                self._inference.exception()
            value = project(self.inputs[identity])
            detail = {"model_input": value.model_dump(), "renderer": self.model.report["renderer"],
                      "model_input_sha256": hashlib.sha256(self.model.render(value).encode()).hexdigest()}
            self._inference = asyncio.create_task(asyncio.to_thread(self.model.predict, value))
            prediction = await asyncio.wait_for(asyncio.shield(self._inference), self.timeout)
            self.neural_calls += 1
            review = prediction["should_request_brain_review"]
            result = EventAssessment(relevance="relevant", urgency="important" if review else "routine",
                should_buffer=True, should_remember=review, should_interrupt_skill=review,
                should_request_brain_review=review, confidence=0., provider="minilm-v7",
                reason_code="neural_review" if review else "neural_continue")
            detail.update(assessment_source="neural", neural_prediction=prediction)
        except asyncio.CancelledError:
            self.after(identity, None, **detail, assessment_source="cancelled")
            raise
        except (ValueError, KeyError, TypeError, TimeoutError, RuntimeError, OSError) as error:
            result = baseline
            self.fallbacks += 1
            detail.update(assessment_source="rules_fallback", fallback_reason=type(error).__name__)
        self.after(identity, result, **detail, inference_ms=(time.perf_counter() - started) * 1000)
        return result

    async def close(self):
        try:
            if self._inference is not None:
                self._inference.add_done_callback(
                    lambda task: None if task.cancelled() else task.exception())
                try:
                    async with asyncio.timeout(min(1., self.timeout)):
                        await asyncio.shield(self._inference)
                except TimeoutError:
                    self.counts["shutdown_inference_pending"] += 1
                except Exception:
                    self.counts["shutdown_inference_errors"] += 1
        finally:
            await super().close()

    def report(self):
        return {"provider": "minilm", "model_sha256": self.model.model_sha, "neural_calls": self.neural_calls,
                "fallbacks": self.fallbacks, "capture": dict(self.counts), "scores_are_calibrated": False,
                "external_requests": 0}


async def factory(**options):
    config = options.get("config")
    timeout = min(1., config.timeout * .8) if config is not None else 1.
    run = Path(os.environ.get("ACTOR_MINILM_MODEL_RUN", DEFAULT_RUN))
    model = await asyncio.to_thread(Classifier, run)
    return MiniLMClassifier(options["rules"], timeout=timeout,
        model=model,
        capture_root=os.environ.get("ACTOR_MINILM_CAPTURE_ROOT"))
