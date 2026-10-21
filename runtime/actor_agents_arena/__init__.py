"""Agents Arena game semantics belong to this extension, never Runtime Core."""

from importlib import resources

from extensions.agents_arena.v1.rooms_pb2 import RoomMembershipObservation, RoomChangedEvent
from extensions.agents_arena.v1.station_signals_pb2 import (
    StationSignalEvent, StationSignalKind, StationSignalAudience,
    ROUTINE_NOTICE, URGENT_RECALL, STATION_WIDE,
)
from extensions.agents_arena.v1.calibration_pb2 import (
    CalibrationStatus, CalibrationRole, CalibrationAction,
    CalibrationTaskStateObservation, CalibrationProgressObservation,
    CalibrationAssignmentEvent, CalibrationTaskChangedEvent,
    StartCalibrationCommand, InspectDiagnosticCommand, SubmitCalibrationCommand, CancelCalibrationCommand,
    DiagnosticResultEvent, CalibrationResultEvent,
    CALIBRATION_STATUS_UNSPECIFIED, CALIBRATION_STATUS_AVAILABLE,
    CALIBRATION_STATUS_AWAITING_DIAGNOSTIC, CALIBRATION_STATUS_AWAITING_AUTHORIZATION,
    CALIBRATION_STATUS_COMPLETED, CALIBRATION_STATUS_FAILED, CALIBRATION_STATUS_EXPIRED, CALIBRATION_STATUS_CANCELLED,
    CALIBRATION_ROLE_UNSPECIFIED, CALIBRATION_ROLE_COORDINATOR, CALIBRATION_ROLE_TECHNICIAN,
    CALIBRATION_ACTION_UNSPECIFIED, CALIBRATION_ACTION_START, CALIBRATION_ACTION_INSPECT_DIAGNOSTIC,
    CALIBRATION_ACTION_SUBMIT, CALIBRATION_ACTION_CANCEL,
)
from actor_runtime.extensions import ExtensionPackage
from extensions.agents_arena.v1 import incidents_pb2
from extensions.agents_arena.v1 import social_pb2
from extensions.agents_arena.v1.incidents_pb2 import (
    CrewRole, CoolingStatus, IncidentStatus, CoolingSeverity, ProposedOrigin, AuditAction,
    EvidenceVisibility, IncidentAction, OwnRoleObservation, DutyRoleEntry, DutyRosterObservation,
    SelectRoleCommand, RoleResultEvent, CoolingStateObservation, IncidentStartedEvent, IncidentChangedEvent,
    EvidenceProvenance, PressureSample, DiagnosticEvidence, MaintenanceRecord, SecurityEvidence,
    OwnInvestigationViewObservation, InspectCoolingCommand, InspectSecurityRecordsCommand,
    DiagnosticEvidenceEvent, SecurityEvidenceEvent, RepairCoolingCommand, CancelCoolingRepairCommand,
    CancelInvestigationCommand, MaintenanceOverrideCommand, SubmitInvestigationReportCommand,
    RepairResultEvent, InvestigationResultEvent, IncidentActionResultEvent,
)

ROOM_MEMBERSHIP = "agents-arena.room-membership"
ROOM_CHANGED = "agents-arena.room-changed"
STATION_SIGNAL = "agents-arena.station-signal"
CALIBRATION_STATE = "agents-arena.calibration-state"
CALIBRATION_PROGRESS = "agents-arena.calibration-progress"
CALIBRATION_ASSIGNMENT = "agents-arena.calibration-assignment"
CALIBRATION_CHANGED = "agents-arena.calibration-changed"
START_CALIBRATION = "agents-arena.start-calibration"
INSPECT_DIAGNOSTIC = "agents-arena.inspect-diagnostic"
SUBMIT_CALIBRATION = "agents-arena.submit-calibration"
CANCEL_CALIBRATION = "agents-arena.cancel-calibration"
DIAGNOSTIC_RESULT = "agents-arena.diagnostic-result"
CALIBRATION_RESULT = "agents-arena.calibration-result"
OWN_ROLE = "agents-arena.own-role"
DUTY_ROSTER = "agents-arena.duty-roster"
SELECT_ROLE = "agents-arena.select-role"
ROLE_RESULT = "agents-arena.role-result"
COOLING_STATE = "agents-arena.cooling-state"
INCIDENT_STARTED = "agents-arena.incident-started"
INCIDENT_CHANGED = "agents-arena.incident-changed"
OWN_INVESTIGATION_VIEW = "agents-arena.own-investigation-view"
INSPECT_COOLING = "agents-arena.inspect-cooling"
INSPECT_SECURITY_RECORDS = "agents-arena.inspect-security-records"
DIAGNOSTIC_EVIDENCE = "agents-arena.diagnostic-evidence"
SECURITY_EVIDENCE = "agents-arena.security-evidence"
REPAIR_COOLING = "agents-arena.repair-cooling"
CANCEL_COOLING_REPAIR = "agents-arena.cancel-cooling-repair"
CANCEL_INVESTIGATION = "agents-arena.cancel-investigation"
MAINTENANCE_OVERRIDE = "agents-arena.maintenance-override"
SUBMIT_INVESTIGATION_REPORT = "agents-arena.submit-investigation-report"
REPAIR_RESULT = "agents-arena.repair-result"
INVESTIGATION_RESULT = "agents-arena.investigation-result"
INCIDENT_ACTION_RESULT = "agents-arena.incident-action-result"

# Social contracts remain game-specific; individual type names are also
# accessible through social_pb2 for an unambiguous generated enum surface.
for _name in social_pb2.DESCRIPTOR.message_types_by_name:
    globals()[_name] = getattr(social_pb2, _name)
for _suffix in ("match", "own-state", "visible-world", "meeting", "tasks", "emergency", "postgame",
    "ready", "kill", "report-body", "call-meeting", "cast-vote", "work-task", "sabotage",
    "repair-emergency", "cancel-work", "action-result", "match-changed", "own-state-changed",
    "meeting-changed", "emergency-changed", "visible-attack", "visible-activity", "match-ended"):
    globals()["SOCIAL_" + _suffix.upper().replace("-", "_")] = "agents-arena.social-" + _suffix


def extension_package() -> ExtensionPackage:
    data = resources.files("extensions.agents_arena.metadata")
    return ExtensionPackage.from_resources(
        data.joinpath("agents_arena.v1.json").read_bytes(),
        data.joinpath("agents_arena.v1.pb").read_bytes(),
        (RoomMembershipObservation, RoomChangedEvent, StationSignalEvent,
         CalibrationTaskStateObservation, CalibrationProgressObservation,
         CalibrationAssignmentEvent, CalibrationTaskChangedEvent,
         StartCalibrationCommand, InspectDiagnosticCommand, SubmitCalibrationCommand, CancelCalibrationCommand,
         DiagnosticResultEvent, CalibrationResultEvent,
         *(getattr(incidents_pb2, name) for name in incidents_pb2.DESCRIPTOR.message_types_by_name),
         *(getattr(social_pb2, name) for name in social_pb2.DESCRIPTOR.message_types_by_name)),
        # ExtensionRegistry validates each generated type against the packaged
        # canonical descriptor, including this optional mode's messages.
    )
