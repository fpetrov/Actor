"""Deterministic actual-game acceptance policies, not model-intelligence evidence.

All participants in a scenario use the same factory and Objective. Decisions use
only that participant's typed permitted context and the public authored layout.
There is no transport client, supervisor channel, hidden roster or forced result.
"""
from __future__ import annotations

import hashlib
from importlib import resources
import json
import math
import time

from google.protobuf.json_format import ParseDict, ParseError
from extensions.agents_arena.v1 import social_pb2 as pb
from actor_runtime.brains import FinishObjective, SkillCall
from actor_runtime.errors import ActorError
from actor_runtime.extensions import type_url
from actor_standard import TRANSFORM, Transform2DObservation
from actor_standard_communication import MESSAGE_RECEIVED, MessageReceivedEvent
from . import (ROOM_MEMBERSHIP, RoomMembershipObservation, SOCIAL_MATCH, SOCIAL_OWN_STATE,
    SOCIAL_VISIBLE_WORLD, SOCIAL_MEETING, SOCIAL_TASKS, SOCIAL_EMERGENCY, SOCIAL_VISIBLE_ATTACK)


NOW = lambda: int(time.time() * 1000)
# This public fixture delay gives independently navigating distant Crew time
# to establish an actual out-of-sight interval. It is not a visibility oracle:
# strict acceptance still requires the real attack timestamp inside that interval.
PRIVACY_INITIAL_DELAY_MS = 100_000
WAIT = "agents-arena.wait-for-match-event"
NAVIGATE = "agents-arena.navigate-social-room"
APPROACH = "agents-arena.approach-visible-actor"
KILL = "agents-arena.kill-visible-actor"
PERFORM = "agents-arena.perform-task"
VOTE = "agents-arena.cast-vote"
SEND = "actor.send-message"


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 128 and all(c.isalnum() or c in "-_." for c in value)


def _decode(record, capability, cls, actor, *, current=True):
    if (getattr(record, "capability_id", None) != capability or getattr(record, "entity_id", None) != actor
            or getattr(record, "type_url", None) != type_url(cls) or getattr(record, "opaque", False)
            or getattr(record, "truncated", False) or getattr(record, "expired", False)
            or current and (getattr(record, "stale", False) or getattr(record, "age_seconds", math.inf) > 3)
            or not isinstance(getattr(record, "value", None), dict)):
        return None
    try:
        return ParseDict(record.value, cls())
    except (ParseError, ValueError, TypeError, OverflowError):
        return None


def _observation(context, capability, cls):
    for record in sorted(context.observations, key=lambda item: item.sequence, reverse=True):
        value = _decode(record, capability, cls, context.actor_id)
        if value is not None:
            return value
    return None


def _events(context, capability, cls):
    for record in (*context.trigger_events, *context.recent_events):
        value = _decode(record, capability, cls, context.actor_id, current=False)
        if value is not None:
            yield value


def _witnessed(context, match_id):
    result = set()
    for event in _events(context, SOCIAL_VISIBLE_ATTACK, pb.SocialVisibleAttackEvent):
        if event.match_id == match_id and event.observer_actor_id == context.actor_id and _identifier(event.attacker_actor_id):
            result.add(event.attacker_actor_id)
    for record in context.working_knowledge:
        if (record.get("kind") != "historical_observation" or record.get("capability_id") != SOCIAL_VISIBLE_ATTACK
                or record.get("source_actor_id") != context.actor_id or record.get("scope_id") != match_id
                or record.get("expires_at_unix_ms", 0) <= NOW() or not isinstance(record.get("content"), dict)):
            continue
        try:
            event = ParseDict(record["content"], pb.SocialVisibleAttackEvent())
        except (ParseError, ValueError, TypeError, OverflowError):
            continue
        if event.match_id == match_id and event.observer_actor_id == context.actor_id and _identifier(event.attacker_actor_id):
            result.add(event.attacker_actor_id)
    return result


