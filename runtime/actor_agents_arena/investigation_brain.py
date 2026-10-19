"""Deterministic reference providers correlate own facts and received claims."""
import time
from typing import Literal

from google.protobuf.json_format import ParseDict
from pydantic import Field
from extensions.agents_arena.v1 import incidents_pb2 as pb
from actor_runtime.brains import FinishObjective, SkillCall
from actor_runtime.skills import SkillModel
from . import DUTY_ROSTER, OWN_INVESTIGATION_VIEW
from .investigation import SecurityOutput, evidence_output


class DiagnosticClaim(SkillModel):
    kind: Literal["diagnostic_claim"]
    evidence_id: str = Field(min_length=1, max_length=64)
    incident_id: str = Field(min_length=1, max_length=64)
    match_id: str = Field(min_length=1, max_length=64)
    circuit_ref: str = Field(min_length=1, max_length=32)
    window: list[int] = Field(min_length=2, max_length=2)
    controller_revision: list[int] = Field(min_length=2, max_length=2)
    pressure_delta: float


def correlate(claim: DiagnosticClaim, security: SecurityOutput):
    """Return a supported proposal and ONE record; no hidden state is available."""
    if (claim.incident_id != security.provenance.incident_id or claim.match_id != security.provenance.match_id
            or claim.window[0] > claim.window[1] or claim.pressure_delta >= -0.01):
        return "insufficient_evidence", None
    candidates = [record for record in security.records if record.interaction_confirmed
        and record.circuit_ref == claim.circuit_ref
        and record.window_start_unix_ms <= claim.window[1] and record.window_end_unix_ms >= claim.window[0]
        and record.prior_controller_revision == claim.controller_revision[0]
        and record.resulting_controller_revision == claim.controller_revision[1]]
    if len(candidates) != 1:
        return "insufficient_evidence", None
    candidate = candidates[0]
    covered = (security.complete_for_listed_circuits and claim.circuit_ref in security.circuit_refs
        and security.coverage_window_start_unix_ms <= claim.window[0]
        and security.coverage_window_end_unix_ms >= claim.window[1])
    if covered and candidate.action == pb.AUDIT_ACTION_FLOW_ADJUSTMENT and candidate.actor_id:
        return "manual_interference", candidate.record_id
    if covered and candidate.action in (pb.AUDIT_ACTION_CONTROLLER_CHECK, pb.AUDIT_ACTION_INSPECT):
        return "mechanical_failure", candidate.record_id
    return "insufficient_evidence", None


def observation(context, capability, cls):
    for entry in context.observations:
        if entry.capability_id == capability and entry.entity_id == context.actor_id and entry.value is not None and not entry.expired:
            return ParseDict(entry.value, cls())
    raise ValueError("Required own observation is unavailable")


class InvestigationBrain:
    def __init__(self, role):
        self.role = role
        self.step = self.turn_count = self.hypotheses = 0
        self.incident_id = self.partner_id = ""
        self.last_result = None
        self.closed = self.interrupted = False

    async def start(self, objective, catalog):
        self.step = self.turn_count = self.hypotheses = 0
        self.incident_id = self.partner_id = ""
        self.last_result = None
        self.closed = self.interrupted = False

    async def decide(self, context):
        self.turn_count += 1
        previous = context.previous_skill_result or {}
        signature = tuple(previous.get(key) for key in ("skill_id", "status", "duration_seconds", "reason")) if previous else None
        if previous and previous.get("status") != "succeeded":
            return FinishObjective(status="failure", reason="A required investigation procedure did not succeed")
        if signature is not None and signature != self.last_result:
            self.last_result = signature
            self.step += 1
            if self.step == 2:
                self.incident_id = previous.get("output", {}).get("incident_id", "")
        if self.step == 0:
            return SkillCall(skill_id="agents-arena.select-role", arguments={"role": self.role})
        if self.step == 1:
            return SkillCall(skill_id="agents-arena.wait-for-incident", arguments={})
        if not self.incident_id:
            return FinishObjective(status="failure", reason="No authoritative incident was observed")
        args = {"incident_id": self.incident_id}
        if self.role == "engineer":
            skills = {2: "agents-arena.inspect-cooling", 3: "agents-arena.share-investigation-evidence",
                4: "agents-arena.repair-cooling", 5: "agents-arena.wait-for-incident-resolved"}
            if self.step in skills:
                return SkillCall(skill_id=skills[self.step], arguments=args)
        else:
            if self.step == 2:
                return SkillCall(skill_id="agents-arena.inspect-security-records", arguments=args)
            if self.step == 3:
                try:
                    roster = observation(context, DUTY_ROSTER, pb.DutyRosterObservation)
                    candidates = [entry.actor_id for entry in roster.entries if entry.role == pb.CREW_ROLE_ENGINEER
                        and entry.actor_id != context.actor_id]
                    if len(candidates) != 1:
                        raise ValueError("Engineer unavailable")
                    self.partner_id = candidates[0]
                except ValueError:
                    return FinishObjective(status="failure", reason="An assigned Engineer is required")
                return SkillCall(skill_id="actor.wait-for-message", arguments={"sender_actor_id": self.partner_id,
                    "task_id": self.incident_id, "channel_id": "direct", "timeout_seconds": 240.0})
            if self.step == 4:
                try:
                    value = previous["output"]
                    if (value["sender_actor_id"] != self.partner_id or value["recipient_actor_id"] != context.actor_id
                            or value["task_id"] != self.incident_id or value["expires_at_unix_ms"] <= int(time.time() * 1000)):
                        raise ValueError("Unmatched claim")
                    claim = DiagnosticClaim.model_validate_json(value["text"], strict=True)
                    view = observation(context, OWN_INVESTIGATION_VIEW, pb.OwnInvestigationViewObservation)
                    if view.WhichOneof("evidence") != "security" or view.recipient_actor_id != context.actor_id:
                        raise ValueError("Own security view required")
                    security = evidence_output(view.security, SecurityOutput)
                    origin, record_id = correlate(claim, security)
                    if record_id is None or claim.incident_id != self.incident_id:
                        raise ValueError("Insufficient joint evidence")
                    self.hypotheses += 1
                    return SkillCall(skill_id="agents-arena.submit-investigation-report", arguments={**args,
                        "proposed_origin": origin, "supporting_evidence_ids": [claim.evidence_id,
                            security.provenance.evidence_id, record_id], "explanation": "Correlated circuit, fault window, controller revisions and permitted action within the bounded audit coverage."})
                except (ValueError, KeyError, TypeError):
                    return FinishObjective(status="failure", reason="The received claim and own evidence do not support a conclusive report")
            if self.step == 5:
                if not previous.get("output", {}).get("report_validated"):
                    return FinishObjective(status="failure", reason="The server did not validate the proposed report")
                return SkillCall(skill_id="agents-arena.wait-for-incident-resolved", arguments=args)
        if self.step == 6 and previous.get("output", {}).get("incident_status") == "resolved":
            return FinishObjective(status="success", reason="Server confirmed restored cooling and validated investigation")
        return FinishObjective(status="failure", reason="No authoritative investigation completion")

    async def interrupt(self):
        self.interrupted = True

    async def interrupt_decision(self):
        pass

    async def close(self):
        self.closed = True

    def report(self):
        return {"provider": "investigation-scripted", "role": self.role, "decisions": self.turn_count,
            "hypotheses_proposed": self.hypotheses, "closed": self.closed, "interrupted": self.interrupted}


