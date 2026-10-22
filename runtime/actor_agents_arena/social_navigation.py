"""Public, authored Social world routes. No hidden or last-seen actor tracking."""
from __future__ import annotations

import heapq
import hashlib
import json
import math
from importlib import resources
from typing import Literal

from pydantic import Field
from actor_runtime.skills import CapabilityRequirement as Req, SkillDefinition, SkillError, SkillModel
from actor_standard import TRANSFORM, MOTION, LOCOMOTION, Transform2DObservation, Motion2DObservation, Locomotion2DCommand
from . import ROOM_MEMBERSHIP, ROOM_CHANGED, RoomMembershipObservation, RoomChangedEvent, SOCIAL_MATCH, SOCIAL_OWN_STATE
from . import SocialMatchStateObservation, SocialOwnStateObservation
from .skills import NavigateToRoom, Waypoint, stopped

Room = Literal["main-hall", "engineering", "security", "storage"]


class SocialNavigateToRoomInput(SkillModel):
    target_room_id: Room = Field(description="A destination in the authored four-room Social world.")


class SocialNavigateToRoomOutput(SkillModel):
    room_id: Room
    stopped: bool
    waypoints_reached: int
    room_change_confirmed: bool
    reroutes: int = Field(default=0, ge=0, le=2)


def layout():
    # Generated from the same Unity builder that validates actual capsule casts.
    data = json.loads(resources.files(__package__).joinpath("social_layout.json").read_text())
    nodes = {node["id"]: {**node, "x": node.get("x", node.get("root_position", {}).get("x", 0)),
             "y": node.get("y", node.get("root_position", {}).get("y", 0))} for node in data["nodes"]}
    return nodes, data["edges"]


def shortest_path(nodes, edges, start, goal, *, blocked_edges=()):
    if start not in nodes or goal not in nodes:
        raise SkillError("route_failed", "Authored route endpoint is unavailable")
    blocked = {frozenset(edge) for edge in blocked_edges}
    adjacent = {key: [] for key in nodes}
    for edge in edges:
        a, b = edge["from"], edge["to"]
        if frozenset((a, b)) in blocked:
            continue
        cost = math.hypot(nodes[a]["x"] - nodes[b]["x"], nodes[a]["y"] - nodes[b]["y"])
        adjacent[a].append((b, cost)); adjacent[b].append((a, cost))
    queue, costs = [(0.0, start, (start,))], {start: 0.0}
    while queue:
        cost, node, path = heapq.heappop(queue)
        if cost != costs[node]:
            continue
        if node == goal:
            return path
        for other, distance in adjacent[node]:
            value = cost + distance
            if value < costs.get(other, math.inf):
                costs[other] = value
                heapq.heappush(queue, (value, other, (*path, other)))
    raise SkillError("route_failed", "No authored path reaches the destination")


