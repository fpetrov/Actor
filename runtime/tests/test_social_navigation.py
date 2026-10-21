"""Closed-loop own-pose navigation over the authored Social graph.

Temporary obstacles use the existing player capsule dimensions. This is a
deterministic game-policy fixture; real Unity static geometry is checked by its
separate physics verification, not replaced by this simulation.
"""
import asyncio
import math
from types import SimpleNamespace

import pytest

from actor_runtime.skills import SkillError
from actor_standard import TRANSFORM, MOTION, LOCOMOTION, Transform2DObservation, Motion2DObservation
from actor_agents_arena import ROOM_MEMBERSHIP, ROOM_CHANGED, RoomMembershipObservation, RoomChangedEvent
from actor_agents_arena.skills import Waypoint
from actor_agents_arena import social_navigation as navigation


class PhysicalContext:
    actor_id = "own-actor"
    def __init__(self, x=0, y=-1.4, *, obstacles=(), transitions=True, cached=False):
        self.x, self.y, self.at, self.sequence = x, y, 0., 1
        self.dx = self.dy = self.vx = self.vy = 0.
        self.obstacles = tuple(obstacles)
        self.room = self.resolve(x, y)
        self.transitions, self.cached = transitions, cached
        self.commands, self.trajectory, self.changed = [], [], []

    @staticmethod
    def resolve(x, y):
        if x > 4 and y > 2:
            return "security"
        if x > 4 and y < -5.7:
            return "storage"
        if x < 2 and y < -5.67:
            return "engineering"
        if x > 3.4 or x > .65 and y < -3.2:
            return "east-corridor"
        return "main-hall"

    def now(self):
        return self.at

    def observation_entry(self, cap, cls):
        if cap == TRANSFORM:
            payload = Transform2DObservation(x=self.x, y=self.y)
        elif cap == MOTION:
            payload = Motion2DObservation(velocity_x=self.vx, velocity_y=self.vy, is_moving=math.hypot(self.vx, self.vy) > .05)
        elif cap == ROOM_MEMBERSHIP:
            payload = RoomMembershipObservation(room_id=self.room)
        else:
            raise AssertionError(cap)
        return SimpleNamespace(payload=payload, sequence=self.sequence)

    def command(self, cap, value, **kwargs):
        assert cap == LOCOMOTION
        self.dx, self.dy = value.desired_move_x, value.desired_move_y
        self.commands.append((self.dx, self.dy))

    def events(self, cap, cls, *, after_sequence=0):
        assert cap == ROOM_CHANGED and cls is RoomChangedEvent
        return [event for event in self.changed if event.sequence > after_sequence]

    def clear(self, x, y):
        # Two 0.28x0.81m vertical capsules: relative centerline length .53m.
        return all((x - ox) ** 2 + max(0, abs(y - oy) - .53) ** 2 >= .28 ** 2 for ox, oy in self.obstacles)

    async def sleep(self, dt):
        old_x, old_y = self.x, self.y
        next_x, next_y = old_x + self.dx * 2.6 * dt, old_y + self.dy * 2.6 * dt
        if self.clear(next_x, old_y):
            self.x = next_x
        if self.clear(self.x, next_y):
            self.y = next_y
        self.vx, self.vy = (self.x - old_x) / dt, (self.y - old_y) / dt
        self.at += dt
        if not self.cached:
            self.sequence += 1
        next_room = self.resolve(self.x, self.y)
        if next_room != self.room:
            if self.transitions:
                self.changed.append(SimpleNamespace(sequence=self.sequence, payload=RoomChangedEvent(previous_room_id=self.room, current_room_id=next_room)))
            self.room = next_room
        self.trajectory.append((self.x, self.y))
        await asyncio.sleep(0)


class TracedNavigation(navigation.SocialNavigateToRoom):
    def __init__(self):
        self.attempts = []

    async def _graph_waypoint(self, context, point, *args, **kwargs):
        self.attempts.append((point.x, point.y))
        return await super()._graph_waypoint(context, point, *args, **kwargs)


