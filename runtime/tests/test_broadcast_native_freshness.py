"""Real Native/projector/scheduler/coordinator gates; fake clocks and IO only."""
import asyncio
import json
from types import SimpleNamespace as Obj

import pytest

import actor_broadcast.native as native
from actor_broadcast.config import CommentaryConfig
from actor_broadcast.contracts import FlushAck, SegmentKey, SpeechSegment
from actor_broadcast.coordinator import BroadcastCoordinator, CoordinatorError
from actor_broadcast.scheduler import CommentaryScheduler
from actor_runtime.world import CachedPayload, WorldState
from extensions.agents_arena.v1 import broadcast_pb2 as pb, social_pb2 as social

SESSION, MATCH, OBSERVER = "a" * 32, "b" * 32, "c" * 32
CAPABILITY = "agents-arena.broadcast-presented-view"


class Clock:
    now = 100.0
    def monotonic(self): return self.now
    def time(self): return 1000 + self.now
    def advance(self, seconds): self.now += seconds


class Speech:
    def __init__(self): self.cancelled = []; self.submitted = []
    async def submit(self, segment): self.submitted.append(segment)
    async def cancel(self, cancel, **kwargs):
        self.cancelled.append(cancel)
        return FlushAck(cancel.scope, cancel.utterance_id, cancel.utterance_epoch, True)


class Obs:
    route_verified = True
    def __init__(self):
        self.starts = self.stops = self.emergencies = 0
        self.entered = asyncio.Event(); self.release = asyncio.Event(); self.block = False
    async def start_stream(self, permit):
        self.starts += 1; self.entered.set()
        if self.block: await self.release.wait()
    async def stop_stream(self): self.stops += 1
    async def emergency_stop(self): self.emergencies += 1


class Harness:
    def __init__(self, monkeypatch):
        self.clock = Clock(); monkeypatch.setattr(native, "time", self.clock)
        self.unsafe = []; self.speech = Speech(); self.observation_sequence = 0; self.view_sequence = 0
        async def report(): return {}
        budget = Obj(config=CommentaryConfig(), report=report)
        provider = Obj()
        self.scheduler = CommentaryScheduler(provider, self.speech, budget,
            clock=self.clock.monotonic, wall_clock=self.clock.time)
        factory = Obj(config=Obj(chat_enabled=False), dependencies=native.NativeDependencies(), store=None, guardian=None)
        self.run = native.PreparedNativeRun(factory, "ABC123", SESSION, CommentaryConfig(),
            lambda _: None, self.unsafe.append, lambda: None)
        self.run.observer_id = OBSERVER; self.run.scheduler = self.scheduler; self.run.budget = budget
        self.run.session = Obj(world=WorldState(OBSERVER), closed=asyncio.Event(), commands=[])
        self.run.projector = native.ArenaBroadcastProjector(observer_id=OBSERVER,
            broadcast_session_id=SESSION, match_id=MATCH, clock=self.clock.monotonic)
        self.run._flags["observer"] = True
        self.obs = self.run.obs = Obs()
        self.run.attestor = Obj(verified=True)

    def frame(self, *, sequence=None, expired=False, generation=1, epoch=1, session=SESSION, receipt=None):
        self.observation_sequence += 1
        self.view_sequence = self.view_sequence + 1 if sequence is None else sequence
        value = pb.BroadcastPresentedViewObservation()
        view = value.view
        view.scope.CopyFrom(pb.BroadcastScope(broadcast_session_id=session, match_id=MATCH,
            observer_id=OBSERVER, connection_generation=generation, view_epoch=epoch))
        view.sequence = view.semantic_revision = self.view_sequence
        view.phase = social.SOCIAL_PHASE_EXPLORATION
        view.observed_at_unix_ms = int(self.clock.time() * 1000) - (1000 if expired else 0)
        view.expires_at_unix_ms = view.observed_at_unix_ms + 500
        fact = view.facts.add(evidence_id="public-fact", version=1, source_event_id="public-event",
            kind=pb.BROADCAST_FACT_KIND_OBSERVATION, predicate="actor-seen", priority=3,
            observed_at_unix_ms=view.observed_at_unix_ms, retention_expires_at_unix_ms=view.observed_at_unix_ms + 60000)
        value.presentation.scope.CopyFrom(view.scope)
        value.presentation.rendered_sequence = view.sequence
        value.presentation.frame_counter = view.sequence
        value.presentation.width = 800; value.presentation.height = 600
        value.presentation.presented_evidence.add(evidence_id=fact.evidence_id, version=fact.version)
        entry = CachedPayload(OBSERVER, CAPABILITY, self.observation_sequence, self.observation_sequence, value,
            self.clock.monotonic() if receipt is None else receipt)
        self.run.session.world.apply_observations([entry], self.observation_sequence, self.observation_sequence)
        self.run._capture()
        return value

    def pending_segment(self):
        view = self.scheduler.view
        segment = SpeechSegment(view.scope, SegmentKey("owned-utterance", self.scheduler._epoch, 0), 3,
            "Разрешённый факт.", (view.facts[0].ref,), view.semantic_revision, int((self.clock.now + 10) * 1e9))
        self.scheduler._speech_pending[(segment.scope, segment.key)] = (segment, None)
        return segment

    async def guard_once(self, monkeypatch):
        async def refresh(): return True
        self.run.attestor.refresh = refresh
        async def stop_loop(_): self.run._closing = True
        monkeypatch.setattr(native, "asyncio", Obj(sleep=stop_loop, CancelledError=asyncio.CancelledError,
            create_task=asyncio.create_task))
        await self.run._guard_loop()
        self.run._closing = False
        for task in tuple(self.run._tasks): await task


