"""Installed reference-game semantics. No policy uses event message keywords."""
from actor_runtime.classifiers import EventAssessment, EventHints, EventRule
from actor_runtime.extensions import type_url


def event_rules():
    from . import (ROOM_CHANGED, STATION_SIGNAL, RoomChangedEvent,
                  StationSignalEvent, ROUTINE_NOTICE, URGENT_RECALL, STATION_WIDE)

    def signal_hints(event):
        value = event.entry.payload
        critical = value.signal_kind != ROUTINE_NOTICE
        return EventHints(priority=100 if critical else 0, potentially_critical=critical,
            semantic_id=value.signal_id,
            aggregation_key="" if critical else "station-routine",
            tags=("station-signal", "urgent-recall" if critical else "routine-notice"))

    def signal_assessment(context, event):
        value = event.entry.payload
        if value.signal_kind == ROUTINE_NOTICE:
            return EventAssessment(relevance="unrelated", urgency="routine", should_buffer=False,
                confidence=1.0, reason_code="routine_station_notice")
        # These are explicit trusted user/runtime policy tags, not game text.
        if "station-observe-only" in context.policy_tags:
            return EventAssessment(relevance="relevant", urgency="important", should_remember=True,
                confidence=1.0, reason_code="station_recall_observe_only")
        valid = (value.signal_kind == URGENT_RECALL and value.signal_id and value.source_actor_id
                 and value.target_room_id == "main-hall" and value.audience == STATION_WIDE
                 and value.server_revision > 0 and value.server_unix_ms > 0)
        return EventAssessment(relevance="relevant" if valid else "uncertain", urgency="critical",
            should_remember=True, should_interrupt_skill=True, should_request_brain_review=True,
            confidence=1.0 if valid else 0.0,
            reason_code="authoritative_urgent_recall" if valid else "incomplete_station_signal_review")

    return (
        EventRule(STATION_SIGNAL, "agents-arena", type_url(StationSignalEvent), signal_hints, signal_assessment),
        EventRule(ROOM_CHANGED, "agents-arena", type_url(RoomChangedEvent),
            lambda event: EventHints(tags=("room-transition",)),
            lambda context, event: EventAssessment(relevance="relevant", confidence=1.0,
                reason_code="expected_room_state_change")),
    )
