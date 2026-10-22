"""Own-context fixture guards; no clients, provider SDK, auth or server state."""
import sys
from pathlib import Path

import pytest
from google.protobuf.json_format import MessageToDict
from extensions.agents_arena.v1 import social_pb2 as pb
from actor_runtime.brains import FinishObjective
from actor_runtime.extensions import type_url
from actor_runtime.objectives import Objective
from actor_runtime.snapshots import DecisionContext, GameMetadata, PayloadSnapshot
from actor_standard import TRANSFORM, Transform2DObservation
from actor_standard_communication import MESSAGE_RECEIVED, MessageReceivedEvent
from actor_agents_arena import (SOCIAL_MATCH, SOCIAL_OWN_STATE, SOCIAL_VISIBLE_WORLD,
    SOCIAL_MEETING, SOCIAL_VISIBLE_ATTACK)

sys.path.insert(0, str(Path(__file__).parent))
import actor_native_human as fixture
from actor_agents_arena import social_fixtures as shared

NOW=1800000000000
SELF="own-actor"
MATCH="own-match"
WAIT=shared.WAIT
ALL=(WAIT,shared.APPROACH,shared.KILL,shared.VOTE,shared.SEND,"actor.wait",
    "agents-arena.ready-for-match","agents-arena.report-body","agents-arena.perform-task")


def record(cap,value):
    return PayloadSnapshot(capability_id=cap,entity_id=SELF,type_url=type_url(type(value)),
        sequence=1,revision=1,age_seconds=0.,value=MessageToDict(value),data_policy="session_only")


def context(*,phase=pb.SOCIAL_PHASE_EXPLORATION,alignment=pb.SOCIAL_ALIGNMENT_IMPOSTOR,
        life=pb.SOCIAL_LIFE_ALIVE,started=NOW-45000,ready=True,assigned=True,actors=(),bodies=(),
        previous=None,events=(),knowledge=(),cooldown=NOW,participants=None):
    values=[(SOCIAL_MATCH,pb.SocialMatchStateObservation(match_id=MATCH,phase=phase,phase_revision=2,
        started_at_unix_ms=started)),(SOCIAL_OWN_STATE,pb.SocialOwnStateObservation(match_id=MATCH,
        actor_id=SELF,assigned=assigned,ready=ready,life=life,alignment=alignment,kill_ready_at_unix_ms=cooldown)),
        (SOCIAL_VISIBLE_WORLD,pb.SocialVisibleWorldObservation(match_id=MATCH,recipient_actor_id=SELF,
            actors=actors,bodies=bodies)),(SOCIAL_MEETING,pb.SocialMeetingObservation(match_id=MATCH,
            meeting_id="meeting",phase_revision=2,participants=participants or [pb.SocialMeetingParticipant(
                actor_id=value,life=pb.SOCIAL_LIFE_ALIVE) for value in (SELF,"peer","witness")])),
        (TRANSFORM,Transform2DObservation(x=0,y=-1.4))]
    return DecisionContext(objective=Objective(text="The same trusted Objective for all participants"),
        decision_index=1,game=GameMetadata(game_id="agents-arena",game_version="0.9.0",protocol_major=1,protocol_minor=0),
        actor_id=SELF,observations=[record(cap,value) for cap,value in values],recent_events=list(events),
        working_knowledge=list(knowledge),previous_skill_result=previous,available_skills=[{"id":item} for item in ALL])


def actor(name="AI",identity="peer",x=.4):
    return pb.SocialVisibleActor(actor_id=identity,display_name=name,x=x,y=-.94)


def known_body(victim="human",**changes):
    return {"kind":"historical_observation","capability_id":SOCIAL_VISIBLE_WORLD,"source_actor_id":SELF,
        "scope_id":MATCH,"expires_at_unix_ms":NOW+60000,"content":{"body_id":"body","victim_actor_id":victim},**changes}


