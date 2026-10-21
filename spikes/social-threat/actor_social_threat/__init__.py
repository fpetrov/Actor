"""Transparent, opt-in acceptance instrumentation; never selects a game action."""
from __future__ import annotations

import math
import time

from google.protobuf.json_format import ParseDict, ParseError
from extensions.agents_arena.v1 import social_pb2 as pb
from actor_runtime.extensions import type_url
from actor_runtime.brains import SkillCall
from actor_agents_arena import (SOCIAL_MATCH, SOCIAL_OWN_STATE, SOCIAL_VISIBLE_WORLD,
                               SOCIAL_VISIBLE_ATTACK, SOCIAL_MEETING)

SCHEMA = "actor.social-permitted-threat-audit.v1"
REACTIONS = frozenset(("agents-arena.report-body", "agents-arena.evade-threat",
    "agents-arena.call-meeting", "actor.send-message", "agents-arena.cast-vote"))
FRESH_SECONDS = 3.0
MAX_ROWS = 512


def _decode(record, capability, cls, actor, *, fresh=True):
    age = getattr(record, "age_seconds", None)
    if (getattr(record, "capability_id", None) != capability or
            getattr(record, "type_url", None) != type_url(cls) or
            getattr(record, "entity_id", None) != actor or
            any(getattr(record, key, False) for key in ("opaque", "truncated", "expired", "stale")) or
            type(age) not in (int, float) or not math.isfinite(age) or age < 0 or
            fresh and age > FRESH_SECONDS or not isinstance(getattr(record, "value", None), dict)):
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


