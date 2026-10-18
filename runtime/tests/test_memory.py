import asyncio
from dataclasses import dataclass
import sqlite3
from threading import Event

import pytest
from google.protobuf.wrappers_pb2 import StringValue

from actor_runtime.classifiers import EventAssessment
from actor_runtime.classifiers import EventHints
from actor_runtime.config import MemoryConfig
from actor_runtime.errors import ActorError
from actor_runtime.extensions import OpaquePayload
from actor_runtime.memory import MemoryStore
from actor_runtime.world import CachedPayload


@dataclass
class EventRecord:
    entry: CachedPayload
    id: str = "session-1:event:3"
    session_id: str = "session-1"
    extension_id: str = "test.extension"
    extension_version: str = "1.0.0"
    assessment: EventAssessment = EventAssessment(relevance="relevant", urgency="critical",
                                                  should_remember=True, confidence=1.0)
    hints: EventHints = EventHints()


def event(text="Observed recall: return to hub", sequence=3, **kwargs):
    return EventRecord(CachedPayload("actor-1", "test.signal", sequence, sequence,
                                    StringValue(value=text)), **kwargs)


def store(**kwargs):
    return MemoryStore(game_id="test-game", session_id="session-1", actor_id="actor-1", **kwargs)


async def fact(memory, identity, **kwargs):
    return await memory.remember(evidence_kind="observed_fact", source_event_id=identity,
                                 summary=kwargs.pop("summary", identity), content=kwargs.pop("content", {}), **kwargs)


async def test_event_evidence_idempotence_and_immutable_returned_copies():
    async with store() as memory:
        first = await memory.remember_event(event())
        duplicate = await memory.remember_event(event("mutated replay"))
        assert first.id == duplicate.id
        assert duplicate.content["event"] == "Observed recall: return to hub"
        assert first.importance == 1.0
        assert first.evidence_kind == "observed_event"
        assert first.source_event_id == "session-1:event:3"
        assert first.extension_id == "test.extension"
        assert first.session_id == "session-1"
        first.content["event"] = "external mutation"
        assert (await memory.retrieve())[0].content["event"] == "Observed recall: return to hub"
        assert (await memory.stats())["entries"] == 1


async def test_stable_profile_persists_observed_events_and_outcomes_across_restart(tmp_path):
    path = str(tmp_path / "nested" / "memory.sqlite3")
    async with store(path=path, profile="agent-alice") as memory:
        await memory.remember_event(event())
        await memory.remember_skill_result({"skill_id": "test.wait", "status": "succeeded",
                                            "output": {"waited_seconds": 2}}, objective_id="objective-1")
        await memory.remember_objective_result({"objective_id": "objective-1", "status": "succeeded"})
    async with MemoryStore(game_id="test-game", session_id="new-session", actor_id="different-network-id",
                           path=path, profile="agent-alice") as reopened:
        entries = await reopened.retrieve(objective="recall signal")
        assert len(entries) == 3
        assert {entry.evidence_kind for entry in entries} == {"observed_event", "skill_result", "objective_result"}
        signal = next(entry for entry in entries if entry.evidence_kind == "observed_event")
        assert signal.session_id == "session-1" and signal.actor_id == "actor-1"
        assert (await reopened.stats())["persistent"]
    assert (tmp_path / "nested" / "memory.sqlite3").stat().st_mode & 0o777 == 0o600


async def test_native_logical_event_key_deduplicates_reconnect_replay(tmp_path):
    path = str(tmp_path / "replay.sqlite3")
    hints = EventHints(semantic_id="game-issued-unique-signal-uuid")
    async with store(path=path, profile="alice") as first:
        original = await first.remember_event(event(hints=hints))
    async with MemoryStore(game_id="test-game", session_id="new-bridge-session", path=path,
                           profile="alice") as second:
        replayed = await second.remember_event(event(sequence=100, id="new-bridge-session:100",
            session_id="new-bridge-session", hints=hints))
        assert replayed.id == original.id
        assert replayed.content["sequence"] == 3
        assert replayed.content["wire_event_id"] == "session-1:event:3"
        assert replayed.content["semantic_id"] == hints.semantic_id
        assert (await second.stats())["entries"] == 1


async def test_default_memory_is_session_transient_not_network_actor_identity():
    async with store() as first:
        await first.remember_event(event())
        assert first.profile == "session:session-1"
    async with store() as second:
        assert await second.retrieve() == []
        assert not (await second.stats())["persistent"]
    with pytest.raises(ValueError, match="explicit agent profile"):
        store(path="not-created.sqlite3")


async def test_profiles_games_and_clear_are_scoped_but_global_row_bound_applies(tmp_path):
    path = str(tmp_path / "shared.sqlite3")
    for profile, game in (("alice", "game-a"), ("bob", "game-a"), ("alice", "game-b")):
        async with MemoryStore(game_id=game, session_id="same-wire-actor", path=path,
                               profile=profile, max_entries=4) as memory:
            for index in range(2):
                await fact(memory, f"{profile}-{game}-{index}")
            assert len(await memory.retrieve()) == 2
            assert (await memory.stats())["global_entries"] <= 4
    async with MemoryStore(game_id="game-b", session_id="other", path=path,
                           profile="alice", max_entries=4) as memory:
        assert await memory.clear() == 2
        assert await memory.retrieve() == []
    async with MemoryStore(game_id="game-a", session_id="other", path=path,
                           profile="bob", max_entries=4) as memory:
        assert len(await memory.retrieve()) == 2


