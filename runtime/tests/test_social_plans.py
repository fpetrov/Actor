import asyncio

from actor_runtime.brains import SkillCall, SkillPlan, PlanReaction
from actor_runtime.plans import ExecutablePlan
from actor_agents_arena import SOCIAL_VISIBLE_WORLD, SOCIAL_ACTION_RESULT
from actor_agents_arena.social_agent import SocialMatchPolicy
from extensions.agents_arena.v1 import social_pb2 as pb
from actor_standard import Locomotion2DCommand
from test_social_runtime import observe, engine, command, event


def plan():
    return ExecutablePlan(SkillPlan(steps=[SkillCall(skill_id="agents-arena.perform-task", arguments={"task_id": "task-1"})],
        conditions=[PlanReaction(capability_id=SOCIAL_VISIBLE_WORLD, field="bodies",
            response=SkillCall(skill_id="agents-arena.report-visible-body", arguments={}))]))


async def test_body_handled_by_an_accepted_plan_does_not_force_another_episode(session):
    await observe(session)
    policy = SocialMatchPolicy()
    before = policy.evaluate(session)
    session.executable_plan = plan()
    await observe(session, bodies=[pb.SocialVisibleBody(body_id="body-1", x=0, y=-.94)])
    assert policy.evaluate(session).episode_key == before.episode_key
    # A phase transition still supersedes the plan immediately.
    await observe(session, phase=pb.SOCIAL_PHASE_DISCUSSION, phase_revision=4)
    assert policy.evaluate(session).episode_key != before.episode_key
    session.executable_plan = None


async def test_a_plan_without_a_body_condition_keeps_ordinary_discovery_behavior(session):
    await observe(session)
    policy = SocialMatchPolicy()
    before = policy.evaluate(session)
    session.executable_plan = ExecutablePlan(SkillPlan(steps=[SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 1.})]))
    await observe(session, bodies=[pb.SocialVisibleBody(body_id="body-1", x=0, y=-.94)])
    assert policy.evaluate(session).episode_key != before.episode_key
    session.executable_plan = None


async def test_report_visible_body_uses_real_visible_target_and_correlated_server_completion(session):
    await observe(session, bodies=[pb.SocialVisibleBody(body_id="visible-body", x=0, y=-.94)])
    scheduler = engine(session)
    pending = asyncio.create_task(scheduler.run("agents-arena.report-visible-body", {}))
    request = await command(session, pb.SocialReportBodyCommand)
    assert request.body_id == "visible-body"
    await event(session, SOCIAL_ACTION_RESULT, pb.SocialActionResultEvent(match_id="match-social",
        correlation_id=request.context.correlation_id, action=pb.SOCIAL_ACTION_REPORT_BODY, accepted=True, completed=True))
    assert (await asyncio.wait_for(pending, 1)).status == "succeeded"


async def test_report_visible_body_without_a_current_body_only_stops_movement(session):
    await observe(session)
    result = await engine(session).run("agents-arena.report-visible-body", {})
    assert result.status == "failed" and result.reason == "target_unavailable"
    assert session.next_command_id == 1
    stop = await command(session, Locomotion2DCommand)
    assert stop.desired_move_x == stop.desired_move_y == 0


async def test_future_plan_is_private_in_public_diagnostics(session):
    await observe(session, alignment=pb.SOCIAL_ALIGNMENT_IMPOSTOR)
    value = {"current_plan": plan().snapshot()}
    assert session.privacy.public(value)["current_plan"] == {"redacted": True}
