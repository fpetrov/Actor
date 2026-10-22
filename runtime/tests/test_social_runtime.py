import asyncio
import json
import time
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from google.protobuf.any_pb2 import Any

from actor.core.v1 import actor_pb2 as core
from extensions.agents_arena.v1 import social_pb2 as pb
from actor_runtime.brains import SequenceBrain
from actor_runtime.episodes import MatchEpisodeController, EpisodeLimits
from actor_runtime.extensions import type_url
from actor_runtime.knowledge import WorkingKnowledge
from actor_runtime.objectives import Objective
from actor_runtime.skills import SkillRegistry, SkillScheduler, SkillError
from actor_runtime.snapshots import build_decision_context
from actor_agents_arena import (SOCIAL_MATCH, SOCIAL_OWN_STATE, SOCIAL_VISIBLE_WORLD, SOCIAL_MEETING,
    SOCIAL_TASKS, SOCIAL_TASK_WORK, SOCIAL_EMERGENCY, SOCIAL_ACTION_RESULT, SOCIAL_VISIBLE_ATTACK, SOCIAL_OWN_STATE_CHANGED)
from actor_agents_arena.social_agent import SocialMatchPolicy, SocialScriptedBrain
from actor_agents_arena.social_skills import SocialActionSkill, skill_package
from actor_standard import TRANSFORM, MOTION, Transform2DObservation, Motion2DObservation
from actor_agents_arena import ROOM_MEMBERSHIP, RoomMembershipObservation
from actor_standard_communication import MESSAGE_RECEIVED, MessageReceivedEvent
from conftest import envelope, extension_version, message

NOW = lambda: int(time.time() * 1000)


async def observe(session, *, phase=pb.SOCIAL_PHASE_EXPLORATION, alignment=pb.SOCIAL_ALIGNMENT_CREW,
                  life=pb.SOCIAL_LIFE_ALIVE, phase_revision=3, visible=(), bodies=(), tasks=(), ready=True,
                  recipient="actor-1", match_id="match-social", connection_generation=1):
    sequence = session.last_received_sequence + 1
    own = pb.SocialOwnStateObservation(match_id=match_id, actor_id=recipient, alignment=alignment, assigned=True,
        life=life, can_move=life == pb.SOCIAL_LIFE_ALIVE and phase == pb.SOCIAL_PHASE_EXPLORATION,
        ready=ready, server_revision=sequence, connection_generation=connection_generation)
    values = [(SOCIAL_MATCH, pb.SocialMatchStateObservation(match_id=match_id, phase=phase,
        phase_revision=phase_revision, server_revision=sequence, mode="SocialDeduction",
        phase_ends_at_unix_ms=NOW() + 60000)), (SOCIAL_OWN_STATE, own),
        (SOCIAL_VISIBLE_WORLD, pb.SocialVisibleWorldObservation(match_id=match_id, recipient_actor_id=recipient,
            server_revision=sequence, captured_at_unix_ms=NOW(), actors=visible, bodies=bodies)),
        (SOCIAL_MEETING, pb.SocialMeetingObservation(match_id=match_id, meeting_id="meeting-1", phase_revision=phase_revision,
            voting_ends_at_unix_ms=NOW() + 60000, participants=[pb.SocialMeetingParticipant(actor_id="peer", life=pb.SOCIAL_LIFE_ALIVE)])),
        (SOCIAL_TASKS, pb.SocialOwnTasksObservation(match_id=match_id, recipient_actor_id=recipient,
            contributes_to_crew_progress=alignment == pb.SOCIAL_ALIGNMENT_CREW, tasks=tasks)),
        (SOCIAL_TASK_WORK, pb.SocialOwnTaskWorkObservation(match_id=match_id, recipient_actor_id=recipient)),
        (SOCIAL_EMERGENCY, pb.SocialEmergencyStateObservation(match_id=match_id))]
    observations = [envelope(cap, "agents-arena", value) for cap, value in values]
    observations += [envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=0, y=-1.4)),
        envelope(MOTION, "actor.standard", Motion2DObservation()),
        envelope(ROOM_MEMBERSHIP, "agents-arena", RoomMembershipObservation(room_id="main-hall"))]
    await session.accept(message(sequence, observation=core.ObservationFrame(revision=sequence, observations=observations)))


