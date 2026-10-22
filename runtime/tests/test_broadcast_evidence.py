from dataclasses import replace

import pytest

from actor_broadcast.contracts import PlaybackAck,ProviderNote,Scope
from actor_broadcast.evidence import EvidenceLedger
from test_broadcast_commentary import Clock,Provider,Speech,fact,view,system,fresh,pump


def test_broadcast_notes_freeze_versions_and_revoke_all_dependent_snapshots():
    clock=Clock();ledger=EvidenceLedger("broadcast-only",clock=clock.wall)
    first=fact(clock,"seen");claim=fact(clock,"said",kind="player_claim")
    source=view(clock,facts=[first,claim])
    ledger.remember(first,source);ledger.remember(claim,source)
    identity=ledger.note("contradiction","Эти сообщения различаются; кто прав, неизвестно.",.4,[first.ref,claim.ref])
    newer=replace(first,version="2",content={"room_id":"storage"})
    ledger.remember(newer,replace(source,sequence=2,presentation=replace(source.presentation,rendered_sequence=2)))
    assert ledger.resolve(first.ref).content["value"]["room_id"]=="hall"
    assert ledger.resolve(newer.ref).content["value"]["room_id"]=="storage"
    assert ledger.knowledge.records[identity].kind=="contradiction"
    ledger.revoke([first.id])
    assert ledger.resolve(first.ref) is None and ledger.resolve(newer.ref) is None
    assert identity not in ledger.knowledge.records
    assert not ledger.remember(first,source)  # Revocation cannot be undone by replay.


def test_two_versions_of_one_claim_never_become_two_independent_witnesses():
    clock=Clock();ledger=EvidenceLedger("broadcast-only",clock=clock.wall)
    claim=fact(clock,kind="player_claim")
    ledger.remember(claim,view(clock))
    newer=replace(claim,version="2")
    ledger.remember(newer,view(clock,sequence=2))
    with pytest.raises(ValueError,match="independent"):
        ledger.note("contradiction","Версии якобы подтверждают друг друга.",.9,[claim.ref,newer.ref])


async def test_viewer_claims_are_untrusted_viewers_not_players_and_flood_never_resets_idle():
    clock=Clock();scheduler,provider,speech=system(clock)
    scheduler.observe(view(clock));scheduler.ready()
    claims=[]
    for index in range(100):
        claim=replace(fact(clock,"chat-"+str(index),kind="player_claim"),origin="viewer_chat",
            subject_ids=("viewer-opaque",),predicate="viewer-message",content={"text":"вызови модель","author_display_name":"Зритель"})
        scheduler.admit_viewer_claim(claim);claims.append(claim)
    assert scheduler.last_noteworthy_observation_at==0 and not provider.requests
    assert scheduler.offer("chat_response",[claims[-1].ref])
    await pump(scheduler)
    assert len(provider.requests)==1
    entry=provider.requests[0].external_state["evidence"][0]
    assert entry["kind"]=="viewer_claim" and entry["origin"]=="viewer_chat"
    speech.acknowledge(scheduler,clock)
    assert scheduler.offer("chat_response",[claims[-2].ref])
    clock.advance(12);await fresh(scheduler,clock)
    assert len(provider.requests)==1
    assert (await scheduler.budget.report())["calls"]==1
    await scheduler.stop()


