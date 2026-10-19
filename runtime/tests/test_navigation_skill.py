"""Typed closed-loop game fixture plus authoritative arrival/freshness failure checks."""
import asyncio
from collections import deque
from dataclasses import replace
import time

import pytest
from google.protobuf.any_pb2 import Any

from actor.core.v1 import actor_pb2 as core
from actor_runtime.skills import SkillRegistry, SkillScheduler
from actor_agents_arena.skills import NavigateToRoom, Waypoint, skill_package
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


class ServiceNavigation(NavigateToRoom):
    def __init__(self, lane):
        self.lane = lane

    async def execute(self, context, arguments):
        target = arguments.target_room_id
        approach = Waypoint(self.lane, -7.15) if target == "engineering" else Waypoint(-1.5, -1.9)
        return await self.execute_to_approach(context, target, approach, lane_x=self.lane)


def service_registry(lane=.3, *, timeout=25):
    return registry(replace(skill_package()[0], factory=lambda: ServiceNavigation(lane), timeout_seconds=timeout))


class OccupiedAnchorsGame:
    """Typed closed-loop fixture with real capsule-shaped stationary obstacles.

    Shared room anchors are permanently occupied. A procedure that insists on
    reaching either anchor cannot complete, while the authored side lanes pass.
    Static-world clearance is separately covered by Unity capsule physics tests.
    """
    def __init__(self, session, x, y, *, transitions=True, speed=10.4, observation_delay=0):
        self.session, self.x, self.y = session, x, y
        self.room = "engineering" if y < -5.67 else "main-hall"
        self.dx = self.dy = 0
        self.sequence = 3
        self.published_room = self.room
        self.transitions = transitions
        self.speed, self.observation_delay = speed, observation_delay
        self.pending_samples = deque()
        self.commands = []
        self.trajectory = []
        self.tasks = []

    async def start(self):
        await observe(self.session, self.sequence, x=self.x, y=self.y, room=self.room)
        self.tasks = [asyncio.create_task(self.receive()), asyncio.create_task(self.game())]

    async def close(self):
        for task in self.tasks: task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    @staticmethod
    def clear(x, y):
        # Two vertical 0.28x0.81 capsules: their centerline segments are 0.53m.
        return all((x - ox) ** 2 + max(0, abs(y - oy) - .53) ** 2 >= .28 ** 2
                   for ox, oy in ((0, -1.4), (0, -7)))

    async def receive(self):
        async for outgoing in self.session.outgoing():
            if outgoing.WhichOneof("body") == "command":
                intent = Locomotion2DCommand()
                assert outgoing.command.payload.Unpack(intent)
                self.dx, self.dy = intent.desired_move_x, intent.desired_move_y
                self.commands.append((self.dx, self.dy))

    async def game(self):
        while True:
            await asyncio.sleep(.01)
            old_x, old_y = self.x, self.y
            # Speed up fixture time without bypassing the command/observation
            # loop or moving through stationary capsule obstacles.
            x, y = self.x + self.dx * self.speed * .01, self.y + self.dy * self.speed * .01
            if self.clear(x, self.y): self.x = x
            if self.clear(self.x, y): self.y = y
            self.trajectory.append((self.x, self.y))
            self.room = "engineering" if self.y < -5.67 else "main-hall"
            clock = asyncio.get_running_loop().time()
            self.pending_samples.append((clock + self.observation_delay, self.x, self.y, self.room,
                                         (self.x - old_x) / .01, (self.y - old_y) / .01))
            while self.pending_samples and self.pending_samples[0][0] <= clock:
                _, x, y, new_room, vx, vy = self.pending_samples.popleft()
                await self.publish(x, y, new_room, vx, vy)

    async def publish(self, x, y, new_room, vx, vy):
        if new_room != self.published_room:
            previous, self.published_room = self.published_room, new_room
            if self.transitions:
                self.sequence += 1
                packed = Any(); packed.Pack(RoomChangedEvent(previous_room_id=previous, current_room_id=new_room))
                await self.session.accept(message(self.sequence, event=core.GameEventEnvelope(revision=self.sequence,
                    capability_id=ROOM_CHANGED, extension_id="agents-arena", extension_version=extension_version("agents-arena"),
                    entity_id="actor-1", payload=packed)))
        self.sequence += 1
        await observe(self.session, self.sequence, x=x, y=y, room=new_room, vx=vx, vy=vy)