async def test_ttl_pruning_does_not_extend_duplicate_lifetime():
    now = [100.0]
    async with store(ttl_seconds=10, clock=lambda: now[0]) as memory:
        first = await memory.remember_event(event())
        now[0] = 105
        assert (await memory.remember_event(event())).expires_at == first.expires_at == 110
        now[0] = 110
        assert await memory.retrieve() == []
        assert (await memory.stats())["entries"] == 0


async def test_objective_terminal_cancellation_reconciles_same_evidence_without_extending_ttl():
    now = [100.0]
    async with store(ttl_seconds=10, clock=lambda: now[0]) as memory:
        provisional = await memory.remember_objective_result({"objective_id": "objective-1",
            "status": "succeeded", "reason": "finished"})
        now[0] = 105
        final = await memory.remember_objective_result({"objective_id": "objective-1",
            "status": "cancelled", "reason": "objective_cancelled"})
        assert final.id == provisional.id
        assert final.created_at == 100 and final.expires_at == 110
        assert final.content["status"] == "cancelled"
        retained = await memory.retrieve()
        assert len(retained) == 1 and retained[0].content["reason"] == "objective_cancelled"
        with pytest.raises(ValueError, match="Only an Objective"):
            await memory.remember(evidence_kind="observed_event", source_event_id="event", summary="event",
                                  content={}, replace_existing=True)


async def test_retrieval_uses_keyword_importance_and_filters_not_all_history():
    async with store(max_retrieved=2) as memory:
        await fact(memory, "old-critical", importance=1.0, content={"signal": "recall engineering"},
                   capability_id="test.signal", type_url="type.googleapis.com/test.Signal", tags=("station",))
        await fact(memory, "routine", importance=0.1, content={"status": "normal"}, capability_id="test.routine")
        await fact(memory, "new-resource", importance=0.5, content={"resource": "fuel"}, tags=("resource",))
        ranked = await memory.retrieve(objective="Recall engineering signal", limit=99)
        assert len(ranked) == 2 and ranked[0].source_event_id == "old-critical"
        assert [item.source_event_id for item in await memory.retrieve(capability_ids=("test.signal",))] == ["old-critical"]
        assert len(await memory.retrieve(type_urls=("type.googleapis.com/test.Signal",), tags=("station",))) == 1
        assert await memory.retrieve(tags=("missing",)) == []
        assert await memory.retrieve(limit=0) == []
        stats = await memory.stats()
        assert stats["retrieval_count"] == 5 and stats["retrieved_entries"] == 4


async def test_row_and_physical_file_bounds_survive_repeated_payload_churn(tmp_path):
    async with store(path=str(tmp_path / "bounded.sqlite3"), profile="churn", max_entries=5,
                     max_payload_bytes=1024) as memory:
        await fact(memory, "salient", content={"important": True}, importance=1.0)
        for index in range(200):
            await fact(memory, str(index), content={"text": str(index) + "x" * 900}, importance=0.1)
        stats = await memory.stats()
        assert stats["global_entries"] == 5
        assert stats["database_bytes"] <= stats["max_database_bytes"]
        assert (tmp_path / "bounded.sqlite3").stat().st_size <= stats["max_database_bytes"]
        assert "salient" in {entry.source_event_id for entry in await memory.retrieve()}


async def test_oversized_or_unknown_event_has_explicit_bounded_evidence_without_binary():
    async with store(max_payload_bytes=512) as memory:
        large = await memory.remember_event(event("x" * 4096))
        assert "x" * 100 not in large.model_dump_json()
        assert large.content_truncated and "payload_omitted" in large.model_dump_json()
        raw = CachedPayload("actor-1", "unknown.optional", 4, 4,
                            OpaquePayload("type.googleapis.com/unknown.Event", b"private raw binary"))
        opaque = await memory.remember_event(raw)
        assert opaque.content["opaque"]
        assert "private raw binary" not in opaque.model_dump_json()
        assert opaque.type_url == "type.googleapis.com/unknown.Event"
        bounded = await fact(memory, "huge", content={"message": "я" * 2048})
        assert bounded.content_truncated
        assert len(__import__("json").dumps(bounded.content).encode()) < 512


async def test_credentials_and_hidden_reasoning_not_persisted_in_structured_content():
    async with store() as memory:
        entry = await fact(memory, "public-result", content={"message": "Observed", "api_key": "secret-key",
            "accessToken": "camel-case-secret", "actorBridgeToken": "runtime-token",
            "nested": [{"authorization": "Bearer secret", "hidden_reasoning": "private", "reason": "skill_failed"}]})
        assert entry.content == {"message": "Observed", "nested": [{"reason": "skill_failed"}]}
        assert "secret-key" not in (await memory.retrieve())[0].model_dump_json()


