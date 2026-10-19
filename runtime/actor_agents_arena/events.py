"""Installed reference-game semantics. No policy uses event message keywords."""
from actor_runtime.classifiers import EventAssessment, EventHints, EventRule
from actor_runtime.extensions import type_url


def event_rules():
    from . import (ROOM_CHANGED, STATION_SIGNAL, RoomChangedEvent,
                  StationSignalEvent, ROUTINE_NOTICE, URGENT_RECALL, STATION_WIDE,
                  DIAGNOSTIC_RESULT, CALIBRATION_RESULT, CALIBRATION_ASSIGNMENT, CALIBRATION_CHANGED,
                  CALIBRATION_STATE, CALIBRATION_STATUS_CANCELLED, CALIBRATION_STATUS_EXPIRED,
                  CALIBRATION_STATUS_FAILED, CalibrationTaskStateObservation,
                  DiagnosticResultEvent, CalibrationResultEvent, CalibrationAssignmentEvent, CalibrationTaskChangedEvent)

    terminal_failures = {CALIBRATION_STATUS_CANCELLED, CALIBRATION_STATUS_EXPIRED, CALIBRATION_STATUS_FAILED}

    def own_task_change(session, entry):
        state = entry.payload.state
        if state.status not in terminal_failures:
            return True
        current = session.world.latest(CalibrationTaskStateObservation, capability_id=CALIBRATION_STATE)
        return (entry.entity_id == session.world.self_actor_id
            and bool(state.task_id) and len(state.task_id) <= 64
            and bool(state.match_id) and len(state.match_id) <= 64 and state.server_revision > 0
            and entry.entity_id in (state.coordinator_actor_id, state.technician_actor_id)
            and current is not None and current.task_id == state.task_id and current.match_id == state.match_id
            and current.server_revision <= state.server_revision)

    def task_hints(event):
        state = event.entry.payload.state
        terminal = state.status in terminal_failures
        return EventHints(priority=100 if terminal else 0, potentially_critical=terminal,
            mandatory_review=terminal,
            semantic_id=f"{state.task_id}:{state.server_revision}:{state.status}", tags=("calibration",))

    def task_assessment(context, event):
        terminal = event.entry.payload.state.status in terminal_failures
        return EventAssessment(relevance="relevant", urgency="critical" if terminal else "routine",
            should_interrupt_skill=terminal, should_request_brain_review=terminal,
            confidence=1.0, reason_code="assigned_task_ended" if terminal else "authoritative_calibration_update")

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
        EventRule(DIAGNOSTIC_RESULT, "agents-arena", type_url(DiagnosticResultEvent),
            lambda event: EventHints(tags=("private-diagnostic",)),
            lambda context, event: EventAssessment(relevance="relevant", confidence=1.0,
                reason_code="private_diagnostic_result"),
            data_policy="sensitive", secret_fields=("authorization_code",), expiry_field="expires_at_unix_ms"),
        EventRule(CALIBRATION_CHANGED, "agents-arena", type_url(CalibrationTaskChangedEvent),
            task_hints, task_assessment, accept=own_task_change),
        *tuple(EventRule(capability, "agents-arena", type_url(message_type),
            lambda event: EventHints(tags=("calibration",)),
            lambda context, event: EventAssessment(relevance="relevant", confidence=1.0,
                reason_code="authoritative_calibration_update"))
            for capability, message_type in (
                (CALIBRATION_RESULT, CalibrationResultEvent),
                (CALIBRATION_ASSIGNMENT, CalibrationAssignmentEvent))),
    )
