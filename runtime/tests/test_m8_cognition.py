"""Cognitive efficiency and provenance invariants; no models or game processes."""
import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from actor.core.v1 import actor_pb2 as core
from extensions.agents_arena.v1 import social_pb2 as pb
from actor_runtime.errors import ActorError
from actor_runtime.knowledge import KnowledgeRecord, WorkingKnowledge, select_knowledge
from actor_runtime.objectives import Objective
from actor_runtime.snapshots import build_decision_context
from actor_agents_arena import (SOCIAL_TASK_WORK, SOCIAL_TASK_STEP, SOCIAL_ACTION_RESULT, SOCIAL_MATCH,
    SOCIAL_MEETING, SOCIAL_OWN_STATE, SOCIAL_VISIBLE_WORLD)
from actor_agents_arena.social_agent import SocialMatchPolicy
from actor_agents_arena.social_skills import SocialActionSkill
from actor_standard import TRANSFORM, Transform2DObservation
from actor_standard_communication import (PEERS, CommunicationPeersObservation, SendMessageCommand,
    MESSAGE_RECEIVED, MESSAGE_DELIVERY_RESULT, MessageReceivedEvent, MessageDeliveryResultEvent,
    MESSAGE_DELIVERY_STAGE_SERVER_ACCEPTED)
from conftest import envelope, message
from test_social_runtime import observe, event, engine, command

NOW=lambda: int(time.time()*1000)


def fact(identity,kind="historical_observation",expiry=100000):
    return KnowledgeRecord(id=identity,scope_id="one",kind=kind,source_event_id="event-"+identity,
        source_actor_id="own" if kind=="historical_observation" else "peer",
        observed_at_unix_ms=1000,expires_at_unix_ms=expiry,content={"statement":identity})


def test_contradiction_is_uncertain_model_note_with_two_actual_sources_and_expiry():
    clock=[2.0]
    store=WorkingKnowledge("one",clock=lambda:clock[0])
    store.remember(fact("seen",expiry=4000)); store.remember(fact("said","player_claim"))
    identity=store.contradict("These accounts appear incompatible",.4,["seen","said"])
    value=store.records[identity]
    assert value.kind=="contradiction" and value.confidence==.4
    assert value.content["epistemic_status"]=="unresolved_incompatibility"
    assert store.records["said"].kind=="player_claim" and store.records["seen"].source_actor_id=="own"
    with pytest.raises(ActorError): store.contradict("invented",.9,["seen","missing"])
    with pytest.raises(ActorError): store.contradict("same source twice",.9,["seen","seen"])
    assert value.expires_at_unix_ms==4000
    clock[0]=4.1
    assert identity not in {record["id"] for record in store.context()}
    assert "statement" not in str(store.report())


def test_context_budget_keeps_note_and_evidence_as_a_group_and_never_changes_time():
    store=WorkingKnowledge("one",clock=lambda:2.0)
    store.remember(fact("seen"));store.remember(fact("said","player_claim"))
    identity=store.contradict("A claim differs from my observation",.5,["seen","said"])
    values=store.context()
    selected=select_knowledge(values,max_entries=2,max_bytes=8192)
    assert identity not in {record["id"] for record in selected}
    selected=select_knowledge(values,max_entries=3,max_bytes=8192)
    selected_by_id={record["id"]:record for record in selected}
    assert len(selected_by_id)==3 and identity in selected_by_id
    assert set(selected_by_id[identity]["evidence_refs"])==set(selected_by_id)-{identity}
    assert selected_by_id[identity]["observed_at_unix_ms"]==2000


def test_notes_pin_exact_observed_version_while_latest_source_and_claim_advance():
    store=WorkingKnowledge("one",clock=lambda:2.0)
    seen=fact("latest-position").model_copy(update={"sequence":1,"revision":1,"content":{"room":"Hall"}})
    claim_value=fact("claim","player_claim").model_copy(update={"sequence":1,"content":{"room":"Engineering"}})
    store.remember(seen);store.remember(claim_value)
    note_id=store.contradict("The locations appear incompatible",.4,[seen.id,claim_value.id])
    note=store.records[note_id]
    sources=[store.records[ref] for ref in note.evidence_refs]
    store.remember(seen.model_copy(update={"sequence":2,"revision":2,"source_event_id":"new-event",
        "observed_at_unix_ms":1900,"content":{"room":"Storage"}}))
    assert store.records[seen.id].content=={"room":"Storage"}
    assert store.records[note_id]==note
    assert {value.content["room"] for value in sources}=={"Hall","Engineering"}
    frozen=next(value for value in sources if value.kind=="historical_observation")
    assert store.records[frozen.id].source_event_id==seen.source_event_id
    assert store.records[frozen.id].sequence==1 and store.records[frozen.id].observed_at_unix_ms==1000


