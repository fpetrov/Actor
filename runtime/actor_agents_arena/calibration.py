"""Reference-game terminal procedures; authoritative outcomes stay in the game.

Each request carries an idempotency correlation and the current match instance.
Local bridge acceptance never substitutes for the server's typed result event.
"""
from __future__ import annotations

import uuid
import time

from pydantic import Field

from actor_runtime.skills import (ActorSkill, CapabilityRequirement, SkillContext,
                                  SkillDefinition, SkillError, SkillModel)
from actor_standard import (TRANSFORM, MOTION, LOCOMOTION, Transform2DObservation,
                            Motion2DObservation, Locomotion2DCommand)
from actor_standard_communication import PEERS, CommunicationPeersObservation
from . import (ROOM_MEMBERSHIP, ROOM_CHANGED, RoomMembershipObservation, RoomChangedEvent,
               CALIBRATION_STATE, CALIBRATION_RESULT, DIAGNOSTIC_RESULT,
               START_CALIBRATION, INSPECT_DIAGNOSTIC, SUBMIT_CALIBRATION, CANCEL_CALIBRATION,
               CalibrationTaskStateObservation, CalibrationTaskChangedEvent, CalibrationResultEvent, DiagnosticResultEvent,
               StartCalibrationCommand, InspectDiagnosticCommand, SubmitCalibrationCommand, CancelCalibrationCommand,
               CALIBRATION_ACTION_START, CALIBRATION_ACTION_SUBMIT, CALIBRATION_ACTION_CANCEL)
from .skills import NavigateToRoom, WAYPOINTS, Waypoint, control_state


# Keep the coordinator clear of the technician's shared x=0 doorway route.
# Server interaction uses the capsule centre, not a client-written position.
DIAGNOSTIC_APPROACH = Waypoint(0.9, -2.4)


def status_name(value: int) -> str:
    # Resolve the installed enum descriptor rather than duplicating wire numbers.
    enum = CalibrationTaskStateObservation.DESCRIPTOR.fields_by_name["status"].enum_type
    return enum.values_by_number[value].name.removeprefix("CALIBRATION_STATUS_").lower()


class StartCalibrationInput(SkillModel):
    technician_actor_id: str = Field(default="", max_length=128,
        description="A different online actor ID from communication peers. If empty, wait for exactly one other peer; ambiguity is a failure.")
    timeout_seconds: float = Field(default=60.0, gt=0, le=120)


class TaskInput(SkillModel):
    task_id: str = Field(min_length=1, max_length=64)


class WaitAssignmentInput(SkillModel):
    timeout_seconds: float = Field(default=180.0, gt=0, le=300)


class WaitCalibrationInput(TaskInput):
    timeout_seconds: float = Field(default=240.0, gt=0, le=300)


class SubmitCalibrationInput(TaskInput):
    authorization_code: str = Field(min_length=1, max_length=64,
        description="Exact short-lived authorization received privately from this task's coordinator. Never guess or obtain it from public state.")


class CalibrationTaskOutput(SkillModel):
    task_id: str
    status: str
    coordinator_actor_id: str
    technician_actor_id: str
    expires_at_unix_ms: int
    attempts_remaining: int


class DiagnosticOutput(SkillModel):
    task_id: str
    authorization_code: str
    expires_at_unix_ms: int
    technician_actor_id: str
    sensitive: bool = True


def task_output(state):
    return CalibrationTaskOutput(task_id=state.task_id, status=status_name(state.status),
        coordinator_actor_id=state.coordinator_actor_id, technician_actor_id=state.technician_actor_id,
        expires_at_unix_ms=state.expires_at_unix_ms, attempts_remaining=state.attempts_remaining)


def task_state(context: SkillContext, task_id: str):
    state = context.observation(CALIBRATION_STATE, CalibrationTaskStateObservation)
    if state.task_id != task_id:
        raise SkillError("task_not_assigned", "Requested task is not this actor's authoritative assignment")
    return state


def check_result(event):
    if not event.accepted:
        raise SkillError(event.reason_code or "server_rejected", "Authoritative server rejected the interaction",
                         {"task_id": event.task_id})


