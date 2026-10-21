"""Optional fixture plugin: permitted public terminal navigation, no probe IPC."""
from __future__ import annotations

import math
import time
from typing import Literal

from google.protobuf.json_format import ParseDict, ParseError
from extensions.agents_arena.v1 import social_pb2 as pb
from actor_runtime.brains import SkillCall, FinishObjective
from actor_runtime.errors import ActorError
from actor_runtime.extensions import type_url
from actor_runtime.skills import SkillModel, SkillDefinition, CapabilityRequirement as Req, SkillError
from actor_agents_arena import SOCIAL_MATCH, SOCIAL_OWN_STATE, SOCIAL_TASKS, SOCIAL_VISIBLE_WORLD, SOCIAL_MEETING
from actor_agents_arena.social_agent import SocialMatchPolicy
from actor_agents_arena.social_navigation import SocialNavigateToRoom, SocialNavigateToRoomOutput, available
from actor_agents_arena.social_skills import BASE, MOVEMENT
from actor_agents_arena.skills import Waypoint


APPROACH = "social-protocol-fixture.approach-terminal"
WAIT = "actor.wait"
NOW = time.monotonic


def observation(context, capability, cls):
    for row in sorted(context.observations, key=lambda row: row.sequence, reverse=True):
        if (row.capability_id != capability or row.entity_id != context.actor_id or row.type_url != type_url(cls)
                or row.opaque or row.truncated or row.expired or row.stale or row.age_seconds > 3 or not isinstance(row.value, dict)):
            continue
        try:
            return ParseDict(row.value, cls())
        except (ParseError, ValueError, TypeError, OverflowError):
            continue
    return None


class ApproachInput(SkillModel):
    target: Literal["main-task", "meeting"]


class ApproachTerminal(SocialNavigateToRoom):
    def preflight(self, context, arguments):
        match = context.observation(SOCIAL_MATCH, pb.SocialMatchStateObservation)
        own = context.observation(SOCIAL_OWN_STATE, pb.SocialOwnStateObservation)
        if match.match_id != own.match_id or own.actor_id != context.actor_id or not available(context):
            raise SkillError("fixture_state_unavailable", "A coherent permitted own Exploration state is required")
        self.state(context)

    async def execute(self, context, arguments):
        match = context.observation(SOCIAL_MATCH, pb.SocialMatchStateObservation)
        if arguments.target == "main-task":
            tasks = context.observation(SOCIAL_TASKS, pb.SocialOwnTasksObservation)
            if tasks.match_id != match.match_id or tasks.recipient_actor_id != context.actor_id:
                raise SkillError("fixture_task_unavailable", "Own task projection is not coherent")
            task = next((task for task in tasks.tasks if task.task_id == "main-panel" and task.room_id == "main-hall" and not task.completed), None)
            if task is None:
                raise SkillError("fixture_task_unavailable", "Own unfinished public Main Hall task is required")
            point = Waypoint(task.approach_x, task.approach_y)
        else:
            world = context.observation(SOCIAL_VISIBLE_WORLD, pb.SocialVisibleWorldObservation)
            terminal = next((terminal for terminal in world.interactables if terminal.kind == "meeting" and terminal.room_id == "main-hall"), None)
            if world.match_id != match.match_id or world.recipient_actor_id != context.actor_id or terminal is None:
                raise SkillError("fixture_terminal_unavailable", "The meeting terminal must be currently permitted")
            point = Waypoint(terminal.approach_x, terminal.approach_y)
            transform, _, membership = self.state(context)
            if membership.payload.room_id != "main-hall":
                raise SkillError("fixture_room_unavailable", "This fixture's public staging is inside Main Hall")
            # The ordinary waiting participants occupy the authored spawn row
            # at root Y=-1.4. This public lower lane has >capsule-height spacing;
            # no current or hidden foreign pose is inspected to choose it.
            for waypoint in (Waypoint(transform.payload.x, -2.3), Waypoint(point.x, -2.3)):
                await self._graph_waypoint(context, waypoint)
        if not all(math.isfinite(value) for value in (point.x, point.y)):
            raise SkillError("fixture_terminal_unavailable", "A finite permitted approach is required")
        return await self.execute_to_approach(context, "main-hall", point, lane_x=.3)