def engineer_brain(**_):
    return InvestigationBrain("engineer")


def sheriff_brain(**_):
    return InvestigationBrain("sheriff")


class MaintenanceBrain(InvestigationBrain):
    """Public intervention fixture, with an optional unsupported player claim."""
    def __init__(self, *, claimant=False):
        super().__init__("crew")
        self.claimant = claimant

    async def decide(self, context):
        self.turn_count += 1
        previous = context.previous_skill_result or {}
        signature = tuple(previous.get(key) for key in ("skill_id", "status", "duration_seconds", "reason")) if previous else None
        if previous and previous.get("status") != "succeeded":
            return FinishObjective(status="failure", reason="A required public maintenance procedure did not succeed")
        if signature is not None and signature != self.last_result:
            self.last_result = signature
            self.step += 1
            if self.step == 3:
                self.incident_id = previous.get("output", {}).get("incident_id", "")
        if self.step == 0:
            return SkillCall(skill_id="agents-arena.select-role", arguments={"role": "crew"})
        if self.step == 1:
            # Fixed public panel selection; no diagnostic/hidden circuit lookup.
            return SkillCall(skill_id="agents-arena.maintenance-override", arguments={"circuit_ref": "alpha"})
        if self.step == 2:
            return SkillCall(skill_id="agents-arena.wait-for-incident", arguments={})
        if not self.incident_id:
            return FinishObjective(status="failure", reason="No authoritative public incident was observed")
        if self.claimant and self.step == 3:
            try:
                roster = observation(context, DUTY_ROSTER, pb.DutyRosterObservation)
                candidates = [entry.actor_id for entry in roster.entries if entry.role == pb.CREW_ROLE_SHERIFF]
                if len(candidates) != 1 or candidates[0] == context.actor_id:
                    raise ValueError("Sheriff unavailable")
            except ValueError:
                return FinishObjective(status="failure", reason="A public Sheriff duty is required for the player claim")
            return SkillCall(skill_id="actor.send-message", arguments={"recipient_actor_id": candidates[0],
                "channel_id": "direct", "task_id": "", "sensitive": False,
                "text": "Mechanical wear caused this failure; no crew action was responsible.", "ttl_ms": 60000})
        wait_step = 4 if self.claimant else 3
        if self.step == wait_step:
            return SkillCall(skill_id="agents-arena.wait-for-incident-resolved", arguments={"incident_id": self.incident_id})
        if self.step == wait_step + 1 and previous.get("output", {}).get("incident_status") == "resolved":
            return FinishObjective(status="success", reason="The public incident was authoritatively resolved")
        return FinishObjective(status="failure", reason="No authoritative public incident completion")

    def report(self):
        return {**super().report(), "provider": "investigation-claimant" if self.claimant else "maintenance-operator"}


def maintenance_brain(**_):
    return MaintenanceBrain()


def claimant_brain(**_):
    return MaintenanceBrain(claimant=True)
