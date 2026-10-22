"""Visibility and private-role policies installed by Arena, not Runtime Core."""
import math
import hashlib

from extensions.agents_arena.v1 import social_pb2 as pb
from actor_runtime.classifiers import EventRule, EventHints, EventAssessment
from actor_runtime.extensions import type_url
from actor_runtime.inbox import EventInboxPolicy
from . import (SOCIAL_MATCH, SOCIAL_OWN_STATE, SOCIAL_VISIBLE_WORLD, SOCIAL_MEETING, SOCIAL_TASKS,
    SOCIAL_EMERGENCY, SOCIAL_POSTGAME, SOCIAL_ACTION_RESULT, SOCIAL_MATCH_CHANGED, SOCIAL_OWN_STATE_CHANGED,
    SOCIAL_MEETING_CHANGED, SOCIAL_EMERGENCY_CHANGED, SOCIAL_VISIBLE_ATTACK, SOCIAL_VISIBLE_ACTIVITY, SOCIAL_MATCH_ENDED)
from . import SOCIAL_WAITING_PLAYERS, SOCIAL_TASK_WORK, SOCIAL_KILL_PRESENTATION


def _match(session, identity):
    current = session.world.latest(pb.SocialMatchStateObservation, capability_id=SOCIAL_MATCH)
    return bool(identity) and (current is None or current.match_id == identity)


def _accept(session, entry):
    value = entry.payload
    state = getattr(value, "state", value)
    is_type = lambda cls: type_url(type(state)) == type_url(cls)
    identity = getattr(state, "match_id", "")
    if entry.capability_id == SOCIAL_MATCH_ENDED:
        identity = value.summary.match_id
    if entry.entity_id != session.world.self_actor_id or not _match(session, identity):
        return False
    recipient = getattr(state, "recipient_actor_id", getattr(state, "observer_actor_id", ""))
    if recipient and recipient != entry.entity_id:
        return False
    if is_type(pb.SocialOwnStateObservation) and state.actor_id != entry.entity_id:
        return False
    if is_type(pb.SocialVisibleWorldObservation):
        if (not recipient or len(state.actors) > 6 or len(state.bodies) > 6 or len(state.interactables) > 16 or
                len({actor.actor_id for actor in state.actors}) != len(state.actors) or
                not all(math.isfinite(number) for actor in state.actors for number in
                        (actor.x, actor.y, actor.velocity_x, actor.velocity_y)) or
                not all(math.isfinite(number) for body in state.bodies for number in (body.x, body.y)) or
                not all(math.isfinite(number) for item in state.interactables for number in
                        (item.x, item.y, item.radius, item.approach_x, item.approach_y))):
            return False
    if is_type(pb.SocialOwnTasksObservation) and (not recipient or len(state.tasks) > 5):
        return False
    if is_type(pb.SocialWaitingPlayersObservation) and (not recipient or len(state.players) > 6):
        return False
    if is_type(pb.SocialOwnTaskWorkObservation) and (not recipient or state.step_count > 8):
        return False
    if is_type(pb.SocialKillPresentationEvent) and (not recipient or not state.presentation_id or
            not math.isfinite(state.x) or not math.isfinite(state.y) or state.server_unix_ms <= 0):
        return False
    if is_type(pb.SocialMeetingObservation) and len(state.participants) > 6:
        return False
    if entry.capability_id in (SOCIAL_POSTGAME, SOCIAL_MATCH_ENDED):
        current = session.world.latest(pb.SocialMatchStateObservation, capability_id=SOCIAL_MATCH)
        summary = value.summary
        terminal = (current is not None and current.phase == pb.SOCIAL_PHASE_GAME_OVER and current.match_id == identity)
        terminal = terminal or any(event.capability_id == SOCIAL_MATCH_CHANGED and
            event.payload.state.match_id == identity and event.payload.state.phase == pb.SOCIAL_PHASE_GAME_OVER
            for event in session.world.events)
        if not terminal or summary.match_id != identity:
            return False
        if len(summary.players) > 6 or len(summary.replay) > 256:
            return False
    # This installed mode policy also protects developers who use a standalone
    # Objective against a Social client instead of the recommended coordinator.
    session.privacy.private_activity = True
    if entry.capability_id in (SOCIAL_MATCH, SOCIAL_MATCH_CHANGED) and state.phase == pb.SOCIAL_PHASE_GAME_OVER:
        session.privacy.activity_revealed = True
    return True