class StartCalibration(ActorSkill):
    def preflight(self, context, arguments):
        if control_state(context)[2].payload.room_id != "main-hall":
            raise SkillError("wrong_room", "Navigate to Main Hall before initializing calibration")

    async def approach(self, context):
        navigation = NavigateToRoom()
        await navigation.waypoint(context, DIAGNOSTIC_APPROACH)
        await navigation.confirm_arrival(context, "main-hall")

    async def execute(self, context, arguments):
        partner = arguments.technician_actor_id

        def partner_ready(current):
            peers = current.observation(PEERS, CommunicationPeersObservation)
            candidates = [peer.actor_id for peer in peers.peers if peer.actor_id != current.actor_id]
            if partner:
                return partner in candidates
            if len(candidates) > 1:
                raise SkillError("partner_ambiguous", "Select one technician_actor_id from the permitted peer roster")
            return len(candidates) == 1

        await context.wait_until(partner_ready, timeout_seconds=arguments.timeout_seconds)
        peers = context.observation(PEERS, CommunicationPeersObservation)
        if not partner:
            partner = next(peer.actor_id for peer in peers.peers if peer.actor_id != context.actor_id)
        await self.approach(context)
        correlation = uuid.uuid4().hex
        context.command(START_CALIBRATION, StartCalibrationCommand(
            technician_actor_id=partner, correlation_id=correlation, match_id=peers.match_id))
        entry = await context.wait_event(CALIBRATION_RESULT, CalibrationResultEvent,
            predicate=lambda value: value.correlation_id == correlation and value.match_id == peers.match_id
                and value.action == CALIBRATION_ACTION_START,
            timeout_seconds=10)
        event = entry.payload
        check_result(event)
        await context.wait_until(lambda current: current.observation(
            CALIBRATION_STATE, CalibrationTaskStateObservation).task_id == event.task_id, timeout_seconds=5)
        return task_output(task_state(context, event.task_id))

    async def cleanup(self, context):
        await NavigateToRoom().cleanup(context)


class WaitForAssignment(ActorSkill):
    async def execute(self, context, arguments):
        await context.wait_until(lambda current: bool(current.observation(
            CALIBRATION_STATE, CalibrationTaskStateObservation).task_id), timeout_seconds=arguments.timeout_seconds)
        state = context.observation(CALIBRATION_STATE, CalibrationTaskStateObservation)
        if state.technician_actor_id != context.actor_id:
            raise SkillError("role_not_authorized", "This actor is not the assigned technician")
        if status_name(state.status) in ("failed", "expired", "cancelled"):
            raise SkillError("task_" + status_name(state.status), "Assignment has already ended")
        return task_output(state)


class TerminalSkill(ActorSkill):
    room = ""
    role_field = ""

    def preflight(self, context, arguments):
        state = task_state(context, arguments.task_id)
        if getattr(state, self.role_field) != context.actor_id:
            raise SkillError("role_not_authorized", "This actor cannot use this task's terminal")
        if control_state(context)[2].payload.room_id != self.room:
            raise SkillError("wrong_room", "Navigate to the terminal's room before interacting")

    async def approach(self, context):
        # Reuse the production movement controller and stop confirmation. This
        # only moves to the existing room anchor; it never alters server state.
        navigation = NavigateToRoom()
        target = DIAGNOSTIC_APPROACH if self.room == "main-hall" else WAYPOINTS[self.room + ".anchor"]
        await navigation.waypoint(context, target)
        await navigation.confirm_arrival(context, self.room)

    async def cleanup(self, context):
        await NavigateToRoom().cleanup(context)


class InspectDiagnostic(TerminalSkill):
    room, role_field = "main-hall", "coordinator_actor_id"

    async def execute(self, context, arguments):
        await self.approach(context)
        state = task_state(context, arguments.task_id)
        correlation = uuid.uuid4().hex
        context.command(INSPECT_DIAGNOSTIC, InspectDiagnosticCommand(
            task_id=state.task_id, correlation_id=correlation, match_id=state.match_id))
        entry = await context.wait_event(DIAGNOSTIC_RESULT, DiagnosticResultEvent,
            predicate=lambda value: value.correlation_id == correlation and value.match_id == state.match_id
                and (not value.accepted or value.task_id == state.task_id),
            timeout_seconds=10)
        event = entry.payload
        check_result(event)
        if (not event.authorization_code or not event.sensitive
                or event.expires_at_unix_ms <= int(time.time() * 1000)):
            raise SkillError("diagnostic_result_invalid", "Server did not supply a private authorization result")
        context.register_known_secret(event.authorization_code, expires_at_unix_ms=event.expires_at_unix_ms)
        return DiagnosticOutput(task_id=event.task_id, authorization_code=event.authorization_code,
            expires_at_unix_ms=event.expires_at_unix_ms, technician_actor_id=state.technician_actor_id)


