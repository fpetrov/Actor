import asyncio
import time

import pytest

from actor_agents_arena import (ROOM_CHANGED, STATION_SIGNAL, STATION_WIDE,
                                ROUTINE_NOTICE, URGENT_RECALL, RoomChangedEvent, StationSignalEvent)
from actor_runtime.classifiers import EventAssessment, EventHints, EventRule, EventRuleRegistry
from actor_runtime.config import RouterConfig
from actor_runtime.errors import ActorError
from actor_runtime.events import EventRouter
from actor_runtime.extensions import type_url
from actor_runtime.memory import MemoryStore
from actor_runtime.objectives import Objective
from actor_runtime.world import CachedPayload


def signal(sequence=3, *, kind=URGENT_RECALL, identity=None):
    return CachedPayload("actor-1", STATION_SIGNAL, sequence, sequence,
        StationSignalEvent(signal_id=identity or f"unique-{sequence}", signal_kind=kind,
            source_actor_id="human", target_room_id="main-hall", message="Observed signal",
            audience=STATION_WIDE, server_unix_ms=int(time.time() * 1000)))


async def spin(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(.001)


class BlockedAdvisoryClassifier:
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.closed = False

    async def classify(self, *_):
        self.entered.set()
        await self.release.wait()
        return EventAssessment(relevance="unrelated", confidence=1.0)

    async def close(self):
        self.closed = True


async def blocked_router(session, *, memory=None):
    classifier = BlockedAdvisoryClassifier()
    router = EventRouter(session, classifier=classifier, memory=memory)
    await router.start(Objective(text="Return on urgent recall"))
    router.ingest(CachedPayload("actor-1", ROOM_CHANGED, 3, 3, RoomChangedEvent()))
    await classifier.entered.wait()
    return router, classifier


async def test_protected_dedup_overflow_stays_bounded_after_failure_under_continued_ingress(session):
    router = EventRouter(session, config=RouterConfig(max_dedup_keys=128, max_pending_events=256,
                                                      max_queue_size=256))
    try:
        for sequence in range(3, 1003):
            router.ingest(signal(sequence))
        assert router.degraded and router.degraded_reason == "critical_dedup_overflow"
        assert len(router._dedup) == 128
        assert len(router._pending) == len(router._queue) == 128
        assert router.report()["counts"]["degraded_transitions"] == 1
    finally:
        await router.close()


async def test_acknowledged_trigger_queued_behind_classifier_is_released_after_processing(session):
    async with MemoryStore(game_id="test-game", session_id=session.session_id) as memory:
        router, classifier = await blocked_router(session, memory=memory)
        try:
            router.ingest(signal(4))
            event = router.take_trigger_events()[0]
            router.acknowledge_context([event.id], router.cognitive_revision)
            assert router.acknowledge_decision([event.id], router.cognitive_revision)
            assert event.handled and event.id in router._pending
            classifier.release.set()
            await spin(lambda: event.id not in router._pending)
            assert event.remembered and event.id not in router._queue
            assert (await memory.retrieve())[0].evidence_kind == "observed_event"
        finally:
            await router.close()


async def test_acknowledged_inflight_write_retains_evidence_until_commit(session):
    class SlowMemory(MemoryStore):
        entered, release = None, None
        async def remember_event(self, *args, **kwargs):
            self.entered.set()
            await self.release.wait()
            return await super().remember_event(*args, **kwargs)
    async with SlowMemory(game_id="test-game", session_id=session.session_id) as memory:
        memory.entered, memory.release = asyncio.Event(), asyncio.Event()
        router = EventRouter(session, memory=memory)
        await router.start(Objective(text="React to recall"))
        try:
            router.ingest(signal())
            event = router.take_trigger_events()[0]
            await memory.entered.wait()
            router.acknowledge_context([event.id], 1)
            router.acknowledge_decision([event.id], 1)
            assert event.id in router._pending and not event.remembered
            memory.release.set()
            await spin(lambda: event.id not in router._pending)
            assert event.remembered and (await memory.stats())["entries"] == 1
        finally:
            memory.release.set()
            await router.close()


async def test_close_flushes_critical_memory_that_has_not_reached_sqlite_worker(session, tmp_path):
    path = str(tmp_path / "history.sqlite3")
    async with MemoryStore(game_id="test-game", session_id=session.session_id,
                           path=path, profile="persistent-agent") as memory:
        router, classifier = await blocked_router(session, memory=memory)
        router.ingest(signal(4))
        event = router.take_trigger_events()[0]
        router.acknowledge_context([event.id], 1)
        router.acknowledge_decision([event.id], 1)
        assert event.id in router._queue and not event.remembered
        await router.close()
        assert classifier.closed and event.remembered
        assert event.id not in router._pending and event.id not in router._queue
    async with MemoryStore(game_id="test-game", session_id="next-session", path=path,
                           profile="persistent-agent") as reopened:
        evidence = await reopened.retrieve()
        assert len(evidence) == 1 and evidence[0].capability_id == STATION_SIGNAL


async def test_close_reports_failed_required_history_and_still_closes_provider(session):
    class BrokenMemory:
        async def remember_event(self, *_args, **_kwargs):
            raise ActorError("memory_unavailable", "Disk unavailable")
    router, classifier = await blocked_router(session, memory=BrokenMemory())
    router.ingest(signal(4))
    with pytest.raises(ActorError) as failure:
        await router.close()
    assert failure.value.code == "memory_unavailable"
    assert classifier.closed and router.degraded
    assert router.degraded_reason == "memory_flush_failed"


async def test_aggregation_key_is_scoped_to_extension_capability_and_entity(session):
    hints = lambda _: EventHints(aggregation_key="routine")
    assessment = lambda *_: EventAssessment(relevance="unrelated", confidence=1.0)
    rules = EventRuleRegistry([
        EventRule(STATION_SIGNAL, "agents-arena", type_url(StationSignalEvent), hints, assessment),
        EventRule(ROOM_CHANGED, "agents-arena", type_url(RoomChangedEvent), hints, assessment),
    ])
    router = EventRouter(session, rules=rules)
    try:
        router.ingest(signal(kind=ROUTINE_NOTICE))
        router.ingest(CachedPayload("actor-1", ROOM_CHANGED, 4, 4, RoomChangedEvent()))
        router.ingest(CachedPayload("other-entity", ROOM_CHANGED, 5, 5, RoomChangedEvent()))
        router.ingest(CachedPayload("actor-1", ROOM_CHANGED, 6, 6, RoomChangedEvent()))
        assert len(router._pending) == 3
        assert router.report()["counts"]["events_aggregated"] == 1
        room = next(event for event in router._pending.values()
                    if event.entry.capability_id == ROOM_CHANGED and event.entry.entity_id == "actor-1")
        assert room.aggregate_count == 2
    finally:
        await router.close()
