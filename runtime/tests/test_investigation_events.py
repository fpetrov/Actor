"""Role-private evidence stays routine; own incident loss interrupts safely."""
import asyncio

import pytest
from extensions.agents_arena.v1 import incidents_pb2 as pb

from actor_agents_arena import INCIDENT_STARTED, INCIDENT_CHANGED
from actor_runtime.brains import SequenceBrain, SkillCall, FinishObjective
from actor_runtime.objectives import Objective, ObjectiveRunner
from actor_runtime.skills import SkillRegistry
from test_investigation_skills import observe, event


async def active(runner, skill):
    async with asyncio.timeout(1):
        while runner.scheduler.report()["active_skill_id"] != skill:
            await asyncio.sleep(0)


def changed(session, status, **changes):
    fields = {"incident_id": "incident-1", "incident_status": status, "match_id": "match-1",
        "server_revision": session.last_received_sequence + 1}
    fields.update(changes)
    return pb.IncidentChangedEvent(state=pb.CoolingStateObservation(**fields))


@pytest.mark.parametrize("status", [pb.INCIDENT_STATUS_CANCELLED, pb.INCIDENT_STATUS_EXPIRED, pb.INCIDENT_STATUS_FAILED])
async def test_own_terminal_incident_preempts_generic_message_wait_without_core_branch(session, status):
    await observe(session, role=pb.CREW_ROLE_SHERIFF)
    brain = SequenceBrain([SkillCall(skill_id="actor.wait-for-message", arguments={"sender_actor_id": "actor-2",
        "task_id": "incident-1", "timeout_seconds": 240.0}), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, SkillRegistry.discover(), brain)
    running = asyncio.create_task(runner.run(Objective(text="Wait for permitted diagnostic evidence.")))
    await active(runner, "actor.wait-for-message")
    await observe(session, role=pb.CREW_ROLE_SHERIFF, status=status)
    await event(session, INCIDENT_CHANGED, changed(session, status))
    result = await asyncio.wait_for(running, 1)
    assert result.status == "failed" and result.skill_results[0]["reason"] == "preempted_by_event"
    assert runner.report()["replan_count"] == 1 and not runner.scheduler.report()["active_skill_id"]
    assert not runner.report()["cleanup_errors"]


@pytest.mark.parametrize("changes", [{"match_id": "another-match"}, {"incident_id": "another-incident"}, {"server_revision": 1}])
async def test_foreign_or_stale_incident_loss_does_not_force_review(session, changes):
    await observe(session, role=pb.CREW_ROLE_SHERIFF)
    router = session.event_router
    await router.start(Objective(text="Wait for my incident."), current_skill=lambda: {"skill_id": "actor.wait-for-message"})
    try:
        await event(session, INCIDENT_CHANGED, changed(session, pb.INCIDENT_STATUS_CANCELLED, **changes))
        await asyncio.sleep(.02)
        assert router.cognitive_revision == 0 and not router.replan.is_set() and not router.preempt.is_set()
    finally:
        await router.close()


async def test_started_event_does_not_cancel_expected_wait_for_incident(session):
    await observe(session, incident="", status=pb.INCIDENT_STATUS_NONE)
    brain = SequenceBrain([SkillCall(skill_id="agents-arena.wait-for-incident", arguments={}),
        SkillCall(skill_id="agents-arena.wait-for-incident-resolved", arguments={"incident_id": "incident-1"}),
        FinishObjective(status="success")])
    runner = ObjectiveRunner(session, SkillRegistry.discover(), brain)
    running = asyncio.create_task(runner.run(Objective(text="Await the public incident.")))
    await active(runner, "agents-arena.wait-for-incident")
    state = await observe(session, status=pb.INCIDENT_STATUS_ACTIVE)
    state.server_revision = session.last_received_sequence + 1
    await event(session, INCIDENT_STARTED, pb.IncidentStartedEvent(state=state))
    await active(runner, "agents-arena.wait-for-incident-resolved")
    await observe(session, status=pb.INCIDENT_STATUS_RESOLVED, cooling=pb.COOLING_STATUS_RESTORED, validated=True)
    result = await asyncio.wait_for(running, 1)
    assert result.status == "succeeded" and result.skill_results[0]["status"] == "succeeded"


async def test_incident_started_preserves_pending_real_maintenance_receipt(session):
    await observe(session, role=pb.CREW_ROLE_CREW, incident="", status=pb.INCIDENT_STATUS_NONE)
    router = session.event_router
    await router.start(Objective(text="Operate the public panel."), current_skill=lambda: {"skill_id": "agents-arena.maintenance-override"})
    try:
        state = await observe(session, role=pb.CREW_ROLE_CREW)
        await event(session, INCIDENT_STARTED, pb.IncidentStartedEvent(state=state))
        assert router.replan.is_set() and not router.preempt.is_set()
    finally:
        await router.close()


async def test_resolved_event_does_not_preempt_the_successful_final_wait(session):
    await observe(session)
    brain = SequenceBrain([SkillCall(skill_id="agents-arena.wait-for-incident-resolved", arguments={"incident_id": "incident-1"}),
        FinishObjective(status="success")])
    runner = ObjectiveRunner(session, SkillRegistry.discover(), brain)
    running = asyncio.create_task(runner.run(Objective(text="Await validated report and physical repair.")))
    await active(runner, "agents-arena.wait-for-incident-resolved")
    await observe(session, status=pb.INCIDENT_STATUS_RESOLVED, cooling=pb.COOLING_STATUS_RESTORED, validated=True)
    await event(session, INCIDENT_CHANGED, changed(session, pb.INCIDENT_STATUS_RESOLVED))
    result = await asyncio.wait_for(running, 1)
    assert result.status == "succeeded" and result.skill_results[0]["status"] == "succeeded"
    assert runner.report()["replan_count"] == 0