def known_human(**changes):
    return {"kind":"historical_observation","capability_id":SOCIAL_VISIBLE_WORLD,"source_actor_id":SELF,
        "scope_id":MATCH,"expires_at_unix_ms":NOW+60000,
        "content":{"actor_id":"human","display_name":fixture.HUMAN_NAME},**changes}


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(fixture,"NOW",lambda:NOW)
    monkeypatch.setattr(shared,"NOW",lambda:NOW)


async def brain(factory=fixture.witness_brain):
    value=factory();await value.start(Objective(text="The same Objective"),[]);return value


@pytest.mark.parametrize("factory",[fixture.witness_brain,fixture.victim_brain])
async def test_ordinary_ready_dead_passive_and_authoritative_terminal(factory):
    value=await brain(factory)
    choice=await value.decide(context(phase=pb.SOCIAL_PHASE_WAITING_FOR_PLAYERS,ready=False,assigned=False))
    assert choice.skill_id=="agents-arena.ready-for-match"
    assert (await value.decide(context(life=pb.SOCIAL_LIFE_DEAD))).skill_id==WAIT
    assert isinstance(await value.decide(context(phase=pb.SOCIAL_PHASE_GAME_OVER)),FinishObjective)


@pytest.mark.parametrize("factory",[fixture.witness_brain,fixture.victim_brain])
async def test_crew_never_automates_task_report_or_meeting(factory):
    value=await brain(factory)
    choice=await value.decide(context(alignment=pb.SOCIAL_ALIGNMENT_CREW,
        bodies=[pb.SocialVisibleBody(body_id="body")],actors=[actor()]))
    assert choice.skill_id==WAIT


async def test_witness_waits_45s_and_excludes_only_public_human_name():
    value=await brain()
    peers=[actor(fixture.HUMAN_NAME,"human",.1),actor()]
    assert (await value.decide(context(actors=peers,started=NOW-44999))).skill_id==WAIT
    assert (await value.decide(context(actors=peers))).arguments=={"actor_id":"peer"}
    choice=await value.decide(context(actors=list(reversed(peers)),previous={"skill_id":shared.APPROACH,"status":"succeeded"}))
    assert choice.skill_id==shared.KILL and choice.arguments=={"actor_id":"peer"}


async def test_lost_visible_target_is_never_killed_from_history():
    value=await brain();await value.decide(context(actors=[actor()]))
    choice=await value.decide(context(actors=[],previous={"skill_id":shared.APPROACH,"status":"succeeded"}))
    assert choice.skill_id==WAIT


@pytest.mark.parametrize("evidence",["current","history","receipt"])
async def test_witness_stops_after_first_body_without_auto_reporting(evidence):
    value=await brain()
    kwargs={"actors":[actor()]}
    if evidence=="current":kwargs["bodies"]=[pb.SocialVisibleBody(body_id="body",victim_actor_id="victim")]
    if evidence=="history":kwargs["knowledge"]=[known_body("victim")]
    if evidence=="receipt":kwargs["previous"]={"skill_id":shared.KILL,"status":"succeeded"}
    assert (await value.decide(context(**kwargs))).skill_id==WAIT


async def test_victim_first_targets_visible_public_human_then_second_ai_after_180s():
    value=await brain(fixture.victim_brain)
    peers=[actor(),actor(fixture.HUMAN_NAME,"human",2)]
    assert (await value.decide(context(actors=peers))).arguments=={"actor_id":"human"}
    assert (await value.decide(context(actors=peers,previous={"skill_id":shared.APPROACH,"status":"succeeded"}))).arguments=={"actor_id":"human"}
    facts=[known_human(),known_body()]
    assert (await value.decide(context(actors=[actor()],knowledge=facts,started=NOW-179999))).skill_id==WAIT
    assert (await value.decide(context(actors=[actor()],knowledge=facts,started=NOW-180000))).arguments=={"actor_id":"peer"}
    choice=await value.decide(context(actors=[actor()],knowledge=facts,started=NOW-180000,
        previous={"skill_id":shared.APPROACH,"status":"succeeded"}))
    assert choice.skill_id==shared.KILL and choice.arguments=={"actor_id":"peer"}
    assert (await value.decide(context(actors=[actor()],knowledge=facts,started=NOW-180000,
        previous={"skill_id":shared.KILL,"status":"succeeded"}))).skill_id==WAIT


