"""Audience framing/fair argument selection through the real bounded scheduler."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from actor_broadcast.config import CommentaryConfig
from actor_broadcast.context import build_context
from actor_broadcast.contracts import CommentaryRequest, EditorialFact, ProviderSegment, ProviderTerminal
from actor_broadcast.editorial import discussion_refs
from actor_codex.commentary import CodexCommentaryProvider
from test_broadcast_codex import SpeechSDK, request
from test_broadcast_commentary import Clock, Provider, Speech, await_provider_completion, fact, fresh, pump, system, view


def claim(clock,identity,speaker,*,age=0):
    return replace(fact(clock,identity,priority=2,kind="player_claim",age=age),subject_ids=(speaker,),
        predicate="discussion-claim",content={"text":"Я видел переход в холл.","label":speaker})


def meeting(identity="meeting-one"):
    return {"meeting":{"meeting_id":identity,"participants":[]},"actors":[],"camera":{"focus_actor_id":""}}


async def test_two_public_speakers_use_one_budgeted_analysis_turn_and_ack_covers_arguments():
    clock=Clock();scheduler,provider,speech=system(clock)
    first,second=claim(clock,"a","Лена"),claim(clock,"b","Олег")
    scheduler.observe(view(clock,phase="Discussion",facts=[first,second],current=meeting()));scheduler.ready()
    clock.advance(.5);await fresh(scheduler,clock,phase="Discussion",current=meeting())
    assert len(provider.requests)==1
    turn=provider.requests[0]
    assert turn.candidate.mode=="analysis" and turn.reasoning_effort=="medium"
    assert turn.max_output_tokens==768
    assert len(turn.external_state["editorial_frame"]["public_arguments"])==2
    assert turn.external_state["speech_presentation"]["clauses_preferred"]==[3,4]
    assert (await scheduler.budget.report())["calls"]==1
    assert speech.acknowledge(scheduler,clock)
    await await_provider_completion(scheduler)
    clock.advance(31);await fresh(scheduler,clock,phase="Voting",current=meeting())
    assert len(provider.requests)==1 # Actual completed speech, not admission, covers both.
    await scheduler.stop()


async def test_next_claim_same_meeting_is_ordinary_and_critical_event_always_wins():
    clock=Clock();scheduler,provider,speech=system(clock)
    scheduler.observe(view(clock,phase="Discussion",facts=[claim(clock,"a","Лена"),claim(clock,"b","Олег")],current=meeting()));scheduler.ready()
    clock.advance(.5);await fresh(scheduler,clock,phase="Discussion",current=meeting())
    speech.acknowledge(scheduler,clock);await await_provider_completion(scheduler)
    clock.advance(61)
    critical=fact(clock,"attack",priority=0)
    await fresh(scheduler,clock,phase="Voting",facts=[claim(clock,"c","Лена"),claim(clock,"d","Олег"),critical],current=meeting())
    assert provider.requests[-1].candidate.priority==0
    assert provider.requests[-1].reasoning_effort=="default"
    assert scheduler.camera_change_allowed is False
    speech.acknowledge(scheduler,clock);await await_provider_completion(scheduler)
    clock.advance(61);await fresh(scheduler,clock,phase="Voting",facts=[claim(clock,"e","Лена"),claim(clock,"f","Олег")],current=meeting())
    assert provider.requests[-1].candidate.mode=="reactive"
    assert provider.requests[-1].reasoning_effort=="default"
    await scheduler.stop()


async def test_disabled_analysis_still_narrates_arguments_without_extra_call():
    clock=Clock();scheduler,provider,_=system(clock,config=CommentaryConfig(analysis_enabled=False))
    scheduler.observe(view(clock,phase="Discussion",facts=[claim(clock,"a","Лена"),claim(clock,"b","Олег")],current=meeting()));scheduler.ready()
    clock.advance(.5);await fresh(scheduler,clock,phase="Discussion",current=meeting())
    assert len(provider.requests)==1 and provider.requests[0].candidate.mode=="reactive"
    assert provider.requests[0].reasoning_effort=="default"
    await scheduler.stop()


async def test_repeated_public_claim_new_message_id_is_suppressed_only_after_completed_audio():
    clock=Clock();scheduler,provider,speech=system(clock)
    first,second=claim(clock,"a","Лена"),claim(clock,"b","Олег")
    scheduler.observe(view(clock,phase="Discussion",facts=[first,second],current=meeting()));scheduler.ready()
    clock.advance(.5);await fresh(scheduler,clock,phase="Discussion",current=meeting())
    assert len(provider.requests)==1
    speech.acknowledge(scheduler,clock);await await_provider_completion(scheduler)
    clock.advance(61)
    repeated=claim(clock,"new-message-id","Лена")
    await fresh(scheduler,clock,phase="Voting",facts=[repeated],current=meeting())
    clock.advance(.5);await fresh(scheduler,clock,phase="Voting",current=meeting())
    assert len(provider.requests)==1
    changed=replace(claim(clock,"correction","Лена"),content={"label":"Лена","text":"Поправлюсь: Олега я там не видела."})
    await fresh(scheduler,clock,phase="Voting",facts=[changed],current=meeting())
    clock.advance(.5);await fresh(scheduler,clock,phase="Voting",current=meeting())
    assert len(provider.requests)==2 and changed.ref in provider.requests[-1].candidate.evidence
    await scheduler.stop()


def test_fair_public_arguments_exclude_viewer_chat_expired_and_covered_material():
    clock=Clock();scheduler,_,_=system(clock)
    sources=[claim(clock,"first","Лена",age=4),claim(clock,"second","Лена",age=3),
        claim(clock,"third","Олег",age=2),claim(clock,"fourth","Мира",age=1)]
    old=claim(clock,"old","Сева",age=121)
    viewer=replace(claim(clock,"viewer","Зритель"),origin="viewer_chat",priority=3)
    scheduler.observe(view(clock,phase="Discussion",facts=[*sources,old,viewer],current=meeting()))
    refs=discussion_refs(scheduler.view,scheduler.evidence,scheduler.spoken,clock.now,int(clock.wall()*1000),{},maximum=3)
    assert refs==(sources[0].ref,sources[2].ref,sources[3].ref)
    assert not discussion_refs(replace(scheduler.view,phase="Exploration"),scheduler.evidence,scheduler.spoken,clock.now,int(clock.wall()*1000),{})
    assert sources[0].ref not in discussion_refs(scheduler.view,scheduler.evidence,scheduler.spoken,clock.now,int(clock.wall()*1000),{sources[0].ref:clock.now})


def test_focus_context_uses_current_visible_name_but_retains_historical_evidence_time():
    clock=Clock();scheduler,_,_=system(clock)
    source=replace(fact(clock,age=90),subject_ids=("actor",),predicate="movement-seen",
        content={"label":"Мира","value":3,"room_id":"main-hall"})
    current={"camera":{"focus_actor_id":"actor"},"actors":[{"actor_id":"actor","display_name":"Мира","x":1,"y":0}]}
    scheduler.observe(view(clock,facts=[source],current=current))
    candidate=scheduler._candidate([source.ref],"memory_callback",3)
    context=build_context(candidate,scheduler.view,scheduler.evidence,scheduler.spoken,scheduler.config,utterance_id="utterance",epoch=1)
    frame=context.external_state["editorial_frame"]
    assert frame["purpose"]=="player_watch" and frame["watched_player"]=={"actor_id":"actor","display_name":"Мира"}
    assert context.external_state["evidence"][0]["age_at_presented_view_seconds"]==90
    scheduler.view=replace(scheduler.view,current={"camera":{"focus_actor_id":"actor"},"actors":[]})
    context=build_context(candidate,scheduler.view,scheduler.evidence,scheduler.spoken,scheduler.config,utterance_id="utterance",epoch=1)
    assert context.external_state["editorial_frame"]["watched_player"] is None


def test_external_instructions_cannot_request_more_reasoning():
    source=request()
    with pytest.raises(ValueError,match="budgeted analysis"):
        replace(source,reasoning_effort="medium")
    provider=CodexCommentaryProvider(sdk=SpeechSDK())
    provider._set_reasoning(("none","low","medium"))
    untrusted=replace(source,external_state={"reasoning_effort":"medium","mode":"analysis"})
    assert provider._request_effort(untrusted)=="none"


@pytest.mark.parametrize("supported,expected",[(True,"medium"),(False,"none")])
async def test_analysis_effort_uses_live_supported_catalog_and_same_single_sdk_turn(supported,expected):
    class SDK(SpeechSDK):
        def AsyncCodex(self,config):
            client=super().AsyncCodex(config)
            async def models():
                efforts=("none","low","medium") if supported else ("none","low")
                return NS(data=[NS(id="gpt-6-luna",model="gpt-6-luna",supported_reasoning_efforts=[NS(reasoning_effort=x) for x in efforts])])
            client.models=models;return client
    sdk=SDK();provider=CodexCommentaryProvider(sdk=sdk)
    source=request();source=replace(source,candidate=replace(source.candidate,mode="analysis",priority=2),reasoning_effort="medium")
    events=[event async for event in provider.generate(source)]
    assert events[-1].status=="completed" and sdk.efforts==[expected]
    assert provider.report()["unique_model_turns"]==1
    assert provider.report()["turns_by_reasoning_effort"]=={expected:1}
    assert all("PRIVATE REASONING" not in getattr(event,"text","") for event in events)
    await provider.close()