async def event(session, capability, value, extension="agents-arena"):
    packed = Any(); packed.Pack(value)
    sequence = session.last_received_sequence + 1
    await session.accept(message(sequence, event=core.GameEventEnvelope(capability_id=capability,
        extension_id=extension, extension_version=extension_version(extension),
        entity_id=session.world.self_actor_id, revision=sequence, payload=packed)))


async def command(session, cls):
    stream = session.outgoing()
    async with asyncio.timeout(1):
        async for outgoing in stream:
            if outgoing.WhichOneof("body") == "command" and outgoing.command.payload.Is(cls.DESCRIPTOR):
                result = cls(); outgoing.command.payload.Unpack(result)
                await stream.aclose()
                return result


def engine(session):
    registry = SkillRegistry.discover()
    policy = SocialMatchPolicy()
    registry.availability_filter = policy.skill_available
    return SkillScheduler(session, registry)


async def test_role_phase_and_life_filter_catalog_and_reject_direct_calls(session):
    await observe(session)
    scheduler = engine(session)
    ids = lambda: {item["id"] for item in scheduler.registry.catalog(session)}
    assert "agents-arena.perform-task" in ids()
    assert "agents-arena.kill-visible-actor" not in ids()
    assert "agents-arena.select-role" not in ids()
    result = await scheduler.run("agents-arena.kill-visible-actor", {"actor_id": "peer"})
    assert result.reason == "skill_unavailable" and session.next_command_id == 0
    await observe(session, alignment=pb.SOCIAL_ALIGNMENT_IMPOSTOR)
    assert "agents-arena.kill-visible-actor" in ids() and "agents-arena.perform-task" not in ids()
    await observe(session, phase=pb.SOCIAL_PHASE_VOTING, phase_revision=4)
    assert "agents-arena.cast-vote" in ids() and "agents-arena.kill-visible-actor" not in ids()
    assert "agents-arena.navigate-to-room" not in ids()
    await observe(session, life=pb.SOCIAL_LIFE_DEAD)
    assert ids() == set()


async def test_current_visible_bundle_replaces_hidden_actor_and_history_does_not_refresh(session):
    await observe(session, visible=[pb.SocialVisibleActor(actor_id="peer", x=2, y=3, observed_revision=4)])
    policy, knowledge = SocialMatchPolicy(), WorkingKnowledge("match-social")
    policy.observe(session, knowledge)
    old = knowledge.context()[0]
    await observe(session, visible=[])
    policy.observe(session, knowledge)
    current = session.world.latest(pb.SocialVisibleWorldObservation, capability_id=SOCIAL_VISIBLE_WORLD)
    assert not current.actors
    assert knowledge.context()[0] == old
    assert old["content"]["historical_not_current"]
    snapshot = build_decision_context(session, Objective(text="Play autonomously"), [], working_knowledge=knowledge.context())
    assert snapshot.working_knowledge[0]["observed_at_unix_ms"] == old["observed_at_unix_ms"]


async def test_own_alignment_and_world_are_session_only_and_foreign_target_is_rejected(session):
    await observe(session, alignment=pb.SOCIAL_ALIGNMENT_IMPOSTOR)
    snapshot = build_decision_context(session, Objective(text="Play"), [])
    own = next(record for record in snapshot.observations if record.capability_id == SOCIAL_OWN_STATE)
    assert own.data_policy == "session_only" and own.value["alignment"] == "SOCIAL_ALIGNMENT_IMPOSTOR"
    assert "SOCIAL_ALIGNMENT_IMPOSTOR" not in str(session.privacy.public(snapshot))
    assert session.privacy.public({"skill_id": "agents-arena.kill-visible-actor"})["skill_id"] == "[private_activity]"
    await observe(session, recipient="foreign", alignment=pb.SOCIAL_ALIGNMENT_CREW)
    assert session.world.latest(pb.SocialOwnStateObservation, capability_id=SOCIAL_OWN_STATE).actor_id == "actor-1"


