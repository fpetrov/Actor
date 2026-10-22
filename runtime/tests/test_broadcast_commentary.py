"""Actual scheduler/evidence/playback boundaries with no network or models."""
import asyncio
import json
from dataclasses import replace

import pytest

from actor_broadcast.budget import BudgetLedger
from actor_broadcast.config import CommentaryConfig, IdleConfig
from actor_broadcast.contracts import (Scope,PresentationAck,PresentedView,EditorialFact,
    ProviderSegment,ProviderTerminal,PlaybackAck,FlushAck)
from actor_broadcast.scheduler import CommentaryScheduler


class Clock:
    def __init__(self):self.now=0.0
    def monotonic(self):return self.now
    def wall(self):return 1000+self.now
    def advance(self,seconds):self.now+=seconds


def view(clock, *, sequence=1, facts=(), scope=None, phase="Exploration", terminal=False, revoked=(), current=None):
    scope=scope or Scope("broadcast","match",1,1)
    return PresentedView(scope,sequence,sequence,int(clock.wall()*1000),int((clock.wall()+3)*1000),phase,
        current or {"visible_actor_ids":["visible"],"room_id":"hall"},tuple(facts),
        PresentationAck(scope,sequence,sequence,clock.monotonic(),tuple(item.ref for item in facts)),terminal,tuple(revoked))


def fact(clock, identity="one", *, priority=3, age=0, lifetime=600, kind="historical_observation", unresolved=False, version="1"):
    return EditorialFact(identity,version,kind,"event-"+identity,("visible",),"room-arrival",
        max(1,int((clock.wall()-age)*1000)),int((clock.wall()+lifetime)*1000),{"room_id":"hall","text":"Разрешённый факт"},
        priority,identity,unresolved)


class Provider:
    max_provider_calls=1
    def __init__(self, *, blocked=False, segments=1):
        self.requests=[];self.release=asyncio.Event();self.blocked=blocked;self.segments=segments
        self.active=0;self.max_active=0;self.interruptions=0;self.closed=False;self.last_terminal=None
    async def generate(self,request):
        self.requests.append(request);self.active+=1;self.max_active=max(self.active,self.max_active)
        interrupted_at_start=self.interruptions
        try:
            if self.blocked:await self.release.wait()
            for index in range(self.segments):
                yield ProviderSegment(index,"Мы видели переход. Это наблюдение, а не доказательство роли.",request.candidate.evidence)
            self.last_terminal=ProviderTerminal("interrupted" if self.interruptions>interrupted_at_start else "completed",100,20,0,0)
            yield self.last_terminal
        finally:self.active-=1
    async def interrupt(self):self.interruptions+=1;self.release.set()
    async def close(self):self.closed=True;self.release.set()


class Speech:
    def __init__(self):self.segments=[];self.cancellations=[]
    async def submit(self,segment):self.segments.append(segment)
    async def cancel(self,request,*,fade_seconds):
        self.cancellations.append((request,fade_seconds))
        return FlushAck(request.scope,request.utterance_id,request.utterance_epoch,True)
    def acknowledge(self,scheduler,clock,index=-1,status="completed",frames=24000):
        segment=self.segments[index]
        return scheduler.playback_ack(PlaybackAck(segment.scope,segment.key,status,frames,24000,clock.monotonic()))


async def pump(scheduler):
    await scheduler.step()
    for _ in range(20):await asyncio.sleep(0)


def system(clock,provider=None,config=None):
    provider=provider or Provider();speech=Speech()
    scheduler=CommentaryScheduler(provider,speech,config=config,clock=clock.monotonic,wall_clock=clock.wall)
    return scheduler,provider,speech


async def fresh(scheduler,clock,**kwargs):
    sequence=(scheduler.view.sequence+1) if scheduler.view else 1
    scheduler.observe(view(clock,sequence=sequence,**kwargs))
    await pump(scheduler)


async def test_provider_prefix_reservation_blocks_before_call_when_token_budget_is_small():
    clock=Clock();provider=Provider();provider.input_token_reservation=lambda request:12000
    scheduler,_,_=system(clock,provider,CommentaryConfig(input_tokens_per_hour=10000))
    scheduler.observe(view(clock,facts=[fact(clock,priority=0)]));assert scheduler.ready()
    await pump(scheduler)
    assert not provider.requests
    assert scheduler.report()["silence_reason"]=="budget"
    await scheduler.stop()