@pytest.mark.asyncio
async def test_brief_expiry_fences_real_segment_cancel_camera_and_ready_without_fatal(monkeypatch):
    h = Harness(monkeypatch); h.frame(); h.run._check_feed(); assert h.scheduler.ready()
    segment = h.pending_segment(); assert h.scheduler.validate_segment(segment)
    h.clock.advance(.65)
    assert not h.scheduler.validate_segment(segment)  # PCM admission closes before guard/await.
    assert not h.run.readiness()["observer"]
    epoch = h.scheduler._epoch
    await h.guard_once(monkeypatch)
    assert not h.unsafe and h.run._failure is None and h.scheduler._epoch > epoch
    assert h.scheduler._needs_cancel
    await h.scheduler.step()
    assert len(h.speech.cancelled) == 1 and not h.scheduler._speech_pending and not h.speech.submitted
    h.run._activated = True
    h.run.director = Obj(next_intent=lambda: pytest.fail("expired view cannot move camera"))
    h.run._direct_camera()
    assert not h.run.session.commands and h.run._route_guard()  # Stop/cleanup route is independent.
    h.run.director = None; h.frame(); h.run._check_feed()
    assert h.run.readiness()["observer"] and h.scheduler._fresh() and not h.unsafe
    assert not h.scheduler.validate_segment(segment)  # New ACK never revives cancelled speech epoch.
    assert [row["code"] for row in h.run.feed_health_report()["transitions"]] == ["presentation_expired", "fresh"]


@pytest.mark.asyncio
async def test_no_unique_ack_beyond_three_seconds_is_fatal(monkeypatch):
    h = Harness(monkeypatch); h.frame(); h.run._check_feed()
    h.clock.advance(3.001)
    await h.guard_once(monkeypatch)
    assert h.unsafe == ["broadcast_feed_stale"] and h.run._failure == "broadcast_feed_stale"
    assert h.run.feed_health_report()["fatal"]["unique_ack_age_ms"] == 3001
    assert not h.run.readiness()["observer"] and h.obs.emergencies == 1


@pytest.mark.parametrize("mode", ["duplicate", "expired", "missing"])
def test_bridge_activity_cannot_launder_unique_ack_deadline(monkeypatch, mode):
    h = Harness(monkeypatch); h.frame(); initial = h.run._last_presented_at
    for _ in range(7):
        h.clock.advance(.45)
        if mode == "duplicate": h.frame(sequence=1)  # Even changed timestamps cannot renew the old ACK.
        elif mode == "expired": h.frame(expired=True)
        else: h.run.session.world.observations.clear(); h.run._capture()
    assert h.run._last_presented_at == initial
    with pytest.raises(native.NativeError, match="broadcast_feed_stale"): h.run._check_feed()
    assert h.scheduler.view.sequence == 1 and not h.run.readiness()["observer"]


@pytest.mark.parametrize("change", [{"session": "d" * 32}, {"generation": 1, "epoch": 99}])
def test_invalid_scope_still_fails_immediately_without_renewing_liveness(monkeypatch, change):
    h = Harness(monkeypatch); h.frame(generation=2, epoch=2); initial = h.run._last_presented_at
    h.clock.advance(.1)
    with pytest.raises(native.NativeError, match="broadcast_feed_invalid"): h.frame(**change)
    assert h.run._last_presented_at == initial


@pytest.mark.asyncio
async def test_disconnect_remains_immediately_fatal_inside_short_expiry_window(monkeypatch):
    h = Harness(monkeypatch); h.frame(); h.clock.advance(.1); h.run.session.closed.set()
    await h.guard_once(monkeypatch)
    assert h.unsafe == ["broadcast_feed_disconnected"] and h.obs.emergencies == 1


def test_original_receipt_timestamp_is_not_replaced_by_projection_time(monkeypatch):
    h = Harness(monkeypatch); receipt = h.clock.now - .2
    h.frame(receipt=receipt)
    assert h.run._last_presented_at == receipt
    assert h.run.feed_health_report()["fatal"] is None


def test_future_receipt_is_invalid_not_extra_liveness(monkeypatch):
    h = Harness(monkeypatch)
    with pytest.raises(native.NativeError, match="broadcast_feed_invalid"): h.frame(receipt=h.clock.now + 1)
    assert h.run._last_presented_at is None


