"""Installed communication retention/visibility rules, independent of a game."""
import time

from actor_runtime.classifiers import EventAssessment, EventHints, EventRule
from actor_runtime.extensions import type_url
from actor_runtime.inbox import EventInboxPolicy
from . import (MESSAGE_RECEIVED, MESSAGE_DELIVERY_RESULT, PEERS,
               MessageReceivedEvent, MessageDeliveryResultEvent, CommunicationPeersObservation)


def _valid_received(session, entry):
    value = entry.payload
    peers = session.world.latest(CommunicationPeersObservation, capability_id=PEERS)
    valid = (entry.entity_id == session.world.self_actor_id and value.recipient_actor_id == entry.entity_id
        and bool(value.message_id) and len(value.message_id) <= 64 and bool(value.sender_actor_id)
        and len(value.sender_actor_id) <= 128 and len(value.text.encode("utf-8")) <= 512
        and bool(value.match_id) and (peers is None or value.match_id == peers.match_id)
        and value.server_sequence > 0 and value.server_unix_ms > 0
        and value.expires_at_unix_ms > int(time.time() * 1000))
    if valid and value.sensitive and len(value.text) >= 16:
        session.register_known_secret(value.text, expires_at_unix_ms=value.expires_at_unix_ms)
    return valid


def _valid_receipt(session, entry):
    value = entry.payload
    peers = session.world.latest(CommunicationPeersObservation, capability_id=PEERS)
    return (entry.entity_id == session.world.self_actor_id and bool(value.correlation_id)
        and len(value.correlation_id) <= 64 and bool(value.match_id)
        and (peers is None or value.match_id == peers.match_id))


def _priority(selection, event):
    value = event.entry.payload
    skill = selection.get("current_skill") or {}
    filters = dict(skill.get("arguments", {})) if isinstance(skill, dict) else {}
    previous = (selection.get("previous_skill_result") or {}).get("output", {})
    for key in ("task_id", "sender_actor_id", "correlation_id", "channel_id"):
        if not filters.get(key) and previous.get(key):
            filters[key] = previous[key]
    score = 0
    for key, expected in filters.items():
        if key in ("task_id", "sender_actor_id", "correlation_id", "channel_id") and expected:
            score += 20 if getattr(value, key, None) == expected else -20
    return score


def event_rules():
    return (
        EventRule(MESSAGE_RECEIVED, "actor.standard.communication", type_url(MessageReceivedEvent),
            lambda event: EventHints(semantic_id=event.entry.payload.message_id,
                                    tags=("communication", event.entry.payload.task_id)),
            lambda context, event: EventAssessment(relevance="relevant", should_buffer=True,
                confidence=1.0, reason_code="received_message_wait_for_skill"),
            inbox_policy=EventInboxPolicy(identity_field="message_id", ordering_field="server_sequence",
                expiry_field="expires_at_unix_ms", max_items=128, max_age_seconds=300),
            data_policy="session_only", accept=_valid_received,
            context_priority=_priority, expiry_field="expires_at_unix_ms"),
        EventRule(MESSAGE_DELIVERY_RESULT, "actor.standard.communication", type_url(MessageDeliveryResultEvent),
            lambda event: EventHints(semantic_id=event.entry.payload.correlation_id + ":" + str(event.entry.payload.stage)),
            lambda context, event: EventAssessment(relevance="relevant", should_buffer=False,
                confidence=1.0, reason_code="authoritative_delivery_receipt"),
            inbox_policy=EventInboxPolicy(max_items=128, max_age_seconds=60),
            data_policy="session_only", accept=_valid_receipt),
    )
