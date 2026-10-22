"""Real scheduler + director + typed Native RPC; no apps, SDK or audio device."""
import asyncio
from dataclasses import asdict
import struct
from types import SimpleNamespace

import pytest
import pytest_asyncio

from actor_broadcast.budget import BudgetLedger
from actor_broadcast.audio import BrowserAudioSink,SpeechAudioOutput
from actor_broadcast.config import CommentaryConfig
from actor_broadcast.contracts import EvidenceRef,FlushAck,PcmChunk,SegmentKey,SpeechSegment,SynthesisDone
from actor_broadcast.speech import decode_header,parse_header
from actor_broadcast.scheduler import CommentaryScheduler
from actor_codex.commentary import INSTRUCTIONS
from extensions.agents_arena.v1 import broadcast_pb2 as pb
from test_broadcast_commentary import Provider, Speech, pump
from test_broadcast_native_director import harness


ROSTER=("first","second","third","fourth")


def event(h,identity,priority=2):
    now=int(h.clock.time()*1000)
    return pb.BroadcastEditorialFact(evidence_id=identity,version=1,
        kind=pb.BROADCAST_FACT_KIND_OBSERVATION,source_event_id="event-"+identity,
        subject_ids=["first"],predicate="actor-seen",observed_at_unix_ms=now,
        retention_expires_at_unix_ms=now+600000,priority=priority,label="Мира",room_id="main-hall")


@pytest_asyncio.fixture
async def camera(harness):
    h=harness;provider=Provider(segments=3);speech=Speech()
    config=CommentaryConfig(coalesce_ms=0,normal_cooldown_seconds=0,
        normal_calls_per_minute=60,normal_burst_capacity=6,absolute_calls_per_rolling_minute=60)
    scheduler=CommentaryScheduler(provider,speech,config=config,
        clock=h.clock.monotonic,wall_clock=h.clock.time)
    h.run.scheduler=scheduler;h.run._activated=True;h.run.director=h.run.deps.director()
    h.frame(targets=ROSTER,actors=("first",));assert scheduler.ready()
    yield h,provider,speech,scheduler
    await scheduler.stop()


async def start_normal_near_dwell(h,scheduler):
    h.advance_frames(17,targets=ROSTER,actors=("first",))
    original=event(h,"original")
    h.frame(targets=ROSTER,actors=("first",),facts=(original,));await pump(scheduler)
    return original


async def due_with_new_normal(h,scheduler):
    h.advance_frames(1,targets=ROSTER,actors=("first",))
    h.frame(targets=ROSTER,actors=("first",),facts=(event(h,"later"),))
    h.run._direct_camera()
    await pump(scheduler)


async def test_continuous_p2_yields_after_real_clause_ack_before_next_old_clause_or_new_turn(camera):
    h,provider,speech,scheduler=camera
    original=await start_normal_near_dwell(h,scheduler)
    assert len(provider.requests)==len(speech.segments)==1
    assert speech.acknowledge(scheduler,h.clock,status="started",frames=0)
    await due_with_new_normal(h,scheduler)
    assert not h.run.session.commands and len(speech.segments)==1
    assert scheduler.report()["camera_boundary"]["requested"]
    # Merely generated/started/progress audio is not the end of a clause.
    assert speech.acknowledge(scheduler,h.clock,status="progress",frames=12000)
    h.run._direct_camera();assert not h.run.session.commands
    assert speech.acknowledge(scheduler,h.clock)
    await pump(scheduler)
    assert provider.interruptions==1 and speech.cancellations
    assert len(speech.segments)==len(provider.requests)==1
    assert scheduler.spoken.covered(EvidenceRef(original.evidence_id,str(original.version)),h.clock.monotonic())
    h.run._direct_camera();assert len(h.run.session.commands)==1
    await pump(scheduler);assert len(provider.requests)==1
    h.result();h.run._direct_camera();await pump(scheduler)
    assert h.run.director.report()["confirmed_presented_switches"]==0
    assert len(provider.requests)==1  # Accepted cannot release the pause.
    target=h.run._director_selection.intent.focus_actor_id
    provider.segments=1
    movement=event(h,"new-focus");movement.predicate="movement-seen";movement.value=3
    h.frame(epoch=2,anchor="",focus=target,actors=(target,),targets=ROSTER,
        facts=(movement,))
    h.run._direct_camera();await pump(scheduler)
    assert h.run.director.report()["confirmed_presented_switches"]==1
    assert len(provider.requests)==2 and len(speech.segments)==2
    assert not scheduler.report()["camera_boundary"]["requested"]
    assert scheduler.report()["counts"]["camera_clause_boundaries"]==1


