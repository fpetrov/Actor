"""Agents Arena navigation procedure and route data; never Runtime Core ontology."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

from pydantic import Field

from actor_runtime.skills import (ActorSkill, CapabilityRequirement, SkillContext, SkillDefinition,
                                  SkillError, SkillModel)
from actor_standard import (TRANSFORM, MOTION, LOCOMOTION, Transform2DObservation,
                            Motion2DObservation, Locomotion2DCommand)
from . import ROOM_MEMBERSHIP, ROOM_CHANGED, RoomMembershipObservation, RoomChangedEvent


class NavigateToRoomInput(SkillModel):
    target_room_id: Literal["engineering", "main-hall"] = Field(
        description="Authoritative destination room: engineering or main-hall.")


class NavigateToRoomOutput(SkillModel):
    room_id: Literal["engineering", "main-hall"]
    stopped: bool
    waypoints_reached: int = Field(ge=0)
    room_change_confirmed: bool


@dataclass(frozen=True)
class Waypoint:
    x: float
    y: float


# A small explicit reference-game graph. It is not a generic pathfinding service.
WAYPOINTS = {
    "main-hall.anchor": Waypoint(0, -1.4),
    "main-hall.door": Waypoint(0, -4.7),
    "engineering.door": Waypoint(0, -6),
    "engineering.anchor": Waypoint(0, -7),
}
ROOM_ROUTES = {
    ("main-hall", "engineering"): ("main-hall.anchor", "main-hall.door", "engineering.door", "engineering.anchor"),
    ("engineering", "main-hall"): ("engineering.door", "main-hall.door", "main-hall.anchor"),
}


def control_state(context: SkillContext):
    transform = context.observation_entry(TRANSFORM, Transform2DObservation)
    motion = context.observation_entry(MOTION, Motion2DObservation)
    membership = context.observation_entry(ROOM_MEMBERSHIP, RoomMembershipObservation)
    position, velocity = transform.payload, motion.payload
    if not all(math.isfinite(value) for value in (
        position.x, position.y, position.facing_x, position.facing_y, velocity.velocity_x, velocity.velocity_y,
    )):
        raise SkillError("stale_world_state", "Movement observations contain nonfinite values")
    if transform.sequence != motion.sequence:
        raise SkillError("stale_world_state", "Movement observations do not describe the same captured frame")
    if membership.payload.room_id not in ("main-hall", "engineering"):
        raise SkillError("route_failed", "Actor is outside the installed reference-game route graph")
    return transform, motion, membership


def stopped(motion: Motion2DObservation) -> bool:
    return not motion.is_moving and math.hypot(motion.velocity_x, motion.velocity_y) <= 0.2


class NavigateToRoom(ActorSkill):
    def preflight(self, context: SkillContext, arguments: NavigateToRoomInput):
        _, _, membership = control_state(context)
        source = membership.payload.room_id
        if source != arguments.target_room_id and (source, arguments.target_room_id) not in ROOM_ROUTES:
            raise SkillError("target_not_found", "No installed route reaches this destination")

    @staticmethod
    def move(context: SkillContext, x: float = 0, y: float = 0):
        context.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=x, desired_move_y=y))

    async def waypoint(self, context: SkillContext, waypoint: Waypoint, timeout_seconds=18):
        deadline = context.now() + timeout_seconds
        distinct_stops = 0
        last_pair = None
        while context.now() < deadline:
            transform, motion, _ = control_state(context)
            dx, dy = waypoint.x - transform.payload.x, waypoint.y - transform.payload.y
            distance = math.hypot(dx, dy)
            pair = (transform.sequence, motion.sequence)
            if distance < 0.12:
                self.move(context)
                if not stopped(motion.payload):
                    distinct_stops = 0
                elif pair != last_pair:
                    distinct_stops += 1
                last_pair = pair
                if distinct_stops >= 4:
                    return
            else:
                distinct_stops = 0
                last_pair = pair
                strength = min(1.0, max(0.06, distance))
                self.move(context, dx / distance * strength, dy / distance * strength)
            await context.sleep(0.05)
        raise SkillError("route_failed", "Navigation waypoint was not reached within its deadline")

    async def confirm_arrival(self, context: SkillContext, target: str, timeout_seconds=3):
        deadline = context.now() + timeout_seconds
        distinct_stops, last_samples = 0, None
        while context.now() < deadline:
            self.move(context)
            transform, motion, membership = control_state(context)
            # A fresh unrelated membership update cannot count an old stopped Motion twice.
            samples = (transform.sequence, motion.sequence)
            if membership.payload.room_id == target and stopped(motion.payload):
                if samples != last_samples:
                    distinct_stops += 1
            else:
                distinct_stops = 0
            last_samples = samples
            if distinct_stops >= 4:
                return
            await context.sleep(0.05)
        raise SkillError("route_failed", "Authoritative arrival and stop were not confirmed")

    async def execute(self, context: SkillContext, arguments: NavigateToRoomInput) -> NavigateToRoomOutput:
        _, _, membership = control_state(context)
        source, target = membership.payload.room_id, arguments.target_room_id
        prior_events = context.events(ROOM_CHANGED, RoomChangedEvent)
        cursor = max((event.sequence for event in prior_events), default=0)
        route = ROOM_ROUTES.get((source, target), ())
        reached = 0
        for name in route:
            await self.waypoint(context, WAYPOINTS[name])
            reached += 1
        await self.confirm_arrival(context, target)
        confirmed = source == target or any(
            event.payload.previous_room_id == source and event.payload.current_room_id == target
            for event in context.events(ROOM_CHANGED, RoomChangedEvent, after_sequence=cursor)
        )
        if not confirmed:
            raise SkillError("route_failed", "Room membership changed without the expected authoritative transition event")
        return NavigateToRoomOutput(room_id=target, stopped=True, waypoints_reached=reached,
                                    room_change_confirmed=confirmed)

    async def cleanup(self, context: SkillContext):
        try:
            self.move(context)
        except SkillError as error:
            if error.code != "session_disconnected":
                raise


def skill_package():
    from .calibration import skill_package as calibration_skills
    return (SkillDefinition(skill_id="agents-arena.navigate-to-room", version="1.0.0",
        description="Navigate to engineering or main-hall through the installed reference-game doorway route. "
                    "Finish only after fresh authoritative RoomMembership confirms arrival and distinct Motion samples confirm stopping.",
        input_model=NavigateToRoomInput, result_model=NavigateToRoomOutput, factory=NavigateToRoom,
        required_capabilities=(
            CapabilityRequirement(TRANSFORM, Transform2DObservation, "observation", 1.0),
            CapabilityRequirement(MOTION, Motion2DObservation, "observation", 1.0),
            CapabilityRequirement(LOCOMOTION, Locomotion2DCommand, "command"),
            CapabilityRequirement(ROOM_MEMBERSHIP, RoomMembershipObservation, "observation", 1.0),
            CapabilityRequirement(ROOM_CHANGED, RoomChangedEvent, "event"),
        ), owned_command_capabilities=(LOCOMOTION,), timeout_seconds=80, cleanup_timeout_seconds=1), *calibration_skills())