async def test_kill_requires_current_visibility_and_authoritative_correlated_outcome(session):
    await observe(session, alignment=pb.SOCIAL_ALIGNMENT_IMPOSTOR)
    scheduler = engine(session)
    result = await scheduler.run("agents-arena.kill-visible-actor", {"actor_id": "hidden-peer"})
    assert result.reason == "target_unavailable" and session.next_command_id == 0
    await observe(session, alignment=pb.SOCIAL_ALIGNMENT_IMPOSTOR,
                  visible=[pb.SocialVisibleActor(actor_id="peer", x=0, y=-1, observed_revision=3)])
    pending = asyncio.create_task(scheduler.run("agents-arena.kill-visible-actor", {"actor_id": "peer"}))
    outgoing = await command(session, pb.SocialKillCommand)
    assert outgoing.context.match_id == "match-social" and outgoing.context.phase_revision == 3
    assert NOW() < outgoing.context.expires_at_unix_ms <= NOW() + 2000
    await event(session, SOCIAL_ACTION_RESULT, pb.SocialActionResultEvent(match_id="match-social",
        correlation_id="unrelated", action=pb.SOCIAL_ACTION_KILL, accepted=True, completed=True))
    await asyncio.sleep(.01); assert not pending.done()
    await event(session, SOCIAL_ACTION_RESULT, pb.SocialActionResultEvent(match_id="match-social",
        correlation_id=outgoing.context.correlation_id, action=pb.SOCIAL_ACTION_KILL, accepted=True, completed=True))
    assert (await asyncio.wait_for(pending, 1)).status == "succeeded"


async def test_work_acceptance_is_not_completion_and_cancel_uses_own_original_correlation(session, monkeypatch):
    monkeypatch.setattr(SocialActionSkill, "approach", AsyncMock())
    task = pb.SocialTask(task_id="task-1", room_id="main-hall", work_duration_ms=2000)
    await observe(session, tasks=[task])
    scheduler = engine(session)
    pending = asyncio.create_task(scheduler.run("agents-arena.perform-task", {"task_id": "task-1"}))
    request = await command(session, pb.SocialWorkTaskCommand)
    await event(session, SOCIAL_ACTION_RESULT, pb.SocialActionResultEvent(match_id="match-social",
        correlation_id=request.context.correlation_id, action=pb.SOCIAL_ACTION_WORK_TASK,
        accepted=True, completed=False, work_id="server-work"))
    await asyncio.sleep(.01); assert not pending.done()
    await event(session, SOCIAL_ACTION_RESULT, pb.SocialActionResultEvent(match_id="match-social",
        correlation_id=request.context.correlation_id, action=pb.SOCIAL_ACTION_WORK_TASK,
        accepted=True, completed=True, work_id="server-work"))
    assert (await pending).status == "succeeded"
    next_pending = asyncio.create_task(scheduler.run("agents-arena.perform-task", {"task_id": "task-1"}))
    second = await command(session, pb.SocialWorkTaskCommand)
    await scheduler.cancel()
    assert (await next_pending).status == "cancelled"
    cancel = await command(session, pb.SocialCancelWorkCommand)
    assert cancel.work_id == second.context.correlation_id


async def test_received_claims_remain_private_unverified_across_episodes(session):
    await observe(session)
    claim = MessageReceivedEvent(message_id="claim-1", sender_actor_id="peer", recipient_actor_id="actor-1",
        channel_id="proximity", text='{"claim":"I am the Sheriff"}', server_unix_ms=NOW(),
        expires_at_unix_ms=NOW() + 60000, match_id="match-social", server_sequence=1)
    await event(session, MESSAGE_RECEIVED, claim, "actor.standard.communication")
    knowledge, policy = WorkingKnowledge("match-social"), SocialMatchPolicy()
    policy.observe(session, knowledge)
    record = knowledge.context()[0]
    assert record["kind"] == "player_claim" and record["source_actor_id"] == "peer"
    assert record["content"]["unverified_player_claim"]
    assert record["content"]["text"] not in str(session.privacy.public(record))
    assert not session.event_router.replan.is_set()


async def test_new_body_changes_episode_key_but_ordinary_actor_motion_does_not(session):
    await observe(session)
    policy = SocialMatchPolicy(); before = policy.evaluate(session)
    await observe(session, visible=[pb.SocialVisibleActor(actor_id="peer", x=4)])
    assert policy.evaluate(session).episode_key == before.episode_key
    await observe(session, bodies=[pb.SocialVisibleBody(body_id="body-1", victim_actor_id="peer", x=4)])
    discovered = policy.evaluate(session)
    assert discovered.episode_key != before.episode_key
    policy.observe(session, WorkingKnowledge("match-social"))
    await observe(session)
    assert policy.evaluate(session).episode_key == discovered.episode_key