class SubmitCalibration(TerminalSkill):
    room, role_field = "engineering", "technician_actor_id"

    async def execute(self, context, arguments):
        state = task_state(context, arguments.task_id)
        context.register_known_secret(arguments.authorization_code, expires_at_unix_ms=state.expires_at_unix_ms)
        await self.approach(context)
        correlation = uuid.uuid4().hex
        context.command(SUBMIT_CALIBRATION, SubmitCalibrationCommand(task_id=state.task_id,
            authorization_code=arguments.authorization_code, correlation_id=correlation, match_id=state.match_id))
        entry = await context.wait_event(CALIBRATION_RESULT, CalibrationResultEvent,
            predicate=lambda value: value.correlation_id == correlation and value.match_id == state.match_id
                and value.action == CALIBRATION_ACTION_SUBMIT
                and (not value.accepted or value.task_id == state.task_id),
            timeout_seconds=10)
        event = entry.payload
        check_result(event)
        await context.wait_until(lambda current: status_name(task_state(current, state.task_id).status)
                                 == "completed", timeout_seconds=5)
        return task_output(task_state(context, state.task_id))


class WaitForCalibration(ActorSkill):
    async def execute(self, context, arguments):
        def finished(current):
            state = task_state(current, arguments.task_id)
            status = status_name(state.status)
            if status in ("failed", "expired", "cancelled"):
                raise SkillError("task_" + status, "Authoritative cooperative task ended without completion",
                                 {"task_id": state.task_id, "reason_code": state.reason_code})
            return status == "completed"
        await context.wait_until(finished, timeout_seconds=arguments.timeout_seconds)
        return task_output(task_state(context, arguments.task_id))


class CancelCalibration(ActorSkill):
    async def execute(self, context, arguments):
        state = task_state(context, arguments.task_id)
        correlation = uuid.uuid4().hex
        context.command(CANCEL_CALIBRATION, CancelCalibrationCommand(task_id=state.task_id,
            correlation_id=correlation, match_id=state.match_id))
        entry = await context.wait_event(CALIBRATION_RESULT, CalibrationResultEvent,
            predicate=lambda value: value.correlation_id == correlation and value.match_id == state.match_id
                and value.action == CALIBRATION_ACTION_CANCEL,
            timeout_seconds=1.5)
        check_result(entry.payload)
        await context.wait_until(lambda current: status_name(task_state(current, state.task_id).status)
                                 == "cancelled", timeout_seconds=1)
        return task_output(task_state(context, state.task_id))

    async def cleanup(self, context):
        pass


STATE_REQUIREMENT = CapabilityRequirement(CALIBRATION_STATE, CalibrationTaskStateObservation, "observation", 3.0)
MOVEMENT_REQUIREMENTS = (
    CapabilityRequirement(TRANSFORM, Transform2DObservation, "observation", 1.0),
    CapabilityRequirement(MOTION, Motion2DObservation, "observation", 1.0),
    CapabilityRequirement(LOCOMOTION, Locomotion2DCommand, "command"),
    CapabilityRequirement(ROOM_MEMBERSHIP, RoomMembershipObservation, "observation", 1.0),
    CapabilityRequirement(ROOM_CHANGED, RoomChangedEvent, "event"),
)