def skill_package():
    return (SkillDefinition(APPROACH, "0.1.0", "Optional validation fixture: approach an own public task or currently visible meeting terminal without starting work or voting.",
        ApproachInput, SocialNavigateToRoomOutput, ApproachTerminal,
        BASE + MOVEMENT + (Req(SOCIAL_TASKS, pb.SocialOwnTasksObservation, "observation", 3),
                           Req(SOCIAL_VISIBLE_WORLD, pb.SocialVisibleWorldObservation, "observation", 1)),
        owned_command_capabilities=("actor.standard.locomotion2d",), timeout_seconds=45,
        cleanup_timeout_seconds=1, result_policy="session_only", availability=available),)


class ProtocolMatchPolicy(SocialMatchPolicy):
    def skill_available(self, session, definition):
        if definition.skill_id != APPROACH:
            return super().skill_available(session, definition)
        match = session.world.latest(pb.SocialMatchStateObservation, capability_id=SOCIAL_MATCH)
        own = session.world.latest(pb.SocialOwnStateObservation, capability_id=SOCIAL_OWN_STATE)
        return (match is not None and own is not None and match.match_id == own.match_id
                and own.actor_id == session.world.self_actor_id and match.phase == pb.SOCIAL_PHASE_EXPLORATION
                and own.life == pb.SOCIAL_LIFE_ALIVE and own.can_move)


class ProtocolBrain:
    async def start(self, objective, catalog):
        self.decisions = 0; self.closed = False
        self.ready_after = NOW() + 8
        self.stage = "task"; self.pending = ""; self.park_until = None

    @staticmethod
    def wait(seconds=10.0):
        return SkillCall(skill_id=WAIT, arguments={"duration_seconds": float(max(.1, min(15, seconds)))})

    async def decide(self, context):
        self.decisions += 1
        match = observation(context, SOCIAL_MATCH, pb.SocialMatchStateObservation)
        own = observation(context, SOCIAL_OWN_STATE, pb.SocialOwnStateObservation)
        if match is None or own is None or match.match_id != own.match_id or own.actor_id != context.actor_id:
            return self.wait(1)
        if match.phase == pb.SOCIAL_PHASE_GAME_OVER:
            return FinishObjective(status="success", reason="Authoritative terminal state observed")
        if own.life != pb.SOCIAL_LIFE_ALIVE:
            return self.wait()
        if match.phase == pb.SOCIAL_PHASE_WAITING_FOR_PLAYERS:
            if NOW() < self.ready_after:
                return self.wait(self.ready_after - NOW())
            return self.wait() if own.ready else SkillCall(skill_id="agents-arena.ready-for-match", arguments={})
        if match.phase != pb.SOCIAL_PHASE_EXPLORATION:
            return self.wait()
        meeting = observation(context, SOCIAL_MEETING, pb.SocialMeetingObservation)
        # A new provider is created for each phase episode. The legitimate
        # retained meeting projection prevents repeating this fixture sequence.
        if meeting is not None and meeting.meeting_id:
            return self.wait()
        previous = context.previous_skill_result or {}
        if self.pending and previous.get("skill_id") == self.pending:
            if previous.get("status") != "succeeded":
                raise ActorError("protocol_fixture_action_failed", "A permitted fixture procedure failed")
            if self.stage == "task":
                self.stage = "park"; self.park_until = NOW() + 12
            elif self.stage == "meeting-approach":
                self.stage = "call"
            elif self.stage == "call":
                self.stage = "done"
            self.pending = ""
        if self.stage == "task":
            self.pending = APPROACH
            return SkillCall(skill_id=APPROACH, arguments={"target": "main-task"})
        if self.stage == "park":
            if NOW() < self.park_until:
                return self.wait(self.park_until - NOW())
            self.stage = "meeting-approach"
        if self.stage == "meeting-approach":
            self.pending = APPROACH
            return SkillCall(skill_id=APPROACH, arguments={"target": "meeting"})
        if self.stage == "call":
            self.pending = "agents-arena.call-meeting"
            return SkillCall(skill_id=self.pending, arguments={})
        return self.wait()

    async def interrupt(self):
        pass

    async def interrupt_decision(self):
        pass

    async def close(self):
        self.closed = True

    def report(self):
        return {"provider": "social-protocol-fixture", "decisions": self.decisions, "closed": self.closed}


def brain_factory(**_):
    return ProtocolBrain()