class ContactContext(PhysicalContext):
    """Capsule contact with tangential sliding and optional live peers.

    The actual saved contacts have 5mm solver overlap. A move may not deepen
    that overlap, but can slide tangent or separate, as the collision constraint
    requires. No position is ever snapped to a waypoint or through an obstacle.
    """
    def __init__(self, *args, actor="own-actor", **kwargs):
        super().__init__(*args, **kwargs)
        self.actor_id = actor
        self.peers = []

    @staticmethod
    def capsule_distance(x, y, ox, oy):
        return math.hypot(x - ox, max(0, abs(y - oy) - .53))

    def clear(self, x, y):
        for ox, oy in (*self.obstacles, *((peer.x, peer.y) for peer in self.peers)):
            before = self.capsule_distance(self.x, self.y, ox, oy)
            after = self.capsule_distance(x, y, ox, oy)
            if after < .28 - 1e-8 and after < before - 1e-8:
                return False
        return True


def node_point(key):
    nodes, _ = navigation.layout()
    return nodes[key]["x"], nodes[key]["y"]


@pytest.mark.parametrize("reverse", [False, True])
async def test_blocked_hall_stem_uses_actual_public_east_loop_after_backtracking(reverse):
    start = node_point("engineering" if reverse else "hall")
    target = "main-hall" if reverse else "engineering"
    ctx = PhysicalContext(*start, obstacles=[(0, -3.1)])
    skill = TracedNavigation()
    result = await skill.execute(ctx, navigation.SocialNavigateToRoomInput(target_room_id=target))
    assert result.room_id == target and result.room_change_confirmed and result.reroutes == 1
    assert ctx.room == target and ctx.commands[-1] == (0, 0)
    assert node_point("east-hub") in skill.attempts and node_point("loop-return") in skill.attempts
    # A physical detour really happened; the actor did not jump a banned edge.
    assert any(x > 6 for x, _ in ctx.trajectory)
    assert all(ctx.clear(x, y) for x, y in ctx.trajectory)
    assert any(event.payload.current_room_id == "east-corridor" for event in ctx.changed)


async def test_single_engineering_branch_cannot_invent_a_route_past_a_blockage():
    ctx = PhysicalContext(*node_point("engineering"), obstacles=[(0, -6.5)])
    skill = TracedNavigation()
    with pytest.raises(SkillError) as error:
        await skill.execute(ctx, navigation.SocialNavigateToRoomInput(target_room_id="main-hall"))
    assert error.value.code == "route_failed" and ctx.commands[-1] == (0, 0)
    assert ctx.at < 15 and ctx.room == "engineering"
    assert skill.attempts[-1] == node_point("engineering")
    assert not any(x > 3 for x, _ in ctx.trajectory)


async def test_blocked_edge_is_bidirectional_and_cannot_be_reintroduced_by_nearest_node():
    nodes, edges = navigation.layout()
    blocked = [("hall", "hall-stem")]
    outward = navigation.shortest_path(nodes, edges, "hall", "hall-stem", blocked_edges=blocked)
    inward = navigation.shortest_path(nodes, edges, "hall-stem", "hall", blocked_edges=blocked)
    assert len(outward) > 2 and len(inward) > 2
    assert "east-hub" in outward and "east-hub" in inward


async def test_two_blocked_alternatives_fail_instead_of_repeating_the_same_edge():
    ctx = PhysicalContext(*node_point("hall"), obstacles=[(0, -3.1), (1.4, -1.0)])
    skill = TracedNavigation()
    with pytest.raises(SkillError) as error:
        await skill.execute(ctx, navigation.SocialNavigateToRoomInput(target_room_id="engineering"))
    assert error.value.code == "route_failed" and ctx.commands[-1] == (0, 0)
    assert skill.attempts.count(node_point("hall-stem")) == 1
    assert skill.attempts.count(node_point("hall-east")) == 1
    assert ctx.at < 25