async def test_own_death_and_game_over_event_invalidate_execution_before_next_frame(session):
    from actor_agents_arena import SOCIAL_MATCH_CHANGED
    await observe(session)
    policy = SocialMatchPolicy()
    assert policy.evaluate(session).state == "active"
    await event(session, SOCIAL_OWN_STATE_CHANGED, pb.SocialOwnStateChangedEvent(state=pb.SocialOwnStateObservation(
        match_id="match-social", actor_id="actor-1", life=pb.SOCIAL_LIFE_DEAD, assigned=True, server_revision=500)))
    assert policy.evaluate(session).state == "suspended"
    assert session.world.latest(pb.SocialOwnStateObservation, capability_id=SOCIAL_OWN_STATE).life == pb.SOCIAL_LIFE_ALIVE
    await event(session, SOCIAL_MATCH_CHANGED, pb.SocialMatchChangedEvent(state=pb.SocialMatchStateObservation(
        match_id="match-social", phase=pb.SOCIAL_PHASE_GAME_OVER, phase_revision=5,
        winning_side=pb.SOCIAL_ALIGNMENT_CREW, win_reason="crew_tasks")))
    directive = policy.evaluate(session)
    assert directive.state == "terminal" and directive.outcome["personal_life"] == "SOCIAL_LIFE_DEAD"


async def test_phase_transition_event_stops_old_catalog_until_coherent_frame(session):
    from actor_agents_arena import SOCIAL_MATCH_CHANGED
    await observe(session)
    policy = SocialMatchPolicy()
    await event(session, SOCIAL_MATCH_CHANGED, pb.SocialMatchChangedEvent(state=pb.SocialMatchStateObservation(
        match_id="match-social", phase=pb.SOCIAL_PHASE_DISCUSSION, phase_revision=4)))
    assert policy.evaluate(session).state == "suspended"
    await observe(session, phase=pb.SOCIAL_PHASE_DISCUSSION, phase_revision=4)
    assert policy.evaluate(session).state == "active"


async def test_postgame_summary_cannot_arrive_before_terminal_but_can_follow_event_before_frame(session):
    from actor_agents_arena import SOCIAL_MATCH_CHANGED, SOCIAL_MATCH_ENDED
    await observe(session)
    summary = pb.SocialMatchEndedEvent(summary=pb.SocialMatchSummary(match_id="match-social",
        winning_side=pb.SOCIAL_ALIGNMENT_CREW, win_reason="crew_tasks",
        players=[pb.SocialFinalPlayer(actor_id="peer", alignment=pb.SOCIAL_ALIGNMENT_IMPOSTOR)]))
    await event(session, SOCIAL_MATCH_ENDED, summary)
    assert not any(entry.capability_id == SOCIAL_MATCH_ENDED for entry in session.world.events)
    await event(session, SOCIAL_MATCH_CHANGED, pb.SocialMatchChangedEvent(state=pb.SocialMatchStateObservation(
        match_id="match-social", phase=pb.SOCIAL_PHASE_GAME_OVER, phase_revision=4,
        winning_side=pb.SOCIAL_ALIGNMENT_CREW, win_reason="crew_tasks")))
    await event(session, SOCIAL_MATCH_ENDED, summary)
    directive = SocialMatchPolicy().evaluate(session)
    assert directive.terminal_complete and directive.outcome["authoritative_summary"]["players"][0]["actor_id"] == "peer"