@pytest.mark.parametrize("changed",[{"kind":"player_claim"},{"scope_id":"other"},
    {"source_actor_id":"other"},{"expires_at_unix_ms":NOW-1}])
async def test_claim_foreign_or_expired_body_cannot_unlock_second_kill(changed):
    value=await brain(fixture.victim_brain)
    choice=await value.decide(context(actors=[actor()],knowledge=[known_human(),known_body(**changed)],started=NOW-180000))
    assert choice.skill_id==WAIT


async def test_victim_ambiguous_public_name_and_cooldown_fail_closed():
    value=await brain(fixture.victim_brain)
    assert (await value.decide(context(actors=[actor(fixture.HUMAN_NAME,"h1"),actor(fixture.HUMAN_NAME,"h2")]))).skill_id==WAIT
    assert (await value.decide(context(actors=[actor(fixture.HUMAN_NAME,"h1")],cooldown=NOW+1))).skill_id==WAIT


async def test_discussion_sends_one_neutral_message_per_meeting_and_never_claims_a_witness():
    value=await brain()
    choice=await value.decide(context(phase=pb.SOCIAL_PHASE_DISCUSSION))
    assert choice.skill_id==shared.SEND and choice.arguments["channel_id"]=="discussion"
    assert "witnessed_attack"not in choice.arguments["text"]
    assert (await value.decide(context(phase=pb.SOCIAL_PHASE_DISCUSSION))).skill_id==WAIT


async def test_vote_crew_requires_own_witness_or_delivered_claim_impostor_skips():
    attack=pb.SocialVisibleAttackEvent(match_id=MATCH,observer_actor_id=SELF,attacker_actor_id="peer",victim_actor_id="victim")
    event=record(SOCIAL_VISIBLE_ATTACK,attack)
    for alignment,expected in [(pb.SOCIAL_ALIGNMENT_CREW,"peer"),(pb.SOCIAL_ALIGNMENT_IMPOSTOR,"")]:
        value=await brain();choice=await value.decide(context(phase=pb.SOCIAL_PHASE_VOTING,alignment=alignment,events=[event]))
        assert choice.arguments=={"candidate_actor_id":expected,"skip":not bool(expected)}
    value=await brain()
    claim=MessageReceivedEvent(match_id=MATCH,recipient_actor_id=SELF,sender_actor_id="witness",channel_id="discussion",
        text='{"claim":"witnessed_attack","actor_id":"peer"}',expires_at_unix_ms=NOW+1000)
    assert (await value.decide(context(phase=pb.SOCIAL_PHASE_VOTING,alignment=pb.SOCIAL_ALIGNMENT_CREW,
        events=[record(MESSAGE_RECEIVED,claim)]))).arguments["candidate_actor_id"]=="peer"


async def test_spoofed_own_witness_and_unreceived_prose_do_not_supply_vote():
    value=await brain();attack=pb.SocialVisibleAttackEvent(match_id=MATCH,observer_actor_id="other",attacker_actor_id="peer")
    choice=await value.decide(context(phase=pb.SOCIAL_PHASE_VOTING,alignment=pb.SOCIAL_ALIGNMENT_CREW,
        events=[record(SOCIAL_VISIBLE_ATTACK,attack)],knowledge=[{"kind":"hypothesis","content":{"attacker":"peer"}}]))
    assert choice.arguments=={"candidate_actor_id":"","skip":True}


async def test_participant_count_is_not_assumed_and_report_omits_actor_names_and_ids():
    value=await brain()
    players=[pb.SocialMeetingParticipant(actor_id=str(index),life=pb.SOCIAL_LIFE_ALIVE) for index in range(6)]
    choice=await value.decide(context(phase=pb.SOCIAL_PHASE_VOTING,participants=players))
    assert choice.arguments["skip"] is True
    await value.close();report=value.report()
    assert report["closed"] and report["provider"]=="native-human-witness-fixture"
    assert SELF not in str(report) and MATCH not in str(report) and fixture.HUMAN_NAME not in str(report)