def event_rules():
    def routine(context, event):
        return EventAssessment(relevance="relevant", confidence=1, reason_code="permitted_social_observation")

    def hints(event):
        value = event.entry.payload
        state = getattr(value, "state", value)
        if event.entry.capability_id == SOCIAL_VISIBLE_ATTACK:
            identity = f"{value.match_id}:attack:{value.observed_revision}"
        elif event.entry.capability_id == SOCIAL_MATCH_ENDED:
            identity = value.summary.match_id + ":ended"
        elif event.entry.capability_id == SOCIAL_OWN_STATE_CHANGED:
            identity = f"{state.match_id}:own:{state.server_revision}"
        elif event.entry.capability_id == SOCIAL_EMERGENCY_CHANGED:
            identity = f"{state.match_id}:emergency:{state.server_revision}"
        elif event.entry.capability_id == SOCIAL_MEETING_CHANGED:
            identity = f"{state.match_id}:meeting:" + hashlib.sha256(state.SerializeToString(deterministic=True)).hexdigest()
        else:
            identity = f"{state.match_id}:{event.entry.capability_id}:{state.phase_revision}"
        return EventHints(priority=100, potentially_critical=True, semantic_id=identity, tags=("social_transition",))

    def assess(context, event):
        capability = event.entry.capability_id
        state = getattr(event.entry.payload, "state", None)
        interrupt = capability in (SOCIAL_MATCH_CHANGED, SOCIAL_MEETING_CHANGED, SOCIAL_MATCH_ENDED,
                                   SOCIAL_VISIBLE_ATTACK, SOCIAL_EMERGENCY_CHANGED)
        if capability == SOCIAL_OWN_STATE_CHANGED:
            interrupt = state.life != pb.SOCIAL_LIFE_ALIVE
        return EventAssessment(relevance="relevant", urgency="critical", should_interrupt_skill=interrupt,
            should_request_brain_review=True, should_buffer=True, should_remember=capability == SOCIAL_MATCH_ENDED,
            confidence=1, reason_code="authoritative_social_transition")

    rules = []
    for cap, cls in ((SOCIAL_MATCH, pb.SocialMatchStateObservation), (SOCIAL_OWN_STATE, pb.SocialOwnStateObservation),
        (SOCIAL_VISIBLE_WORLD, pb.SocialVisibleWorldObservation), (SOCIAL_MEETING, pb.SocialMeetingObservation),
        (SOCIAL_TASKS, pb.SocialOwnTasksObservation), (SOCIAL_EMERGENCY, pb.SocialEmergencyStateObservation),
        (SOCIAL_WAITING_PLAYERS, pb.SocialWaitingPlayersObservation), (SOCIAL_TASK_WORK, pb.SocialOwnTaskWorkObservation),
        (SOCIAL_POSTGAME, pb.SocialPostgameObservation)):
        rules.append(EventRule(cap, "agents-arena", type_url(cls), lambda event: EventHints(), routine,
            data_policy="public" if cap == SOCIAL_POSTGAME else "session_only", accept=_accept))
    for cap, cls in ((SOCIAL_MATCH_CHANGED, pb.SocialMatchChangedEvent), (SOCIAL_OWN_STATE_CHANGED, pb.SocialOwnStateChangedEvent),
        (SOCIAL_MEETING_CHANGED, pb.SocialMeetingChangedEvent), (SOCIAL_EMERGENCY_CHANGED, pb.SocialEmergencyChangedEvent),
        (SOCIAL_VISIBLE_ATTACK, pb.SocialVisibleAttackEvent), (SOCIAL_MATCH_ENDED, pb.SocialMatchEndedEvent)):
        rules.append(EventRule(cap, "agents-arena", type_url(cls), hints, assess,
            data_policy="public" if cap == SOCIAL_MATCH_ENDED else "session_only", accept=_accept))
    rules.append(EventRule(SOCIAL_VISIBLE_ACTIVITY, "agents-arena", type_url(pb.SocialVisibleActivityEvent),
        lambda event: EventHints(semantic_id=f"{event.entry.payload.match_id}:{event.entry.payload.observed_revision}"),
        routine, data_policy="session_only", accept=_accept))
    rules.append(EventRule(SOCIAL_KILL_PRESENTATION, "agents-arena", type_url(pb.SocialKillPresentationEvent),
        lambda event: EventHints(semantic_id=event.entry.payload.presentation_id),
        routine, data_policy="session_only", accept=_accept))
    # Multi-stage work results share correlation IDs. Retain by wire sequence,
    # never deduplicate the completed result against the earlier acceptance.
    rules.append(EventRule(SOCIAL_ACTION_RESULT, "agents-arena", type_url(pb.SocialActionResultEvent),
        lambda event: EventHints(), routine, data_policy="session_only", accept=_accept,
        inbox_policy=EventInboxPolicy(max_items=64, max_age_seconds=120)))
    return tuple(rules)