def test_replacing_a_mutable_source_invalidates_direct_dependent_note_cascade():
    store=WorkingKnowledge("one",clock=lambda:2.0)
    source=fact("mutable").model_copy(update={"sequence":1})
    store.remember(source)
    # Installed policies may supply notes directly; unlike the public note
    # Skill, these deliberately refer to a mutable slot, so fail closed.
    direct=fact("direct","hypothesis").model_copy(update={"evidence_refs":[source.id]})
    indirect=fact("indirect","hypothesis").model_copy(update={"evidence_refs":[direct.id]})
    store.remember(direct);store.remember(indirect)
    store.remember(source.model_copy(update={"sequence":2}))
    assert set(store.records)=={source.id}


@pytest.mark.parametrize("references",[2,8])
def test_full_knowledge_store_installs_exact_citation_group_atomically(references):
    store=WorkingKnowledge("one",max_entries=references+2,clock=lambda:2.0)
    for index in range(references+2):
        store.remember(fact("source-"+str(index)))
    original=[store.records["source-"+str(index)].model_dump() for index in range(references)]
    identity=store.contradict("These are conflicting accounts",.4,[value["id"] for value in original])
    note=store.records[identity]
    assert len(note.evidence_refs)==references and len(store.records)<=references+2
    for ref,source in zip(note.evidence_refs,original):
        frozen=store.records[ref]
        assert frozen.content==source["content"] and frozen.source_event_id==source["source_event_id"]
        assert frozen.observed_at_unix_ms==source["observed_at_unix_ms"]


def test_citation_group_that_cannot_fit_fails_without_partial_snapshots():
    store=WorkingKnowledge("one",max_entries=2,clock=lambda:2.0)
    store.remember(fact("first"));store.remember(fact("second"))
    original=dict(store.records)
    with pytest.raises(ActorError,match="knowledge_capacity"):
        store.contradict("Both cannot hold",.5,["first","second"])
    assert dict(store.records)==original


def test_same_fact_via_latest_and_snapshot_alias_cannot_form_a_contradiction():
    store=WorkingKnowledge("one",clock=lambda:2.0)
    store.remember(fact("latest"))
    prior=store.hypothesize("This may matter",.4,["latest"])
    snapshot=store.records[prior].evidence_refs[0]
    original=dict(store.records)
    with pytest.raises(ActorError,match="hypothesis_evidence_missing"):
        store.contradict("These aliases conflict",.5,["latest",snapshot])
    assert dict(store.records)==original


async def test_phase_selection_reduces_external_bytes_without_synthesizing_facts(session):
    await observe(session,phase=pb.SOCIAL_PHASE_DISCUSSION)
    objective=Objective(text="Play using only permitted evidence")
    full=build_decision_context(session,objective,[])
    compact=build_decision_context(session,objective,[],selection=SocialMatchPolicy().context_selection(session))
    ids={value.capability_id for value in compact.observations}
    assert {SOCIAL_MATCH,SOCIAL_OWN_STATE,SOCIAL_MEETING}<=ids
    assert TRANSFORM not in ids and SOCIAL_VISIBLE_WORLD not in ids
    assert compact.external_bytes < full.external_bytes*.8
    assert compact.observations_omitted > full.observations_omitted
    assert "SOCIAL_ALIGNMENT_CREW" not in str(session.privacy.public(compact))
    await observe(session,phase=pb.SOCIAL_PHASE_EXPLORATION)
    exploring=build_decision_context(session,objective,[],selection=SocialMatchPolicy().context_selection(session))
    selected=next(value for value in exploring.observations if value.capability_id==TRANSFORM)
    original=session.world.latest_entry(Transform2DObservation,capability_id=TRANSFORM)
    from google.protobuf.json_format import ParseDict
    assert selected.sequence==original.sequence
    assert ParseDict(selected.value,Transform2DObservation())==original.payload


