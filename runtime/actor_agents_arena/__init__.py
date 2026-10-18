"""Agents Arena game semantics belong to this extension, never Runtime Core."""

from importlib import resources

from extensions.agents_arena.v1.rooms_pb2 import RoomMembershipObservation, RoomChangedEvent
from extensions.agents_arena.v1.station_signals_pb2 import (
    StationSignalEvent, StationSignalKind, StationSignalAudience,
    ROUTINE_NOTICE, URGENT_RECALL, STATION_WIDE,
)
from actor_runtime.extensions import ExtensionPackage

ROOM_MEMBERSHIP = "agents-arena.room-membership"
ROOM_CHANGED = "agents-arena.room-changed"
STATION_SIGNAL = "agents-arena.station-signal"


def extension_package() -> ExtensionPackage:
    data = resources.files("extensions.agents_arena.metadata")
    return ExtensionPackage.from_resources(
        data.joinpath("agents_arena.v1.json").read_bytes(),
        data.joinpath("agents_arena.v1.pb").read_bytes(),
        (RoomMembershipObservation, RoomChangedEvent, StationSignalEvent),
    )