async def test_maximum_two_reroutes_bounds_three_distinct_blocked_alternatives(monkeypatch):
    nodes = {
        "a": {"id": "a", "room_id": "main-hall", "x": 0, "y": 0},
        "left": {"id": "left", "room_id": "east-corridor", "x": -2, "y": 3},
        "right": {"id": "right", "room_id": "east-corridor", "x": 2, "y": 3},
        "goal": {"id": "goal", "room_id": "engineering", "x": 0, "y": 6},
    }
    edges = [{"from": a, "to": b} for a, b in [("a", "goal"), ("a", "left"), ("left", "goal"), ("a", "right"), ("right", "goal")]]
    monkeypatch.setattr(navigation, "layout", lambda: (nodes, edges))
    ctx = PhysicalContext(0, 0)
    # One closed physical crossing obstructs all three authored alternatives.
    ctx.clear = lambda x, y: y < .65
    with pytest.raises(SkillError) as error:
        await TracedNavigation().execute_to_approach(ctx, "engineering", Waypoint(0, 6), lane_x=.3)
    assert error.value.code == "route_failed" and "reroute budget" in error.value.message
    assert ctx.commands[-1] == (0, 0) and ctx.at < 30


async def test_authoritative_arrival_requires_room_transition_event_even_after_detour():
    ctx = PhysicalContext(*node_point("hall"), obstacles=[(0, -3.1)], transitions=False)
    with pytest.raises(SkillError) as error:
        await TracedNavigation().execute(ctx, navigation.SocialNavigateToRoomInput(target_room_id="engineering"))
    assert ctx.room == "engineering" and error.value.code == "route_failed"
    assert "transition" in error.value.message and ctx.commands[-1] == (0, 0)


async def test_cached_stop_sample_cannot_satisfy_anchor_arrival():
    ctx = PhysicalContext(*node_point("hall"), cached=True)
    with pytest.raises(SkillError) as error:
        await TracedNavigation().execute(ctx, navigation.SocialNavigateToRoomInput(target_room_id="main-hall"))
    assert error.value.code == "route_failed" and ctx.at >= 18 and ctx.commands[-1] == (0, 0)


async def test_nonfinite_own_pose_fails_before_any_nonzero_intent():
    ctx = PhysicalContext(float("nan"), -1.4)
    with pytest.raises(SkillError) as error:
        await TracedNavigation().execute(ctx, navigation.SocialNavigateToRoomInput(target_room_id="engineering"))
    assert error.value.code == "stale_world_state"
    assert not any(x or y for x, y in ctx.commands)


async def test_cancellation_during_blocked_travel_clears_intent():
    ctx = PhysicalContext(*node_point("hall"), obstacles=[(0, -3.1)])
    pending = asyncio.create_task(TracedNavigation().execute(ctx, navigation.SocialNavigateToRoomInput(target_room_id="engineering")))
    while not any(x or y for x, y in ctx.commands):
        await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert ctx.commands[-1] == (0, 0)


async def test_same_room_service_approach_is_reached_and_stopped():
    ctx = PhysicalContext(*node_point("hall"))
    result = await TracedNavigation().execute_to_approach(ctx, "main-hall", Waypoint(1.5, -1.0), lane_x=.3)
    assert result.reroutes == 0 and result.room_change_confirmed
    assert math.hypot(ctx.x - 1.5, ctx.y + 1.0) < .06 and ctx.commands[-1] == (0, 0)


@pytest.mark.parametrize("reverse", [False, True])
async def test_initial_meeting_anchor_contact_tries_distinct_own_room_nodes_without_edge_ban(monkeypatch, reverse):
    x, blocker = (-1.6375, -1.3625) if not reverse else (-1.3625, -1.6375)
    ctx = ContactContext(x, -1.9, obstacles=[(blocker, -1.9)])
    skill = TracedNavigation()
    path_calls = []
    original = navigation.shortest_path
    def path(*args, **kwargs):
        path_calls.append((args[2], tuple(kwargs.get("blocked_edges", ()))))
        return original(*args, **kwargs)
    monkeypatch.setattr(navigation, "shortest_path", path)
    result = await skill.execute(ctx, navigation.SocialNavigateToRoomInput(target_room_id="security"))
    assert result.room_id == "security" and result.room_change_confirmed and result.reroutes == 0
    assert skill.attempts[0] == node_point("meeting-main-hall")
    assert node_point("hall") in skill.attempts
    assert path_calls and path_calls[0][0] != "meeting-main-hall" and not path_calls[0][1]
    assert ctx.room == "security" and ctx.commands[-1] == (0, 0)
    assert ctx.at < 35
    # Contact depth never increases: no teleport or solver-overlap exploitation.
    assert all(ctx.capsule_distance(px, py, blocker, -1.9) >= .275 - 1e-6 for px, py in ctx.trajectory)


