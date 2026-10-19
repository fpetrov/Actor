"""Role-gated Arena investigation procedures over ordinary typed capabilities."""
from __future__ import annotations

import json
import time
from typing import Literal
import uuid

from pydantic import Field, field_validator
from extensions.agents_arena.v1 import incidents_pb2 as pb

from actor_runtime.skills import ActorSkill, CapabilityRequirement as Requirement, SkillDefinition, SkillError, SkillModel
from actor_standard import LOCOMOTION
from actor_standard_communication import (PEERS, SEND_MESSAGE, MESSAGE_DELIVERY_RESULT,
    CommunicationPeersObservation, SendMessageCommand, MessageDeliveryResultEvent)
from actor_standard_communication.skills import SendMessage, SendMessageInput, SendMessageOutput
from . import (OWN_ROLE, DUTY_ROSTER, SELECT_ROLE, ROLE_RESULT, COOLING_STATE, OWN_INVESTIGATION_VIEW,
    INSPECT_COOLING, INSPECT_SECURITY_RECORDS, DIAGNOSTIC_EVIDENCE, SECURITY_EVIDENCE, REPAIR_COOLING,
    CANCEL_COOLING_REPAIR, CANCEL_INVESTIGATION, SUBMIT_INVESTIGATION_REPORT, REPAIR_RESULT,
    INVESTIGATION_RESULT, INCIDENT_ACTION_RESULT, MAINTENANCE_OVERRIDE)
from .calibration import MOVEMENT_REQUIREMENTS
from .skills import NavigateToRoom, Waypoint

ROLES = {"crew": pb.CREW_ROLE_CREW, "engineer": pb.CREW_ROLE_ENGINEER, "sheriff": pb.CREW_ROLE_SHERIFF}
ORIGINS = {"mechanical_failure": pb.PROPOSED_ORIGIN_MECHANICAL_FAILURE,
    "manual_interference": pb.PROPOSED_ORIGIN_MANUAL_INTERFERENCE,
    "insufficient_evidence": pb.PROPOSED_ORIGIN_INSUFFICIENT_EVIDENCE}
TERMINAL_FAILURES = {pb.INCIDENT_STATUS_EXPIRED, pb.INCIDENT_STATUS_CANCELLED, pb.INCIDENT_STATUS_FAILED}
APPROACHES = {"engineering": Waypoint(-.3, -7.15), "main-hall": Waypoint(-1.5, -1.9)}


def enum_name(message, field, prefix):
    descriptor = message.DESCRIPTOR.fields_by_name[field].enum_type
    value = descriptor.values_by_number.get(getattr(message, field))
    return value.name.removeprefix(prefix).lower() if value else "unknown"


class RoleInput(SkillModel):
    role: Literal["crew", "engineer", "sheriff"]


class RoleOutput(SkillModel):
    actor_id: str
    role: str
    selection_locked: bool


class IncidentInput(SkillModel):
    incident_id: str = Field(min_length=1, max_length=64)


class WaitIncidentInput(SkillModel):
    timeout_seconds: float = Field(default=180.0, gt=0, le=300)


class WaitResolvedInput(IncidentInput):
    timeout_seconds: float = Field(default=240.0, gt=0, le=300)


class IncidentOutput(SkillModel):
    incident_id: str
    incident_status: str
    cooling_status: str
    report_validated: bool
    expires_at_unix_ms: int


