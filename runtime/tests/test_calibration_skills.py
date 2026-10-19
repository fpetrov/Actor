"""Typed terminal requests require private authoritative assignment and outcomes."""
import asyncio
import time

import pytest
from google.protobuf.any_pb2 import Any

from actor.core.v1 import actor_pb2 as core
from actor_agents_arena import (
    CALIBRATION_STATE, CALIBRATION_RESULT, DIAGNOSTIC_RESULT, ROOM_MEMBERSHIP,
    CalibrationTaskStateObservation, CalibrationResultEvent, DiagnosticResultEvent,
    CALIBRATION_STATUS_AWAITING_DIAGNOSTIC, CALIBRATION_STATUS_COMPLETED,
    CALIBRATION_STATUS_CANCELLED, RoomMembershipObservation,
    StartCalibrationCommand,
    CALIBRATION_ACTION_START,
)
from actor_agents_arena.calibration import skill_package, InspectDiagnostic, StartCalibration
from actor_runtime.skills import SkillRegistry, SkillScheduler
from actor_standard import TRANSFORM, MOTION, Transform2DObservation, Motion2DObservation
from actor_standard_communication import PEERS, CommunicationPeersObservation, CommunicationPeer
from conftest import envelope, extension_version, message


def scheduler(session):
    registry = SkillRegistry()
    for definition in skill_package():
        registry.register(definition)
    return SkillScheduler(session, registry)


async def observe(session, *, role="coordinator", room="main-hall", status=CALIBRATION_STATUS_AWAITING_DIAGNOSTIC,
                  task_id="task-1"):
    sequence = session.last_received_sequence + 1
    state = CalibrationTaskStateObservation(task_id=task_id, status=status,
        coordinator_actor_id="actor-1" if role == "coordinator" else "actor-2",
        technician_actor_id="actor-2" if role == "coordinator" else "actor-1",
        expires_at_unix_ms=int(time.time() * 1000) + 300000, attempts_remaining=3,
        match_id="match-1", server_revision=sequence)
    await session.accept(message(sequence, observation=core.ObservationFrame(revision=sequence,
        observations=[envelope(CALIBRATION_STATE, "agents-arena", state),
            envelope(ROOM_MEMBERSHIP, "agents-arena", RoomMembershipObservation(room_id=room)),
            envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=0, y=-1.4)),
            envelope(MOTION, "actor.standard", Motion2DObservation()),
            envelope(PEERS, "actor.standard.communication", CommunicationPeersObservation(
                match_id="match-1", peers=[CommunicationPeer(actor_id="actor-2", display_name="Technician")],
                allowed_channels=["direct", "station"]))])))


async def event(session, capability, value):
    sequence = session.last_received_sequence + 1
    packed = Any(); packed.Pack(value)
    await session.accept(message(sequence, event=core.GameEventEnvelope(entity_id="actor-1",
        capability_id=capability, extension_id="agents-arena",
        extension_version=extension_version("agents-arena"), revision=sequence, payload=packed)))


async def command(session, message_type):
    async for outgoing in session.outgoing():
        if outgoing.WhichOneof("body") == "command" and outgoing.command.payload.Is(message_type.DESCRIPTOR):
            value = message_type(); outgoing.command.payload.Unpack(value)
            return value
    pytest.fail("No typed command was issued")


@pytest.fixture(autouse=True)
def start_already_at_terminal(monkeypatch):
    # Terminal approach uses NavigateToRoom's separately tested control loop;
    # these fixtures isolate authoritative request/result handling.
    async def approach(self, context):
        pass
    monkeypatch.setattr(StartCalibration, "approach", approach)


async def test_start_requires_server_outcome_and_matching_assignment(session):
    await observe(session, task_id="")
    running = asyncio.create_task(scheduler(session).run("agents-arena.start-calibration", {}))
    request = await asyncio.wait_for(command(session, StartCalibrationCommand), 1)
    assert request.technician_actor_id == "actor-2" and request.match_id == "match-1"
    await asyncio.sleep(0.02)
    assert not running.done(), "Local command acceptance is not server acceptance"
    # An unrelated receipt, including an old match, cannot finish this Skill.
    await event(session, CALIBRATION_RESULT, CalibrationResultEvent(
        correlation_id=request.correlation_id, action=CALIBRATION_ACTION_START,
        accepted=True, task_id="task-1", match_id="old-match"))
    await asyncio.sleep(0.02)
    assert not running.done()
    await event(session, CALIBRATION_RESULT, CalibrationResultEvent(
        correlation_id=request.correlation_id, action=CALIBRATION_ACTION_START,
        accepted=True, task_id="task-1", match_id="match-1"))
    await observe(session)
    result = await asyncio.wait_for(running, 1)
    assert result.status == "succeeded" and result.output["task_id"] == "task-1"