class SocialNavigateToRoom(NavigateToRoom):
    rooms = ("main-hall", "engineering", "east-corridor", "security", "storage")

    def state(self, context):
        from extensions.agents_arena.v1 import social_pb2 as pb
        match = context.observation(SOCIAL_MATCH, SocialMatchStateObservation)
        own = context.observation(SOCIAL_OWN_STATE, SocialOwnStateObservation)
        boundary = match.match_id, match.phase_revision, own.connection_generation
        if (not match.match_id or own.match_id != match.match_id or own.actor_id != context.actor_id or
                match.phase != pb.SOCIAL_PHASE_EXPLORATION or own.life != pb.SOCIAL_LIFE_ALIVE or not own.can_move):
            raise SkillError("execution_boundary_changed", "Own movement permission changed")
        if getattr(self, "_boundary", boundary) != boundary:
            raise SkillError("execution_boundary_changed", "Own connection or phase changed during navigation")
        self._boundary = boundary
        return super().state(context)

    def preflight(self, context, arguments):
        self.state(context)
        if arguments.target_room_id not in self.rooms or arguments.target_room_id == "east-corridor":
            raise SkillError("target_not_found", "Unknown Social destination")

    async def execute(self, context, arguments):
        nodes, _ = layout()
        anchor = next(node for node in nodes.values() if node["id"] == {
            "main-hall": "hall", "engineering": "engineering", "security": "security", "storage": "storage"
        }[arguments.target_room_id])
        return await self.execute_to_approach(context, arguments.target_room_id, Waypoint(anchor["x"], anchor["y"]), lane_x=.3)

    async def execute_to_approach(self, context, target, approach, *, lane_x):
        transform, _, membership = self.state(context)
        nodes, edges = layout()
        source = membership.payload.room_id
        if target not in self.rooms or not all(math.isfinite(v) for v in (approach.x, approach.y)):
            raise SkillError("target_not_found", "Unknown Social approach")
        goals = [node for node in nodes.values() if node["room_id"] == target]
        goal = min(goals, key=lambda n: math.hypot(n["x"] - approach.x, n["y"] - approach.y))["id"]
        prior = context.events(ROOM_CHANGED, RoomChangedEvent)
        cursor = max((e.sequence for e in prior), default=0)
        blocked, reroutes, reached = set(), 0, 0
        point = lambda key: Waypoint(nodes[key]["x"], nodes[key]["y"])
        # An own-pose connector has no traversed graph edge to ban. A crowded
        # nearest node is not the actor's anchor until physically reached; try
        # bounded alternatives in the current own room before entering the graph.
        # Once anchored, never choose a new nearest node after a blockage: doing
        # that could silently place the actor on the far side of the banned edge.
        try:
            anchor = await self._initial_anchor(context, nodes)
            reached += 1
            while anchor != goal:
                path = shortest_path(nodes, edges, anchor, goal, blocked_edges=blocked)
                rerouting = False
                for other in path[1:]:
                    try:
                        await self._graph_waypoint(context, point(other))
                    except SkillError as error:
                        if error.code != "route_blocked":
                            raise
                        if reroutes >= 2:
                            raise SkillError("route_failed", "Temporary route blockage exhausted the reroute budget") from error
                        blocked.add(frozenset((anchor, other))); reroutes += 1
                        # Return along the already attempted edge to the last
                        # actually reached node before trying its alternate path.
                        try:
                            await self._graph_waypoint(context, point(anchor))
                        except SkillError as backtrack:
                            if backtrack.code == "route_blocked":
                                raise SkillError("route_failed", "The last reached navigation node is no longer accessible") from backtrack
                            raise
                        reached += 1; rerouting = True
                        break
                    anchor = other; reached += 1
                if not rerouting:
                    break
            if math.hypot(nodes[goal]["x"] - approach.x, nodes[goal]["y"] - approach.y) > .02:
                try:
                    await self._graph_waypoint(context, approach)
                except SkillError as error:
                    if error.code == "route_blocked":
                        raise SkillError("route_failed", "The physical terminal approach is temporarily blocked") from error
                    raise
                reached += 1
            await self.confirm_arrival(context, target)
            transitions = context.events(ROOM_CHANGED, RoomChangedEvent, after_sequence=cursor)
            confirmed = source == target or any(e.payload.current_room_id == target for e in transitions)
            if not confirmed:
                raise SkillError("route_failed", "Arrival lacks an authoritative room transition")
            return SocialNavigateToRoomOutput(room_id=target, stopped=True, waypoints_reached=reached,
                room_change_confirmed=True, reroutes=reroutes)
        finally:
            try:
                self.move(context)
            except SkillError as error:
                if error.code not in ("session_disconnected", "command_ownership_lost", "execution_boundary_changed"):
                    raise

    @staticmethod
    def _connector_rank(actor_id, node_id):
        # Own identity breaks authored equidistant choices and avoids symmetric
        # retry timing. It never contains an Alignment or another actor's pose.
        return int.from_bytes(hashlib.sha256((actor_id + ":" + node_id).encode()).digest()[:8], "big")

    async def _initial_anchor(self, context, nodes):
        tried = set()
        for attempt in range(3):
            transform, _, membership = self.state(context)
            x, y = transform.payload.x, transform.payload.y
            candidates = [node for node in nodes.values() if node["room_id"] == membership.payload.room_id and node["id"] not in tried]
            if not candidates:
                raise SkillError("route_failed", "Own room has no further authored connector anchors")
            # Refresh both position and authoritative room after a failed
            # connector. Distances come only from self and the public graph.
            anchor = min(candidates, key=lambda node: (round(math.hypot(node["x"] - x, node["y"] - y), 6),
                self._connector_rank(context.actor_id, node["id"])))
            tried.add(anchor["id"])
            try:
                await self._graph_waypoint(context, Waypoint(anchor["x"], anchor["y"]))
                return anchor["id"]
            except SkillError as error:
                if error.code != "route_blocked":
                    raise
                self.move(context)
                if attempt == 2:
                    raise SkillError("route_failed", "Three authored own-room connector anchors are temporarily blocked") from error
                # Stop before choosing a distinct anchor. A short deterministic
                # stagger lets contacting peers leave a shared point at different
                # times; it does not wait for or inspect their hidden positions.
                rank = self._connector_rank(context.actor_id, anchor["id"])
                pause_until = context.now() + .1 + .35 * rank / (2**64 - 1)
                while context.now() < pause_until:
                    self.state(context)
                    self.move(context)
                    remaining = pause_until - context.now()
                    if remaining <= 0:
                        break
                    await context.sleep(min(.05, remaining))

    async def _graph_waypoint(self, context, waypoint, timeout_seconds=18, *, arrival_radius=.06):
        """Own fresh physical stagnation can block an edge, never locate an actor."""
        deadline = context.now() + timeout_seconds
        distinct_stops, last_pair = 0, None
        best_distance, progress_at, distinct_since_progress = math.inf, context.now(), 0
        while context.now() < deadline:
            transform, motion, _ = self.state(context)
            dx, dy = waypoint.x - transform.payload.x, waypoint.y - transform.payload.y
            distance = math.hypot(dx, dy)
            pair = (transform.sequence, motion.sequence)
            fresh_sample = pair != last_pair
            if distance < arrival_radius:
                self.move(context)
                if not stopped(motion.payload):
                    distinct_stops = 0
                elif fresh_sample:
                    distinct_stops += 1
                if distinct_stops >= 4:
                    return
            else:
                distinct_stops = 0
                if distance < best_distance - .03:
                    best_distance, progress_at, distinct_since_progress = distance, context.now(), 0
                elif fresh_sample:
                    distinct_since_progress += 1
                if context.now() - progress_at >= 2 and distinct_since_progress >= 2:
                    self.move(context)
                    raise SkillError("route_blocked", "Fresh own movement made no progress along an authored edge")
                strength = min(1.0, max(.06, distance))
                self.move(context, dx / distance * strength, dy / distance * strength)
            last_pair = pair
            await context.sleep(.05)
        raise SkillError("route_failed", "Authored navigation waypoint exceeded its bounded deadline")


