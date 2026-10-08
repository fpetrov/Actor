"""A small reference-game route policy, deliberately outside ACTOR Core."""

from __future__ import annotations

import asyncio
import logging
import math
import time

from actor_runtime.errors import ActorError
from actor_runtime.session import RuntimeSession
from actor_standard import (TRANSFORM, MOTION, LOCOMOTION, Transform2DObservation,
                            Motion2DObservation, Locomotion2DCommand)
from . import ROOM_MEMBERSHIP, ROOM_CHANGED, RoomMembershipObservation, RoomChangedEvent

LOG = logging.getLogger("actor_runtime")
MAX_STATE_AGE_SECONDS = 1.0


def move(session: RuntimeSession, x=0.0, y=0.0):
    session.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=x, desired_move_y=y))


def has_transition(session: RuntimeSession, previous: str, current: str) -> bool:
    for event in session.world.events:
        payload = event.payload
        if isinstance(payload, RoomChangedEvent) and event.entity_id == session.world.self_actor_id:
            if payload.previous_room_id == previous and payload.current_room_id == current:
                return True
    return False


def control_state(session: RuntimeSession):
    if session.closed.is_set():
        raise ActorError("SessionUnavailable", "Game disconnected during scripted route")
    transform = session.world.latest_entry(Transform2DObservation, capability_id=TRANSFORM)
    motion = session.world.latest_entry(Motion2DObservation, capability_id=MOTION)
    now = time.monotonic()
    for name, entry in (("Transform", transform), ("Motion", motion)):
        if entry is None or now - entry.received_at > MAX_STATE_AGE_SECONDS:
            raise ActorError("ObservationTimeout", f"{name} observation is missing or stale")
    position, velocity = transform.payload, motion.payload
    if not all(math.isfinite(value) for value in (
        position.x, position.y, velocity.velocity_x, velocity.velocity_y,
    )):
        raise ActorError("ObservationInvalid", "Transform and Motion values must be finite")
    return transform, motion


def stopped(motion):
    return (not motion.is_moving and
            math.hypot(motion.velocity_x, motion.velocity_y) <= 0.2)


async def waypoint(session: RuntimeSession, x: float, y: float, timeout=18):
    deadline = time.monotonic() + timeout
    stable = 0
    last_stable_sequence = -1
    while time.monotonic() < deadline:
        try:
            transform, motion = control_state(session)
        except ActorError:
            if not session.closed.is_set() and not session.mailbox.closed:
                move(session)
            raise
        position = transform.payload
        dx, dy = x - position.x, y - position.y
        distance = math.hypot(dx, dy)
        if distance < 0.12:
            move(session)
            if not stopped(motion.payload):
                stable = 0
            elif transform.sequence != last_stable_sequence:
                stable += 1
            last_stable_sequence = transform.sequence
            if stable >= 4:
                LOG.info("Scripted waypoint reached x=%.2f y=%.2f", x, y)
                return
        else:
            stable = 0
            last_stable_sequence = transform.sequence
            # Slow near a waypoint: the route has no navigation/pathfinding layer.
            strength = min(1.0, max(0.06, distance))
            move(session, dx / distance * strength, dy / distance * strength)
        await asyncio.sleep(0.05)
    move(session)
    raise ActorError("RouteTimeout", "Scripted waypoint was not reached",
                     {"x": str(x), "y": str(y)})


async def hold_stopped(session: RuntimeSession, minimum_duration=0.5, timeout=6):
    started = time.monotonic()
    stable = 0
    last_transform_sequence = last_motion_sequence = -1
    while time.monotonic() - started < timeout:
        move(session)
        transform, motion = control_state(session)
        if not stopped(motion.payload):
            stable = 0
        elif (transform.sequence != last_transform_sequence and
              motion.sequence != last_motion_sequence):
            stable += 1
        last_transform_sequence, last_motion_sequence = transform.sequence, motion.sequence
        if stable >= 4 and time.monotonic() - started >= minimum_duration:
            return
        await asyncio.sleep(0.05)
    raise ActorError("MovementDidNotStop", "Four fresh distinct samples did not confirm stopped movement")


async def expect_room(session: RuntimeSession, room: str, previous: str | None = None):
    def ready(world):
        observation = world.latest(RoomMembershipObservation)
        return (observation is not None and observation.room_id == room and
                (previous is None or has_transition(session, previous, room)))
    await session.world.wait_until(ready, timeout=12)
    LOG.info("RoomMembership=%s%s", room,
             f" RoomChanged={previous}->{room}" if previous is not None else "")


async def run(session: RuntimeSession) -> dict:
    started = time.monotonic()
    for capability, message in (
        (TRANSFORM, Transform2DObservation), (MOTION, Motion2DObservation),
        (LOCOMOTION, Locomotion2DCommand), (ROOM_MEMBERSHIP, RoomMembershipObservation),
        (ROOM_CHANGED, RoomChangedEvent),
    ):
        session.require(capability, message)
    checkpoints = []
    try:
        await expect_room(session, "main-hall")
        def has_control_observations(world):
            entries = (world.latest_entry(Transform2DObservation, capability_id=TRANSFORM),
                       world.latest_entry(Motion2DObservation, capability_id=MOTION))
            return all(entry is not None and time.monotonic() - entry.received_at <= MAX_STATE_AGE_SECONDS
                       for entry in entries)
        await session.world.wait_until(has_control_observations, timeout=10)
        checkpoints.append("initial MainHall observation")
        # Geometry belongs to this game example. Core knows no room names or route.
        for x, y in ((0, -1.4), (0, -4.7), (0, -6), (0, -7)):
            await waypoint(session, x, y)
        move(session)
        await expect_room(session, "engineering", previous="main-hall")
        checkpoints.append("MainHall -> Engineering event and authoritative membership")
        for _ in range(20):
            move(session)
            await asyncio.sleep(0.05)
        for x, y in ((0, -6), (0, -4.7), (0, -1.4)):
            await waypoint(session, x, y)
        move(session)
        await expect_room(session, "main-hall", previous="engineering")
        checkpoints.append("Engineering -> MainHall event and authoritative membership")
        await hold_stopped(session)
        checkpoints.append("stopped after return")
        transitions = [{"previous_room_id": event.payload.previous_room_id,
                        "current_room_id": event.payload.current_room_id,
                        "entity_id": event.entity_id, "sequence": event.sequence,
                        "revision": event.revision}
                       for event in session.world.events
                       if isinstance(event.payload, RoomChangedEvent) and
                       event.entity_id == session.world.self_actor_id]
        return {"passed": True, "checkpoints": checkpoints, "room_transitions": transitions,
                "elapsed_seconds": time.monotonic() - started}
    finally:
        if not session.closed.is_set() and not session.mailbox.closed:
            move(session)