async def test_two_live_contacting_crew_escape_initial_anchor_in_closed_loop():
    left = ContactContext(-1.6375, -1.9, actor="crew-left")
    right = ContactContext(-1.3625, -1.9, actor="crew-right")
    left.peers = [right]; right.peers = [left]
    skills = [TracedNavigation(), TracedNavigation()]
    # Different actual service poses let both complete without requiring two
    # capsules to occupy the identical final room-center anchor simultaneously.
    outcomes = await asyncio.gather(
        skills[0].execute_to_approach(left, "security", Waypoint(5.8, 3.31), lane_x=.3),
        skills[1].execute_to_approach(right, "security", Waypoint(6.9, 3.31), lane_x=.3))
    assert all(outcome.room_change_confirmed for outcome in outcomes)
    assert left.room == right.room == "security"
    assert left.commands[-1] == right.commands[-1] == (0, 0)
    assert left.at < 40 and right.at < 40
    assert all(skill.attempts[0] == node_point("meeting-main-hall") for skill in skills)
    assert left.capsule_distance(left.x, left.y, right.x, right.y) >= .28 - 1e-6


async def test_initial_connector_budget_is_three_physically_attempted_anchors_without_false_graph_progress(monkeypatch):
    nodes = {key: {"id": key, "room_id": "main-hall", "x": x, "y": y}
        for key, x, y in (("a", 0, 1), ("b", 1, 0), ("c", -1, 0), ("d", 0, -1))}
    nodes["goal"] = {"id": "goal", "room_id": "security", "x": 6, "y": 3}
    monkeypatch.setattr(navigation, "layout", lambda: (nodes, []))
    def forbidden_path(*args, **kwargs):
        raise AssertionError("No initial anchor has actually been reached")
    monkeypatch.setattr(navigation, "shortest_path", forbidden_path)
    ctx = ContactContext(0, 0, obstacles=[(node["x"], node["y"]) for node in nodes.values() if node["room_id"] == "main-hall"])
    skill = TracedNavigation()
    with pytest.raises(SkillError) as failure:
        await skill.execute_to_approach(ctx, "security", Waypoint(6, 3), lane_x=.3)
    assert failure.value.code == "route_failed" and "Three authored" in failure.value.message
    assert len(skill.attempts) == len(set(skill.attempts)) == 3
    assert ctx.commands[-1] == (0, 0) and ctx.at < 15


async def test_initial_connector_identity_stagger_is_deterministic_and_cancellation_stops():
    class TimedNavigation(TracedNavigation):
        def __init__(self):
            super().__init__(); self.times = []
        async def _graph_waypoint(self, context, point, *args, **kwargs):
            self.times.append(context.now())
            return await super()._graph_waypoint(context, point, *args, **kwargs)
    starts = []
    for actor in ("crew-left", "crew-right", "crew-left"):
        ctx = ContactContext(-1.3625, -1.9, actor=actor, obstacles=[(-1.6375, -1.9)])
        skill = TimedNavigation()
        await skill.execute(ctx, navigation.SocialNavigateToRoomInput(target_room_id="security"))
        starts.append(skill.times[1])
    assert starts[0] == starts[2] and starts[0] != starts[1]
    ctx = ContactContext(-1.3625, -1.9, obstacles=[(-1.6375, -1.9)])
    task = asyncio.create_task(TracedNavigation().execute(ctx, navigation.SocialNavigateToRoomInput(target_room_id="security")))
    while ctx.at < 2.05:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ctx.commands[-1] == (0, 0)
