import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from actor_minilm.contract import ClassifierInput
from actor_minilm.evaluation import read_rows, score_rows
from actor_minilm.model import Classifier
from actor_minilm.projection import project
from actor_minilm.training import validate_split

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def model():
    return Classifier()


def example(name):
    return ClassifierInput.model_validate_json((ROOT / "examples" / name).read_text())


@pytest.mark.parametrize("name,expected", [("body-covered.json", False), ("body-uncovered.json", True), ("game-over.json", False)])
def test_bundled_weights_known_contrasts(model, name, expected):
    assert model.predict(example(name))["should_request_brain_review"] is expected


def test_refuses_silent_token_truncation(model):
    value = example("body-covered.json").model_copy(update={"objective": "task " * 600})
    with pytest.raises(ValueError, match="Refusing silent truncation"):
        model.predict(value)


def test_checkpoint_manifest_and_identity(model):
    from actor_minilm.model import DEFAULT_RUN
    manifest = json.loads((DEFAULT_RUN / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((DEFAULT_RUN / name).read_bytes()).hexdigest() == digest
    assert model.max_length == 512
    assert model.report["model_version"] == "minilm-v7-r2"


def test_training_split_rejects_same_match_and_duplicate_text():
    a = {"input": example("body-covered.json"), "label": 0, "group": "match-1"}
    b = {"input": example("body-uncovered.json"), "label": 1, "group": "match-1"}
    with pytest.raises(ValueError, match="groups overlap"):
        validate_split([a], [b])
    with pytest.raises(ValueError, match="Identical rendered"):
        validate_split([a], [{**a, "group": "match-2"}])
    validate_split([a], [{**b, "group": "match-2"}])


def typed_input():
    from actor_runtime.classifiers import ClassificationContext, EventHints
    from actor_runtime.events import RoutedEvent
    from actor_runtime.extensions import type_url
    from actor_runtime.objectives import Objective
    from actor_runtime.world import CachedPayload
    from extensions.agents_arena.v1 import social_pb2 as pb

    own = CachedPayload("self", "agents-arena.social-own-state", 1, 1,
        pb.SocialOwnStateObservation(alignment=pb.SOCIAL_ALIGNMENT_CREW, life=pb.SOCIAL_LIFE_ALIVE))
    match = CachedPayload("self", "agents-arena.social-match-state", 2, 2,
        pb.SocialMatchStateObservation(phase=pb.SOCIAL_PHASE_EXPLORATION))
    body = CachedPayload("self", "agents-arena.social-visible-world", 3, 3,
        pb.SocialVisibleWorldObservation(recipient_actor_id="self", bodies=[pb.SocialVisibleBody(body_id="body-1")]))
    context = ClassificationContext(Objective(text="Survive and complete crew tasks."), None, (own, match))
    event = RoutedEvent("event-1", "session-1", body, "agents-arena", "0.6.0", type_url(type(body.payload)), EventHints())
    return context, event


def planned_context(context):
    return SimpleNamespace(objective=context.objective, current_skill=context.current_skill,
        relevant_state=context.relevant_state, policy_tags=context.policy_tags,
        current_plan={"schema_version": "actor.executable-plan.v1", "id": "plan-1", "state": "running",
            "current": {"skill_id": "agents-arena.perform-task", "arguments": {"task_id": "task-1"}},
            "remaining_steps": [], "fallback": None,
            "conditions": [{"capability_id": "agents-arena.social-visible-world", "field": "bodies", "when": "nonempty",
                "response": {"skill_id": "agents-arena.report-visible-body", "arguments": {}}}]})


async def test_installed_entry_point(monkeypatch, model):
    from actor_runtime.classifiers import ClassifierRegistry, EventRuleRegistry
    from actor_runtime.config import ClassifierConfig
    from actor_minilm import provider
    monkeypatch.setattr(provider, "Classifier", lambda _: model)
    monkeypatch.delenv("ACTOR_MINILM_MODEL_RUN", raising=False)
    monkeypatch.delenv("ACTOR_MINILM_CAPTURE_ROOT", raising=False)
    classifier = await ClassifierRegistry.create_async("minilm", rules=EventRuleRegistry(), config=ClassifierConfig(provider="minilm"))
    try:
        assert classifier.report()["provider"] == "minilm"
        assert classifier.model.model_sha == "fd10238301587a517815ea573d5923731d768c4ded9e99f972e4b941a77f09ff"
    finally:
        await classifier.close()


async def test_provider_real_model_main_compatibility_and_optional_plan(tmp_path, monkeypatch, model):
    from actor_runtime.classifiers import EventRuleRegistry
    from actor_minilm import provider
    monkeypatch.setattr(provider, "Classifier", lambda _: model)
    classifier = provider.MiniLMClassifier(EventRuleRegistry(), capture_root=tmp_path)
    context, event = typed_input()
    try:
        # No active accepted plan means the condition is genuinely unknown.
        first = await classifier.classify(context, event)
        second = await classifier.classify(planned_context(context), event)
        assert first.should_request_brain_review
        assert not second.should_request_brain_review
        assert classifier.fallbacks == 0
        rows = [json.loads(x) for x in classifier.path.read_text().splitlines()]
        inputs = {r["call_id"]: r for r in rows if r["schema"].endswith("input.v3")}
        outputs = [r for r in rows if r["schema"].endswith("output.v4")]
        assert len(inputs) == len(outputs) == 2
        for row in outputs:
            assert row["assessment_source"] == "neural"
            value = project(inputs[row["call_id"]])
            assert row["model_input"] == value.model_dump()
            assert row["model_input_sha256"] == hashlib.sha256(model.render(value).encode()).hexdigest()
        bad = deepcopy(next(iter(inputs.values())))
        bad["input"]["event"]["entry"]["payload"]["recipient_actor_id"] = "another-actor"
        with pytest.raises(ValueError, match="own view"):
            project(bad)
    finally:
        await classifier.close()


async def test_critical_rules_preserved_and_restored(tmp_path, monkeypatch, model):
    from actor_runtime.classifiers import EventAssessment, EventHints, EventRule, EventRuleRegistry
    from actor_minilm import provider
    monkeypatch.setattr(provider, "Classifier", lambda _: model)
    context, event = typed_input()
    event.hints = EventHints(potentially_critical=True, mandatory_review=True)
    expected = EventAssessment(urgency="critical", should_request_brain_review=True, confidence=1.)
    rule = EventRule(event.entry.capability_id, event.extension_id, event.type_url, lambda _: event.hints, lambda *_: expected)
    registry = EventRuleRegistry([rule])
    classifier = provider.MiniLMClassifier(registry, capture_root=tmp_path)
    try:
        assert await classifier.classify(context, event) == expected
        assert classifier.neural_calls == 0
        rows = [json.loads(x) for x in classifier.path.read_text().splitlines()]
        assert rows[-1]["assessment_source"] == "trusted_game_rule"
    finally:
        await classifier.close()
    assert registry.lookup(event) is rule


async def test_unsupported_event_falls_back_and_capture_can_be_disabled(monkeypatch, model):
    from actor_runtime.classifiers import EventRuleRegistry
    from actor_minilm import provider
    monkeypatch.setattr(provider, "Classifier", lambda _: model)
    classifier = provider.MiniLMClassifier(EventRuleRegistry())
    context, event = typed_input()
    event.type_url = "type.googleapis.com/unsupported.Event"
    try:
        result = await classifier.classify(context, event)
        assert not result.should_request_brain_review
        assert classifier.fallbacks == 1 and classifier.path is None
        assert classifier.inputs == {}
    finally:
        await classifier.close()


async def test_timeout_does_not_queue_additional_inference(monkeypatch, model):
    from actor_runtime.classifiers import EventRuleRegistry
    from actor_minilm import provider
    monkeypatch.setattr(provider, "Classifier", lambda _: model)
    calls = []
    def slow(_):
        calls.append(1)
        time.sleep(.05)
        return {"should_request_brain_review": False}
    monkeypatch.setattr(model, "predict", slow)
    classifier = provider.MiniLMClassifier(EventRuleRegistry(), timeout=.001)
    context, event = typed_input()
    try:
        await classifier.classify(context, event)
        await classifier.classify(context, event)
        assert classifier.fallbacks == 2 and len(calls) == 1
    finally:
        await classifier.close()


async def test_capture_write_failure_does_not_change_prediction(monkeypatch, model, tmp_path):
    from actor_runtime.classifiers import EventRuleRegistry
    from actor_minilm import provider
    import actor_minilm.capture as capture
    monkeypatch.setattr(provider, "Classifier", lambda _: model)
    classifier = provider.MiniLMClassifier(EventRuleRegistry(), capture_root=tmp_path)
    def fail(*_):
        raise OSError("test disk error")
    monkeypatch.setattr(capture.os, "write", fail)
    try:
        context, event = typed_input()
        result = await classifier.classify(context, event)
        assert result.should_request_brain_review
        assert classifier.fallbacks == 0
        assert classifier.counts["capture_errors"] == 2
    finally:
        await classifier.close()


async def test_capture_is_self_only_and_trace_sink_is_restored(tmp_path, model, monkeypatch):
    from actor_runtime.classifiers import EventRuleRegistry
    from dataclasses import replace
    from actor_minilm import provider
    monkeypatch.setattr(provider, "Classifier", lambda _: model)
    classifier = provider.MiniLMClassifier(EventRuleRegistry(), capture_root=tmp_path)
    original = lambda *_: None
    session = SimpleNamespace(world=SimpleNamespace(self_actor_id="self"), trace_sink=original,
        trace_errors=0, session_id="session-1")
    router = SimpleNamespace(session=session)
    classifier.bind_router(router)
    classifier.bind_router(router)
    context, event = typed_input()
    foreign = replace(context.relevant_state[0], entity_id="another-actor")
    context = replace(context, relevant_state=(*context.relevant_state, foreign))
    try:
        await classifier.classify(context, event)
        records = [json.loads(x) for x in classifier.path.read_text().splitlines()]
        row = next(r for r in records if r["schema"].endswith("input.v3"))
        assert all(x["entity_id"] == "self" for x in row["input"]["context"]["relevant_state"])
        assert classifier.path.stat().st_mode & 0o777 == 0o600
        assert session.trace_sink is not original
    finally:
        await classifier.close()
    assert session.trace_sink is original


@pytest.mark.parametrize("cancel_close", [False, True])
async def test_close_releases_capture_without_waiting_forever_for_inference(tmp_path, model, monkeypatch, cancel_close):
    from actor_runtime.classifiers import EventRuleRegistry
    from actor_minilm import provider
    monkeypatch.setattr(provider, "Classifier", lambda _: model)
    classifier = provider.MiniLMClassifier(EventRuleRegistry(), capture_root=tmp_path, timeout=.01)
    original = lambda *_: None
    session = SimpleNamespace(world=SimpleNamespace(self_actor_id="self"), trace_sink=original,
        trace_errors=0, session_id="session-1")
    classifier.bind_router(SimpleNamespace(session=session))
    gate = asyncio.Event()
    pending = classifier._inference = asyncio.create_task(gate.wait())
    try:
        closing = asyncio.create_task(classifier.close())
        if cancel_close:
            await asyncio.sleep(0)
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
        else:
            await asyncio.wait_for(closing, .5)
            assert classifier.counts["shutdown_inference_pending"] == 1
        assert classifier.fd is None
        assert session.trace_sink is original
        assert not pending.cancelled()
    finally:
        gate.set()
        await pending


async def test_throwing_previous_sink_does_not_prevent_capture_cleanup(tmp_path, model, monkeypatch):
    from actor_runtime.classifiers import EventRuleRegistry
    from actor_minilm import provider
    monkeypatch.setattr(provider, "Classifier", lambda _: model)
    classifier = provider.MiniLMClassifier(EventRuleRegistry(), capture_root=tmp_path)
    def broken(*_):
        raise OSError("previous local observer failed")
    session = SimpleNamespace(world=SimpleNamespace(self_actor_id="self"), trace_sink=broken,
        trace_errors=0, session_id="session-1")
    classifier.bind_router(SimpleNamespace(session=session))
    await classifier.close()
    assert classifier.fd is None
    assert session.trace_sink is broken
    assert classifier.counts["trace_callback_errors"] == 1


async def test_async_factory_keeps_connection_loop_running_during_cold_start(monkeypatch, model):
    import threading
    from actor_minilm import provider
    from actor_runtime.classifiers import ClassifierRegistry, EventRuleRegistry
    started, release = threading.Event(), threading.Event()
    def load(_):
        started.set()
        if not release.wait(timeout=1):
            raise TimeoutError("Event loop could not service the connection during model load")
        return model
    monkeypatch.setattr(provider, "Classifier", load)
    monkeypatch.delenv("ACTOR_MINILM_CAPTURE_ROOT", raising=False)
    creating = asyncio.create_task(ClassifierRegistry.create_async("minilm", rules=EventRuleRegistry()))
    try:
        async with asyncio.timeout(.5):
            while not started.is_set():
                await asyncio.sleep(.001)
        # A heartbeat callback can run before the model finishes loading.
        release.set()
        classifier = await creating
        await classifier.close()
    finally:
        release.set()
        if not creating.done():
            creating.cancel()
            await asyncio.gather(creating, return_exceptions=True)


async def test_cancelled_model_load_does_not_create_capture_or_bind_session(monkeypatch, model, tmp_path):
    import threading
    from actor_minilm import provider
    from actor_runtime.classifiers import ClassifierRegistry, EventRuleRegistry
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def load(_):
        started.set()
        release.wait(timeout=1)
        finished.set()
        return model
    monkeypatch.setattr(provider, "Classifier", load)
    monkeypatch.setenv("ACTOR_MINILM_CAPTURE_ROOT", str(tmp_path / "not-created"))
    creating = asyncio.create_task(ClassifierRegistry.create_async("minilm", rules=EventRuleRegistry()))
    try:
        async with asyncio.timeout(.5):
            while not started.is_set():
                await asyncio.sleep(.001)
        creating.cancel()
        with pytest.raises(asyncio.CancelledError):
            await creating
        release.set()
        async with asyncio.timeout(.5):
            while not finished.is_set():
                await asyncio.sleep(.001)
        assert not (tmp_path / "not-created").exists()
    finally:
        release.set()