@pytest.mark.parametrize("kind", ["corrupt", "foreign", "empty"])
async def test_bad_database_is_explicitly_degraded_and_never_deleted_or_replaced(tmp_path, kind):
    path = tmp_path / "user.sqlite3"
    if kind == "foreign":
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE users(name TEXT)")
            db.execute("INSERT INTO users VALUES ('keep me')")
    else:
        path.write_bytes(b"not sqlite, keep original" if kind == "corrupt" else b"")
    before = path.read_bytes()
    memory = store(path=str(path), profile="alice")
    try:
        with pytest.raises(ActorError) as failure:
            await memory.remember_event(event())
        assert failure.value.code == "memory_unavailable"
        assert memory.state == "degraded"
        assert (await memory.stats())["state"] == "degraded"
        assert path.read_bytes() == before
        with pytest.raises(ActorError, match="degraded"):
            await memory.retrieve()
    finally:
        await memory.close()


async def test_missing_database_parent_path_error_is_controlled(tmp_path):
    parent = tmp_path / "not-a-directory"
    parent.write_text("owned existing data")
    memory = store(path=str(parent / "db.sqlite3"), profile="alice")
    try:
        with pytest.raises(ActorError) as failure:
            await memory.retrieve()
        assert failure.value.code == "memory_unavailable"
        assert parent.read_text() == "owned existing data"
    finally:
        await memory.close()


async def test_administration_stats_never_prunes_large_history_or_expired_other_profiles(tmp_path):
    path = tmp_path / "large history #1.sqlite3"
    # Build a valid large-history fixture in one transaction. The production
    # store creates/validates its format; bulk fixture insertion avoids thousands
    # of unrelated fsyncs in a read-only administration regression.
    async with store(path=str(path), profile="alice", max_entries=8000) as writer:
        await fact(writer, "seed")
    with sqlite3.connect(path) as db:
        prototype = list(db.execute("SELECT * FROM memories").fetchone())
        rows = []
        for index in range(5000):
            row = list(prototype)
            row[0], row[6] = f"fixture-{index}", f"source-{index}"
            if index >= 4990:
                row[1] = "bob"
                row[17] = 1.0  # even expired other-profile evidence stays intact
            rows.append(row)
        db.executemany("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
    before = path.read_bytes()
    async with store(path=str(path), profile="alice", max_entries=8, max_payload_bytes=512,
                     administration=True) as admin:
        assert path.stat().st_size > admin.max_database_bytes  # valid older file may exceed admin defaults
        stats = await admin.stats()
        assert stats["entries"] == 4991 and stats["global_entries"] == 5001
        assert stats["administration"] and not stats["limits_enforced"]
        assert path.read_bytes() == before
        with pytest.raises(ActorError) as failure:
            await admin.retrieve()
        assert failure.value.code == "memory_administration"
        assert await admin.clear() == 4991
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM memories").fetchone()[0] == 10
        assert db.execute("SELECT DISTINCT profile FROM memories").fetchall() == [("bob",)]


async def test_administration_missing_database_does_not_create_file_or_directories(tmp_path):
    path = tmp_path / "not-created" / "memory.sqlite3"
    async with store(path=str(path), profile="alice", administration=True) as admin:
        stats = await admin.stats()
        assert stats["state"] == "missing" and stats["entries"] == 0
        assert await admin.clear() == 0
        assert not path.exists() and not path.parent.exists()


async def test_disk_worker_does_not_block_event_loop_and_cancelled_write_is_still_serialized():
    memory = store()
    entered, release = Event(), Event()
    def blocked_work():
        entered.set()
        release.wait(2)
    blocker = asyncio.create_task(memory._call(blocked_work))
    try:
        async with asyncio.timeout(1):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        pending_write = asyncio.create_task(fact(memory, "queued-evidence"))
        await asyncio.sleep(0.01)  # a blocked SQLite worker cannot freeze ingest/timers
        assert not pending_write.done()
        pending_write.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending_write
        release.set()
        await blocker
        assert (await memory.retrieve())[0].source_event_id == "queued-evidence"
    finally:
        release.set()
        await memory.close()


async def test_executor_submission_queue_is_bounded_under_disk_backpressure():
    memory = store()
    entered, release = Event(), Event()
    def blocked_work():
        entered.set()
        release.wait(2)
    operations = [asyncio.create_task(memory._call(blocked_work)) for _ in range(32)]
    try:
        async with asyncio.timeout(1):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        with pytest.raises(ActorError) as failure:
            await memory.retrieve()
        assert failure.value.code == "memory_busy"
        assert memory._pending == 32
    finally:
        release.set()
        await asyncio.gather(*operations)
        await memory.close()


async def test_disabled_close_and_config_convenience(session):
    memory = MemoryStore.from_config(MemoryConfig(enabled=False), session)
    assert await memory.remember_event(event()) is None
    assert await memory.retrieve() == []
    assert (await memory.stats())["state"] == "disabled"
    await memory.close()
    await memory.close()
    with pytest.raises(ActorError) as failure:
        await memory.retrieve()
    assert failure.value.code == "memory_closed"
