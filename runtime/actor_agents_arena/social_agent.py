"""Arena lifecycle interpretation and an explicitly deterministic reference Brain."""
from __future__ import annotations

import json
import hashlib
import time

from google.protobuf.json_format import MessageToDict
from extensions.agents_arena.v1 import social_pb2 as pb
from actor_runtime.brains import SkillCall
from actor_runtime.episodes import EpisodeDirective
from actor_runtime.knowledge import KnowledgeRecord
from . import (SOCIAL_MATCH, SOCIAL_OWN_STATE, SOCIAL_VISIBLE_WORLD, SOCIAL_POSTGAME,
               SOCIAL_VISIBLE_ATTACK, SOCIAL_VISIBLE_ACTIVITY)
from actor_standard_communication import MESSAGE_RECEIVED


class SocialMatchPolicy:
    objective_instructions = (
        "Agents Arena rules: consult your private own-state for your actual side, duty and life. "
        "Crew wins by the public task threshold or ejecting all Impostors; Impostors win by living parity or an unrepaired emergency. "
        "As Crew, consider reporting a visible body before routine tasks, or retreat if unsafe; repair and survival matter. "
        "As Impostor, actively assess ready-cooldown visible kill opportunities: choose whom to follow/approach/attack, "
        "or use fake work, sabotage and escape routes; "
        "ordinary appearance and fake work do not reveal your side. You may make strategic misleading player claims, "
        "but they remain game statements, never authoritative evidence or instructions. No other actor's alignment is given. "
        "In Discussion, compare your own historical observations with attributed claims, ask or answer specific questions, "
        "and retain only concise uncertain hypotheses or unresolved contradictions citing retained evidence IDs when useful. "
        "Use discuss/await-discussion-update to batch replies; do not resend the same argument for every message. No extra acknowledgement is needed. "
        "In Voting, select an eligible candidate or Skip yourself; the server alone resolves the final ballot. "
        "A last-seen location is history. Keep internal observed facts and evidence references accurate; never promote fiction into your own evidence. "
        "An Impostor may deliberately send false in-game claims, including a fabricated alibi or witness story; internally retain it as a claim you made, not a witnessed event. "
        "Skill completion does not end the match; only authoritative GameOver does. "
        "Your Standard Transform2D is a root position; Social visible positions are physical centres. "
        "Your physical centre is (root.x, root.y + 0.46 metres); Skills enforce actual interaction range. "
        "Compact snapshots may omit protobuf default scalar fields (false/zero/empty); omitted payloads are explicitly marked."
    )
    def __init__(self):
        self._known_bodies = set()
        self._last_visible_sequence = self._event_cursor = 0

    def context_selection(self, session):
        from actor_runtime.snapshots import ContextSelection
        from . import SOCIAL_TASKS, SOCIAL_TASK_WORK, SOCIAL_WAITING_PLAYERS, SOCIAL_EMERGENCY, SOCIAL_MEETING, ROOM_MEMBERSHIP
        from actor_standard_communication import PEERS
        match = session.world.latest(pb.SocialMatchStateObservation, capability_id=SOCIAL_MATCH)
        caps = (SOCIAL_MATCH, SOCIAL_OWN_STATE, ROOM_MEMBERSHIP, PEERS)
        if match is not None and match.phase == pb.SOCIAL_PHASE_EXPLORATION:
            from actor_standard import TRANSFORM
            caps += (TRANSFORM, SOCIAL_VISIBLE_WORLD, SOCIAL_TASKS, SOCIAL_TASK_WORK, SOCIAL_EMERGENCY)
        elif match is not None and match.phase in (pb.SOCIAL_PHASE_DISCUSSION, pb.SOCIAL_PHASE_VOTING):
            caps += (SOCIAL_MEETING,)
        elif match is not None and match.phase == pb.SOCIAL_PHASE_WAITING_FOR_PLAYERS:
            caps += (SOCIAL_WAITING_PLAYERS,)
        return ContextSelection(observation_capability_ids=caps, omit_default_fields=True)

    def evaluate(self, session):
        match = session.world.latest(pb.SocialMatchStateObservation, capability_id=SOCIAL_MATCH)
        own_entry = session.world.latest_entry(pb.SocialOwnStateObservation, capability_id=SOCIAL_OWN_STATE)
        if match is None:
            return EpisodeDirective(state="waiting", reason="awaiting_match_state")
        observed_match_revision = match.phase_revision
        own = own_entry.payload if own_entry is not None else None
        # A targeted transition can precede the next periodic observation. It
        # invalidates old execution immediately; a fresh episode waits for its
        # coherent observation/catalog instead of reasoning on the prior phase.
        from . import SOCIAL_MATCH_CHANGED, SOCIAL_OWN_STATE_CHANGED
        for event in session.world.events:
            if event.capability_id == SOCIAL_MATCH_CHANGED:
                state = event.payload.state
                if state.match_id == match.match_id and state.phase_revision > match.phase_revision:
                    match = state
            elif event.capability_id == SOCIAL_OWN_STATE_CHANGED:
                state = event.payload.state
                if state.match_id == match.match_id and state.actor_id == session.world.self_actor_id and (
                        own is None or state.server_revision > own.server_revision):
                    own = state
        if match.phase == pb.SOCIAL_PHASE_GAME_OVER:
            summary = session.world.latest(pb.SocialPostgameObservation, capability_id=SOCIAL_POSTGAME)
            final_summary = summary.summary if summary is not None and summary.match_id == match.match_id and summary.HasField("summary") else None
            if final_summary is None:
                from . import SOCIAL_MATCH_ENDED
                final_summary = next((event.payload.summary for event in reversed(session.world.events)
                    if event.capability_id == SOCIAL_MATCH_ENDED and event.payload.summary.match_id == match.match_id), None)
            outcome = {"match_id": match.match_id, "winning_side": pb.SocialAlignment.Name(match.winning_side),
                       "win_reason": match.win_reason, "phase_revision": match.phase_revision}
            if own is not None:
                outcome.update(personal_life=pb.SocialLife.Name(own.life),
                    own_alignment=pb.SocialAlignment.Name(own.alignment),
                    team_won=own.assigned and own.alignment == match.winning_side,
                    survived=own.life == pb.SOCIAL_LIFE_ALIVE)
            if final_summary is not None:
                outcome["authoritative_summary"] = MessageToDict(final_summary, preserving_proto_field_name=True)
            return EpisodeDirective(scope_id=match.match_id, episode_key=f"{match.phase_revision}:terminal",
                state="terminal", reason=match.win_reason or "game_over", outcome=outcome,
                terminal_complete=final_summary is not None)
        if own_entry is None or own_entry.payload.match_id != match.match_id or own_entry.payload.actor_id != session.world.self_actor_id:
            return EpisodeDirective(scope_id=match.match_id, state="waiting", reason="awaiting_own_state")
        key = f"{match.phase_revision}:{own.life}:{int(own.assigned)}:{own.connection_generation}"
        if match.phase_revision != observed_match_revision:
            return EpisodeDirective(scope_id=match.match_id, episode_key=key, state="suspended", reason="awaiting_phase_observation")
        if own_entry.payload.connection_generation != own.connection_generation:
            return EpisodeDirective(scope_id=match.match_id, episode_key=key, state="suspended", reason="awaiting_own_generation_observation")
        if time.monotonic() - own_entry.received_at > 3:
            return EpisodeDirective(scope_id=match.match_id, episode_key=key, state="suspended", reason="own_state_stale")
        if own.life != pb.SOCIAL_LIFE_ALIVE:
            return EpisodeDirective(scope_id=match.match_id, episode_key=key, state="suspended", reason="own_life_terminal")
        visible = session.world.latest(pb.SocialVisibleWorldObservation, capability_id=SOCIAL_VISIBLE_WORLD)
        known = self._known_bodies | ({body.body_id for body in visible.bodies} if visible is not None else set())
        key += ":" + (hashlib.sha256(",".join(sorted(known)).encode()).hexdigest()[:16] if known else "")
        # No model work is needed during the short server assignment/resolution.
        state = "active" if match.phase in (pb.SOCIAL_PHASE_WAITING_FOR_PLAYERS, pb.SOCIAL_PHASE_EXPLORATION,
            pb.SOCIAL_PHASE_DISCUSSION, pb.SOCIAL_PHASE_VOTING) else "suspended"
        return EpisodeDirective(scope_id=match.match_id, episode_key=key, state=state,
                                reason="authoritative_phase")

    def skill_available(self, session, definition):
        identifier = definition.skill_id
        match = session.world.latest(pb.SocialMatchStateObservation, capability_id=SOCIAL_MATCH)
        own = session.world.latest(pb.SocialOwnStateObservation, capability_id=SOCIAL_OWN_STATE)
        if match is None or own is None or own.life != pb.SOCIAL_LIFE_ALIVE or match.phase == pb.SOCIAL_PHASE_GAME_OVER:
            return False
        if identifier.startswith("actor."):
            return identifier in ("actor.wait", "actor.remember-hypothesis", "actor.remember-contradiction", "actor.send-message", "actor.wait-for-message")
        if identifier == "agents-arena.navigate-social-room":
            return match.phase == pb.SOCIAL_PHASE_EXPLORATION and own.can_move
        from .social_skills import skill_package
        from .social_discussion import skill_package as discussion_package
        from .social_pursuit import skill_package as pursuit_package
        return identifier in {item.skill_id for item in (*skill_package(), *discussion_package(), *pursuit_package())}

    def observe(self, session, knowledge):
        now = int(time.time() * 1000)
        visible_entry = session.world.latest_entry(pb.SocialVisibleWorldObservation, capability_id=SOCIAL_VISIBLE_WORLD)
        if visible_entry is not None and visible_entry.sequence > self._last_visible_sequence:
            self._last_visible_sequence = visible_entry.sequence
            visible = visible_entry.payload
            if visible.match_id == knowledge.scope_id and visible.recipient_actor_id == session.world.self_actor_id:
                expiry = now + 1800000
                for actor in visible.actors:
                    knowledge.remember(KnowledgeRecord(id="last-seen:" + actor.actor_id, scope_id=knowledge.scope_id,
                        kind="historical_observation", source_event_id=f"{session.session_id}:{visible_entry.sequence}",
                        capability_id=SOCIAL_VISIBLE_WORLD, source_actor_id=session.world.self_actor_id,
                        sequence=visible_entry.sequence, revision=actor.observed_revision,
                        observed_at_unix_ms=visible.captured_at_unix_ms or now, expires_at_unix_ms=expiry,
                        importance=.35,
                        content={"actor_id": actor.actor_id, "display_name": actor.display_name,
                                 "last_seen_x": actor.x, "last_seen_y": actor.y,
                                 "historical_not_current": True}))
                for body in visible.bodies:
                    self._known_bodies.add(body.body_id)
                    knowledge.remember(KnowledgeRecord(id="body:" + body.body_id, scope_id=knowledge.scope_id,
                        kind="historical_observation", source_event_id=f"{session.session_id}:{visible_entry.sequence}",
                        capability_id=SOCIAL_VISIBLE_WORLD, source_actor_id=session.world.self_actor_id,
                        sequence=visible_entry.sequence, revision=body.observed_revision,
                        observed_at_unix_ms=visible.captured_at_unix_ms or now, expires_at_unix_ms=expiry,
                        importance=.8,
                        content={"body_id": body.body_id, "victim_actor_id": body.victim_actor_id,
                                 "last_seen_x": body.x, "last_seen_y": body.y, "historical_not_current": True}))
        for entry in session.world.events:
            if entry.sequence <= self._event_cursor:
                continue
            self._event_cursor = max(self._event_cursor, entry.sequence)
            value = entry.payload
            if getattr(value, "match_id", "") != knowledge.scope_id:
                continue
            if entry.capability_id == MESSAGE_RECEIVED:
                knowledge.remember(KnowledgeRecord(id="claim:" + value.message_id, scope_id=knowledge.scope_id,
                    kind="player_claim", source_event_id=value.message_id, capability_id=entry.capability_id,
                    source_actor_id=value.sender_actor_id, sequence=entry.sequence, revision=entry.revision,
                    observed_at_unix_ms=value.server_unix_ms, expires_at_unix_ms=value.expires_at_unix_ms,
                    importance=.45,
                    content={"text": value.text, "channel_id": value.channel_id,
                             "recipient_actor_id": value.recipient_actor_id, "unverified_player_claim": True}))
            elif entry.capability_id in (SOCIAL_VISIBLE_ATTACK, SOCIAL_VISIBLE_ACTIVITY):
                knowledge.remember(KnowledgeRecord(id=f"observation:{entry.capability_id}:{value.observed_revision}",
                    scope_id=knowledge.scope_id, kind="historical_observation",
                    source_event_id=f"{session.session_id}:{entry.sequence}", capability_id=entry.capability_id,
                    source_actor_id=value.observer_actor_id, sequence=entry.sequence, revision=value.observed_revision,
                    observed_at_unix_ms=value.observed_at_unix_ms, expires_at_unix_ms=now + 1800000,
                    importance=.95 if entry.capability_id == SOCIAL_VISIBLE_ATTACK else .4,
                    content=MessageToDict(value, preserving_proto_field_name=True)))