async def test_exact_idle_deadline_and_playback_end_not_tts_end():
    clock=Clock();scheduler,provider,speech=system(clock)
    scheduler.observe(view(clock,facts=[fact(clock)]));assert scheduler.ready()
    clock.advance(19.9);await fresh(scheduler,clock)
    assert not provider.requests
    clock.advance(.1);await fresh(scheduler,clock)
    assert len(provider.requests)==1 and provider.requests[0].candidate.mode=="idle_recap"
    assert scheduler.spoken.report()["completed"]==0
    clock.advance(8);await fresh(scheduler,clock)
    assert len(provider.requests)==1  # TTS generation alone never starts idle clock.
    assert speech.acknowledge(scheduler,clock)
    assert scheduler.idle_deadline==48
    await scheduler.stop()


async def test_noteworthy_at_nineteen_replaces_timer_and_coalesces_burst():
    clock=Clock();scheduler,provider,speech=system(clock)
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    clock.advance(19)
    await fresh(scheduler,clock,facts=[fact(clock,"urgent",priority=1),fact(clock,"other",priority=2)])
    assert not provider.requests and scheduler.idle_deadline==39
    clock.advance(.5);await fresh(scheduler,clock)
    assert len(provider.requests)==1 and len(provider.requests[0].candidate.evidence)==2
    await scheduler.stop()


async def test_transform_heartbeat_and_duplicate_flood_neither_calls_nor_postpones_idle():
    clock=Clock();scheduler,provider,_=system(clock)
    original=fact(clock)
    scheduler.observe(view(clock,facts=[original]));scheduler.ready()
    for index in range(2000):
        clock.advance(.01)
        scheduler.observe(view(clock,sequence=index+2,facts=[original],current={"x":index/100}))
    assert scheduler.last_noteworthy_observation_at==0 and not provider.requests
    clock.advance(.01);await fresh(scheduler,clock)
    assert len(provider.requests)==1
    assert scheduler.evidence.report()["entries"]==1
    await scheduler.stop()