async def peers(session):
    value=CommunicationPeersObservation(match_id="match-social",allowed_channels=["discussion"],
        peers=[{"actor_id":"peer","display_name":"Peer"}])
    await session.accept(message(session.last_received_sequence+1,observation=core.ObservationFrame(
        revision=session.last_received_sequence+1,observations=[envelope(PEERS,"actor.standard.communication",value)])))


async def claim(session,index,text="ordinary player claim"):
    await event(session,MESSAGE_RECEIVED,MessageReceivedEvent(message_id="m-"+str(index),sender_actor_id="peer",
        recipient_actor_id="actor-1",channel_id="discussion",text=text,server_unix_ms=NOW(),
        expires_at_unix_ms=NOW()+60000,match_id="match-social",server_sequence=index),"actor.standard.communication")


async def test_early_discussion_burst_returns_once_as_attributed_claims_without_replan(session):
    await observe(session,phase=pb.SOCIAL_PHASE_DISCUSSION);await peers(session)
    for index in range(1,7): await claim(session,index)
    scheduler=engine(session)
    result=await scheduler.run("agents-arena.await-discussion-update",{"settle_seconds":.01})
    assert result.status=="succeeded" and result.commands_sent==0
    assert result.output["received_count"]==6 and result.output["latest_server_sequence"]==6
    assert all(value["epistemic_status"]=="unverified_player_claim" for value in result.output["claims"])
    assert not session.event_router.replan.is_set()
    assert "ordinary player claim" not in str(session.privacy.public(result))


async def test_discuss_requires_real_receipt_then_batches_without_claiming_read(session):
    await observe(session,phase=pb.SOCIAL_PHASE_DISCUSSION);await peers(session)
    scheduler=engine(session)
    pending=asyncio.create_task(scheduler.run("agents-arena.discuss",{"text":"What did you observe?","settle_seconds":.01,"timeout_seconds":.1,"decision_reserve_seconds":0.0}))
    outgoing=await command(session,SendMessageCommand)
    assert outgoing.channel_id=="discussion"
    await claim(session,1,"I was in Security.")
    assert not pending.done()
    await event(session,MESSAGE_DELIVERY_RESULT,MessageDeliveryResultEvent(correlation_id=outgoing.correlation_id,
        message_id="sent-1",accepted_by_server=True,stage=MESSAGE_DELIVERY_STAGE_SERVER_ACCEPTED,
        server_unix_ms=NOW(),match_id="match-social"),"actor.standard.communication")
    result=await pending
    assert result.status=="succeeded" and result.output["accepted_by_server"]
    assert result.output["receipt_stage"]=="server_accepted" and result.output["received_count"]==1
    assert "I was in Security" not in str(result.public())


async def work_observation(session,**kwargs):
    value=pb.SocialOwnTaskWorkObservation(match_id="match-social",recipient_actor_id="actor-1",**kwargs)
    await session.accept(message(session.last_received_sequence+1,observation=core.ObservationFrame(
        revision=session.last_received_sequence+1,observations=[envelope(SOCIAL_TASK_WORK,"agents-arena",value)])))


@pytest.mark.parametrize("mechanic,choice",[(pb.SOCIAL_TASK_MECHANIC_CIRCUIT_CALIBRATION,pb.SOCIAL_TASK_CHOICE_BETA),
    (pb.SOCIAL_TASK_MECHANIC_SENSOR_OBSERVATION,pb.SOCIAL_TASK_CHOICE_RISING)])