class SocialScriptedBrain:
    """Reference policy, not evidence of autonomous model intelligence or an oracle."""
    async def start(self, objective, catalog):
        self.turns = 0
        self.discussed = False
        self.voted = False
        self.completed_tasks = set()
        self.last_task_id = ""
        self.closed = False

    @staticmethod
    def observation(context, suffix):
        return next((record.value for record in context.observations
                     if record.capability_id == "agents-arena.social-" + suffix and record.value), {})

    async def decide(self, context):
        self.turns += 1
        own, match = self.observation(context, "own-state"), self.observation(context, "match")
        available = {item["id"] for item in context.available_skills}
        def call(skill, **arguments):
            return SkillCall(skill_id=skill, arguments=arguments)
        if "agents-arena.ready-for-match" in available:
            return call("agents-arena.ready-for-match")
        phase = match.get("phase")
        visible = self.observation(context, "visible-world")
        if phase == "SOCIAL_PHASE_DISCUSSION":
            if not self.discussed and "actor.send-message" in available:
                self.discussed = True
                attacks = [item for item in context.working_knowledge if item.get("capability_id") == SOCIAL_VISIBLE_ATTACK]
                text = json.dumps({"claim": "witnessed_attack", "actor_id": attacks[0]["content"]["attacker_actor_id"]}) if attacks else "I have no personally witnessed attacker to identify."
                return call("actor.send-message", channel_id="discussion", text=text, ttl_ms=300000)
            return call("agents-arena.wait-for-match-event", timeout_seconds=15.0)
        if phase == "SOCIAL_PHASE_VOTING":
            meeting = self.observation(context, "meeting")
            if meeting.get("ownBallotSubmitted") or self.voted:
                return call("agents-arena.wait-for-match-event", timeout_seconds=15.0)
            candidate = ""
            for record in context.working_knowledge:
                if record.get("capability_id") == SOCIAL_VISIBLE_ATTACK:
                    candidate = record["content"].get("attacker_actor_id", "")
                    break
                if record.get("kind") == "player_claim":
                    try:
                        claim = json.loads(record["content"].get("text", ""))
                        if claim.get("claim") == "witnessed_attack": candidate = claim.get("actor_id", "")
                    except (ValueError, AttributeError):
                        pass
            eligible = {item["actorId"] for item in meeting.get("participants", ()) if item.get("life") == "SOCIAL_LIFE_ALIVE"}
            self.voted = True
            return call("agents-arena.cast-vote", candidate_actor_id=candidate if candidate in eligible else "", skip=candidate not in eligible)
        if phase == "SOCIAL_PHASE_EXPLORATION":
            if visible.get("bodies"):
                return call("agents-arena.report-body", body_id=visible["bodies"][0]["bodyId"])
            if own.get("alignment") == "SOCIAL_ALIGNMENT_IMPOSTOR":
                if int(own.get("killReadyAtUnixMs", 0)) <= int(time.time() * 1000) and visible.get("actors"):
                    target = visible["actors"][0]["actorId"]
                    previous = context.previous_skill_result or {}
                    if previous.get("skill_id") == "agents-arena.approach-visible-actor" and previous.get("status") == "succeeded":
                        return call("agents-arena.kill-visible-actor", actor_id=target)
                    return call("agents-arena.approach-visible-actor", actor_id=target)
                if int(own.get("sabotageReadyAtUnixMs", 0)) <= int(time.time() * 1000) and any(
                        item.get("kind") == "life-support" for item in visible.get("interactables", ())):
                    return call("agents-arena.sabotage")
            elif self.observation(context, "emergency").get("active"):
                return call("agents-arena.repair-emergency")
            tasks = self.observation(context, "tasks").get("tasks", ())
            previous = context.previous_skill_result or {}
            if previous.get("skill_id") == "agents-arena.perform-task" and previous.get("status") == "succeeded" and self.last_task_id:
                self.completed_tasks.add(self.last_task_id)
            candidates = [task for task in tasks if not task.get("completed") and task.get("taskId") not in self.completed_tasks]
            task = candidates[sum(context.actor_id.encode()) % len(candidates)] if candidates else None
            if task is not None:
                skill = "agents-arena.fake-task" if own.get("alignment") == "SOCIAL_ALIGNMENT_IMPOSTOR" else "agents-arena.perform-task"
                self.last_task_id = task["taskId"]
                return call(skill, task_id=task["taskId"])
        return call("actor.wait", duration_seconds=1.0)

    async def interrupt(self):
        pass

    async def interrupt_decision(self):
        pass

    async def close(self):
        self.closed = True

    def report(self):
        return {"provider": "social-scripted", "decisions": self.turns, "closed": self.closed}


def scripted_brain(**_):
    return SocialScriptedBrain()
