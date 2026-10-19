"""Game-owned terminal task policy preempts generic message waits promptly."""
import asyncio

import pytest

from actor_agents_arena import (CALIBRATION_CHANGED, CalibrationTaskStateObservation,
    CalibrationTaskChangedEvent, CALIBRATION_STATUS_CANCELLED, CALIBRATION_STATUS_EXPIRED,
    CALIBRATION_STATUS_FAILED, CALIBRATION_STATUS_COMPLETED)
from actor_runtime.brains import SequenceBrain, SkillCall, FinishObjective
from actor_runtime.objectives import Objective, ObjectiveRunner
from actor_runtime.skills import SkillRegistry
from test_calibration_skills import observe, event


async def active(runner, skill_id):
    async with asyncio.timeout(1):
        while runner.scheduler.report()["active_skill_id"] != skill_id:
            await asyncio.sleep(0)


def changed(session, status, **overrides):
    fields = dict(task_id="task-1", match_id="match-1", coordinator_actor_id="actor-2",
        technician_actor_id="actor-1", status=status, server_revision=session.last_received_sequence + 1)
    fields.update(overrides)
    return CalibrationTaskChangedEvent(state=CalibrationTaskStateObservation(**fields))


@pytest.mark.parametrize("status", [CALIBRATION_STATUS_CANCELLED, CALIBRATION_STATUS_EXPIRED,
                                    CALIBRATION_STATUS_FAILED])
async def test_partner_task_terminal_event_preempts_generic_message_wait(session, status):
    await observe(session, role="technician", room="engineering")
    brain = SequenceBrain([SkillCall(skill_id="actor.wait-for-message", arguments={
        "sender_actor_id": "actor-2", "task_id": "task-1", "timeout_seconds": 240.0}),
        FinishObjective(status="success")])
    runner = ObjectiveRunner(session, SkillRegistry.discover(), brain)
    running = asyncio.create_task(runner.run(Objective(text="Wait for the coordinator's authorization.")))
    await active(runner, "actor.wait-for-message")
    await observe(session, role="technician", room="engineering", status=status)
    await event(session, CALIBRATION_CHANGED, changed(session, status))
    result = await asyncio.wait_for(running, 1)
    assert result.status == "failed" and result.reason == "brain_reported_failure"
    assert result.skill_results[0]["status"] == "cancelled"
    assert result.skill_results[0]["reason"] == "preempted_by_event"
    assert brain.turn_count == 2 and runner.report()["replan_count"] == 1
    assert not runner.scheduler.report()["active_skill_id"]
    assert not runner.report()["cleanup_errors"]


@pytest.mark.parametrize("overrides", [
    {"task_id": "unrelated-task"}, {"match_id": "old-match"},
    {"coordinator_actor_id": "another", "technician_actor_id": "unrelated"},
    {"server_revision": 1},
])
async def test_unrelated_terminal_task_never_forces_message_wait_replan(session, overrides):
    await observe(session, role="technician", room="engineering")
    brain = SequenceBrain([SkillCall(skill_id="actor.wait-for-message", arguments={
        "sender_actor_id": "actor-2", "task_id": "task-1", "timeout_seconds": 240.0})])
    runner = ObjectiveRunner(session, SkillRegistry.discover(), brain)
    running = asyncio.create_task(runner.run(Objective(text="Wait for this task only.")))
    await active(runner, "actor.wait-for-message")
    await event(session, CALIBRATION_CHANGED, changed(session, CALIBRATION_STATUS_CANCELLED, **overrides))
    await asyncio.sleep(0.02)
    assert not running.done() and brain.turn_count == 1
    assert runner.report()["replan_count"] == 0
    # Arrange terminal observation so this test need not synthesize a Cancel receipt.
    await observe(session, role="technician", status=CALIBRATION_STATUS_CANCELLED)
    await runner.cancel()
    assert (await running).status == "cancelled"


async def test_completed_event_does_not_preempt_successful_calibration_wait(session):
    await observe(session)
    brain = SequenceBrain([SkillCall(skill_id="agents-arena.wait-for-calibration",
        arguments={"task_id": "task-1"}), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, SkillRegistry.discover(), brain)
    running = asyncio.create_task(runner.run(Objective(text="Await authoritative completion.")))
    await active(runner, "agents-arena.wait-for-calibration")
    await observe(session, status=CALIBRATION_STATUS_COMPLETED)
    await event(session, CALIBRATION_CHANGED, changed(session, CALIBRATION_STATUS_COMPLETED))
    result = await asyncio.wait_for(running, 1)
    assert result.status == "succeeded"
    assert result.skill_results[0]["status"] == "succeeded"
    assert runner.report()["replan_count"] == 0