def skill_package():
    return (
        SkillDefinition("agents-arena.start-calibration", "1.0.0",
            "Initialize a server-authoritative cooperative task with a different online technician. "
            "From Main Hall, approach the diagnostic terminal and choose their actor ID from communication peers. "
            "Await server acceptance and private assignment.",
            StartCalibrationInput, CalibrationTaskOutput, StartCalibration,
            (CapabilityRequirement(PEERS, CommunicationPeersObservation, "observation", 3.0), STATE_REQUIREMENT,
             *MOVEMENT_REQUIREMENTS,
             CapabilityRequirement(START_CALIBRATION, StartCalibrationCommand, "command"),
             CapabilityRequirement(CALIBRATION_RESULT, CalibrationResultEvent, "event")),
            (START_CALIBRATION, LOCOMOTION), timeout_seconds=135,
            context_fields=("technician_actor_id",)),
        SkillDefinition("agents-arena.wait-for-calibration-assignment", "1.0.0",
            "Wait without model polling for this actor's private server assignment as the calibration technician.",
            WaitAssignmentInput, CalibrationTaskOutput, WaitForAssignment,
            (STATE_REQUIREMENT,), timeout_seconds=305),
        SkillDefinition("agents-arena.inspect-diagnostic", "1.0.0",
            "Assigned coordinator only: after navigating to Main Hall, approach its diagnostic terminal and "
            "obtain the task's private short-lived authorization. Send it only by sensitive direct message to the assigned technician.",
            TaskInput, DiagnosticOutput, InspectDiagnostic,
            (STATE_REQUIREMENT, *MOVEMENT_REQUIREMENTS,
             CapabilityRequirement(INSPECT_DIAGNOSTIC, InspectDiagnosticCommand, "command"),
             CapabilityRequirement(DIAGNOSTIC_RESULT, DiagnosticResultEvent, "event")),
            (INSPECT_DIAGNOSTIC, LOCOMOTION), timeout_seconds=40, result_policy="sensitive",
            context_fields=("task_id",)),
        SkillDefinition("agents-arena.submit-calibration", "1.0.0",
            "Assigned technician only: after navigating to Engineering, approach its calibration console and "
            "submit the exact authorization received from the coordinator through private in-game communication. "
            "Succeed only on authoritative server completion.",
            SubmitCalibrationInput, CalibrationTaskOutput, SubmitCalibration,
            (STATE_REQUIREMENT, *MOVEMENT_REQUIREMENTS,
             CapabilityRequirement(SUBMIT_CALIBRATION, SubmitCalibrationCommand, "command"),
             CapabilityRequirement(CALIBRATION_RESULT, CalibrationResultEvent, "event")),
            (SUBMIT_CALIBRATION, LOCOMOTION), timeout_seconds=40, context_fields=("task_id",)),
        SkillDefinition("agents-arena.wait-for-calibration", "1.0.0",
            "Wait for this assigned task's authoritative Completed state. Fail on Failed, Expired or Cancelled; "
            "a sent message or accepted local command never counts as task completion.",
            WaitCalibrationInput, CalibrationTaskOutput, WaitForCalibration,
            (STATE_REQUIREMENT,), timeout_seconds=305, context_fields=("task_id",)),
        SkillDefinition("agents-arena.cancel-calibration", "1.0.0",
            "Cancel this actor's unfinished cooperative task through the authoritative server. "
            "This cannot erase a Completed task or cancel another actor's assignment.",
            TaskInput, CalibrationTaskOutput, CancelCalibration,
            (STATE_REQUIREMENT, CapabilityRequirement(CANCEL_CALIBRATION, CancelCalibrationCommand, "command"),
             CapabilityRequirement(CALIBRATION_RESULT, CalibrationResultEvent, "event")),
            (CANCEL_CALIBRATION,), timeout_seconds=3, context_fields=("task_id",)),
    )


async def finalize_objective(*, session, objective, status, reason, scheduler):
    """Reference-game policy: an ended objective releases an unfinished task.

    The generic Runner invokes installed bounded finalizers after foreground
    cleanup. The same Scheduler executes the typed cancellation; no second
    cognitive loop, direct transport access or server-state mutation is used.
    """
    if session.closed.is_set() or CALIBRATION_STATE not in session.capabilities:
        return
    state = session.world.latest(CalibrationTaskStateObservation, capability_id=CALIBRATION_STATE)
    # A terminal live event may preempt a Skill before the next periodic
    # observation frame. Its newer authoritative state already releases the
    # assignment; do not send a redundant Cancel against a finished task.
    if state is not None:
        for entry in session.world.events:
            if (isinstance(entry.payload, CalibrationTaskChangedEvent)
                    and entry.entity_id == session.world.self_actor_id):
                changed = entry.payload.state
                if (changed.task_id == state.task_id and changed.match_id == state.match_id
                        and changed.server_revision >= state.server_revision):
                    state = changed
    if (state is None or not state.task_id or status_name(state.status) not in (
            "awaiting_diagnostic", "awaiting_authorization")
            or session.world.self_actor_id not in (state.coordinator_actor_id, state.technician_actor_id)):
        return
    result = await scheduler.run("agents-arena.cancel-calibration", {"task_id": state.task_id})
    if result.status != "succeeded":
        raise SkillError("task_cleanup_failed", "The server did not confirm release of the cooperative assignment",
                         {"reason_code": result.reason})
