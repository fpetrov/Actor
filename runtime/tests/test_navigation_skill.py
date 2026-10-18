"""Typed closed-loop game fixture plus authoritative arrival/freshness failure checks."""
import asyncio
from dataclasses import replace
import time

import pytest
from google.protobuf.any_pb2 import Any

from actor.core.v1 import actor_pb2 as core
from actor_runtime.skills import SkillRegistry, SkillScheduler
from actor_agents_arena.skills import NavigateToRoom, skill_package
from actor_standard import (TRANSFORM, MOTION, LOCOMOTION, Transform2DObservation,
                            Motion2DObservation, Locomotion2DCommand)
from actor_agents_arena import ROOM_MEMBERSHIP, ROOM_CHANGED, RoomMembershipObservation, RoomChangedEvent
from conftest import envelope, message, extension_version


async def observe(session, sequence, x=0, y=-1.4, room="main-hall", vx=0, vy=0):
    await session.accept(message(sequence, observation=core.ObservationFrame(revision=sequence,
        observations=[envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=x, y=y)),
                      envelope(MOTION, "actor.standard", Motion2DObservation(velocity_x=vx, velocity_y=vy,
                                is_moving=abs(vx) + abs(vy) > 0)),
                      envelope(ROOM_MEMBERSHIP, "agents-arena", RoomMembershipObservation(room_id=room))])))


def registry(definition=None):
    result = SkillRegistry(); result.register(definition or skill_package()[0]); return result


def zero_pending(session):
    intent = Locomotion2DCommand()
    assert next(iter(session.mailbox.commands.values())).command.payload.Unpack(intent)
    assert intent.desired_move_x == intent.desired_move_y == 0


async def test_missing_and_stale_observations_fail_before_skill_start(session):
    scheduler = SkillScheduler(session, registry())
    missing = await scheduler.run("agents-arena.navigate-to-room", {"target_room_id": "engineering"})
    assert missing.reason == "required_observation_missing" and scheduler.report()["events"] == []
    await observe(session, 3)
    key = ("actor-1", TRANSFORM)
    session.world.observations[key] = replace(session.world.observations[key], received_at=time.monotonic() - 2)
    await session.accept(message(4, observation=core.ObservationFrame(revision=4)))
    stale = await scheduler.run("agents-arena.navigate-to-room", {"target_room_id": "engineering"})
    assert stale.reason == "stale_world_state" and scheduler.report()["events"] == []
    assert session.next_command_id == 0


async def test_unknown_target_is_typed_failure_without_movement(session):
    await observe(session, 3)
    result = await SkillScheduler(session, registry()).run("agents-arena.navigate-to-room", {"target_room_id": "unknown"})
    assert result.reason == "invalid_arguments" and session.next_command_id == 0


async def test_one_cached_stopped_sample_cannot_complete_navigation(session):
    await observe(session, 3)
    definition = replace(skill_package()[0], timeout_seconds=0.25)
    result = await SkillScheduler(session, registry(definition)).run("agents-arena.navigate-to-room", {"target_room_id": "main-hall"})
    assert result.status == "timed_out"
    zero_pending(session)


async def test_new_room_samples_do_not_refresh_cached_movement_or_prove_stopped(session):
    await observe(session, 3)
    definition = replace(skill_package()[0], timeout_seconds=0.25)
    scheduler = SkillScheduler(session, registry(definition))
    task = asyncio.create_task(scheduler.run("agents-arena.navigate-to-room", {"target_room_id": "main-hall"}))
    for sequence in range(4, 10):
        await asyncio.sleep(0.03)
        await session.accept(message(sequence, observation=core.ObservationFrame(revision=sequence,
            observations=[envelope(ROOM_MEMBERSHIP, "agents-arena", RoomMembershipObservation(room_id="main-hall"))])))
    assert (await task).status == "timed_out"
    zero_pending(session)


async def test_nonfinite_movement_state_fails_preflight(session):
    await observe(session, 3, vx=float("nan"))
    result = await SkillScheduler(session, registry()).run("agents-arena.navigate-to-room", {"target_room_id": "engineering"})
    assert result.reason == "stale_world_state" and session.next_command_id == 0


async def test_midroute_stale_state_and_cancel_always_zero(session):
    await observe(session, 3, x=-1.8)
    scheduler = SkillScheduler(session, registry())
    task = asyncio.create_task(scheduler.run("agents-arena.navigate-to-room", {"target_room_id": "engineering"}))
    while session.next_command_id == 0: await asyncio.sleep(0)
    key = ("actor-1", TRANSFORM)
    session.world.observations[key] = replace(session.world.observations[key], received_at=time.monotonic() - 2)
    result = await task
    assert result.reason == "stale_world_state"
    zero_pending(session)
    await observe(session, 4, x=-1.8)
    task = asyncio.create_task(scheduler.run("agents-arena.navigate-to-room", {"target_room_id": "engineering"}))
    await asyncio.sleep(0.01)
    await scheduler.cancel()
    assert (await task).status == "cancelled"
    zero_pending(session)


async def test_navigation_round_trip_uses_real_typed_session_and_authoritative_room_events(session):
    x, y, dx, dy, room, sequence = -1.8, -1.4, 0, 0, "main-hall", 2
    commands = []
    await observe(session, 3, x=x, y=y); sequence = 3

    async def receive():
        nonlocal dx, dy
        async for outgoing in session.outgoing():
            if outgoing.WhichOneof("body") == "command":
                value = Locomotion2DCommand(); assert outgoing.command.payload.Unpack(value)
                dx, dy = value.desired_move_x, value.desired_move_y
                commands.append((dx, dy))

    async def game():
        nonlocal x, y, room, sequence
        # Faster deterministic fixture physics; production game speed/physics remain unchanged.
        while True:
            await asyncio.sleep(0.02)
            x += dx * 10.4 * 0.02; y += dy * 10.4 * 0.02
            next_room = "engineering" if y < -5.67 else "main-hall"
            if next_room != room:
                packed = Any(); packed.Pack(RoomChangedEvent(previous_room_id=room, current_room_id=next_room))
                room = next_room; sequence += 1
                await session.accept(message(sequence, event=core.GameEventEnvelope(revision=sequence,
                    capability_id=ROOM_CHANGED, extension_id="agents-arena", extension_version=extension_version("agents-arena"),
                    entity_id="actor-1", payload=packed)))
            sequence += 1
            await observe(session, sequence, x=x, y=y, room=room, vx=dx * 10.4, vy=dy * 10.4)

    tasks = [asyncio.create_task(receive()), asyncio.create_task(game())]
    scheduler = SkillScheduler(session, registry())
    try:
        outward = await asyncio.wait_for(scheduler.run("agents-arena.navigate-to-room", {"target_room_id": "engineering"}), 15)
        assert outward.status == "succeeded", outward.model_dump()
        assert outward.output["room_id"] == "engineering" and outward.output["stopped"]
        assert outward.output["room_change_confirmed"] and outward.output["waypoints_reached"] == 4
        inward = await asyncio.wait_for(scheduler.run("agents-arena.navigate-to-room", {"target_room_id": "main-hall"}), 15)
        assert inward.status == "succeeded", inward.model_dump()
        assert inward.output["room_id"] == "main-hall" and room == "main-hall"
        await asyncio.sleep(0.03)
        assert commands[-1] == (0, 0)
        assert any(value[1] < 0 for value in commands) and any(value[1] > 0 for value in commands)
        assert session.events_received == 2
        assert scheduler.report()["skill_count"] == 2
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
