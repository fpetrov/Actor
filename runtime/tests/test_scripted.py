"""Deterministic reference-policy test; remote Unity proof is a separate check."""

import asyncio
from collections import deque
from dataclasses import replace
import math
import time

import pytest
from google.protobuf.any_pb2 import Any
from actor.core.v1 import actor_pb2 as core
from actor_runtime.errors import ActorError
from actor_standard import TRANSFORM, MOTION, Locomotion2DCommand, Transform2DObservation, Motion2DObservation
from actor_agents_arena import ROOM_MEMBERSHIP, ROOM_CHANGED, RoomMembershipObservation, RoomChangedEvent
from actor_agents_arena.scripted import run, waypoint, hold_stopped
from conftest import message, envelope, extension_version


# Match live room resolution: EngineeringRoomBuilder's shared floor edge is
# y=-5.21, and NetworkCharacter's capsule center is 0.46 above its root. Putting
# the edge at the route's y=-6 waypoint makes delayed arrival corrections look
# like extra room crossings even though that waypoint is inside Engineering.
ROOM_BOUNDARY_ROOT_Y = -5.21 - 0.46


@pytest.mark.parametrize("input_delay,observation_delay", [(0, 0), (0.1, 0.1)])
async def test_scripted_observation_command_event_round_trip(session, input_delay, observation_delay):
    x, y = -1.8, -1.4
    desired_x, desired_y = 0.0, 0.0
    room = "main-hall"
    sequence = 2
    revision = 0
    commands = []
    pending_inputs = deque()
    pending_observations = deque()

    async def receive_commands():
        async for outgoing in session.outgoing():
            if outgoing.WhichOneof("body") == "command":
                value = Locomotion2DCommand()
                assert outgoing.command.payload.Unpack(value)
                intent = (value.desired_move_x, value.desired_move_y)
                pending_inputs.append((time.monotonic() + input_delay, intent))
                commands.append(intent)

    async def game():
        nonlocal x, y, room, sequence, revision, desired_x, desired_y
        observed_at = 0
        while True:
            await asyncio.sleep(0.02)
            while pending_inputs and pending_inputs[0][0] <= time.monotonic():
                _, (desired_x, desired_y) = pending_inputs.popleft()
            x += desired_x * 2.6 * 0.02
            y += desired_y * 2.6 * 0.02
            current_room = "engineering" if y < ROOM_BOUNDARY_ROOT_Y else "main-hall"
            if current_room != room:
                packed = Any()
                packed.Pack(RoomChangedEvent(previous_room_id=room, current_room_id=current_room))
                room = current_room
                sequence += 1
                pending_observations.append((time.monotonic() + observation_delay,
                    message(sequence, event=core.GameEventEnvelope(
                    revision=revision, capability_id=ROOM_CHANGED, extension_id="agents-arena",
                    extension_version=extension_version("agents-arena"), entity_id="actor-1", payload=packed))))
            if time.monotonic() - observed_at >= 1 / 15:
                observed_at = time.monotonic()
                revision += 1
                sequence += 1
                pending_observations.append((time.monotonic() + observation_delay,
                    message(sequence, observation=core.ObservationFrame(
                    revision=revision, observations=[
                        envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=x, y=y)),
                        envelope(MOTION, "actor.standard", Motion2DObservation(
                            velocity_x=desired_x * 2.6, velocity_y=desired_y * 2.6,
                            is_moving=abs(desired_x) + abs(desired_y) > 0)),
                        envelope(ROOM_MEMBERSHIP, "agents-arena", RoomMembershipObservation(room_id=room)),
                    ]))))
            while pending_observations and pending_observations[0][0] <= time.monotonic():
                _, incoming = pending_observations.popleft()
                await session.accept(incoming)

    tasks = [asyncio.create_task(receive_commands()), asyncio.create_task(game())]
    try:
        result = await asyncio.wait_for(run(session), timeout=30)
        assert result["passed"]
        assert len(result["checkpoints"]) == 4
        assert session.events_received == 2
        transitions = result["room_transitions"]
        assert [(event["previous_room_id"], event["current_room_id"]) for event in transitions] == [
            ("main-hall", "engineering"), ("engineering", "main-hall")]
        assert all(event["entity_id"] == "actor-1" for event in transitions)
        assert transitions[0]["sequence"] < transitions[1]["sequence"]
        assert y == pytest.approx(-1.4, abs=0.15)
        assert room == "main-hall"
        assert any(value[1] < 0 for value in commands)
        assert any(value[1] > 0 for value in commands)
        assert commands[-1] == (0, 0)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_scripted_stale_observations_zero_movement(session):
    await session.accept(message(3, observation=core.ObservationFrame(revision=1,
        observations=[envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=1, y=2)),
                      envelope(MOTION, "actor.standard", Motion2DObservation())])))
    key = ("actor-1", TRANSFORM)
    session.world.observations[key] = replace(session.world.observations[key], received_at=time.monotonic() - 2)
    await session.accept(message(4, observation=core.ObservationFrame(revision=2,
        observations=[envelope(MOTION, "actor.standard", Motion2DObservation())])))
    await session.accept(message(5, observation=core.ObservationFrame(revision=3)))
    with pytest.raises(ActorError, match="Transform observation is missing or stale"):
        await waypoint(session, 0, -1.4)
    pending = next(iter(session.mailbox.commands.values()))
    payload = Locomotion2DCommand()
    pending.command.payload.Unpack(payload)
    assert payload.desired_move_x == payload.desired_move_y == 0


async def test_missing_motion_cannot_prove_stopped(session):
    await session.accept(message(3, observation=core.ObservationFrame(revision=1,
        observations=[envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=0, y=-1.4))])))
    with pytest.raises(ActorError, match="Motion observation is missing or stale"):
        await hold_stopped(session, minimum_duration=0)


@pytest.mark.parametrize("velocity", [float("nan"), float("inf")])
async def test_nonfinite_motion_is_rejected_and_movement_zeroed(session, velocity):
    await session.accept(message(3, observation=core.ObservationFrame(revision=1, observations=[
        envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=0, y=-1.4)),
        envelope(MOTION, "actor.standard", Motion2DObservation(velocity_x=velocity)),
    ])))
    with pytest.raises(ActorError, match="ObservationInvalid"):
        await waypoint(session, 0, -1.4)
    queued = next(iter(session.mailbox.commands.values()))
    intent = Locomotion2DCommand()
    queued.command.payload.Unpack(intent)
    assert intent.desired_move_x == intent.desired_move_y == 0


async def test_arrival_needs_four_distinct_transform_samples(session):
    await session.accept(message(3, observation=core.ObservationFrame(revision=1, observations=[
        envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=0, y=-1.4)),
        envelope(MOTION, "actor.standard", Motion2DObservation()),
    ])))
    with pytest.raises(ActorError, match="RouteTimeout"):
        await waypoint(session, 0, -1.4, timeout=0.28)


async def test_stopped_confirmation_does_not_count_one_cached_sample_four_times(session):
    await session.accept(message(3, observation=core.ObservationFrame(revision=1, observations=[
        envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=0, y=-1.4)),
        envelope(MOTION, "actor.standard", Motion2DObservation()),
    ])))
    with pytest.raises(ActorError, match="MovementDidNotStop"):
        await hold_stopped(session, minimum_duration=0, timeout=0.28)
