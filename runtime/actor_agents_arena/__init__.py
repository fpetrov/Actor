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


def extension_package() -> ExtensionPackage:
    data = resources.files("extensions.agents_arena.metadata")
    return ExtensionPackage.from_resources(
        data.joinpath("agents_arena.v1.json").read_bytes(),
        data.joinpath("agents_arena.v1.pb").read_bytes(),
        (RoomMembershipObservation, RoomChangedEvent, StationSignalEvent,
         CalibrationTaskStateObservation, CalibrationProgressObservation,
         CalibrationAssignmentEvent, CalibrationTaskChangedEvent,
         StartCalibrationCommand, InspectDiagnosticCommand, SubmitCalibrationCommand, CancelCalibrationCommand,
         DiagnosticResultEvent, CalibrationResultEvent),
    )