def controller(h):
    async def rooms(): return []
    result = BroadcastCoordinator(Obj(rooms=rooms), chat_enabled=False)
    result._run = h.run; result.state = "PreviewReady"; result.broadcast_session_id = SESSION
    result._ready = dict.fromkeys(result._ready, True)
    return result


def permit():
    return {"channel": "fedorr_codex", "broadcast_session_id": SESSION, "confirmation": "START PUBLIC STREAM"}


@pytest.mark.asyncio
async def test_dynamic_status_and_expired_start_refusal_recover_without_fatal(monkeypatch):
    h = Harness(monkeypatch); h.frame(); c = controller(h)
    h.run._flags.update(dict.fromkeys(("model", "tts", "obs", "audio"), True))
    h.clock.advance(.65)
    assert (await c.status())["ready"]["observer"] is False
    with pytest.raises(CoordinatorError, match="broadcast_preview_not_ready"): await c.execute("public_start", permit())
    assert h.obs.starts == 0 and c.state == "PreviewReady" and not h.unsafe
    h.frame()
    assert (await c.status())["ready"]["observer"] is True
    await c.execute("public_start", permit()); await c._start_task
    assert c.state == "Live" and h.obs.starts == 1


@pytest.mark.asyncio
async def test_queued_public_start_rechecks_freshness_before_any_obs_start(monkeypatch):
    h = Harness(monkeypatch); h.frame(); c = controller(h)
    h.run._flags.update(dict.fromkeys(("model", "tts", "obs", "audio"), True))
    await c.execute("public_start", permit())
    h.clock.advance(.65); await c._start_task
    assert h.obs.starts == 0 and c.state == "PreviewReady" and not h.unsafe


@pytest.mark.asyncio
async def test_expiry_during_owned_start_drains_stream_without_committing_live_or_losing_route(monkeypatch):
    h = Harness(monkeypatch); h.frame(); c = controller(h)
    h.run._flags.update(dict.fromkeys(("model", "tts", "obs", "audio"), True))
    h.obs.block = True
    await c.execute("public_start", permit()); await h.obs.entered.wait()
    h.clock.advance(.65); h.obs.release.set(); await c._start_task
    assert h.obs.starts == h.obs.stops == 1 and h.obs.emergencies == 0
    assert c.state == "PreviewReady" and c.metrics["public_starts"] == 0 and h.run._route_guard()
    assert not h.scheduler._public_intro_marked


@pytest.mark.asyncio
async def test_failed_drain_after_start_is_still_unsafe_and_never_live(monkeypatch):
    h = Harness(monkeypatch); h.frame(); c = controller(h)
    h.run._flags.update(dict.fromkeys(("model", "tts", "obs", "audio"), True))
    h.obs.block = True; unsafe = []
    c.unsafe = lambda code, **kwargs: unsafe.append(code)
    async def failed_stop(): raise CoordinatorError("obs_owned_stream_stop_unconfirmed")
    h.obs.stop_stream = failed_stop
    await c.execute("public_start", permit()); await h.obs.entered.wait()
    h.clock.advance(.65); h.obs.release.set(); await c._start_task
    assert unsafe == ["obs_owned_stream_stop_unconfirmed"] and c.metrics["public_starts"] == 0
    assert c.state != "Live" and not h.scheduler._public_intro_marked


@pytest.mark.asyncio
async def test_unknown_dynamic_readiness_cannot_admit_public_start(monkeypatch):
    h = Harness(monkeypatch); h.frame(); c = controller(h)
    h.run.readiness = lambda: None
    with pytest.raises(CoordinatorError, match="broadcast_preview_not_ready"): await c.execute("public_start", permit())
    assert not any((await c.status())["ready"].values()) and h.obs.starts == 0


@pytest.mark.asyncio
async def test_feed_status_is_bounded_numeric_without_ids_or_content(monkeypatch):
    h = Harness(monkeypatch); h.frame(); h.run._check_feed(); c = controller(h)
    for _ in range(12):
        h.clock.advance(.65); h.run._check_feed(); h.frame(); h.run._check_feed()
    health = (await c.status())["feed_health"]
    assert health["transition_count"] == 24 and len(health["transitions"]) == 8
    assert all(row["expiry_remaining_ms"] <= 500 for row in health["transitions"])
    encoded = json.dumps(health)
    assert not any(value in encoded for value in (SESSION, MATCH, OBSERVER, "public-fact", "owned-utterance"))
    assert len(encoded) < 6000
    async def hostile(): return {"feed_health": {"transition_count": True, "transitions": [{
        "code": "private", "eligible": 1, "entry_age_ms": float("inf"), "actor_id": "private"}], "private": "secret"}}
    h.run.status = hostile
    filtered = (await c.status())["feed_health"]
    assert filtered["transition_count"] is None and filtered["transitions"][0]["code"] is None
    assert "private" not in json.dumps(filtered) and "secret" not in json.dumps(filtered)