class AuditStore:
    """Own identifiers exist only in RAM; the projection contains fixed metadata."""
    def __init__(self, *, clock=lambda: int(time.time() * 1000)):
        self.clock = clock
        self.actor = self.scope = None
        self.rows = []
        self._windows = {}
        self.episodes = 0
        self.invalid_identity = self.overflow = False
        self.terminal_verified = False

    def new_episode(self):
        self.episodes += 1
        return self.episodes

    def before(self, context, episode):
        now = self.clock()
        for window in self._windows.values():
            if window["returned_at"] is not None and window["until"] is None:
                window["until"] = now
        match = _observation(context, SOCIAL_MATCH, pb.SocialMatchStateObservation)
        own = _observation(context, SOCIAL_OWN_STATE, pb.SocialOwnStateObservation)
        coherent = bool(match is not None and own is not None and match.match_id and
            match.mode == "SocialDeduction" and match.phase_revision > 0 and
            own.match_id == match.match_id and own.actor_id == context.actor_id)
        if coherent:
            if self.actor is None:
                self.actor, self.scope = context.actor_id, match.match_id
            elif (self.actor, self.scope) != (context.actor_id, match.match_id):
                self.invalid_identity = True
                coherent = False
        crew = coherent and own.assigned and own.life == pb.SOCIAL_LIFE_ALIVE and own.alignment == pb.SOCIAL_ALIGNMENT_CREW
        body_ids, attackers, historical = set(), set(), set()
        seen = set()
        if coherent:
            visible = _observation(context, SOCIAL_VISIBLE_WORLD, pb.SocialVisibleWorldObservation)
            if (visible is not None and visible.match_id == self.scope and visible.recipient_actor_id == self.actor
                    and len(visible.bodies) <= 6 and len(visible.actors) <= 6
                    and match.started_at_unix_ms <= visible.captured_at_unix_ms <= now + 1000
                    and now - visible.captured_at_unix_ms <= int(FRESH_SECONDS * 1000)):
                body_ids = {item.body_id for item in visible.bodies if item.body_id and item.victim_actor_id and item.observed_revision > 0}
            for record in (*context.trigger_events, *context.recent_events):
                event = _decode(record, SOCIAL_VISIBLE_ATTACK, pb.SocialVisibleAttackEvent, context.actor_id)
                if (event is None or event.match_id != self.scope or event.observer_actor_id != self.actor
                        or not event.attacker_actor_id or not event.victim_actor_id or event.observed_revision <= 0
                        or not match.started_at_unix_ms <= event.observed_at_unix_ms <= now + 1000
                        or now - event.observed_at_unix_ms > int(FRESH_SECONDS * 1000)):
                    continue
                key = (event.observed_revision, event.attacker_actor_id, event.victim_actor_id)
                if key not in seen:
                    attackers.add(event.attacker_actor_id)
                    seen.add(key)
            # Historical witness evidence is counted separately, never promoted
            # into the fresh-threat acceptance condition.
            for record in context.working_knowledge:
                if (record.get("kind") != "historical_observation" or record.get("capability_id") != SOCIAL_VISIBLE_ATTACK
                        or record.get("source_actor_id") != self.actor or record.get("scope_id") != self.scope
                        or type(record.get("expires_at_unix_ms")) is not int or record["expires_at_unix_ms"] <= now
                        or not isinstance(record.get("content"), dict)):
                    continue
                try:
                    event = ParseDict(record["content"], pb.SocialVisibleAttackEvent())
                except (ParseError, TypeError, ValueError, OverflowError):
                    continue
                if (event.match_id == self.scope and event.observer_actor_id == self.actor
                        and event.attacker_actor_id and event.victim_actor_id and event.observed_revision > 0
                        and match.started_at_unix_ms <= event.observed_at_unix_ms <= now + 1000):
                    historical.add(event.attacker_actor_id)
        row = {"episode": episode, "decision_index": context.decision_index,
            "cognitive_revision": context.cognitive_revision, "own_crew_alive_context": bool(crew),
            "in_discussion_context": bool(coherent and match.phase == pb.SOCIAL_PHASE_DISCUSSION),
            "discussion_channel_selected": False,
            "coherent_context": bool(coherent), "fresh_attack_count": len(seen),
            "fresh_visible_body_count": len(body_ids), "historical_attack_count": len(historical),
            "fresh_threat_before_decide": bool(crew and (seen or body_ids)),
            "returned_skill_id": "none", "candidate_returned": False,
            "target_matches_permitted_fact": False, "postgame_action_confirmed": False}
        if len(self.rows) < MAX_ROWS:
            self.rows.append(row)
            self._windows[id(row)] = {"started_at": match.started_at_unix_ms if coherent else 0,
                "returned_at": None, "until": None}
        else:
            self.overflow = True
        # The temporary sets are used only to compare the real returned action;
        # no arguments, positions, message text or identifiers enter the row.
        meeting = _observation(context, SOCIAL_MEETING, pb.SocialMeetingObservation) if coherent else None
        eligible = {item.actor_id for item in meeting.participants if item.life == pb.SOCIAL_LIFE_ALIVE} if (
            meeting is not None and meeting.match_id == self.scope and meeting.phase_revision == match.phase_revision) else set()
        return row, body_ids, attackers, eligible

    def after(self, captured, decision):
        row, bodies, attackers, eligible = captured
        if not isinstance(decision, SkillCall):
            return
        if id(row) in self._windows:
            self._windows[id(row)]["returned_at"] = self.clock()
        row["candidate_returned"] = True
        row["returned_skill_id"] = decision.skill_id if decision.skill_id in REACTIONS else "other"
        args = decision.arguments
        row["discussion_channel_selected"] = decision.skill_id == "actor.send-message" and args.get("channel_id") == "discussion"
        if decision.skill_id == "agents-arena.report-body":
            target = args.get("body_id")
            row["target_matches_permitted_fact"] = isinstance(target, str) and target in bodies
        elif decision.skill_id == "agents-arena.evade-threat":
            target = args.get("actor_id")
            row["target_matches_permitted_fact"] = isinstance(target, str) and target in attackers
        elif decision.skill_id == "agents-arena.cast-vote":
            target = args.get("candidate_actor_id")
            row["target_matches_permitted_fact"] = (isinstance(target, str) and args.get("skip") is not True and
                target in attackers & eligible)
        elif decision.skill_id in ("agents-arena.call-meeting", "actor.send-message"):
            # This says only which action was selected after the facts. Message
            # meaning and the model's internal motivation are not inspected.
            row["target_matches_permitted_fact"] = row["fresh_threat_before_decide"]

    def finalize(self, session, directive):
        if directive.state != "terminal" or not directive.terminal_complete:
            return
        summary = directive.outcome.get("authoritative_summary")
        players = summary.get("players", ()) if isinstance(summary, dict) else ()
        own = [p for p in players if isinstance(p, dict) and p.get("actor_id") == self.actor]
        self.terminal_verified = bool(isinstance(summary, dict) and not self.invalid_identity and not self.overflow and self.actor and self.scope
            and session.world.self_actor_id == self.actor and directive.scope_id == self.scope
            and summary.get("match_id") == self.scope and summary.get("mode") == "SocialDeduction"
            and summary.get("replay_truncated") is not True and len(own) == 1
            and own[0].get("alignment") == "SOCIAL_ALIGNMENT_CREW")
        if self.terminal_verified:
            # Report/call-meeting changes phase before its receipt is delivered.
            # Confirm the real own action from the now-permitted server replay
            # without relabelling a locally cancelled Skill as succeeded.
            replay = summary.get("replay", ())
            used = set()
            for row in self.rows:
                row["postgame_action_confirmed"] = False
                outcome = {"agents-arena.report-body": "body_report",
                           "agents-arena.call-meeting": "emergency_meeting"}.get(row["returned_skill_id"])
                window = self._windows.get(id(row), {})
                returned = window.get("returned_at")
                if (outcome is None or returned is None or not row["fresh_threat_before_decide"]
                        or not row["candidate_returned"] or not row["target_matches_permitted_fact"]
                        or not isinstance(replay, list) or len(replay) > 256):
                    continue
                eligible = []
                for index, action in enumerate(replay):
                    if (index in used or not isinstance(action, dict) or action.get("kind") != "meeting"
                            or action.get("actor_id") != self.actor or action.get("outcome") != outcome):
                        continue
                    elapsed = action.get("elapsed_ms")
                    if isinstance(elapsed, str) and len(elapsed) <= 12 and elapsed.isascii() and elapsed.isdecimal():
                        elapsed = int(elapsed)
                    if type(elapsed) is not int or not 0 <= elapsed <= 14400000:
                        continue
                    at = window["started_at"] + elapsed
                    if returned <= at <= (window["until"] if window["until"] is not None else self.clock()):
                        eligible.append(index)
                if len(eligible) == 1:
                    used.add(eligible[0]); row["postgame_action_confirmed"] = True

    def projection(self):
        if not self.terminal_verified:
            return None
        return {"schema": SCHEMA, "postgame_own_crew_verified": True,
            "bounded": not self.overflow, "episodes": self.episodes,
            "decisions_observed": len(self.rows), "records": [dict(row) for row in self.rows],
            "scope": "permitted facts before actual provider calls and returned candidates; execution requires separate Runner/Skill proof"}