def available(context):
    from extensions.agents_arena.v1 import social_pb2 as pb
    match = context.observation(SOCIAL_MATCH, SocialMatchStateObservation)
    own = context.observation(SOCIAL_OWN_STATE, SocialOwnStateObservation)
    return (match.phase == pb.SOCIAL_PHASE_EXPLORATION and own.life == pb.SOCIAL_LIFE_ALIVE and own.can_move)


def skill_package():
    return (SkillDefinition(skill_id="agents-arena.navigate-social-room", version="1.0.0",
        description="Navigate through the authored four-room Social world and alternate corridor; confirm physical arrival and stop.",
        input_model=SocialNavigateToRoomInput, result_model=SocialNavigateToRoomOutput, factory=SocialNavigateToRoom,
        required_capabilities=(Req(TRANSFORM, Transform2DObservation, "observation", 1), Req(MOTION, Motion2DObservation, "observation", 1),
            Req(LOCOMOTION, Locomotion2DCommand, "command"), Req(ROOM_MEMBERSHIP, RoomMembershipObservation, "observation", 1),
            Req(ROOM_CHANGED, RoomChangedEvent, "event"), Req(SOCIAL_MATCH, SocialMatchStateObservation, "observation", 3),
            Req(SOCIAL_OWN_STATE, SocialOwnStateObservation, "observation", 3)),
        owned_command_capabilities=(LOCOMOTION,), timeout_seconds=120, cleanup_timeout_seconds=1, availability=available),)
