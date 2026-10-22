"""Optional deterministic UI peers, never model or Human acceptance evidence.

Only the participant's ordinary DecisionContext is read. QA Human is a public
display name, not a control-mode/Alignment oracle. The server gates every Skill.
"""
from __future__ import annotations

import math
import time

from extensions.agents_arena.v1 import social_pb2 as pb
from actor_runtime.brains import FinishObjective, SkillCall
from actor_runtime.errors import ActorError
from actor_standard import TRANSFORM, Transform2DObservation
from actor_agents_arena import (SOCIAL_MATCH, SOCIAL_OWN_STATE, SOCIAL_VISIBLE_WORLD,
    SOCIAL_MEETING, SOCIAL_WAITING_PLAYERS)
from actor_agents_arena.social_fixtures import (SocialFixtureBrain, _observation,
    _witnessed, _received_claims, _known_body, _identifier, APPROACH, KILL, VOTE, SEND)


NOW = lambda: int(time.time() * 1000)
HUMAN_NAME = "QA Human"
INITIAL_DELAY_MS = 45_000
SECOND_DELAY_MS = 180_000


class NativeHumanBrain(SocialFixtureBrain):
    def __init__(self, scenario):
        if scenario not in ("witness", "victim"):
            raise ValueError("Unknown native UI fixture")
        # Reuse only ordinary waiting and Brain lifecycle, not its vote policy.
        super().__init__("vote")
        self.native_scenario = scenario

    async def start(self, objective, catalog):
        await super().start(objective, catalog)
        self._pending_second = False
        self._second_kill_completed = False

    @staticmethod
    def _human_ids(context, visible, meeting, match_id):
        candidates = {item.actor_id for item in visible.actors if item.display_name == HUMAN_NAME}
        if meeting is not None and meeting.match_id == match_id:
            candidates.update(item.actor_id for item in meeting.participants if item.display_name == HUMAN_NAME)
        waiting = _observation(context, SOCIAL_WAITING_PLAYERS, pb.SocialWaitingPlayersObservation)
        if waiting is not None and waiting.match_id == match_id and waiting.recipient_actor_id == context.actor_id:
            candidates.update(item.actor_id for item in waiting.players if item.display_name == HUMAN_NAME)
        for record in context.working_knowledge:
            content = record.get("content")
            if (record.get("kind") == "historical_observation" and record.get("capability_id") == SOCIAL_VISIBLE_WORLD
                    and record.get("source_actor_id") == context.actor_id and record.get("scope_id") == match_id
                    and record.get("expires_at_unix_ms", 0) > NOW() and isinstance(content, dict)
                    and content.get("display_name") == HUMAN_NAME):
                candidates.add(content.get("actor_id"))
        return {value for value in candidates if _identifier(value)}

    @staticmethod
    def _known_victims(context, visible, match_id):
        result = {body.victim_actor_id for body in visible.bodies}
        for record in context.working_knowledge:
            content = record.get("content")
            if (record.get("kind") == "historical_observation" and record.get("capability_id") == SOCIAL_VISIBLE_WORLD
                    and record.get("source_actor_id") == context.actor_id and record.get("scope_id") == match_id
                    and record.get("expires_at_unix_ms", 0) > NOW() and isinstance(content, dict)
                    and _identifier(content.get("body_id")) and _identifier(content.get("victim_actor_id"))):
                result.add(content["victim_actor_id"])
        return result

    async def decide(self, context):
        self.decisions += 1
        available = {item["id"] for item in context.available_skills}
        match = _observation(context, SOCIAL_MATCH, pb.SocialMatchStateObservation)
        own = _observation(context, SOCIAL_OWN_STATE, pb.SocialOwnStateObservation)
        if match is None or own is None or match.match_id != own.match_id or own.actor_id != context.actor_id:
            return self._wait(available)
        if self._scope and self._scope != match.match_id:
            raise ActorError("fixture_scope_changed", "One UI fixture episode cannot change match")
        self._scope = match.match_id
        if match.phase == pb.SOCIAL_PHASE_GAME_OVER:
            return FinishObjective(status="success", reason="Authoritative terminal state observed")
        if own.life != pb.SOCIAL_LIFE_ALIVE:
            return self._wait(available)
        if match.phase == pb.SOCIAL_PHASE_WAITING_FOR_PLAYERS:
            return self._call(available, "agents-arena.ready-for-match") if not own.ready else self._wait(available)
        if not own.assigned:
            return self._wait(available)
        previous = self._previous(context)
        if previous.get("skill_id") == KILL and previous.get("status") == "succeeded" and self._pending_second:
            self._second_kill_completed = True
        meeting = _observation(context, SOCIAL_MEETING, pb.SocialMeetingObservation)
        valid_meeting = (meeting is not None and meeting.match_id == match.match_id
            and meeting.phase_revision == match.phase_revision and bool(meeting.meeting_id))
        if match.phase == pb.SOCIAL_PHASE_DISCUSSION:
            if valid_meeting and meeting.meeting_id not in self._spoken and SEND in available:
                self._spoken.add(meeting.meeting_id)
                return SkillCall(skill_id=SEND, arguments={"channel_id": "discussion", "ttl_ms": 300000,
                    "text": "I am waiting for the discussion and will use only information delivered to my character."})
            return self._wait(available)
        if match.phase == pb.SOCIAL_PHASE_VOTING:
            if not valid_meeting or meeting.own_ballot_submitted or meeting.meeting_id in self._voted:
                return self._wait(available)
            candidate = ""
            if own.alignment == pb.SOCIAL_ALIGNMENT_CREW:
                eligible = {item.actor_id for item in meeting.participants
                    if item.life == pb.SOCIAL_LIFE_ALIVE and item.actor_id != context.actor_id}
                witnessed = _witnessed(context, match.match_id) & eligible
                claims = _received_claims(context, match.match_id, eligible) & eligible
                candidate = next(iter(sorted(witnessed or claims)), "")
            if VOTE in available:
                self._voted.add(meeting.meeting_id)
            return self._call(available, VOTE, candidate_actor_id=candidate, skip=not bool(candidate))
        # Crew leave all physical task/report/meeting controls to the native
        # operator. No task completion, sabotage, body report or emergency call.
        if match.phase != pb.SOCIAL_PHASE_EXPLORATION or own.alignment != pb.SOCIAL_ALIGNMENT_IMPOSTOR:
            return self._wait(available)
        visible = _observation(context, SOCIAL_VISIBLE_WORLD, pb.SocialVisibleWorldObservation)
        if visible is None or visible.match_id != match.match_id or visible.recipient_actor_id != context.actor_id:
            return self._wait(available)
        human_ids = self._human_ids(context, visible, meeting, match.match_id)
        victims = self._known_victims(context, visible, match.match_id)
        second = False
        if self.native_scenario == "witness":
            if self._kill_completed or visible.bodies or _known_body(context, match.match_id):
                return self._wait(available)
        else:
            # Ambiguous public names cannot identify the intended UI subject.
            if len(human_ids) != 1:
                return self._wait(available)
            if self._second_kill_completed:
                return self._wait(available)
            second = bool(human_ids & victims)
            if not second and (self._kill_completed or victims):
                return self._wait(available)
            if second and any(victim not in human_ids for victim in victims):
                return self._wait(available)
        if match.started_at_unix_ms <= 0 or match.started_at_unix_ms > NOW():
            return self._wait(available)
        ready = max(own.kill_ready_at_unix_ms, match.started_at_unix_ms + (SECOND_DELAY_MS if second else INITIAL_DELAY_MS))
        if ready > NOW():
            return self._wait(available, (ready - NOW()) / 1000)
        targets = [item for item in visible.actors if item.actor_id != context.actor_id and _identifier(item.actor_id)
            and all(math.isfinite(value) for value in (item.x, item.y))
            and (item.display_name != HUMAN_NAME if self.native_scenario == "witness" or second
                 else item.actor_id in human_ids)]
        if not targets:
            self._target = ""
            return self._wait(available)
        if self._target and any(item.actor_id == self._target for item in targets) and previous.get("skill_id") == APPROACH and previous.get("status") == "succeeded":
            target, self._target = self._target, ""
            self._pending_second = second
            return self._call(available, KILL, actor_id=target)
        transform = _observation(context, TRANSFORM, Transform2DObservation)
        def distance(item):
            return math.hypot(item.x - transform.x, item.y - transform.y - .46) if transform is not None else 0
        self._target = min(targets, key=lambda item: (distance(item), item.actor_id)).actor_id
        return self._call(available, APPROACH, actor_id=self._target)

    def report(self):
        return {"provider": "native-human-" + self.native_scenario + "-fixture",
            "decisions": self.decisions, "closed": self.closed,
            "scope": "Deterministic ordinary-game UI peers; no model or native-input acceptance claim"}


def witness_brain(**_):
    return NativeHumanBrain("witness")


def victim_brain(**_):
    return NativeHumanBrain("victim")