async def test_server_rejection_is_a_structured_skill_failure(session):
    await observe(session, task_id="")
    running = asyncio.create_task(scheduler(session).run("agents-arena.start-calibration", {}))
    request = await asyncio.wait_for(command(session, StartCalibrationCommand), 1)
    await event(session, CALIBRATION_RESULT, CalibrationResultEvent(
        correlation_id=request.correlation_id, action=CALIBRATION_ACTION_START,
        accepted=False, reason_code="partner_unavailable", match_id="match-1"))
    result = await asyncio.wait_for(running, 1)
    assert result.status == "failed" and result.reason == "partner_unavailable"


@pytest.mark.parametrize("role,room,reason", [
    ("technician", "main-hall", "role_not_authorized"),
    ("coordinator", "engineering", "wrong_room"),
])
async def test_inspection_checks_assignment_and_room_before_any_command(session, role, room, reason):
    await observe(session, role=role, room=room)
    result = await scheduler(session).run("agents-arena.inspect-diagnostic", {"task_id": "task-1"})
    assert result.status == "failed" and result.reason == reason
    assert session.next_command_id == 0


async def test_early_diagnostic_result_stays_private_after_success(session, monkeypatch):
    from actor_agents_arena import InspectDiagnosticCommand
    await observe(session)

    async def already_at_terminal(self, context):
        pass

    monkeypatch.setattr(InspectDiagnostic, "approach", already_at_terminal)
    owner = scheduler(session)
    running = asyncio.create_task(owner.run("agents-arena.inspect-diagnostic", {"task_id": "task-1"}))
    request = await asyncio.wait_for(command(session, InspectDiagnosticCommand), 1)
    secret = "a" * 32
    await event(session, DIAGNOSTIC_RESULT, DiagnosticResultEvent(correlation_id=request.correlation_id,
        task_id="task-1", accepted=True, authorization_code=secret, sensitive=True,
        expires_at_unix_ms=int(time.time() * 1000) + 60000, match_id="match-1"))
    result = await asyncio.wait_for(running, 1)
    assert result.output["authorization_code"] == secret  # authorized working context
    assert result.data_policy == "sensitive"
    assert secret not in str(owner.report())


async def test_wait_requires_completed_and_disconnect_cancels_without_hanging(session):
    await observe(session)
    owner = scheduler(session)
    running = asyncio.create_task(owner.run("agents-arena.wait-for-calibration", {"task_id": "task-1"}))
    await asyncio.sleep(0.02)
    assert not running.done()
    await observe(session, status=CALIBRATION_STATUS_COMPLETED)
    result = await asyncio.wait_for(running, 1)
    assert result.status == "succeeded" and result.output["status"] == "completed"
    await observe(session)
    running = asyncio.create_task(owner.run("agents-arena.wait-for-calibration", {"task_id": "task-1"}))
    await asyncio.sleep(0.02)
    session.close()
    result = await asyncio.wait_for(running, 1)
    assert result.status == "failed" and result.reason == "session_disconnected"
    assert owner.report()["active_skill_id"] == ""


async def test_cancelled_task_does_not_become_success(session):
    await observe(session, status=CALIBRATION_STATUS_CANCELLED)
    result = await scheduler(session).run("agents-arena.wait-for-calibration", {"task_id": "task-1"})
    assert result.status == "failed" and result.reason == "task_cancelled"


async def test_objective_cancel_releases_task_even_when_bridge_stays_connected(session):
    from actor_agents_arena import CancelCalibrationCommand, CALIBRATION_ACTION_CANCEL
    from actor_runtime.brains import SequenceBrain, SkillCall
    from actor_runtime.objectives import Objective, ObjectiveRunner

    await observe(session)
    owner = scheduler(session)
    runner = ObjectiveRunner(session, owner.registry, SequenceBrain([
        SkillCall(skill_id="agents-arena.wait-for-calibration", arguments={"task_id": "task-1"}),
    ]), scheduler=owner)
    running = asyncio.create_task(runner.run(Objective(text="Cooperate until calibration completes")))
    while owner.report()["active_skill_id"] != "agents-arena.wait-for-calibration":
        await asyncio.sleep(0)
    cancellation = asyncio.create_task(runner.cancel())
    request = await asyncio.wait_for(command(session, CancelCalibrationCommand), 1)
    assert request.task_id == "task-1" and request.match_id == "match-1"
    await observe(session, status=CALIBRATION_STATUS_CANCELLED)
    await event(session, CALIBRATION_RESULT, CalibrationResultEvent(correlation_id=request.correlation_id,
        task_id="task-1", accepted=True, action=CALIBRATION_ACTION_CANCEL,
        status=CALIBRATION_STATUS_CANCELLED, match_id="match-1"))
    result = await asyncio.wait_for(running, 2)
    await asyncio.wait_for(cancellation, 1)
    assert result.status == "cancelled"
    assert not session.closed.is_set()
    assert owner.report()["active_skill_id"] == ""
    assert not runner.report()["cleanup_errors"]
    assert session.world.latest(CalibrationTaskStateObservation, capability_id=CALIBRATION_STATE).status == CALIBRATION_STATUS_CANCELLED