async def test_only_validated_postgame_summary_becomes_durable_public_evidence(session):
    from actor_agents_arena import SOCIAL_MATCH_CHANGED, SOCIAL_MATCH_ENDED
    from actor_runtime.memory import MemoryStore
    await observe(session, alignment=pb.SOCIAL_ALIGNMENT_IMPOSTOR)
    memory = MemoryStore(game_id=session.manifest.game_id, session_id=session.session_id, actor_id="actor-1")
    memory.bind_scope("match-social")
    session.event_router.configure(memory=memory)
    await session.event_router.start(Objective(text="Play autonomously"))
    summary = pb.SocialMatchEndedEvent(summary=pb.SocialMatchSummary(match_id="match-social",
        winning_side=pb.SOCIAL_ALIGNMENT_CREW, win_reason="crew_tasks"))
    try:
        await event(session, SOCIAL_MATCH_ENDED, summary)
        assert await memory.retrieve() == []
        await event(session, SOCIAL_MATCH_CHANGED, pb.SocialMatchChangedEvent(state=pb.SocialMatchStateObservation(
            match_id="match-social", phase=pb.SOCIAL_PHASE_GAME_OVER, phase_revision=4,
            winning_side=pb.SOCIAL_ALIGNMENT_CREW, win_reason="crew_tasks")))
        await event(session, SOCIAL_MATCH_ENDED, summary)
        await session.event_router.close()
        records = await memory.retrieve()
        assert len(records) == 1 and records[0].capability_id == SOCIAL_MATCH_ENDED
        assert records[0].evidence_kind == "observed_event"
        assert "SOCIAL_ALIGNMENT_IMPOSTOR" not in str(records[0].content)
    finally:
        await memory.close()


async def test_scripted_roles_use_only_own_state_and_actual_claim_context(session):
    await observe(session, phase=pb.SOCIAL_PHASE_VOTING, phase_revision=4)
    knowledge = WorkingKnowledge("match-social")
    await event(session, SOCIAL_VISIBLE_ATTACK, pb.SocialVisibleAttackEvent(match_id="match-social",
        observer_actor_id="actor-1", attacker_actor_id="peer", victim_actor_id="victim", observed_at_unix_ms=NOW(), observed_revision=9))
    SocialMatchPolicy().observe(session, knowledge)
    scheduler = engine(session)
    context = build_decision_context(session, Objective(text="Play autonomously"), scheduler.registry.catalog(session),
                                     working_knowledge=knowledge.context())
    brain = SocialScriptedBrain(); await brain.start(context.objective, context.available_skills)
    choice = await brain.decide(context)
    assert choice.skill_id == "agents-arena.cast-vote" and choice.arguments == {"candidate_actor_id": "peer", "skip": False}


async def test_visible_approach_stops_inside_default_kill_range_without_impossible_vertical_overlap():
    from actor_agents_arena.social_skills import ApproachVisible, ApproachInput
    from actor_standard import LOCOMOTION
    class Physics:
        actor_id = "actor-1"
        def __init__(self):
            self.at, self.center_y, self.desired, self.sequence = 0.0, -.96, 0.0, 1
            self.speeds = []
        def now(self): return self.at
        def observation(self, cap, cls): return self.observation_entry(cap, cls).payload
        def observation_entry(self, cap, cls, **kwargs):
            if cap == TRANSFORM:
                value = Transform2DObservation(y=self.center_y - .46)
            elif cap == MOTION:
                value = Motion2DObservation(is_moving=bool(self.desired), velocity_y=self.desired * 2.6)
            elif cap == SOCIAL_MATCH:
                value = pb.SocialMatchStateObservation(match_id="match", phase=pb.SOCIAL_PHASE_EXPLORATION)
            elif cap == SOCIAL_OWN_STATE:
                value = pb.SocialOwnStateObservation(match_id="match", actor_id="actor-1",life=pb.SOCIAL_LIFE_ALIVE,can_move=True)
            elif cap == ROOM_MEMBERSHIP:
                value = RoomMembershipObservation(room_id="main-hall")
            else:
                value = pb.SocialVisibleWorldObservation(match_id="match", recipient_actor_id="actor-1",
                    actors=[pb.SocialVisibleActor(actor_id="peer", x=0, y=0)])
            return SimpleNamespace(sequence=self.sequence, payload=value,received_at=self.at)
        def command(self, cap, value):
            assert cap == LOCOMOTION
            self.desired = value.desired_move_y
            self.speeds.append(self.desired)
        async def sleep(self, seconds):
            self.center_y = min(-.81, self.center_y + self.desired * 2.6 * seconds)
            self.at += seconds; self.sequence += 1
            await asyncio.sleep(0)
    physics = Physics()
    result = await ApproachVisible().execute(physics, ApproachInput(actor_id="peer"))
    assert result.completed and any(speed > 0 for speed in physics.speeds)
    assert .81 <= -physics.center_y < .9
    assert physics.desired == 0