@pytest.mark.parametrize("lane", [-.3, .3])
async def test_service_access_bypasses_occupied_hall_and_engineering_anchors(session, lane):
    game = OccupiedAnchorsGame(session, -1.8, -1.4)
    await game.start()
    try:
        result = await asyncio.wait_for(SkillScheduler(session, service_registry(lane)).run(
            "agents-arena.navigate-to-room", {"target_room_id": "engineering"}), 15)
        assert result.status == "succeeded", result.model_dump()
        assert result.output["room_change_confirmed"] and result.output["waypoints_reached"] == 4
        assert abs(game.x - lane) < .025 and abs(game.y + 7.15) < .025
        assert all(game.clear(x, y) for x, y in game.trajectory)
        await asyncio.sleep(.02); assert game.commands[-1] == (0, 0)
    finally:
        await game.close()


async def test_sheriff_service_return_changes_lane_above_waiting_engineering_capsule(session):
    game = OccupiedAnchorsGame(session, .3, -7.15)
    await game.start()
    try:
        result = await asyncio.wait_for(SkillScheduler(session, service_registry(-.3)).run(
            "agents-arena.navigate-to-room", {"target_room_id": "main-hall"}), 15)
        assert result.status == "succeeded", result.model_dump()
        assert result.output["room_change_confirmed"] and result.output["waypoints_reached"] == 5
        assert abs(game.x + 1.5) < .025 and abs(game.y + 1.9) < .025
        assert all(game.clear(x, y) for x, y in game.trajectory)
        # Lane crossing is above the waiting capsule, not through its center.
        assert all(y > -6.2 for x, y in game.trajectory if abs(x) < .2 and y < -5.67)
    finally:
        await game.close()


async def test_service_same_room_still_reaches_its_actual_panel_approach(session):
    game = OccupiedAnchorsGame(session, .3, -6.2)
    await game.start()
    try:
        result = await asyncio.wait_for(SkillScheduler(session, service_registry()).run(
            "agents-arena.navigate-to-room", {"target_room_id": "engineering"}), 10)
        assert result.status == "succeeded", result.model_dump()
        assert result.output["waypoints_reached"] == 1 and result.output["room_change_confirmed"]
        assert abs(game.y + 7.15) < .025
        assert any(dy < 0 for _, dy in game.commands), "Existing room membership is not panel arrival"
    finally:
        await game.close()


async def test_service_cross_room_membership_without_transition_event_cannot_succeed(session):
    game = OccupiedAnchorsGame(session, -1.8, -1.4, transitions=False)
    await game.start()
    try:
        result = await asyncio.wait_for(SkillScheduler(session, service_registry()).run(
            "agents-arena.navigate-to-room", {"target_room_id": "engineering"}), 15)
        assert result.status == "failed" and result.reason == "route_failed"
        await asyncio.sleep(.02); assert game.commands[-1] == (0, 0)
    finally:
        await game.close()


async def test_service_cached_stop_and_cancel_cannot_leak_movement_or_false_arrival(session):
    await observe(session, 3, x=.3, y=-7.15, room="engineering")
    owner = SkillScheduler(session, service_registry(timeout=.25))
    result = await owner.run("agents-arena.navigate-to-room", {"target_room_id": "engineering"})
    assert result.status == "timed_out"; zero_pending(session)
    await observe(session, 4, x=-1.8)
    owner = SkillScheduler(session, service_registry())
    previous_commands = session.next_command_id
    running = asyncio.create_task(owner.run("agents-arena.navigate-to-room", {"target_room_id": "engineering"}))
    while session.next_command_id <= previous_commands: await asyncio.sleep(0)
    await owner.cancel()
    assert (await running).status == "cancelled"; zero_pending(session)


async def test_service_tight_stop_converges_with_150ms_authoritative_delay_at_actual_speed(session):
    game = OccupiedAnchorsGame(session, .3, -6.85, speed=2.6, observation_delay=.15)
    await game.start()
    try:
        result = await asyncio.wait_for(SkillScheduler(session, service_registry()).run(
            "agents-arena.navigate-to-room", {"target_room_id": "engineering"}), 8)
        assert result.status == "succeeded", result.model_dump()
        assert abs(game.x - .3) < .025 and abs(game.y + 7.15) < .025
        assert result.output["stopped"] and result.output["room_change_confirmed"]
        await asyncio.sleep(.17); assert game.commands[-1] == (0, 0)
    finally:
        await game.close()