_STORE = AuditStore()


class ThreatAuditBrain:
    def __init__(self, delegate, *, store=None):
        self.delegate, self.store = delegate, store if store is not None else _STORE
        self.episode = self.store.new_episode()

    async def start(self, objective, catalog):
        return await self.delegate.start(objective, catalog)

    async def decide(self, context):
        captured = self.store.before(context, self.episode)
        decision = await self.delegate.decide(context)
        self.store.after(captured, decision)
        return decision

    async def interrupt(self):
        return await self.delegate.interrupt()

    async def interrupt_decision(self):
        return await self.delegate.interrupt_decision()

    async def close(self):
        return await self.delegate.close()

    def report(self):
        original = self.delegate.report()
        audit = self.store.projection()
        if audit is not None and (original.get("adapter") or {}).get("closed") is True:
            return {**original, "threat_audit": audit}
        return original


class ThreatAuditPolicy:
    def __init__(self, *, store=None, delegate=None):
        from actor_agents_arena.social_agent import SocialMatchPolicy
        self.store = store if store is not None else _STORE
        self.delegate = delegate if delegate is not None else SocialMatchPolicy()

    def evaluate(self, session):
        directive = self.delegate.evaluate(session)
        self.store.finalize(session, directive)
        audit = self.store.projection()
        if audit is not None and directive.state == "terminal":
            return directive.model_copy(update={"outcome": {**directive.outcome, "threat_audit": audit}})
        return directive

    def observe(self, session, knowledge):
        return self.delegate.observe(session, knowledge)

    def skill_available(self, session, definition):
        return self.delegate.skill_available(session, definition)


def brain(*, model=None):
    from actor_codex.brain import CodexBrain
    # Normal CLI can supply a model name. This fixture factory offers no seam
    # for a fake adapter, alternate provider or silently increased limits.
    return ThreatAuditBrain(CodexBrain(model=model, max_turns=24,
        usage_warning_tokens=300000, usage_limit_tokens=1000000))


def policy():
    return ThreatAuditPolicy()