async def test_displayed_task_step_and_final_original_receipt_are_both_required(session,monkeypatch,mechanic,choice):
    monkeypatch.setattr(SocialActionSkill,"approach",AsyncMock())
    task=pb.SocialTask(task_id="task-1",room_id="main-hall",work_duration_ms=2000,mechanic=mechanic)
    await observe(session,tasks=[task],connection_generation=7)
    scheduler=engine(session)
    pending=asyncio.create_task(scheduler.run("agents-arena.perform-task",{"task_id":"task-1"}))
    start=await command(session,pb.SocialWorkTaskCommand)
    assert start.context.connection_generation==7
    await event(session,SOCIAL_ACTION_RESULT,pb.SocialActionResultEvent(match_id="match-social",correlation_id=start.context.correlation_id,
        action=pb.SOCIAL_ACTION_WORK_TASK,accepted=True,work_id="work-1"))
    await work_observation(session,work_id="work-1",task_id="task-1",mechanic=mechanic,step_revision=3,step_count=3,
        required_choice=pb.SOCIAL_TASK_CHOICE_BETA,sample_a=2,sample_b=5,sample_b_ready=True,deadline_unix_ms=NOW()+10000)
    step=await command(session,pb.SocialTaskStepCommand)
    assert step.choice==choice and step.step_revision==3 and step.context.connection_generation==7
    await event(session,SOCIAL_ACTION_RESULT,pb.SocialActionResultEvent(match_id="match-social",correlation_id=step.context.correlation_id,
        action=pb.SOCIAL_ACTION_TASK_STEP,accepted=True,completed=True,work_id="work-1"))
    await asyncio.sleep(.02);assert not pending.done()
    await event(session,SOCIAL_ACTION_RESULT,pb.SocialActionResultEvent(match_id="match-social",correlation_id=start.context.correlation_id,
        action=pb.SOCIAL_ACTION_WORK_TASK,accepted=True,completed=True,work_id="work-1"))
    assert (await asyncio.wait_for(pending,1)).status=="succeeded"


async def test_connection_generation_changes_episode_even_without_phase_change(session):
    await observe(session,connection_generation=1)
    policy=SocialMatchPolicy();key=policy.evaluate(session).episode_key
    await observe(session,connection_generation=2)
    assert policy.evaluate(session).episode_key!=key


async def test_resume_event_blocks_reasoning_until_new_generation_observation(session):
    from actor_agents_arena import SOCIAL_OWN_STATE_CHANGED
    await observe(session,connection_generation=1)
    policy=SocialMatchPolicy();old=policy.evaluate(session).episode_key
    await event(session,SOCIAL_OWN_STATE_CHANGED,pb.SocialOwnStateChangedEvent(state=pb.SocialOwnStateObservation(
        match_id="match-social",actor_id="actor-1",life=pb.SOCIAL_LIFE_ALIVE,assigned=True,can_move=True,
        connection_generation=2,server_revision=500)))
    assert policy.evaluate(session).state=="suspended"
    await observe(session,connection_generation=2)
    assert policy.evaluate(session).state=="active" and policy.evaluate(session).episode_key!=old


async def test_compact_provider_catalog_deduplicates_exact_schemas_without_changing_validation(session):
    import json
    from actor_codex.catalog import compact_catalog
    from actor_codex.schema import decision_schema
    await observe(session)
    catalog=engine(session).registry.catalog(session)
    compact=compact_catalog(catalog)
    original={value["id"]:value for value in catalog}
    for value in compact["skills"]:
        for field in ("input_schema","result_schema"):
            assert compact["schemas"][value[field+"_ref"]]==original[value["id"]][field]
    assert len(json.dumps(compact))<len(json.dumps(catalog))*.8
    assert decision_schema(catalog)==decision_schema(list(reversed(catalog)))


