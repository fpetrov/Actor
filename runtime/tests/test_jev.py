import asyncio
import sys
from types import SimpleNamespace

import pytest
from google.protobuf.struct_pb2 import Struct

from actor_jev.classifier import JevEventClassifier
from actor_runtime.classifiers import ClassificationContext, EventHints, EventRuleRegistry
from actor_runtime.config import ClassifierConfig
from actor_runtime.events import RoutedEvent
from actor_runtime.objectives import Objective
from actor_runtime.world import CachedPayload


def sample():
    payload = Struct()
    payload.update({"message": "A new fact", "api_key": "must-not-leave", "details": "x"*12000})
    return RoutedEvent("s:3:event", "s", CachedPayload("actor", "test.event", 3, 3, payload),
        "test", "1", "type.googleapis.com/google.protobuf.Struct", EventHints())


def answer(*, confidence=.99, interrupt=.1, review=.1):
    return SimpleNamespace(choices={"relevance": SimpleNamespace(choice="relevant", confidence=confidence)},
        scores={"urgency": SimpleNamespace(score=0., confidence=confidence)},
        nouls={"remember": SimpleNamespace(noul=.9), "interrupt": SimpleNamespace(noul=interrupt),
               "review": SimpleNamespace(noul=review)})


@pytest.fixture
def context():
    return ClassificationContext(Objective(text="Observe event"))


@pytest.fixture(autouse=True)
def fake_question_surface(monkeypatch):
    # Public SDK names only; deterministic CI does not install the optional SDK.
    def primitive(**kwargs):
        return kwargs
    monkeypatch.setitem(sys.modules, "typesafe_sdk", SimpleNamespace(Choice=primitive, Score=primitive, Noul=primitive))


async def test_missing_api_key_is_explicit_local_fallback(monkeypatch, context):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    provider = JevEventClassifier(rules=EventRuleRegistry())
    result = await provider.classify(context, sample())
    assert result.provider == "rules-fallback" and result.reason_code == "jev_api_key_missing"
    assert result.should_buffer and provider.counts["external_requests"] == 0
    await provider.close()


async def test_provider_timeout_is_bounded_and_falls_back(context):
    class Client:
        async def system_one(self, **_):
            await asyncio.sleep(10)
        async def aclose(self):
            self.closed = True
    client = Client()
    provider = JevEventClassifier(config=ClassifierConfig(timeout=.01), rules=EventRuleRegistry(), client_factory=lambda: client)
    result = await provider.classify(context, sample())
    assert result.reason_code == "jev_timeout" and provider.counts["external_requests"] == 1
    await provider.close()
    assert client.closed


async def test_rate_limit_and_concurrency_never_build_unbounded_waiters(context):
    entered, release = asyncio.Event(), asyncio.Event()
    class Client:
        async def system_one(self, **_):
            entered.set()
            await release.wait()
            return answer()
        async def aclose(self): pass
    provider = JevEventClassifier(config=ClassifierConfig(requests_per_minute=1),
        rules=EventRuleRegistry(), client_factory=Client)
    first = asyncio.create_task(provider.classify(context, sample()))
    await entered.wait()
    second = await provider.classify(context, sample())
    assert second.reason_code == "jev_concurrency_limited"
    release.set()
    assert (await first).provider == "jev"
    third = await provider.classify(context, sample())
    assert third.reason_code == "jev_rate_limited"
    assert provider.counts["external_requests"] == 1
    await provider.close()


async def test_http429_bounded_retry_then_typed_response(context):
    class RateLimit(Exception):
        status = 429
        headers = {}
    class Client:
        count = 0
        async def system_one(self, **_):
            self.count += 1
            if self.count == 1:
                raise RateLimit()
            return answer()
        async def aclose(self): pass
    provider = JevEventClassifier(rules=EventRuleRegistry(), client_factory=Client)
    result = await provider.classify(context, sample())
    assert result.provider == "jev"
    assert provider.counts["external_requests"] == 2 and provider.counts["http_429"] == 1
    await provider.close()


async def test_long_retry_after_falls_back_without_early_retry(context):
    class RateLimit(Exception):
        status = 429
        headers = {"retry-after": "60"}
    class Client:
        async def system_one(self, **_): raise RateLimit()
        async def aclose(self): pass
    provider = JevEventClassifier(rules=EventRuleRegistry(), client_factory=Client)
    assert (await provider.classify(context, sample())).reason_code == "jev_retry_after_deferred"
    assert provider.counts["external_requests"] == 1
    await provider.close()


async def test_ambiguous_assessment_requests_review_instead_of_ignore(context):
    class Client:
        async def system_one(self, **_): return answer(confidence=.4, interrupt=.5)
        async def aclose(self): pass
    provider = JevEventClassifier(rules=EventRuleRegistry(), client_factory=Client)
    result = await provider.classify(context, sample())
    assert result.should_remember and result.should_request_brain_review
    assert result.relevance == "uncertain" and not result.should_interrupt_skill
    await provider.close()


@pytest.mark.parametrize("field,value", [("choice_confidence", 1.5), ("score_confidence", float("nan")),
    ("score_confidence", float("inf")), ("score", float("nan")), ("score", float("inf")), ("score", 3.0)])
async def test_malformed_numeric_provider_answers_use_deterministic_fallback(context, field, value):
    response = answer()
    if field == "choice_confidence":
        response.choices["relevance"].confidence = value
    elif field == "score_confidence":
        response.scores["urgency"].confidence = value
    else:
        response.scores["urgency"].score = value
    class Client:
        async def system_one(self, **_): return response
        async def aclose(self): pass
    provider = JevEventClassifier(rules=EventRuleRegistry(), client_factory=Client)
    try:
        assessed = await provider.classify(context, sample())
        assert assessed.provider == "rules-fallback"
        assert assessed.reason_code == "jev_provider_error"
        assert not assessed.should_interrupt_skill
        assert provider.counts["external_requests"] == 1
    finally:
        await provider.close()


async def test_critical_metadata_is_never_downgraded_by_semantic_provider(context):
    event = sample()
    event.hints = EventHints(potentially_critical=True)
    def forbidden():
        raise AssertionError("critical data must remain local deterministic")
    provider = JevEventClassifier(rules=EventRuleRegistry(), client_factory=forbidden)
    await provider.classify(context, event)
    assert provider.counts["external_requests"] == 0
    await provider.close()


async def test_minimal_external_state_is_bounded_redacted_and_questions_typed(context):
    captured = {}
    class Client:
        async def system_one(self, **kwargs):
            captured.update(kwargs)
            return answer()
        async def aclose(self): pass
    provider = JevEventClassifier(config=ClassifierConfig(max_request_bytes=1024),
        rules=EventRuleRegistry(), client_factory=Client)
    await provider.classify(context, sample())
    import json
    assert len(json.dumps(captured["state"]).encode()) <= 1024
    assert "must-not-leave" not in json.dumps(captured["state"])
    assert set(captured["questions"]) == {"relevance", "urgency", "remember", "interrupt", "review"}
    assert "criteria" in captured["questions"]["relevance"]
    await provider.close()