class Provenance(SkillModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    incident_id: str = Field(min_length=1, max_length=64)
    source_entity_id: str = Field(min_length=1, max_length=128)
    observed_at_unix_ms: int = Field(gt=0)
    expires_at_unix_ms: int = Field(gt=0)
    server_revision: int = Field(gt=0)
    match_id: str = Field(min_length=1, max_length=64)
    recipient_actor_id: str = Field(min_length=1, max_length=128)
    recipient_role: int = Field(ge=1, le=3)
    visibility: int = Field(ge=1, le=1)


class PressureSample(SkillModel):
    at_unix_ms: int
    pressure: float


class DiagnosticOutput(SkillModel):
    provenance: Provenance
    circuit_ref: str = Field(min_length=1, max_length=32)
    failure_window_start_unix_ms: int
    failure_window_end_unix_ms: int
    samples: list[PressureSample] = Field(min_length=2, max_length=8)
    equipment_wear: float = Field(ge=0, le=1,
        description="Wear and pressure loss can occur in both mechanical failure and manual interference; neither alone identifies origin.")
    controller_revision_before: int = Field(ge=0,
        description="Correlate this revision pair with the audit record. Physical valve adjustment does not rewrite controller configuration; R→R occurs for both origins.")
    controller_revision_after: int = Field(ge=0,
        description="An unchanged controller revision does not exclude a physical valve adjustment.")
    source_kind: Literal["own_authoritative_observation"] = "own_authoritative_observation"


class MaintenanceRecord(SkillModel):
    record_id: str
    source_entity_id: str
    circuit_ref: str
    window_start_unix_ms: int
    window_end_unix_ms: int
    action: int = Field(ge=0, le=4,
        description="0 Unspecified; 1 Access (panel visit); 2 Inspect (read-only inspection); 3 FlowAdjustment (observed physical valve actuation, without rewriting controller revision); 4 ControllerCheck (neutral automatic checkpoint). Access/Inspect alone do not establish manual interference.")
    actor_id: str
    interaction_confirmed: bool = Field(description="Whether this specific recorded action was confirmed; panel presence alone is not a confirmed flow adjustment.")
    prior_controller_revision: int = Field(ge=0)
    resulting_controller_revision: int = Field(ge=0)


class SecurityOutput(SkillModel):
    provenance: Provenance
    records: list[MaintenanceRecord] = Field(max_length=6)
    coverage_window_start_unix_ms: int
    coverage_window_end_unix_ms: int
    complete_for_listed_circuits: bool = Field(
        description="Complete only for circuit_refs within the coverage interval. A conclusive report requires coverage of the diagnostic failed circuit and entire failure window.")
    circuit_refs: list[str] = Field(max_length=2)
    source_kind: Literal["own_authoritative_observation"] = "own_authoritative_observation"


class RepairInput(IncidentInput):
    diagnostic_evidence_id: str = Field(default="", max_length=64)


class ReportInput(IncidentInput):
    proposed_origin: Literal["mechanical_failure", "manual_interference", "insufficient_evidence"] = Field(
        description="Hypothesis from the received diagnostic claim plus own audit: manual_interference requires a confirmed matching physical FlowAdjustment (3); mechanical_failure requires complete matching coverage and a neutral check/read-only record without a matching actual flow adjustment. Pressure loss, wear and unchanged R→R revisions occur for both. Choose insufficient_evidence when joint support is incomplete; its acceptance does not validate or resolve the incident.")
    supporting_evidence_ids: list[str] = Field(min_length=3, max_length=3,
        description="Exactly the diagnostic EvidenceId, own security EvidenceId and one selected maintenance RecordId.")
    explanation: str = Field(default="", max_length=256)

    @field_validator("supporting_evidence_ids")
    @classmethod
    def bounded_ids(cls, values):
        if len(set(values)) != len(values) or any(not value or len(value) > 64 for value in values):
            raise ValueError("Supporting references must be distinct bounded evidence IDs")
        return values


class ReportOutput(SkillModel):
    incident_id: str
    report_validated: bool = Field(description="Only true confirms a server-validated conclusive report. An accepted insufficient_evidence report returns false and does not resolve the incident.")
    incident_status: str


class ShareInput(IncidentInput):
    recipient_actor_id: str = Field(default="", max_length=128)


class MaintenanceInput(SkillModel):
    circuit_ref: Literal["alpha", "beta"]


class MaintenanceOutput(SkillModel):
    action: Literal["maintenance_override"] = "maintenance_override"
    circuit_ref: str
    accepted: bool
    incident_id: str


def incident_output(state):
    return IncidentOutput(incident_id=state.incident_id,
        incident_status=enum_name(state, "incident_status", "INCIDENT_STATUS_"),
        cooling_status=enum_name(state, "cooling_status", "COOLING_STATUS_"),
        report_validated=state.report_validated, expires_at_unix_ms=state.expires_at_unix_ms)


def require_role(context, expected):
    role = context.observation(OWN_ROLE, pb.OwnRoleObservation)
    if role.actor_id != context.actor_id or role.role != expected or not role.match_id:
        raise SkillError("role_not_authorized", "This procedure requires the designated station duty")
    return role


def current_incident(context, incident_id, *, allow_resolved=False):
    state = context.observation(COOLING_STATE, pb.CoolingStateObservation)
    if state.incident_id != incident_id or not state.match_id:
        raise SkillError("incident_not_current", "The requested incident is not current")
    if state.incident_status in TERMINAL_FAILURES:
        raise SkillError("incident_" + enum_name(state, "incident_status", "INCIDENT_STATUS_"),
                         "The authoritative incident ended without resolution")
    if state.incident_status == pb.INCIDENT_STATUS_ACTIVE and state.expires_at_unix_ms <= int(time.time() * 1000):
        raise SkillError("incident_expired", "The authoritative incident deadline has passed")
    if state.incident_status != pb.INCIDENT_STATUS_ACTIVE and not (
            allow_resolved and state.incident_status == pb.INCIDENT_STATUS_RESOLVED):
        raise SkillError("incident_not_active", "The requested incident is not active")
    return state


def own_evidence(context, incident_id, kind):
    view = context.observation(OWN_INVESTIGATION_VIEW, pb.OwnInvestigationViewObservation)
    state = current_incident(context, incident_id, allow_resolved=True)
    if (view.recipient_actor_id != context.actor_id or view.incident_id != incident_id
            or view.match_id != state.match_id or view.WhichOneof("evidence") != kind
            or view.expires_at_unix_ms <= int(time.time() * 1000)):
        raise SkillError("evidence_unavailable", "Inspect the permitted source for fresh own evidence")
    value = getattr(view, kind)
    provenance = value.provenance
    role = context.observation(OWN_ROLE, pb.OwnRoleObservation)
    if (provenance.recipient_actor_id != context.actor_id or provenance.incident_id != incident_id
            or provenance.match_id != state.match_id or provenance.expires_at_unix_ms <= int(time.time() * 1000)
            or provenance.recipient_role != (pb.CREW_ROLE_ENGINEER if kind == "diagnostic" else pb.CREW_ROLE_SHERIFF)
            or provenance.visibility != pb.EVIDENCE_VISIBILITY_ROLE_PRIVATE
            or role.actor_id != context.actor_id or role.match_id != state.match_id or role.role != provenance.recipient_role):
        raise SkillError("evidence_unavailable", "The own evidence does not belong to this incident and actor")
    return value


def evidence_output(value, model):
    # Protobuf JSON encodes int64 as strings; strict Skill DTOs retain integers.
    def native(message):
        result = {}
        for field in message.DESCRIPTOR.fields:
            item = getattr(message, field.name)
            if field.is_repeated:
                result[field.name] = [native(child) if field.message_type else child for child in item]
            else:
                result[field.name] = native(item) if field.message_type else item
        return result
    return model.model_validate(native(value), strict=True)


def check_result(value):
    if not value.accepted:
        raise SkillError(value.reason_code or "server_rejected", "The authoritative game rejected this interaction")


def check_evidence_result(value, context, state, role):
    provenance = value.evidence.provenance
    now = int(time.time() * 1000)
    if (value.recipient_actor_id != context.actor_id or provenance.recipient_actor_id != context.actor_id
            or provenance.incident_id != state.incident_id or provenance.match_id != state.match_id
            or provenance.recipient_role != role or provenance.visibility != pb.EVIDENCE_VISIBILITY_ROLE_PRIVATE
            or min(value.expires_at_unix_ms, provenance.expires_at_unix_ms) <= now):
        raise SkillError("evidence_result_invalid", "Fresh own-role provenance is required for inspected evidence")


class SelectRole(ActorSkill):
    def preflight(self, context, arguments):
        current = context.observation(OWN_ROLE, pb.OwnRoleObservation)
        if current.actor_id != context.actor_id or not current.match_id:
            raise SkillError("role_state_invalid", "An own authoritative role state is required")

    async def execute(self, context, arguments):
        current = context.observation(OWN_ROLE, pb.OwnRoleObservation)
        correlation = uuid.uuid4().hex
        context.command(SELECT_ROLE, pb.SelectRoleCommand(role=ROLES[arguments.role],
            correlation_id=correlation, match_id=current.match_id), ttl_ms=1000)
        result = (await context.wait_event(ROLE_RESULT, pb.RoleResultEvent,
            predicate=lambda value: value.correlation_id == correlation and value.match_id == current.match_id,
            timeout_seconds=10)).payload
        check_result(result)
        if (result.state.actor_id != context.actor_id or result.state.role != ROLES[arguments.role]
                or result.state.match_id != current.match_id):
            raise SkillError("role_result_invalid", "Role acceptance did not confirm the requested own duty")
        await context.wait_until(lambda ctx: ctx.observation(OWN_ROLE, pb.OwnRoleObservation).role == result.state.role,
            timeout_seconds=3)
        return RoleOutput(actor_id=result.state.actor_id, role=arguments.role, selection_locked=result.state.selection_locked)

    async def cleanup(self, context):
        pass


class MaintenanceOverride(ActorSkill):
    """A real public panel operation; acceptance never asserts an origin."""
    def preflight(self, context, arguments):
        role = context.observation(OWN_ROLE, pb.OwnRoleObservation)
        if role.actor_id != context.actor_id or role.role not in (pb.CREW_ROLE_CREW, pb.CREW_ROLE_ENGINEER):
            raise SkillError("role_not_authorized", "Maintenance requires a permitted station duty")

    async def execute(self, context, arguments):
        role = context.observation(OWN_ROLE, pb.OwnRoleObservation)
        def duties_ready(ctx):
            roster = ctx.observation(DUTY_ROSTER, pb.DutyRosterObservation)
            engineers = {entry.actor_id for entry in roster.entries if entry.role == pb.CREW_ROLE_ENGINEER}
            sheriffs = {entry.actor_id for entry in roster.entries if entry.role == pb.CREW_ROLE_SHERIFF}
            return roster.match_id == role.match_id and len(engineers) == len(sheriffs) == 1 and not engineers & sheriffs
        await context.wait_until(duties_ready, timeout_seconds=180)
        await self.approach(context)
        correlation = uuid.uuid4().hex
        context.command(MAINTENANCE_OVERRIDE, pb.MaintenanceOverrideCommand(circuit_ref=arguments.circuit_ref,
            correlation_id=correlation, match_id=role.match_id), ttl_ms=1000)
        result = (await context.wait_event(INCIDENT_ACTION_RESULT, pb.IncidentActionResultEvent,
            predicate=lambda value: value.correlation_id == correlation and value.match_id == role.match_id
                and value.action == pb.INCIDENT_ACTION_MAINTENANCE_OVERRIDE, timeout_seconds=10)).payload
        check_result(result)
        return MaintenanceOutput(circuit_ref=arguments.circuit_ref, accepted=True, incident_id=result.incident_id)

    async def approach(self, context):
        navigation = NavigateToRoom()
        await navigation.execute_to_approach(context, "engineering", Waypoint(.3, -7.15), lane_x=.3)
    async def cleanup(self, context):
        await NavigateToRoom().cleanup(context)


class WaitForIncident(ActorSkill):
    async def execute(self, context, arguments):
        await context.wait_until(lambda ctx: bool(ctx.observation(COOLING_STATE, pb.CoolingStateObservation).incident_id)
            and ctx.observation(COOLING_STATE, pb.CoolingStateObservation).incident_status == pb.INCIDENT_STATUS_ACTIVE
            and ctx.observation(COOLING_STATE, pb.CoolingStateObservation).expires_at_unix_ms > int(time.time() * 1000),
            timeout_seconds=arguments.timeout_seconds)
        return incident_output(context.observation(COOLING_STATE, pb.CoolingStateObservation))


class TerminalSkill(ActorSkill):
    role, room = 0, ""

    def preflight(self, context, arguments):
        role = require_role(context, self.role)
        state = current_incident(context, arguments.incident_id)
        if role.match_id != state.match_id:
            raise SkillError("incident_not_current", "Role and incident must belong to the same match")

    async def approach(self, context):
        navigation = NavigateToRoom()
        await navigation.execute_to_approach(context, self.room, APPROACHES[self.room], lane_x=-.3)

    async def cleanup(self, context):
        await NavigateToRoom().cleanup(context)


class InspectCooling(TerminalSkill):
    role, room = pb.CREW_ROLE_ENGINEER, "engineering"

    async def execute(self, context, arguments):
        await self.approach(context)
        state = current_incident(context, arguments.incident_id)
        correlation = uuid.uuid4().hex
        context.command(INSPECT_COOLING, pb.InspectCoolingCommand(incident_id=state.incident_id,
            correlation_id=correlation, match_id=state.match_id), ttl_ms=1000)
        result = (await context.wait_event(DIAGNOSTIC_EVIDENCE, pb.DiagnosticEvidenceEvent,
            predicate=lambda value: value.correlation_id == correlation and value.match_id == state.match_id,
            timeout_seconds=10)).payload
        check_result(result)
        check_evidence_result(result, context, state, self.role)
        context.expire_result_at(min(result.expires_at_unix_ms, result.evidence.provenance.expires_at_unix_ms))
        value = evidence_output(result.evidence, DiagnosticOutput)
        if (value.provenance.recipient_actor_id != context.actor_id or value.provenance.incident_id != state.incident_id
                or value.provenance.match_id != state.match_id):
            raise SkillError("evidence_result_invalid", "Diagnostic provenance did not confirm the own requested source")
        await context.wait_until(lambda ctx: ctx.observation(OWN_INVESTIGATION_VIEW,
            pb.OwnInvestigationViewObservation).diagnostic.provenance.evidence_id == value.provenance.evidence_id,
            timeout_seconds=3)
        return value


class InspectSecurityRecords(TerminalSkill):
    role, room = pb.CREW_ROLE_SHERIFF, "main-hall"

    async def execute(self, context, arguments):
        await self.approach(context)
        state = current_incident(context, arguments.incident_id)
        correlation = uuid.uuid4().hex
        context.command(INSPECT_SECURITY_RECORDS, pb.InspectSecurityRecordsCommand(incident_id=state.incident_id,
            correlation_id=correlation, match_id=state.match_id), ttl_ms=1000)
        result = (await context.wait_event(SECURITY_EVIDENCE, pb.SecurityEvidenceEvent,
            predicate=lambda value: value.correlation_id == correlation and value.match_id == state.match_id,
            timeout_seconds=10)).payload
        check_result(result)
        check_evidence_result(result, context, state, self.role)
        context.expire_result_at(min(result.expires_at_unix_ms, result.evidence.provenance.expires_at_unix_ms))
        value = evidence_output(result.evidence, SecurityOutput)
        if (value.provenance.recipient_actor_id != context.actor_id or value.provenance.incident_id != state.incident_id
                or value.provenance.match_id != state.match_id):
            raise SkillError("evidence_result_invalid", "Security provenance did not confirm the own requested source")
        await context.wait_until(lambda ctx: ctx.observation(OWN_INVESTIGATION_VIEW,
            pb.OwnInvestigationViewObservation).security.provenance.evidence_id == value.provenance.evidence_id,
            timeout_seconds=3)
        return value


class RepairCooling(TerminalSkill):
    role, room = pb.CREW_ROLE_ENGINEER, "engineering"

    def __init__(self):
        self.pending = False
        self.incident_id = self.match_id = ""

    def preflight(self, context, arguments):
        super().preflight(context, arguments)
        evidence = own_evidence(context, arguments.incident_id, "diagnostic")
        if arguments.diagnostic_evidence_id and arguments.diagnostic_evidence_id != evidence.provenance.evidence_id:
            raise SkillError("evidence_not_owned", "Use the own diagnostic evidence ID for this incident")

    async def execute(self, context, arguments):
        await self.approach(context)
        state = current_incident(context, arguments.incident_id)
        evidence = own_evidence(context, arguments.incident_id, "diagnostic")
        self.incident_id, self.match_id = state.incident_id, state.match_id
        correlation = uuid.uuid4().hex
        self.pending = True
        context.command(REPAIR_COOLING, pb.RepairCoolingCommand(incident_id=state.incident_id,
            diagnostic_evidence_id=evidence.provenance.evidence_id, correlation_id=correlation, match_id=state.match_id), ttl_ms=1000)
        predicate = lambda value: value.correlation_id == correlation and value.match_id == state.match_id and value.incident_id == state.incident_id
        result = (await context.wait_event(REPAIR_RESULT, pb.RepairResultEvent, predicate=predicate, timeout_seconds=10)).payload
        if not result.accepted:
            self.pending = False
        check_result(result)
        if not result.completed:
            result = (await context.wait_event(REPAIR_RESULT, pb.RepairResultEvent,
                predicate=lambda value: predicate(value) and (not value.accepted or value.completed), timeout_seconds=10)).payload
            if not result.accepted:
                self.pending = False
            check_result(result)
        if not result.completed or result.cooling_status != pb.COOLING_STATUS_RESTORED:
            raise SkillError("repair_not_completed", "Repair success requires authoritative physical completion")
        self.pending = False
        await context.wait_until(lambda ctx: ctx.observation(COOLING_STATE, pb.CoolingStateObservation).incident_id == state.incident_id
            and ctx.observation(COOLING_STATE, pb.CoolingStateObservation).cooling_status == pb.COOLING_STATUS_RESTORED,
            timeout_seconds=3)
        return incident_output(context.observation(COOLING_STATE, pb.CoolingStateObservation))

    async def cleanup(self, context):
        try:
            if self.pending:
                context.command(CANCEL_COOLING_REPAIR, pb.CancelCoolingRepairCommand(incident_id=self.incident_id,
                    match_id=self.match_id, correlation_id=uuid.uuid4().hex), ttl_ms=1000)
        except SkillError as error:
            if error.code != "session_disconnected":
                raise
        finally:
            await super().cleanup(context)


class SubmitInvestigationReport(TerminalSkill):
    role, room = pb.CREW_ROLE_SHERIFF, "main-hall"

    def preflight(self, context, arguments):
        super().preflight(context, arguments)
        evidence = own_evidence(context, arguments.incident_id, "security")
        if evidence.provenance.evidence_id not in arguments.supporting_evidence_ids:
            raise SkillError("own_evidence_required", "Cite the own inspected security evidence and the received diagnostic reference")
        if sum(record.record_id in arguments.supporting_evidence_ids for record in evidence.records) != 1:
            raise SkillError("one_record_required", "Cite exactly one selected own maintenance RecordId")

    async def execute(self, context, arguments):
        await self.approach(context)
        state = current_incident(context, arguments.incident_id)
        correlation = uuid.uuid4().hex
        context.command(SUBMIT_INVESTIGATION_REPORT, pb.SubmitInvestigationReportCommand(incident_id=state.incident_id,
            proposed_origin=ORIGINS[arguments.proposed_origin], supporting_evidence_ids=arguments.supporting_evidence_ids,
            explanation=arguments.explanation, correlation_id=correlation, match_id=state.match_id), ttl_ms=1000)
        result = (await context.wait_event(INVESTIGATION_RESULT, pb.InvestigationResultEvent,
            predicate=lambda value: value.correlation_id == correlation and value.match_id == state.match_id
                and value.incident_id == state.incident_id, timeout_seconds=10)).payload
        check_result(result)
        return ReportOutput(incident_id=result.incident_id, report_validated=result.report_validated,
            incident_status=enum_name(result, "incident_status", "INCIDENT_STATUS_"))


class WaitForIncidentResolved(ActorSkill):
    async def execute(self, context, arguments):
        def resolved(ctx):
            state = current_incident(ctx, arguments.incident_id, allow_resolved=True)
            return state.incident_status == pb.INCIDENT_STATUS_RESOLVED and state.cooling_status == pb.COOLING_STATUS_RESTORED and state.report_validated
        await context.wait_until(resolved, timeout_seconds=arguments.timeout_seconds)
        return incident_output(context.observation(COOLING_STATE, pb.CoolingStateObservation))


class CancelInvestigation(ActorSkill):
    async def execute(self, context, arguments):
        state = current_incident(context, arguments.incident_id)
        correlation = uuid.uuid4().hex
        context.command(CANCEL_INVESTIGATION, pb.CancelInvestigationCommand(incident_id=state.incident_id,
            match_id=state.match_id, correlation_id=correlation), ttl_ms=1000)
        result = (await context.wait_event(INCIDENT_ACTION_RESULT, pb.IncidentActionResultEvent,
            predicate=lambda value: value.correlation_id == correlation and value.match_id == state.match_id
                and value.incident_id == state.incident_id
                and value.action == pb.INCIDENT_ACTION_CANCEL_INVESTIGATION, timeout_seconds=1.5)).payload
        check_result(result)
        return IncidentOutput(incident_id=result.incident_id,
            incident_status=enum_name(result, "incident_status", "INCIDENT_STATUS_"),
            cooling_status=enum_name(result, "cooling_status", "COOLING_STATUS_"), report_validated=False,
            expires_at_unix_ms=state.expires_at_unix_ms)

    async def cleanup(self, context):
        pass


def diagnostic_claim(value):
    result = evidence_output(value, DiagnosticOutput)
    return json.dumps({"kind": "diagnostic_claim", "evidence_id": result.provenance.evidence_id,
        "incident_id": result.provenance.incident_id, "match_id": result.provenance.match_id,
        "circuit_ref": result.circuit_ref,
        "window": [result.failure_window_start_unix_ms, result.failure_window_end_unix_ms],
        "controller_revision": [result.controller_revision_before, result.controller_revision_after],
        "pressure_delta": round(result.samples[-1].pressure - result.samples[0].pressure, 4)},
        separators=(",", ":"), allow_nan=False)


class ShareInvestigationEvidence(ActorSkill):
    def preflight(self, context, arguments):
        require_role(context, pb.CREW_ROLE_ENGINEER)
        own_evidence(context, arguments.incident_id, "diagnostic")

    async def execute(self, context, arguments):
        value = own_evidence(context, arguments.incident_id, "diagnostic")
        roster = context.observation(DUTY_ROSTER, pb.DutyRosterObservation)
        peers = context.observation(PEERS, CommunicationPeersObservation)
        if roster.match_id != value.provenance.match_id or peers.match_id != value.provenance.match_id:
            raise SkillError("recipient_not_authorized", "Peer and duty views must belong to the current incident match")
        candidates = [entry.actor_id for entry in roster.entries if entry.role == pb.CREW_ROLE_SHERIFF
            and entry.actor_id != context.actor_id and entry.actor_id in {peer.actor_id for peer in peers.peers}]
        recipient = arguments.recipient_actor_id
        if not recipient:
            if len(candidates) != 1:
                raise SkillError("sheriff_unavailable", "Exactly one permitted Sheriff must be online")
            recipient = candidates[0]
        if recipient not in candidates:
            raise SkillError("recipient_not_authorized", "Diagnostic evidence may be shared with the assigned Sheriff")
        text = diagnostic_claim(value)
        if len(text.encode("utf-8")) > 512:
            raise SkillError("evidence_claim_too_large", "The bounded diagnostic claim exceeds communication limits")
        ttl_ms = min(300000, value.provenance.expires_at_unix_ms - int(time.time() * 1000))
        if ttl_ms < 1000:
            raise SkillError("evidence_unavailable", "Inspect again before sharing evidence near its expiry")
        context.expire_result_at(value.provenance.expires_at_unix_ms)
        return await SendMessage().execute(context, SendMessageInput(recipient_actor_id=recipient, channel_id="direct",
            text=text, task_id=arguments.incident_id, sensitive=True, ttl_ms=ttl_ms))

    async def cleanup(self, context):
        pass


def skill_package():
    role = Requirement(OWN_ROLE, pb.OwnRoleObservation, "observation", 3)
    state = Requirement(COOLING_STATE, pb.CoolingStateObservation, "observation", 3)
    view = Requirement(OWN_INVESTIGATION_VIEW, pb.OwnInvestigationViewObservation, "observation", 3)
    base = (role, state)
    def command(cap, cls): return Requirement(cap, cls, "command")
    def event(cap, cls): return Requirement(cap, cls, "event")
    terminal = (*base, *MOVEMENT_REQUIREMENTS)
    return (
        SkillDefinition("agents-arena.select-role", "1.0.0", "Request a server-validated station duty; locked while an investigation is active.",
            RoleInput, RoleOutput, SelectRole, (role, command(SELECT_ROLE, pb.SelectRoleCommand), event(ROLE_RESULT, pb.RoleResultEvent)),
            (SELECT_ROLE,), timeout_seconds=15, context_fields=("role",)),
        SkillDefinition("agents-arena.wait-for-incident", "1.0.0", "Wait for an active public cooling incident without model polling. No origin is revealed.",
            WaitIncidentInput, IncidentOutput, WaitForIncident, (state,), timeout_seconds=305),
        SkillDefinition("agents-arena.maintenance-override", "1.0.0", "Crew/Engineer: wait for distinct investigation duties, approach the public engineering panel and perform a real alpha/beta flow adjustment. Acceptance does not establish incident origin.",
            MaintenanceInput, MaintenanceOutput, MaintenanceOverride, (*base, *MOVEMENT_REQUIREMENTS,
                Requirement(DUTY_ROSTER, pb.DutyRosterObservation, "observation", 3),
                command(MAINTENANCE_OVERRIDE, pb.MaintenanceOverrideCommand), event(INCIDENT_ACTION_RESULT, pb.IncidentActionResultEvent)),
            (LOCOMOTION, MAINTENANCE_OVERRIDE), timeout_seconds=300, context_fields=("circuit_ref",)),
        SkillDefinition("agents-arena.inspect-cooling", "1.0.0", "Engineer: navigate to the cooling console and inspect private failed-circuit, failure-window, pressure, wear and controller-revision evidence. Pressure loss, wear and R→R revisions occur for BOTH mechanical failure and manual interference; this view alone cannot identify origin. ShareInvestigationEvidence sends the bounded finding needed for joint correlation to the Sheriff.",
            IncidentInput, DiagnosticOutput, InspectCooling, (*terminal, view, command(INSPECT_COOLING, pb.InspectCoolingCommand),
                event(DIAGNOSTIC_EVIDENCE, pb.DiagnosticEvidenceEvent)), (LOCOMOTION, INSPECT_COOLING), timeout_seconds=100,
                result_policy="session_only", context_fields=("incident_id",)),
        SkillDefinition("agents-arena.inspect-security-records", "1.0.0", "Sheriff: navigate to security records and inspect own bounded audit. Action 3 FlowAdjustment records physical valve actuation without changing controller revision; 4 ControllerCheck is a neutral automatic checkpoint; 1 Access/2 Inspect alone are not manual interference. Correlate the actual received Engineer finding's failed circuit, window and revision pair with ONE confirmed record inside complete audit coverage. That received finding plus own audit needs no extra Engineer acknowledgement.",
            IncidentInput, SecurityOutput, InspectSecurityRecords, (*terminal, view, command(INSPECT_SECURITY_RECORDS, pb.InspectSecurityRecordsCommand),
                event(SECURITY_EVIDENCE, pb.SecurityEvidenceEvent)), (LOCOMOTION, INSPECT_SECURITY_RECORDS), timeout_seconds=100,
                result_policy="session_only", context_fields=("incident_id",)),
        SkillDefinition("agents-arena.repair-cooling", "1.0.0", "Engineer: approach the console and complete the server-timed repair using own inspected evidence. Repair alone does not resolve investigation.",
            RepairInput, IncidentOutput, RepairCooling, (*terminal, view, command(REPAIR_COOLING, pb.RepairCoolingCommand),
                command(CANCEL_COOLING_REPAIR, pb.CancelCoolingRepairCommand), event(REPAIR_RESULT, pb.RepairResultEvent)),
            (LOCOMOTION, REPAIR_COOLING, CANCEL_COOLING_REPAIR), timeout_seconds=110, context_fields=("incident_id",)),
        SkillDefinition("agents-arena.submit-investigation-report", "1.0.0", "Sheriff: cite exactly diagnostic EvidenceId, own security EvidenceId and ONE actual matching maintenance RecordId. Use the diagnostic failed circuit/window/revision pair and complete audit coverage. Confirmed action 3 is physical FlowAdjustment even with R→R; action 4 is a neutral automatic ControllerCheck; Access 1/Inspect 2 alone do not establish manual interference. Pressure/wear/R→R occur in BOTH origins. The received bounded Engineer finding plus own audit is sufficient for correlation without an extra acknowledgement. Text remains a claim; server validation decides acceptance. An accepted insufficient_evidence report is not validated completion; wrong conclusive submissions are bounded.",
            ReportInput, ReportOutput, SubmitInvestigationReport, (*terminal, view, command(SUBMIT_INVESTIGATION_REPORT, pb.SubmitInvestigationReportCommand),
                event(INVESTIGATION_RESULT, pb.InvestigationResultEvent)), (LOCOMOTION, SUBMIT_INVESTIGATION_REPORT), timeout_seconds=100,
                result_policy="session_only", context_fields=("incident_id",)),
        SkillDefinition("agents-arena.wait-for-incident-resolved", "1.0.0", "Require both authoritative restored cooling and a validated report before success; fail on terminal incident loss.",
            WaitResolvedInput, IncidentOutput, WaitForIncidentResolved, (state,), timeout_seconds=305, context_fields=("incident_id",)),
        SkillDefinition("agents-arena.cancel-investigation", "1.0.0", "Release an unfinished own-role investigation through server validation; no direct state mutation.",
            IncidentInput, IncidentOutput, CancelInvestigation, (*base, command(CANCEL_INVESTIGATION, pb.CancelInvestigationCommand),
                event(INCIDENT_ACTION_RESULT, pb.IncidentActionResultEvent)), (CANCEL_INVESTIGATION,), timeout_seconds=3,
                context_fields=("incident_id",)),
        SkillDefinition("agents-arena.share-investigation-evidence", "1.0.0", "Engineer: send the complete bounded diagnostic finding for correlation: evidence/incident/match IDs, failed circuit, failure window, controller-revision pair and pressure delta. Uses own inspected view and direct delivery to the Sheriff, valid for at most 5 minutes and never beyond evidence expiry. Await server acceptance; the recipient's real received claim plus own audit requires no additional Engineer acknowledgement. Received text remains a player claim.",
            ShareInput, SendMessageOutput, ShareInvestigationEvidence, (*base, view, Requirement(DUTY_ROSTER, pb.DutyRosterObservation, "observation", 3),
                Requirement(PEERS, CommunicationPeersObservation, "observation", 3), command(SEND_MESSAGE, SendMessageCommand),
                event(MESSAGE_DELIVERY_RESULT, MessageDeliveryResultEvent)), (SEND_MESSAGE,), timeout_seconds=35,
                result_policy="session_only", context_fields=("incident_id", "recipient_actor_id")),
    )


async def finalize_objective(*, session, objective, status, reason, scheduler):
    if session.closed.is_set() or COOLING_STATE not in session.capabilities or OWN_ROLE not in session.capabilities:
        return
    state = session.world.latest(pb.CoolingStateObservation, capability_id=COOLING_STATE)
    role = session.world.latest(pb.OwnRoleObservation, capability_id=OWN_ROLE)
    if state is None or role is None or role.role not in (pb.CREW_ROLE_ENGINEER, pb.CREW_ROLE_SHERIFF):
        return
    # Lifecycle events can precede the next periodic frame, including the start
    # of an incident while a pre-alarm objective is being cancelled.
    from . import INCIDENT_STARTED, INCIDENT_CHANGED
    def latest_state():
        latest = session.world.latest(pb.CoolingStateObservation, capability_id=COOLING_STATE)
        for entry in session.world.events:
            if entry.capability_id in (INCIDENT_STARTED, INCIDENT_CHANGED) and hasattr(entry.payload, "state"):
                changed = entry.payload.state
                if (changed.match_id == latest.match_id and changed.server_revision >= latest.server_revision
                        and (changed.incident_id == latest.incident_id or entry.capability_id == INCIDENT_STARTED)):
                    latest = changed
        return latest
    state = latest_state()
    if state.incident_status == pb.INCIDENT_STATUS_NONE and status in ("cancelled", "failed"):
        # A successful role-only objective intentionally keeps its duty. A
        # failed/cancelled one must reset the pending cycle on a live bridge.
        result = await scheduler.run("agents-arena.select-role", {"role": "crew"})
        if result.status == "succeeded":
            return
        if result.reason != "role_selection_locked":
            raise SkillError("investigation_cleanup_failed", "Server did not confirm release of the pending investigation duty")
        try:
            await session.world.wait_until(lambda _: latest_state().incident_status != pb.INCIDENT_STATUS_NONE,
                timeout=.5)
            started = latest_state()
            # The ordinary CancelInvestigation Skill reads the current public
            # observation. Wait for its frame after an earlier start event.
            await session.world.wait_until(lambda world: (
                world.latest(pb.CoolingStateObservation, capability_id=COOLING_STATE).incident_id == started.incident_id
                and world.latest(pb.CoolingStateObservation, capability_id=COOLING_STATE).match_id == started.match_id
                and world.latest(pb.CoolingStateObservation, capability_id=COOLING_STATE).incident_status != pb.INCIDENT_STATUS_NONE),
                timeout=.5)
        except TimeoutError:
            raise SkillError("investigation_cleanup_failed", "An incident locked the duty without a current authoritative identity") from None
        state = latest_state()
    if not state.incident_id or state.incident_status != pb.INCIDENT_STATUS_ACTIVE:
        return
    result = await scheduler.run("agents-arena.cancel-investigation", {"incident_id": state.incident_id})
    if result.status != "succeeded":
        raise SkillError("investigation_cleanup_failed", "Server did not confirm release of the unfinished investigation")