async def test_new_social_capability_negotiates_into_typed_skill_with_unchanged_core(session,manifest):
    import hashlib
    from actor.core.v1 import bridge_pb2 as wire
    from actor_runtime.extensions import type_url
    # Fingerprint of the last committed Core1.0 Python descriptor pair, before
    # M8. This is an ABI guard, not a snapshot of game-specific definitions.
    assert hashlib.sha256(core.DESCRIPTOR.serialized_pb+wire.DESCRIPTOR.serialized_pb).hexdigest()==(
        "c790a32969e49f5e933d08693672dc1429270e76696df5a9f412cc94545c9134")
    capability=next(value for value in manifest.capabilities if value.capability_id==SOCIAL_TASK_STEP)
    assert capability.extension_id=="agents-arena" and capability.input_type_url==type_url(pb.SocialTaskStepCommand)
    assert "agents-arena" in session.negotiated.available and session.manifest.protocol_version.major==1
    await observe(session,tasks=[pb.SocialTask(task_id="new-calibration",mechanic=pb.SOCIAL_TASK_MECHANIC_CIRCUIT_CALIBRATION)])
    definition=next(value for value in engine(session).registry.catalog(session) if value["id"]=="agents-arena.perform-task")
    assert SOCIAL_TASK_STEP in definition["owned_command_capabilities"]
    assert SOCIAL_TASK_WORK in definition["required_capabilities"]
    for required in (SOCIAL_TASK_WORK,SOCIAL_TASK_STEP):
        saved=session.capabilities.pop(required)
        try:
            assert "agents-arena.perform-task" not in {value["id"] for value in engine(session).registry.catalog(session)}
        finally:
            session.capabilities[required]=saved
    from actor_agents_arena import SOCIAL_KILL_PRESENTATION, SOCIAL_WAITING_PLAYERS
    rules=session.event_router.rules
    for cap in (SOCIAL_WAITING_PLAYERS,SOCIAL_TASK_WORK,SOCIAL_KILL_PRESENTATION):
        assert any(rule.capability_id==cap and rule.data_policy=="session_only" for rule in rules.rules.values())
    # Recipient-specific presentation never becomes a public/unknown event;
    # malformed empty/foreign recipients are rejected before the world cache.
    value=pb.SocialKillPresentationEvent(match_id="match-social",recipient_actor_id="foreign",
        presentation_id="kill-presentation",victim_actor_id="victim",server_unix_ms=NOW())
    await event(session,SOCIAL_KILL_PRESENTATION,value)
    assert not any(entry.capability_id==SOCIAL_KILL_PRESENTATION for entry in session.world.events)
    value.recipient_actor_id=""
    await event(session,SOCIAL_KILL_PRESENTATION,value)
    assert not any(entry.capability_id==SOCIAL_KILL_PRESENTATION for entry in session.world.events)
    value.recipient_actor_id="actor-1"
    await event(session,SOCIAL_KILL_PRESENTATION,value)
    entry=next(entry for entry in session.world.events if entry.capability_id==SOCIAL_KILL_PRESENTATION)
    assert entry.data_policy=="session_only"


def test_task_completion_budget_covers_public_timed_steps_instead_of_hold_duration():
    from actor_agents_arena.social_skills import WorkTask
    sensor=pb.SocialTask(work_duration_ms=1000,sample_delay_ms=30000,
        mechanic=pb.SOCIAL_TASK_MECHANIC_SENSOR_OBSERVATION)
    calibration=pb.SocialTask(work_duration_ms=1000,step_count=3,
        mechanic=pb.SOCIAL_TASK_MECHANIC_CIRCUIT_CALIBRATION)
    assert WorkTask.completion_timeout(sensor)==40
    assert WorkTask.completion_timeout(calibration)==55
    calibration.step_count=1000
    assert WorkTask.completion_timeout(calibration)==90


async def test_task_step_waits_for_own_sample_window_sends_once_and_fences_resume(monkeypatch):
    from types import SimpleNamespace
    from actor_agents_arena.social_skills import WorkTask, TaskInput
    from actor_agents_arena import SOCIAL_OWN_STATE
    clock=[1000];monkeypatch.setattr("actor_agents_arena.social_skills.NOW",lambda:clock[0])
    own=pb.SocialOwnStateObservation(match_id="own-match",actor_id="own",connection_generation=1)
    match=pb.SocialMatchStateObservation(match_id="own-match",phase_revision=5)
    work=pb.SocialOwnTaskWorkObservation(match_id="own-match",recipient_actor_id="own",work_id="work",
        task_id="task",step_revision=2,step_index=1,step_count=1,mechanic=pb.SOCIAL_TASK_MECHANIC_SENSOR_OBSERVATION,
        not_before_unix_ms=2000,deadline_unix_ms=4000,sample_a=5,sample_b=2)
    values={SOCIAL_MATCH:match,SOCIAL_OWN_STATE:own,SOCIAL_TASK_WORK:work}
    sent=[]
    context=SimpleNamespace(actor_id="own",observation=lambda capability,*_:values[capability],
        command=lambda capability,value,**_:sent.append(value),wait_event=AsyncMock(return_value=SimpleNamespace(
            payload=pb.SocialActionResultEvent(accepted=True))))
    skill=WorkTask();skill.preflight(context,TaskInput(task_id="task"));skill.work_id="work";skill.task_id="task"
    await skill.step(context);assert not sent
    clock[0]=2000;await skill.step(context);assert not sent
    work.sample_b_ready=True
    await skill.step(context);await skill.step(context)
    assert len(sent)==1 and sent[0].choice==pb.SOCIAL_TASK_CHOICE_FALLING
    own.connection_generation=2;work.step_revision=3
    with pytest.raises(ActorError,match="phase_changed"):await skill.step(context)
    assert len(sent)==1