async def test_old_unresolved_claim_is_a_callback_without_new_event_ids():
    clock=Clock();scheduler,provider,_=system(clock)
    scheduler.observe(view(clock,facts=[fact(clock,age=120,kind="player_claim",unresolved=True)]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    assert len(provider.requests)==1 and provider.requests[0].candidate.mode=="memory_callback"
    evidence=provider.requests[0].external_state["evidence"][0]
    assert evidence["kind"]=="player_claim" and evidence["observed_at_unix_ms"]==880000
    await scheduler.stop()


async def test_empty_feed_due_is_zero_calls_with_backoff_and_single_due_flag():
    clock=Clock();scheduler,provider,_=system(clock)
    scheduler.observe(view(clock));scheduler.ready()
    for _ in range(250):
        clock.advance(.25);await fresh(scheduler,clock)
    assert not provider.requests and scheduler.report()["pending_facts"]==0
    assert scheduler.next_idle_retry_at>clock.now
    await scheduler.stop()


async def test_covered_version_and_topic_block_paraphrase_spam():
    clock=Clock();scheduler,provider,speech=system(clock)
    original=fact(clock)
    scheduler.observe(view(clock,facts=[original]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    assert speech.acknowledge(scheduler,clock)
    for _ in range(4):clock.advance(20);await fresh(scheduler,clock,facts=[original])
    assert len(provider.requests)==1
    await scheduler.stop()


@pytest.mark.parametrize("mode",["idle_recap","memory_callback"])
async def test_p0_during_recap_fences_late_provider_and_audio_and_stays_single_flight(mode):
    clock=Clock();provider=Provider(blocked=True);scheduler,_,speech=system(clock,provider)
    scheduler.observe(view(clock,facts=[fact(clock,age=120 if mode=="memory_callback" else 0,unresolved=True)]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    old=provider.requests[0]
    await fresh(scheduler,clock,facts=[fact(clock,"critical",priority=0)])
    for _ in range(30):await asyncio.sleep(0)
    assert provider.max_active==1 and provider.interruptions==1
    assert all(segment.key.utterance_id!=old.utterance_id for segment in speech.segments)
    assert len(provider.requests)==2 and provider.requests[1].candidate.mode=="reactive"
    assert speech.cancellations
    await scheduler.stop()


async def test_p1_wait_is_bounded_and_completed_prefix_only_is_covered():
    clock=Clock();provider=Provider(segments=3);scheduler,_,speech=system(clock,provider)
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    assert len(speech.segments)==1  # Full 12-second reservation; next segment waits for actual ACK.
    first=speech.segments[0]
    clock.advance(.2);await fresh(scheduler,clock,facts=[fact(clock,"important",priority=1)])
    assert not speech.cancellations
    assert speech.acknowledge(scheduler,clock,index=0)
    await pump(scheduler)
    assert len(speech.cancellations)==1
    rows=list(scheduler.spoken.records.values())
    assert len(rows)==1 and rows[0].status=="completed"  # Unsubmitted suffix is not fabricated playback history.
    assert len(scheduler.spoken.recent())==1
    assert not scheduler.playback_ack(PlaybackAck(first.scope,first.key,"completed",24000,24000,clock.now))
    await scheduler.stop()


async def test_short_russian_segment_gets_full_twelve_second_frame_cap_and_ack_backpressure():
    clock=Clock();provider=Provider(segments=2);scheduler,_,speech=system(clock,provider)
    scheduler.observe(view(clock,facts=[fact(clock,priority=0)]));assert scheduler.ready()
    await pump(scheduler)
    assert len(speech.segments)==1
    first=speech.segments[0]
    assert first.max_audio_frames==288000
    assert sum(row[1] for row in scheduler._speech_pending.values())==12
    # Actual positive progress past six seconds does not free the whole segment.
    assert scheduler.playback_ack(PlaybackAck(first.scope,first.key,"progress",168000,192000,clock.monotonic()))
    await pump(scheduler);assert len(speech.segments)==1
    assert scheduler.playback_ack(PlaybackAck(first.scope,first.key,"completed",192000,192000,clock.monotonic()))
    await pump(scheduler)
    assert len(speech.segments)==2 and speech.segments[1].max_audio_frames==288000
    assert len(scheduler._speech_pending)==1
    assert sum(row[1] for row in scheduler._speech_pending.values())<=12
    assert len(scheduler.spoken.recent())==1
    await scheduler.stop()


async def test_operator_lower_speech_hard_cap_is_preserved_without_word_prediction():
    clock=Clock();scheduler,_,speech=system(clock,config=CommentaryConfig(max_pending_speech_seconds=4.0))
    scheduler.observe(view(clock,facts=[fact(clock,priority=0)]));scheduler.ready();await pump(scheduler)
    assert speech.segments[0].max_audio_frames==96000
    assert sum(row[1] for row in scheduler._speech_pending.values())==4
    await scheduler.stop()


@pytest.mark.parametrize("ending",["expiry","revocation","stale","reconnect","new_match"])
async def test_invalidated_evidence_or_view_never_speaks_and_does_not_extend_ttl(ending):
    clock=Clock();provider=Provider(blocked=True);scheduler,_,speech=system(clock,provider)
    original=fact(clock,lifetime=21)
    scheduler.observe(view(clock,facts=[original]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    if ending=="expiry":clock.advance(2);await fresh(scheduler,clock)
    elif ending=="revocation":await fresh(scheduler,clock,revoked=[original.id])
    elif ending=="stale":clock.advance(4);await pump(scheduler)
    elif ending=="reconnect":await fresh(scheduler,clock,scope=Scope("broadcast","match",2,1))
    else:await fresh(scheduler,clock,scope=Scope("broadcast","other",1,1))
    assert not speech.segments
    if ending=="new_match":assert scheduler.evidence.report()["entries"]==0 and not scheduler._ready
    await scheduler.stop()


async def test_terminal_at_idle_deadline_runs_only_outro_and_stop_flushes_fallback():
    clock=Clock();scheduler,provider,speech=system(clock)
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    clock.advance(20)
    await fresh(scheduler,clock,phase="GameOver",terminal=True)
    assert not provider.requests and scheduler.report()["counts"]["neutral_outros"]==1
    for _ in range(3):await pump(scheduler)
    assert len(speech.segments)==1
    await scheduler.stop(emergency=True)
    assert len(speech.cancellations)==1 and speech.cancellations[0][1]==0


async def test_terminal_outro_is_finished_only_after_actual_complete_playback_ack():
    clock=Clock();scheduler,provider,speech=system(clock)
    scheduler.observe(view(clock));scheduler.ready()
    clock.advance(1)
    await fresh(scheduler,clock,phase="GameOver",terminal=True,facts=[fact(clock,"outcome",priority=0)])
    assert len(provider.requests)==1 and provider.requests[0].candidate.mode=="outro"
    status=scheduler.report()
    assert status["terminal_outro_started"] and status["terminal_outro_pending"] and not status["terminal_outro_finished"]
    assert status["last_spoken"] is None
    assert speech.acknowledge(scheduler,clock,status="progress",frames=12000)
    assert scheduler.report()["terminal_outro_finished"] is False
    clock.advance(1)
    assert speech.acknowledge(scheduler,clock)
    status=scheduler.report()
    assert status["terminal_outro_finished"] and not status["terminal_outro_pending"]
    assert status["last_spoken"]["mode"]=="outro" and status["last_spoken"]["completed_at"]==clock.now
    await scheduler.stop()


async def test_two_concurrent_timer_calls_admit_only_one_atomic_request():
    clock=Clock();scheduler,provider,_=system(clock)
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    clock.advance(20);scheduler.observe(view(clock,sequence=2))
    await asyncio.gather(scheduler.step(),scheduler.step())
    for _ in range(20):await asyncio.sleep(0)
    assert len(provider.requests)==1 and provider.max_active==1
    await scheduler.stop()


async def test_presented_ack_exact_scope_sequence_is_required_and_public_reports_have_no_text():
    clock=Clock();sample=view(clock)
    with pytest.raises(ValueError):replace(sample,presentation=replace(sample.presentation,rendered_sequence=2))
    with pytest.raises(ValueError):replace(sample,presentation=replace(sample.presentation,scope=Scope("foreign","match",1,1)))
    scheduler,provider,_=system(clock)
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    assert "Разрешённый" not in str(scheduler.report()) and "Мы видели" not in str(scheduler.report())
    await scheduler.stop()


async def test_context_telemetry_measures_exact_sdk_json_bytes():
    clock=Clock();scheduler,provider,_=system(clock)
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    external=provider.requests[0].external_state
    actual=len(json.dumps(external,ensure_ascii=False,separators=(",",":")).encode("utf-8"))
    assert scheduler.report()["context_bytes_max"]==actual
    await scheduler.stop()


async def test_repeated_stop_caller_cancellation_keeps_owned_cleanup_alive():
    clock=Clock();scheduler,provider,speech=system(clock)
    entered=asyncio.Event();release=asyncio.Event();original=speech.cancel
    async def delayed(*args,**kwargs):
        entered.set();await release.wait();return await original(*args,**kwargs)
    speech.cancel=delayed
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    first=asyncio.create_task(scheduler.stop());await entered.wait();first.cancel()
    with pytest.raises(asyncio.CancelledError):await first
    second=asyncio.create_task(scheduler.stop());await asyncio.sleep(0);second.cancel()
    with pytest.raises(asyncio.CancelledError):await second
    assert not scheduler.report()["stopped"] and not provider.closed
    release.set();await asyncio.wait_for(scheduler.stop(),.5)
    assert scheduler.report()["stopped"] and scheduler.report()["pending_segments"]==0
    assert provider.closed and scheduler.budget.closed and len(speech.cancellations)==1


async def test_failed_provider_close_keeps_scheduler_retryable_and_does_not_skip_budget_close():
    class RetryProvider(Provider):
        attempts=0
        async def close(self):
            self.attempts+=1
            if self.attempts==1:raise RuntimeError("private close error")
            await super().close()
    clock=Clock();provider=RetryProvider();scheduler,_,_=system(clock,provider)
    with pytest.raises(RuntimeError,match="commentary_cleanup_failed"):await scheduler.stop()
    assert not scheduler.report()["stopped"] and scheduler.budget.closed and not provider.closed
    await scheduler.stop()
    assert scheduler.report()["stopped"] and provider.closed and provider.attempts==2


async def test_unresponsive_audio_flush_is_bounded_and_exact_pending_ownership_can_retry():
    clock=Clock();scheduler,provider,speech=system(clock,config=CommentaryConfig(interrupt_timeout_seconds=.01))
    release=asyncio.Event();finished=asyncio.Event();original=speech.cancel
    async def unresponsive(*args,**kwargs):
        try:
            while not release.is_set():
                try:await release.wait()
                except asyncio.CancelledError:pass
            return await original(*args,**kwargs)
        finally:finished.set()
    speech.cancel=unresponsive
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    try:
        with pytest.raises(RuntimeError,match="commentary_cleanup_failed"):
            await asyncio.wait_for(scheduler.stop(),.5)
        assert not scheduler.report()["stopped"] and scheduler.report()["pending_segments"]==1
        assert provider.closed and scheduler.budget.closed
    finally:
        release.set();await asyncio.wait_for(finished.wait(),.5)
    await scheduler.stop()
    assert scheduler.report()["stopped"] and scheduler.report()["pending_segments"]==0
