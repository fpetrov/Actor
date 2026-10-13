"""Bounded reflection snapshots; game-originated data has a separate surface."""

from __future__ import annotations

import json
import time

from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .extensions import OpaquePayload, type_url
from .objectives import Objective


class GameMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    game_id: str
    game_version: str
    protocol_major: int
    protocol_minor: int


class PayloadSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    capability_id: str
    entity_id: str
    type_url: str
    sequence: int
    revision: int
    age_seconds: float
    opaque: bool = False
    truncated: bool = False
    value: JsonValue | None = None


class DecisionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    objective: Objective
    decision_index: int = Field(ge=1)
    game: GameMetadata
    actor_id: str
    observations: list[PayloadSnapshot]
    recent_events: list[PayloadSnapshot]
    previous_skill_result: dict[str, JsonValue] | None = None
    available_skills: list[dict[str, JsonValue]]
    validation_error: dict[str, JsonValue] | None = None
    observations_omitted: int = 0
    events_omitted: int = 0

    def external_state(self) -> dict[str, JsonValue]:
        """Only external state/results; user objective and installed catalog excluded."""
        return self.model_dump(mode="json", exclude={"objective", "available_skills"}, exclude_none=True)

    def external_json(self) -> str:
        return json.dumps(self.external_state(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _encoded_size(value) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _record(entry, now, max_payload_bytes):
    opaque = isinstance(entry.payload, OpaquePayload)
    value = None
    truncated = False
    url = entry.payload.type_url if opaque else type_url(type(entry.payload))
    if not opaque:
        if entry.payload.ByteSize() > max_payload_bytes:
            truncated = True
        else:
            value = MessageToDict(entry.payload, preserving_proto_field_name=False,
                                  always_print_fields_with_no_presence=True)
            if _encoded_size(value) > max_payload_bytes:
                value, truncated = None, True
    return PayloadSnapshot(capability_id=entry.capability_id[:256], entity_id=entry.entity_id[:128],
                           type_url=url[:512], sequence=entry.sequence, revision=entry.revision,
                           age_seconds=round(max(0.0, now - entry.received_at), 3),
                           opaque=opaque, truncated=truncated, value=value)


def build_decision_context(session, objective: Objective, catalog: list[dict], *, decision_index=1,
                           event_cursor=0, previous_skill_result=None, validation_error=None,
                           now=None, max_observations=64, max_events=32,
                           max_payload_bytes=8192, max_external_bytes=65536) -> DecisionContext:
    if not all(value > 0 for value in (max_observations, max_events, max_payload_bytes, max_external_bytes)):
        raise ValueError("Decision snapshot limits must be positive")
    now = time.monotonic() if now is None else now
    world = session.world
    observations = sorted(world.observations.values(),
                          key=lambda item: (item.entity_id != world.self_actor_id,
                                            item.entity_id, item.capability_id))
    events = sorted((event for event in world.events if event.sequence > event_cursor),
                    key=lambda item: (item.sequence, item.entity_id, item.capability_id))
    observation_records = [_record(entry, now, max_payload_bytes) for entry in observations[:max_observations]]
    event_records = [_record(entry, now, max_payload_bytes) for entry in events[-max_events:]]
    if previous_skill_result is not None and _encoded_size(previous_skill_result) > max_payload_bytes:
        previous_skill_result = {key: previous_skill_result[key]
                                 for key in ("skill_id", "status", "reason") if key in previous_skill_result}
        previous_skill_result["output_omitted"] = True
    manifest = session.manifest
    context = DecisionContext(
        objective=objective.model_copy(deep=True), decision_index=decision_index,
        game=GameMetadata(game_id=manifest.game_id[:160], game_version=manifest.game_version[:80],
                          protocol_major=manifest.protocol_version.major,
                          protocol_minor=manifest.protocol_version.minor),
        actor_id=world.self_actor_id, observations=observation_records, recent_events=event_records,
        previous_skill_result=previous_skill_result, available_skills=catalog,
        validation_error=validation_error,
        observations_omitted=max(0, len(observations) - max_observations),
        events_omitted=max(0, len(events) - max_events),
    )
    # Drop the largest values first; metadata and recent event identity remain visible.
    while _encoded_size(context.external_state()) > max_external_bytes:
        candidates = [("observations", i, _encoded_size(item.value))
                      for i, item in enumerate(context.observations) if item.value is not None]
        candidates += [("recent_events", i, _encoded_size(item.value))
                       for i, item in enumerate(context.recent_events) if item.value is not None]
        if candidates:
            field, index, _ = max(candidates, key=lambda item: (item[2], item[0], item[1]))
            values = list(getattr(context, field))
            values[index] = values[index].model_copy(update={"value": None, "truncated": True})
            context = context.model_copy(update={field: values})
        elif context.observations:
            context = context.model_copy(update={"observations": context.observations[:-1],
                                                  "observations_omitted": context.observations_omitted + 1})
        elif context.recent_events:
            context = context.model_copy(update={"recent_events": context.recent_events[1:],
                                                  "events_omitted": context.events_omitted + 1})
        else:
            raise ValueError("Decision snapshot limit is too small for fixed metadata")
    return context