@pytest.mark.parametrize("priority",[0,1])
async def test_important_public_events_keep_priority_over_due_camera(camera,priority):
    h,provider,speech,scheduler=camera
    provider.segments=1
    h.advance_frames(18,targets=ROSTER,actors=("first",))
    urgent=event(h,"important",priority)
    h.frame(targets=ROSTER,actors=("first",),facts=(urgent,))
    h.run._direct_camera();await pump(scheduler)
    assert len(provider.requests)==1 and provider.requests[0].candidate.priority==priority
    assert not h.run.session.commands
    h.run._direct_camera();assert not h.run.session.commands
    assert speech.acknowledge(scheduler,h.clock)
    await asyncio.wait_for(asyncio.shield(scheduler._active),2)
    await pump(scheduler);h.run._direct_camera()
    assert len(h.run.session.commands)==1


async def test_p1_arriving_at_camera_clause_boundary_keeps_its_authorized_cooldown_bypass(camera):
    h,provider,speech,scheduler=camera
    scheduler.configure(scheduler.config.model_copy(update={"normal_cooldown_seconds":30.0}))
    await start_normal_near_dwell(h,scheduler);await due_with_new_normal(h,scheduler)
    provider.segments=1
    h.frame(targets=ROSTER,actors=("first",),facts=(event(h,"important-boundary",1),))
    assert scheduler._p1_deadline is not None
    assert speech.acknowledge(scheduler,h.clock)
    await pump(scheduler)
    assert len(provider.requests)==2 and provider.requests[-1].candidate.priority==1
    assert h.clock.monotonic()-scheduler._last_admission<1.5
    h.run._direct_camera();assert not h.run.session.commands
    assert speech.acknowledge(scheduler,h.clock)
    await asyncio.wait_for(asyncio.shield(scheduler._active),2)
    await pump(scheduler);h.run._direct_camera()
    assert len(h.run.session.commands)==1


@pytest.mark.parametrize("priority",[0,1])
async def test_late_old_normal_ack_cannot_invalidate_active_important_speech(camera,priority):
    h,provider,speech,scheduler=camera
    await start_normal_near_dwell(h,scheduler);await due_with_new_normal(h,scheduler)
    provider.segments=1
    h.frame(targets=ROSTER,actors=("first",),facts=(event(h,"urgent",priority),))
    if priority==1:
        h.advance_frames(1.5,targets=ROSTER,actors=("first",))
    await pump(scheduler)
    assert len(provider.requests)==2 and provider.requests[-1].candidate.priority==priority
    epoch=scheduler._epoch
    assert not speech.acknowledge(scheduler,h.clock,index=0)
    assert scheduler._epoch==epoch and scheduler.validate_segment(speech.segments[-1])
    h.run._direct_camera();assert not h.run.session.commands
    assert speech.acknowledge(scheduler,h.clock)
    await asyncio.wait_for(asyncio.shield(scheduler._active),2)
    await pump(scheduler);h.run._direct_camera();assert len(h.run.session.commands)==1


async def test_real_output_two_queued_clauses_cannot_autostart_tail_after_completed_callback(camera,monkeypatch):
    """Actual sink calls scheduler synchronously before releasing its next play.

    The production full12s reservation currently admits one clause at a time.
    Two smaller already-queued clauses also obey the same transport contract;
    this adversarial queue proves the callback/drain ordering itself.
    """
    import actor_broadcast.audio as audio
    h,provider,_,scheduler=camera
    monkeypatch.setattr(audio,"time",SimpleNamespace(monotonic=h.clock.monotonic,
        monotonic_ns=lambda:int(h.clock.monotonic()*1e9)))
    source=event(h,"queued-audio");h.frame(targets=ROSTER,actors=("first",),facts=(source,))
    class Voice:
        def __init__(self):self.keys=[]
        async def synthesize(self,segment):
            self.keys.append(segment.key)
            yield PcmChunk(segment.scope,segment.key,0,0,struct.pack("<8f",*([.1]*8)))
            yield SynthesisDone(segment.scope,segment.key,8,1,False,"generator_completed")
        async def cancel(self,request):pass
        async def aclose(self):pass
    sink=BrowserAudioSink(route_guard=lambda:True,on_ack=scheduler.playback_ack)
    class Wire:
        def __init__(self):self.begins=[];self.end=asyncio.Event()
        async def send(self,raw):
            header,_=parse_header(raw[:8]);kind,value=decode_header(raw[8:8+header])
            if kind=="begin":self.begins.append(value["key"])
            if kind=="end":self.end.set()
            if kind=="flush":await sink._message({"type":"flushed","scope":value["scope"],
                "utterance_id":value["utterance_id"],"utterance_epoch":value["utterance_epoch"]})
        async def close(self,**kwargs):pass
    wire=Wire();sink._ws=wire;sink._ready.set();voice=Voice()
    output=SpeechAudioOutput(voice,sink,validate=scheduler.validate_segment)
    scheduler.speech=output
    scope=scheduler.view.scope;refs=(EvidenceRef(source.evidence_id,str(source.version)),)
    segments=[SpeechSegment(scope,SegmentKey("queued",scheduler._epoch,index),2,"Разрешённая фраза.",
        refs,scheduler.view.semantic_revision,int((h.clock.monotonic()+30)*1e9),max_audio_frames=6*24000) for index in range(2)]
    try:
        for segment in segments:
            scheduler.spoken.register(segment,topic_key="queued",mode="reactive")
            scheduler._speech_pending[(scope,segment.key)]=(segment,6.0)
        await output.submit(segments[0]);await asyncio.wait_for(wire.end.wait(),1)
        await output.submit(segments[1]);assert output._queue.qsize()==1
        assert scheduler.request_camera_change(scope)
        await sink._message({"type":"complete","scope":asdict(scope),"key":asdict(segments[0].key),
            "consumed_frames":8,"underrun_frames":0})
        for _ in range(30):await asyncio.sleep(0)
        assert len(wire.begins)==1 and voice.keys==[segments[0].key]
        assert scheduler.spoken.records[(scope,segments[0].key)].status=="completed"
        await pump(scheduler)
        assert scheduler.spoken.records[(scope,segments[1].key)].status=="interrupted"
        assert scheduler.camera_change_allowed
    finally:
        await scheduler.stop();await output.close()


