"""Real Native/director/projector+typed inbox, fake clocks/RPC only."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from actor_broadcast.arena import ArenaBroadcastProjector
from actor_broadcast.config import CommentaryConfig
from actor_broadcast.director import CameraDirector
from actor_broadcast.native import NativeDependencies, NativeError, PreparedNativeRun
from actor_runtime.world import CachedPayload, WorldState
from extensions.agents_arena.v1 import broadcast_pb2 as pb
from extensions.agents_arena.v1 import social_pb2 as social


SESSION, MATCH, OBSERVER = "a"*32, "b"*32, "c"*32
SELECT = "agents-arena.broadcast-select-view"
RESULT = "agents-arena.broadcast-view-selection-result"
PRESENTED = "agents-arena.broadcast-presented-view"


class Clock:
    def __init__(self): self.value=100.0
    def monotonic(self): return self.value
    def time(self): return 1000+self.value
    def advance(self,seconds): self.value+=seconds


class Scheduler:
    def __init__(self): self.view=None; self.camera_change_allowed=True; self.run_calls=0;self.camera_requested=False
    def observe(self,view): self.view=view; return True
    def _fresh(self):return self.view is not None
    def request_camera_change(self,scope):self.camera_requested=True;return self.view.scope==scope
    def release_camera_change(self):self.camera_requested=False
    async def run(self): self.run_calls+=1; await asyncio.Event().wait()
    def report(self): return {}
    idle_deadline=0


class Session:
    def __init__(self):
        self.world=WorldState(OBSERVER); self.closed=asyncio.Event(); self.last_received_sequence=0
        self.commands=[]; self.command_error=False
    def command(self,capability,payload,*,target_actor_id,ttl_ms):
        assert capability==SELECT and isinstance(payload,pb.BroadcastSelectViewCommand)
        assert target_actor_id==OBSERVER and type(ttl_ms)is int and 1<=ttl_ms<=500
        copy=pb.BroadcastSelectViewCommand();copy.CopyFrom(payload)
        self.commands.append((capability,copy,target_actor_id,ttl_ms))
        if self.command_error: raise RuntimeError("unknown queued command fixture")
        return len(self.commands)


class Harness:
    def __init__(self,clock):
        self.clock=clock;self.source_sequence=0;self.frame_sequence=0
        dependencies=NativeDependencies(director=lambda:CameraDirector(clock=clock.monotonic,
            wall_clock=clock.time,rng=SimpleNamespace(uniform=lambda low,high:18)))
        factory=SimpleNamespace(config=SimpleNamespace(chat_enabled=False),store=None,
            dependencies=dependencies,guardian=None,_owned=[])
        self.run=PreparedNativeRun(factory,"ABC123",SESSION,CommentaryConfig(),lambda _:None,lambda _:None,lambda:None)
        self.run.observer_id=OBSERVER;self.run.scheduler=Scheduler();self.run.session=Session()
        self.run.projector=ArenaBroadcastProjector(observer_id=OBSERVER,broadcast_session_id=SESSION,
            match_id=MATCH,clock=clock.monotonic)
    def frame(self,*,epoch=1,generation=1,anchor="main-hall",focus="",actors=("first","second"),terminal=False,targets=(),facts=()):
        self.source_sequence+=1;self.frame_sequence+=1
        value=pb.BroadcastPresentedViewObservation()
        value.view.scope.CopyFrom(pb.BroadcastScope(broadcast_session_id=SESSION,match_id=MATCH,
            observer_id=OBSERVER,connection_generation=generation,view_epoch=epoch))
        value.view.sequence=self.frame_sequence;value.view.semantic_revision=self.frame_sequence
        value.view.observed_at_unix_ms=int(self.clock.time()*1000);value.view.expires_at_unix_ms=value.view.observed_at_unix_ms+500
        value.view.camera.anchor_id=anchor;value.view.camera.focus_actor_id=focus
        value.view.phase=social.SOCIAL_PHASE_GAME_OVER if terminal else social.SOCIAL_PHASE_EXPLORATION
        value.view.terminal=terminal
        for actor in actors:value.view.actors.add(actor_id=actor,display_name="Имя "+actor)
        for actor in targets:value.view.camera_targets.add(actor_id=actor,display_name="Имя "+actor)
        for fact in facts:value.view.facts.add().CopyFrom(fact)
        value.presentation.scope.CopyFrom(value.view.scope);value.presentation.rendered_sequence=value.view.sequence
        value.presentation.frame_counter=value.view.sequence;value.presentation.width=800;value.presentation.height=600
        value.presentation.camera_targets_presented=bool(targets)
        for fact in facts:value.presentation.presented_evidence.add(evidence_id=fact.evidence_id,version=fact.version)
        entry=CachedPayload(OBSERVER,PRESENTED,self.source_sequence,self.frame_sequence,value,self.clock.monotonic())
        self.run.session.world.apply_observations([entry],self.source_sequence,self.frame_sequence)
        self.run.session.last_received_sequence=self.source_sequence
        self.run._capture()
        return value
    def advance_frames(self,seconds,**options):
        for _ in range(round(seconds*4)):
            self.clock.advance(.25);self.frame(**options)
    def result(self,*,accepted=True,correlation=None,scope=None,entity=OBSERVER,capability=RESULT,
               received_at=None,sequence=None):
        pending=self.run._director_selection
        intent=pending.intent
        source=pb.BroadcastViewSelectionResultEvent(correlation_id=correlation or pending.correlation_id,
            accepted=accepted,reason_code="view_selected" if accepted else "view_unavailable")
        source.scope.CopyFrom(scope or pb.BroadcastScope(broadcast_session_id=SESSION,match_id=MATCH,
            observer_id=OBSERVER,connection_generation=intent.scope.connection_generation,
            view_epoch=intent.scope.view_epoch+(1 if accepted else 0)))
        self.source_sequence+=1
        entry=CachedPayload(entity,capability,self.source_sequence if sequence is None else sequence,1,source,
            self.clock.monotonic() if received_at is None else received_at)
        self.run.session.world.apply_event(entry)
        self.run.session.last_received_sequence=self.source_sequence
        return source


@pytest_asyncio.fixture
async def harness(monkeypatch):
    import actor_broadcast.native as native
    import actor_runtime.inbox as inbox
    clock=Clock();monkeypatch.setattr(native,"time",clock);monkeypatch.setattr(inbox,"time",clock)
    value=Harness(clock)
    yield value
    tasks=tuple(value.run._tasks)
    for task in tasks:task.cancel()
    if tasks:await asyncio.gather(*tasks,return_exceptions=True)


async def activate(h):
    h.frame();await h.run.activate()


async def send_first(h):
    await activate(h);h.advance_frames(18);h.run._direct_camera()
    assert len(h.run.session.commands)==1
    return h.run.session.commands[0][1]


@pytest.mark.asyncio
async def test_preparation_time_never_earns_camera_dwell_and_activation_is_idempotent(harness):
    h=harness;h.frame();h.advance_frames(40)
    assert h.run.director is None and not h.run.session.commands
    await h.run.activate();director=h.run.director
    await h.run.activate();assert h.run.director is director
    h.advance_frames(17.75);h.run._direct_camera();assert not h.run.session.commands
    h.advance_frames(.25);h.run._direct_camera()
    command=h.run.session.commands[0][1]
    assert command.scope==pb.BroadcastScope(broadcast_session_id=SESSION,match_id=MATCH,
        observer_id=OBSERVER,connection_generation=1,view_epoch=1)
    assert command.focus_actor_id=="first" and command.anchor_id=="" and len(command.correlation_id)==32
    assert command.expires_at_unix_ms==h.run.scheduler.view.expires_at_unix_ms


@pytest.mark.asyncio
async def test_speech_drain_priority_gate_blocks_intent_admission_not_just_wire_send(harness):
    h=harness;await activate(h);h.advance_frames(18)
    h.run.scheduler.camera_change_allowed=False
    for _ in range(20):h.run._direct_camera()
    assert not h.run.session.commands and not h.run.director.report()["pending"]
    h.run.scheduler.camera_change_allowed=True;h.run._direct_camera()
    assert len(h.run.session.commands)==1


@pytest.mark.asyncio
async def test_server_acceptance_is_not_presented_confirmation_and_duplicate_cannot_complete_it(harness):
    h=harness;await send_first(h);h.result();h.run._direct_camera()
    assert h.run._director_results_accepted==1
    assert h.run.director.report()["confirmed_presented_switches"]==0
    assert h.run._director_selection is not None
    h.result();h.run._direct_camera()
    assert h.run._director_results_accepted==1 and len(h.run.session.commands)==1
    h.frame(epoch=2,anchor="",focus="first");h.run._direct_camera()
    assert h.run._director_selection is None
    assert h.run.director.report()["confirmed_presented_switches"]==1
    assert len(h.run.session.commands)==1


@pytest.mark.asyncio
async def test_actual_new_frame_before_result_poll_preserves_exact_result_accounting(harness):
    h=harness;await send_first(h);h.result()
    h.frame(epoch=2,anchor="",focus="first");h.run._direct_camera()
    assert h.run._director_results_accepted==1 and h.run.director.report()["confirmed_presented_switches"]==1


@pytest.mark.asyncio
async def test_explicit_denial_and_unknown_timeout_use_bounded_backoff(harness):
    h=harness;await send_first(h);h.result(accepted=False);h.run._direct_camera()
    assert h.run._director_results_rejected==1 and h.run._director_selection is None
    h.advance_frames(17.75);h.run._direct_camera();assert len(h.run.session.commands)==1
    h.advance_frames(.25);h.run._direct_camera();assert len(h.run.session.commands)==2
    h.advance_frames(5);h.run._direct_camera();assert h.run._director_selection is None
    h.advance_frames(17.75);h.run._direct_camera();assert len(h.run.session.commands)==2
    assert h.run.director.report()["selection_timeouts"]==1


@pytest.mark.asyncio
@pytest.mark.parametrize("change",["correlation","observer_scope","generation","session","match","epoch","entity","capability","old_sequence","old_time"])
async def test_foreign_stale_or_uncorrelated_result_never_retires_pending_selection(harness,change):
    h=harness;await send_first(h);options={}
    scope=pb.BroadcastScope(broadcast_session_id=SESSION,match_id=MATCH,observer_id=OBSERVER,
        connection_generation=1,view_epoch=2)
    if change=="correlation":options["correlation"]="d"*32
    if change=="observer_scope":scope.observer_id="foreign"
    if change=="generation":scope.connection_generation=2
    if change=="session":scope.broadcast_session_id="foreign"
    if change=="match":scope.match_id="foreign"
    if change=="epoch":scope.view_epoch=3
    if change=="entity":options["entity"]="foreign"
    if change=="capability":options["capability"]="unrelated"
    if change=="old_sequence":options["sequence"]=h.run._director_selection.after_sequence
    if change=="old_time":options["received_at"]=h.clock.monotonic()-1
    h.result(scope=scope,**options);h.run._direct_camera()
    assert h.run._director_results_accepted==0 and h.run._director_selection is not None
    assert h.run.director.report()["confirmed_presented_switches"]==0 and len(h.run.session.commands)==1


@pytest.mark.asyncio
@pytest.mark.parametrize("change",["sequence","scope","expiry","speech_gate"])
async def test_exact_final_admission_fence_catches_change_during_local_intent_selection(harness,change):
    h=harness;await activate(h);h.advance_frames(18)
    original=h.run.director.next_intent
    def raced():
        intent=original();view=h.run.scheduler.view
        if change=="sequence":h.run.scheduler.view=replace(view,sequence=view.sequence+1,
            presentation=replace(view.presentation,rendered_sequence=view.sequence+1))
        if change=="scope":
            scope=replace(view.scope,view_epoch=2)
            h.run.scheduler.view=replace(view,scope=scope,presentation=replace(view.presentation,scope=scope))
        if change=="expiry":h.clock.advance(1)
        if change=="speech_gate":h.run.scheduler.camera_change_allowed=False
        return intent
    h.run.director.next_intent=raced;h.run._direct_camera()
    assert not h.run.session.commands and h.run._director_selection is None


@pytest.mark.asyncio
async def test_unknown_command_failure_retains_single_pending_handle_and_fails_closed(harness):
    h=harness;await activate(h);h.advance_frames(18);h.run.session.command_error=True
    with pytest.raises(NativeError,match="broadcast_camera_command_failed"):h.run._direct_camera()
    assert h.run._director_selection is not None and len(h.run.session.commands)==1
    h.run._direct_camera();assert len(h.run.session.commands)==1


@pytest.mark.asyncio
@pytest.mark.parametrize("flag,value",[("_closing",True),("_terminal",True),("_failure","fixture_unsafe"),("_activated",False)])
async def test_stopped_unsafe_terminal_or_not_activated_never_dispatches(harness,flag,value):
    h=harness;await activate(h);h.advance_frames(18);setattr(h.run,flag,value)
    h.run._direct_camera();assert not h.run.session.commands


@pytest.mark.asyncio
async def test_terminal_capture_and_numeric_only_director_status(harness):
    h=harness;await send_first(h);h.result();h.frame(epoch=2,anchor="",focus="first")
    h.run._direct_camera();h.frame(epoch=2,anchor="",focus="first",terminal=True);h.run._direct_camera()
    status=await h.run.status();report=status["director"]
    assert report["commands_dispatched"]==1 and report["confirmed_presented_switches"]==1
    assert report["terminal"] is True and all(type(value)in(int,bool)for value in report.values())
    encoded=json.dumps(report)
    assert all(identity not in encoded for identity in (SESSION,MATCH,OBSERVER,"first","correlation_id"))


@pytest.mark.parametrize("event,expected",[("obs_owned_stream_start_failed","obs_owned_stream_start_failed"),
    ("unrecognized_graph_event","obs_graph_changed")])
async def test_stream_start_failure_keeps_exact_safe_diagnostic_without_loosening_unknown_events(harness,event,expected):
    run=harness.run;run._flags["obs"]=True
    run._obs_event(event)
    assert run._failure==expected and run._failed.is_set()
