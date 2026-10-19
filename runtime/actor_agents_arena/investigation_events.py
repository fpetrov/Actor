"""Arena evidence visibility and incident lifecycle, never message prose policy."""
from extensions.agents_arena.v1 import incidents_pb2 as pb
from actor_runtime.classifiers import EventAssessment, EventHints, EventRule
from actor_runtime.extensions import type_url
from actor_runtime.inbox import EventInboxPolicy
from . import (OWN_ROLE, ROLE_RESULT, COOLING_STATE, OWN_INVESTIGATION_VIEW, INCIDENT_STARTED,
    INCIDENT_CHANGED, DIAGNOSTIC_EVIDENCE, SECURITY_EVIDENCE, REPAIR_RESULT,
    INVESTIGATION_RESULT, INCIDENT_ACTION_RESULT)


def _own_private(session, entry):
    value = entry.payload
    role = session.world.latest(pb.OwnRoleObservation, capability_id=OWN_ROLE)
    if (entry.entity_id != session.world.self_actor_id or value.recipient_actor_id != entry.entity_id
            or role is None or value.match_id != role.match_id):
        return False
    if hasattr(value, "accepted"):
        if not value.accepted:
            return bool(value.correlation_id)
        kind = "diagnostic" if entry.capability_id == DIAGNOSTIC_EVIDENCE else "security"
        evidence = value.evidence
    else:
        kind = value.WhichOneof("evidence")
        if kind is None:
            return True  # An explicit authoritative clear replaces old knowledge.
        evidence = getattr(value, kind)
    expected = pb.CREW_ROLE_ENGINEER if kind == "diagnostic" else pb.CREW_ROLE_SHERIFF
    provenance = evidence.provenance
    state = session.world.latest(pb.CoolingStateObservation, capability_id=COOLING_STATE)
    return (role.actor_id == entry.entity_id and role.role == expected and provenance.recipient_role == expected
        and provenance.recipient_actor_id == entry.entity_id and bool(provenance.evidence_id)
        and provenance.match_id == value.match_id and provenance.visibility == pb.EVIDENCE_VISIBILITY_ROLE_PRIVATE
        and state is not None and provenance.incident_id == state.incident_id
        and (hasattr(value, "accepted") or value.incident_id == provenance.incident_id)
        and state.match_id == value.match_id and 0 < value.expires_at_unix_ms <= provenance.expires_at_unix_ms)


def _own_incident(session, entry):
    state = entry.payload.state
    current = session.world.latest(pb.CoolingStateObservation, capability_id=COOLING_STATE)
    role = session.world.latest(pb.OwnRoleObservation, capability_id=OWN_ROLE)
    new_start = (entry.capability_id == INCIDENT_STARTED and current is not None
        and current.incident_status != pb.INCIDENT_STATUS_ACTIVE)
    return (entry.entity_id == session.world.self_actor_id and bool(state.incident_id) and state.server_revision > 0
        and role is not None and role.match_id == state.match_id
        and (current is None or not current.incident_id or current.incident_id == state.incident_id or new_start)
        and (current is None or new_start or current.server_revision <= state.server_revision))


def event_rules():
    def routine(context, event):
        return EventAssessment(relevance="relevant", confidence=1.0, reason_code="typed_investigation_outcome")

    def lifecycle_hints(event):
        state = event.entry.payload.state
        critical = event.entry.capability_id == INCIDENT_STARTED or state.incident_status in (pb.INCIDENT_STATUS_EXPIRED,
            pb.INCIDENT_STATUS_CANCELLED, pb.INCIDENT_STATUS_FAILED)
        return EventHints(priority=100 if critical else 0, potentially_critical=critical,
            semantic_id=f"{state.match_id}:{state.incident_id}:{state.server_revision}:{state.incident_status}",
            tags=("investigation",))

    def lifecycle(context, event):
        state = event.entry.payload.state
        skill = context.current_skill
        skill_id = skill.get("skill_id") if isinstance(skill, dict) else skill
        started = event.entry.capability_id == INCIDENT_STARTED
        failed = state.incident_status in (pb.INCIDENT_STATUS_EXPIRED, pb.INCIDENT_STATUS_CANCELLED, pb.INCIDENT_STATUS_FAILED)
        expected = (started and skill_id in ("agents-arena.wait-for-incident", "agents-arena.maintenance-override") or
            failed and skill_id == "agents-arena.cancel-investigation")
        return EventAssessment(relevance="relevant", urgency="critical" if started or failed else "routine",
            should_interrupt_skill=(started or failed) and not expected,
            should_request_brain_review=started or failed, should_remember=True, confidence=1.0,
            reason_code="incident_started" if started else "incident_terminal" if failed else "incident_progress")

    rules = [
        EventRule(OWN_INVESTIGATION_VIEW, "agents-arena", type_url(pb.OwnInvestigationViewObservation),
            lambda _: EventHints(), routine, data_policy="session_only", accept=_own_private,
            expiry_field="expires_at_unix_ms"),
        EventRule(INCIDENT_STARTED, "agents-arena", type_url(pb.IncidentStartedEvent), lifecycle_hints, lifecycle, accept=_own_incident),
        EventRule(INCIDENT_CHANGED, "agents-arena", type_url(pb.IncidentChangedEvent), lifecycle_hints, lifecycle, accept=_own_incident),
    ]
    for capability, cls in ((DIAGNOSTIC_EVIDENCE, pb.DiagnosticEvidenceEvent), (SECURITY_EVIDENCE, pb.SecurityEvidenceEvent)):
        rules.append(EventRule(capability, "agents-arena", type_url(cls),
            lambda event: EventHints(semantic_id=event.entry.payload.correlation_id, tags=("private-evidence",)),
            routine, inbox_policy=EventInboxPolicy(identity_field="correlation_id", expiry_field="expires_at_unix_ms",
                max_items=32, max_age_seconds=300, max_payload_bytes=8192),
            data_policy="session_only", accept=_own_private, expiry_field="expires_at_unix_ms"))
    for capability, cls in ((ROLE_RESULT, pb.RoleResultEvent), (REPAIR_RESULT, pb.RepairResultEvent),
            (INVESTIGATION_RESULT, pb.InvestigationResultEvent), (INCIDENT_ACTION_RESULT, pb.IncidentActionResultEvent)):
        rules.append(EventRule(capability, "agents-arena", type_url(cls),
            lambda event: EventHints(semantic_id=f"{event.entry.payload.correlation_id}:{event.entry.payload.server_revision}:"
                f"{getattr(event.entry.payload, 'completed', '')}", tags=("investigation-outcome",)),
            routine, inbox_policy=EventInboxPolicy(max_items=32, max_age_seconds=60)))
    return tuple(rules)