@pytest.mark.parametrize("resolution",["rejected","timeout"])
async def test_selection_rejection_or_timeout_releases_normal_pause_without_invented_cut(camera,resolution):
    h,provider,speech,scheduler=camera
    provider.segments=1
    h.advance_frames(18,targets=ROSTER,actors=("first",));h.run._direct_camera()
    assert len(h.run.session.commands)==1
    h.frame(targets=ROSTER,actors=("first",),facts=(event(h,"queued"),))
    await pump(scheduler);assert not provider.requests
    if resolution=="rejected":h.result(accepted=False)
    else:h.advance_frames(5,targets=ROSTER,actors=("first",))
    h.run._direct_camera();await pump(scheduler)
    assert len(provider.requests)==1 and len(h.run.session.commands)==1
    assert not scheduler.report()["camera_boundary"]["requested"]
    assert h.run.director.report()["confirmed_presented_switches"]==0


async def test_stale_frame_cannot_dispatch_or_keep_normal_pause(camera):
    h,provider,speech,scheduler=camera
    h.advance_frames(18,targets=ROSTER,actors=("first",))
    h.clock.advance(1);h.run._direct_camera()
    assert not h.run.session.commands and not scheduler.report()["camera_boundary"]["requested"]


async def test_failed_actual_flush_never_authorizes_camera(camera):
    h,provider,speech,scheduler=camera
    await start_normal_near_dwell(h,scheduler);await due_with_new_normal(h,scheduler)
    async def fail(request,*,fade_seconds):return FlushAck(request.scope,request.utterance_id,request.utterance_epoch,False)
    speech.cancel=fail
    assert speech.acknowledge(scheduler,h.clock)
    await pump(scheduler);h.run._direct_camera()
    assert not h.run.session.commands and not scheduler.camera_change_allowed
    assert scheduler.report()["counts"]["audio_flush_failed"]==1
    # Test cleanup can retry the retained exact owner; no success is fabricated.
    async def recover(request,*,fade_seconds):return FlushAck(request.scope,request.utterance_id,request.utterance_epoch,True)
    speech.cancel=recover


async def test_camera_request_during_durable_reservation_prevents_old_normal_turn(camera):
    h,provider,speech,previous=camera
    await previous.stop()
    class GateBudget(BudgetLedger):
        def __init__(self):
            super().__init__(clock=h.clock.time);self.entered=asyncio.Event();self.release=asyncio.Event()
        async def reserve(self,*args,**kwargs):
            self.entered.set();await self.release.wait();return await super().reserve(*args,**kwargs)
    budget=GateBudget()
    scheduler=CommentaryScheduler(provider,speech,budget,config=previous.config,
        clock=h.clock.monotonic,wall_clock=h.clock.time)
    # previous.stop closes its provider, which is a fake and is replaced here.
    provider=Provider(segments=1);scheduler.provider=provider;h.run.scheduler=scheduler
    h.frame(targets=ROSTER,actors=("first",),facts=(event(h,"reserved"),));assert scheduler.ready()
    h.advance_frames(18,targets=ROSTER,actors=("first",))
    step=asyncio.create_task(scheduler.step());await budget.entered.wait()
    h.run._direct_camera();budget.release.set();await step
    assert not provider.requests and not speech.segments
    assert scheduler.report()["counts"]["camera_superseded_admissions"]==1
    assert (await budget.report())["calls"]==1
    assert len(h.run.session.commands)==1
    await scheduler.stop();await budget.close()


def test_provider_never_treats_camera_selector_names_as_sighting_or_life_evidence():
    assert "camera_targets" in INSTRUCTIONS
    assert "не свидетельство видимости, жизни, позиции или роли" in INSTRUCTIONS
    assert "отказ выбора тоже ничего этого не доказывает" in INSTRUCTIONS