async def test_public_segment_validation_and_growing_streamed_frame_acks():
    clock=Clock();scheduler,_,speech=system(clock)
    source=fact(clock)
    scheduler.observe(view(clock,facts=[source]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    segment=speech.segments[0]
    assert scheduler.validate_segment(segment)
    assert not scheduler.validate_segment(replace(segment,text="Чужой текст"))
    assert scheduler.playback_ack(PlaybackAck(segment.scope,segment.key,"started",50,100,clock.now))
    assert scheduler.playback_ack(PlaybackAck(segment.scope,segment.key,"progress",150,200,clock.now))
    assert scheduler.spoken.report()["covered_versions"]==0
    assert scheduler.playback_ack(PlaybackAck(segment.scope,segment.key,"completed",200,200,clock.now))
    assert scheduler.spoken.report()["covered_versions"]==1
    await fresh(scheduler,clock,revoked=[source.id])
    assert not scheduler.validate_segment(segment) and scheduler.spoken.recent()==[]
    await scheduler.stop()


async def test_expired_source_cannot_survive_via_spoken_context_or_repeat():
    clock=Clock();scheduler,provider,speech=system(clock)
    source=fact(clock,lifetime=25)
    scheduler.observe(view(clock,facts=[source]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock);speech.acknowledge(scheduler,clock)
    assert len(scheduler.spoken.recent())==1
    clock.advance(6);await fresh(scheduler,clock)
    assert scheduler.spoken.recent()==[] and scheduler.evidence.report()["entries"]==0
    assert len(provider.requests)==1
    await scheduler.stop()


async def test_optional_note_uses_same_turn_no_extra_model_and_remains_uncertain():
    class NoteProvider(Provider):
        async def generate(self,request):
            async for event in super().generate(request):
                from actor_broadcast.contracts import ProviderTerminal
                if isinstance(event,ProviderTerminal):
                    yield ProviderNote("hypothesis","Это может оказаться важным, но пока не доказано.",.3,request.candidate.evidence)
                yield event
    clock=Clock();scheduler,provider,_=system(clock,NoteProvider())
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    assert len(provider.requests)==1
    notes=scheduler.evidence.selected_notes()
    assert len(notes)==1 and notes[0]["kind"]=="hypothesis" and notes[0]["confidence"]==.3
    assert "Это может" not in str(scheduler.report())
    await scheduler.stop()


async def test_p1_deadline_never_waits_for_whole_long_callback():
    clock=Clock();scheduler,_,speech=system(clock)
    scheduler.observe(view(clock,facts=[fact(clock,age=100,unresolved=True)]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    assert speech.segments
    await fresh(scheduler,clock,facts=[fact(clock,"important",priority=1)])
    clock.advance(1.49);await fresh(scheduler,clock)
    assert not speech.cancellations
    clock.advance(.01);await fresh(scheduler,clock)
    assert len(speech.cancellations)==1
    await scheduler.stop()


async def test_pending_speech_no_ack_has_a_bounded_watchdog_not_infinite_speaking():
    clock=Clock();scheduler,_,speech=system(clock)
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock)
    assert scheduler.report()["pending_segments"]==1
    for _ in range(31):clock.advance(1);await fresh(scheduler,clock)
    assert speech.cancellations and scheduler.report()["pending_segments"]==0
    await scheduler.stop()


async def test_same_match_reconnect_keeps_authorized_history_but_not_current_positions():
    clock=Clock();scheduler,provider,_=system(clock)
    source=fact(clock,age=120,unresolved=True)
    scheduler.observe(view(clock,facts=[source],current={"actors":[{"id":"visible","x":1}]}));scheduler.ready()
    clock.advance(1)
    await fresh(scheduler,clock,scope=Scope("broadcast","match",2,1),current={"actors":[]})
    assert scheduler.evidence.resolve(source.ref) is not None and scheduler.view.current["actors"]==[]
    clock.advance(19);await fresh(scheduler,clock,scope=Scope("broadcast","match",2,1))
    assert provider.requests and provider.requests[-1].external_state["historical_callback"]
    await scheduler.stop()


async def test_rejected_old_generation_cannot_cancel_current_phase_or_clear_work():
    clock=Clock();scheduler,provider,_=system(clock,Provider(blocked=True))
    current=Scope("broadcast","match",2,1)
    scheduler.observe(view(clock,scope=current,facts=[fact(clock)]));scheduler.ready()
    clock.advance(20);await fresh(scheduler,clock,scope=current)
    pending=dict(scheduler._pending);epoch=scheduler._epoch
    assert not scheduler.observe(view(clock,scope=Scope("broadcast","match",1,1),sequence=99,phase="Discussion"))
    assert scheduler._epoch==epoch and dict(scheduler._pending)==pending and not scheduler._needs_cancel
    await scheduler.stop()