def _claim(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > 512:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return (value["actor_id"] if isinstance(value, dict) and set(value) == {"claim", "actor_id"}
        and value["claim"] == "witnessed_attack" and _identifier(value["actor_id"]) else None)


def _received_claims(context, match_id, senders):
    """A delivered player's claim stays a claim, never a verified Alignment."""
    result = set()
    for event in _events(context, MESSAGE_RECEIVED, MessageReceivedEvent):
        if (event.match_id == match_id and event.recipient_actor_id == context.actor_id and event.sender_actor_id in senders
                and event.channel_id == "discussion" and event.expires_at_unix_ms > NOW()):
            candidate = _claim(event.text)
            if candidate:
                result.add(candidate)
    for record in context.working_knowledge:
        content = record.get("content")
        if (record.get("kind") != "player_claim" or record.get("capability_id") != MESSAGE_RECEIVED
                or record.get("scope_id") != match_id or record.get("source_actor_id") not in senders
                or record.get("expires_at_unix_ms", 0) <= NOW() or not isinstance(content, dict)
                or content.get("unverified_player_claim") is not True or content.get("channel_id") != "discussion"
                or content.get("recipient_actor_id") != context.actor_id):
            continue
        candidate = _claim(content.get("text"))
        if candidate:
            result.add(candidate)
    return result


def _known_body(context, match_id):
    return any(record.get("kind") == "historical_observation" and record.get("capability_id") == SOCIAL_VISIBLE_WORLD
        and record.get("scope_id") == match_id and record.get("source_actor_id") == context.actor_id
        and record.get("expires_at_unix_ms", 0) > NOW() and isinstance(record.get("content"), dict)
        and _identifier(record["content"].get("body_id")) for record in context.working_knowledge)


def _life_support_room():
    # Static package data is public map geometry, never a file written by a
    # supervisor or an observation of another participant's current position.
    document = json.loads(resources.files(__package__).joinpath("social_layout.json").read_text())
    return next(terminal["room_id"] for terminal in document["terminals"] if terminal["kind"] == "life-support")


class SocialFixtureBrain:
    def __init__(self, scenario):
        if scenario not in ("vote", "parity", "sabotage", "tasks", "privacy"):
            raise ValueError("Unknown deterministic social fixture")
        self.scenario = scenario

    async def start(self, objective, catalog):
        self.decisions = 0
        self.closed = False
        self._scope = ""
        self._target = ""
        self._kill_completed = False
        self._spoken = set()
        self._voted = set()
        self._completed_tasks = set()
        self._pending_task = ""
        self._task_rotation = 0
        self._initial_x = None

    @staticmethod
    def _wait(available, seconds=15.0):
        seconds = float(min(15, max(.1, seconds)))
        if WAIT in available:
            return SkillCall(skill_id=WAIT, arguments={"timeout_seconds": seconds})
        if "actor.wait" in available:
            return SkillCall(skill_id="actor.wait", arguments={"duration_seconds": seconds})
        raise ActorError("fixture_inactive", "No permitted waiting Skill is currently available")

    def _call(self, available, skill, **arguments):
        return SkillCall(skill_id=skill, arguments=arguments) if skill in available else self._wait(available)

    def _previous(self, context):
        previous = context.previous_skill_result or {}
        if previous.get("skill_id") == KILL and previous.get("status") == "succeeded":
            self._kill_completed = True
        if self._pending_task and previous.get("skill_id") == PERFORM and previous.get("status") in ("succeeded", "failed", "cancelled"):
            output = previous.get("output")
            if (previous.get("status") == "succeeded" and isinstance(output, dict)
                    and output.get("accepted") is True and output.get("completed") is True):
                self._completed_tasks.add(self._pending_task)
            else:
                self._task_rotation += 1
            self._pending_task = ""
        return previous

    async def decide(self, context):
        self.decisions += 1
        available = {item["id"] for item in context.available_skills}
        match = _observation(context, SOCIAL_MATCH, pb.SocialMatchStateObservation)
        own = _observation(context, SOCIAL_OWN_STATE, pb.SocialOwnStateObservation)
        if match is None or own is None or match.match_id != own.match_id or own.actor_id != context.actor_id:
            return self._wait(available)
        if self._scope and self._scope != match.match_id:
            raise ActorError("fixture_scope_changed", "One fixture episode cannot change its match identity")
        self._scope = match.match_id
        if match.phase == pb.SOCIAL_PHASE_GAME_OVER:
            return FinishObjective(status="success", reason="Authoritative match terminal state observed")
        if own.life != pb.SOCIAL_LIFE_ALIVE:
            return self._wait(available)
        if match.phase == pb.SOCIAL_PHASE_WAITING_FOR_PLAYERS:
            return self._call(available, "agents-arena.ready-for-match") if not own.ready else self._wait(available)
        if not own.assigned:
            return self._wait(available)
        previous = self._previous(context)
        meeting = _observation(context, SOCIAL_MEETING, pb.SocialMeetingObservation)
        if match.phase == pb.SOCIAL_PHASE_DISCUSSION:
            if (self.scenario == "vote" and own.alignment == pb.SOCIAL_ALIGNMENT_CREW and meeting is not None
                    and meeting.match_id == match.match_id and meeting.meeting_id not in self._spoken):
                candidates = _witnessed(context, match.match_id)
                if candidates and SEND in available:
                    self._spoken.add(meeting.meeting_id)
                    return SkillCall(skill_id=SEND, arguments={"channel_id": "discussion", "ttl_ms": 300000,
                        "text": json.dumps({"claim": "witnessed_attack", "actor_id": sorted(candidates)[0]}, separators=(",", ":"))})
            return self._wait(available)
        if match.phase == pb.SOCIAL_PHASE_VOTING:
            if (meeting is None or meeting.match_id != match.match_id or meeting.phase_revision != match.phase_revision
                    or not meeting.meeting_id or meeting.own_ballot_submitted or meeting.meeting_id in self._voted):
                return self._wait(available)
            candidate = ""
            if self.scenario == "vote" and own.alignment == pb.SOCIAL_ALIGNMENT_CREW:
                eligible = {player.actor_id for player in meeting.participants if player.life == pb.SOCIAL_LIFE_ALIVE}
                witnessed = _witnessed(context, match.match_id) & eligible
                claims = _received_claims(context, match.match_id, eligible) & eligible
                options = witnessed or claims
                if options:
                    candidate = sorted(options)[0]
                elif meeting.voting_ends_at_unix_ms - NOW() > 5000:
                    return self._wait(available, (meeting.voting_ends_at_unix_ms - NOW() - 5000) / 1000)
            if VOTE in available:
                self._voted.add(meeting.meeting_id)
            return self._call(available, VOTE, candidate_actor_id=candidate, skip=not bool(candidate))
        if match.phase != pb.SOCIAL_PHASE_EXPLORATION:
            return self._wait(available)
        visible = _observation(context, SOCIAL_VISIBLE_WORLD, pb.SocialVisibleWorldObservation)
        if visible is None or visible.match_id != match.match_id or visible.recipient_actor_id != context.actor_id:
            return self._wait(available)
        membership = _observation(context, ROOM_MEMBERSHIP, RoomMembershipObservation)
        own_room = membership.room_id if membership is not None else ""
        transform = _observation(context, TRANSFORM, Transform2DObservation)
        if transform is not None and self._initial_x is None and math.isfinite(transform.x):
            self._initial_x = transform.x
        if self.scenario == "tasks":
            if own.alignment != pb.SOCIAL_ALIGNMENT_CREW:
                return self._gather(available, own_room)
            tasks = _observation(context, SOCIAL_TASKS, pb.SocialOwnTasksObservation)
            if tasks is None or tasks.match_id != match.match_id or tasks.recipient_actor_id != context.actor_id or not tasks.contributes_to_crew_progress:
                return self._wait(available)
            ordered = sorted(tasks.tasks, key=lambda task: task.task_id)
            if ordered:
                start = (int.from_bytes(hashlib.sha256(context.actor_id.encode()).digest()[:8], "big") + self._task_rotation) % len(ordered)
                for step in range(len(ordered)):
                    task = ordered[(start + step) % len(ordered)]
                    if not task.completed and task.task_id not in self._completed_tasks and PERFORM in available:
                        self._pending_task = task.task_id
                        return SkillCall(skill_id=PERFORM, arguments={"task_id": task.task_id})
            return self._wait(available)
        if self.scenario == "sabotage":
            if own.alignment != pb.SOCIAL_ALIGNMENT_IMPOSTOR:
                return self._gather(available, own_room)
            emergency = _observation(context, SOCIAL_EMERGENCY, pb.SocialEmergencyStateObservation)
            if emergency is None or emergency.match_id != match.match_id or emergency.active:
                return self._wait(available)
            if own.sabotage_ready_at_unix_ms > NOW():
                return self._wait(available, (own.sabotage_ready_at_unix_ms - NOW()) / 1000)
            if any(item.kind == "life-support" for item in visible.interactables):
                return self._call(available, "agents-arena.sabotage")
            return self._call(available, NAVIGATE, target_room_id=_life_support_room())
        if own.alignment == pb.SOCIAL_ALIGNMENT_CREW:
            if self.scenario == "vote" and visible.bodies:
                return self._call(available, "agents-arena.report-body", body_id=sorted(visible.bodies, key=lambda body: body.body_id)[0].body_id)
            if self.scenario == "privacy":
                if own_room == "security":
                    return self._wait(available)
                if own_room and (own_room != "main-hall" or self._initial_x is not None and self._initial_x < 0):
                    return self._call(available, NAVIGATE, target_room_id="security")
                return self._wait(available)
            return self._gather(available, own_room)
        if own.alignment != pb.SOCIAL_ALIGNMENT_IMPOSTOR:
            return self._wait(available)
        if self.scenario in ("vote", "privacy") and (self._kill_completed or visible.bodies or _known_body(context, match.match_id)):
            return self._wait(available)
        if own_room and own_room != "main-hall":
            return self._call(available, NAVIGATE, target_room_id="main-hall")
        ready = max(own.kill_ready_at_unix_ms, match.started_at_unix_ms + (PRIVACY_INITIAL_DELAY_MS if self.scenario == "privacy" else 0))
        if ready > NOW():
            return self._wait(available, (ready - NOW()) / 1000)
        targets = [actor for actor in visible.actors if actor.actor_id != context.actor_id and _identifier(actor.actor_id)
            and all(math.isfinite(number) for number in (actor.x, actor.y))]
        if not targets:
            self._target = ""
            return self._wait(available)
        if self._target and any(target.actor_id == self._target for target in targets) and previous.get("skill_id") == APPROACH and previous.get("status") == "succeeded":
            target, self._target = self._target, ""
            return self._call(available, KILL, actor_id=target)
        # Own root transform plus the public authored capsule offset is enough
        # for choosing a visible approach. No hidden target coordinates exist.
        def distance(target):
            return math.hypot(target.x - transform.x, target.y - transform.y - .46) if transform is not None else 0
        self._target = min(targets, key=lambda target: (distance(target), target.actor_id)).actor_id
        return self._call(available, APPROACH, actor_id=self._target)

    def _gather(self, available, own_room):
        return self._call(available, NAVIGATE, target_room_id="main-hall") if own_room and own_room != "main-hall" else self._wait(available)

    async def interrupt(self):
        pass

    async def interrupt_decision(self):
        pass

    async def close(self):
        self.closed = True

    def report(self):
        return {"provider": "social-privacy-fixture" if self.scenario == "privacy" else "social-fixture-" + self.scenario,
            "decisions": self.decisions, "closed": self.closed}


def vote_brain(**_):
    return SocialFixtureBrain("vote")


def parity_brain(**_):
    return SocialFixtureBrain("parity")


def sabotage_brain(**_):
    return SocialFixtureBrain("sabotage")


def tasks_brain(**_):
    return SocialFixtureBrain("tasks")


def privacy_brain(**_):
    return SocialFixtureBrain("privacy")
