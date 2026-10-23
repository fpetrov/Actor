"""Bounded reflection snapshots; game-originated data has a separate surface."""

from __future__ import annotations

import json
import math
import re
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


class ContextSelection(BaseModel):
    """Optional installed-policy selection, not a world-state summarizer."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    observation_capability_ids: tuple[str, ...] | None = Field(default=None, max_length=128)
    omit_default_fields: bool = False


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
    stale: bool = False
    event_id: str | None = None
    extension_id: str | None = None
    extension_version: str | None = None
    assessment: dict[str, JsonValue] | None = None
    aggregate_count: int = 1
    data_policy: str = "public"
    expired: bool = False


class MemorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str
    evidence_kind: str
    source_event_id: str
    source_session_id: str
    capability_id: str = ""
    type_url: str = ""
    summary: str
    content: JsonValue | None
    age_seconds: float
    importance: float
    truncated: bool = False


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
    trigger_events: list[PayloadSnapshot] = Field(default_factory=list)
    memories: list[MemorySnapshot] = Field(default_factory=list)
    memories_omitted: int = 0
    decision_reason: str = "objective_started"
    cognitive_revision: int = 0
    current_skill: dict[str, JsonValue] | None = None
    current_plan: dict[str, JsonValue] | None = None
    planning: dict[str, JsonValue] | None = None
    working_knowledge: list[dict[str, JsonValue]] = Field(default_factory=list)
    knowledge_omitted: int = 0

    @property
    def external_bytes(self) -> int:
        return len(self.external_json().encode("utf-8"))

    @property
    def estimated_tokens(self) -> int:
        """A conservative byte heuristic, never measured provider usage."""
        return math.ceil(self.external_bytes / 4)

    @property
    def payloads_truncated(self) -> int:
        return sum(item.truncated for item in (*self.observations, *self.recent_events, *self.trigger_events)) + sum(
            item.truncated for item in self.memories)

    def external_state(self) -> dict[str, JsonValue]:
        """Only external state/results; user objective and installed catalog excluded."""
        return self.model_dump(mode="json", exclude={"objective", "available_skills"}, exclude_none=True)

    def external_json(self) -> str:
        return json.dumps(self.external_state(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _encoded_size(value) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _preview(value, budget):
    """Keep a deterministic bounded part of actual data, rather than only its ID."""
    if _encoded_size(value) <= budget:
        return value
    if isinstance(value, str):
        # JSON escaping can use more bytes than UTF-8 itself.
        low, high = 0, len(value)
        while low < high:
            middle = (low + high + 1) // 2
            if _encoded_size(value[:middle] + "…") <= budget:
                low = middle
            else:
                high = middle - 1
        return value[:low] + "…" if _encoded_size(value[:low] + "…") <= budget else None
    if isinstance(value, dict):
        result = {}
        for key, item in sorted(value.items(), key=lambda pair: (_encoded_size(pair[1]), pair[0]))[:64]:
            remaining = budget - _encoded_size(result) - _encoded_size(key) - 2
            if remaining < 4:
                break
            result[key] = _preview(item, remaining)
            if _encoded_size(result) > budget:
                del result[key]
                break
        return result
    if isinstance(value, list):
        result = []
        for item in value[:32]:
            remaining = budget - _encoded_size(result) - 1
            if remaining < 4:
                break
            result.append(_preview(item, remaining))
        return result
    return None


def _record(source, now, max_payload_bytes, *, trigger=False, stale_after_seconds=3.0, omit_default_fields=False):
    entry = getattr(source, "entry", source)
    opaque = isinstance(entry.payload, OpaquePayload)
    value = None
    truncated = False
    url = entry.payload.type_url if opaque else type_url(type(entry.payload))
    if not opaque:
        if entry.payload.ByteSize() > max_payload_bytes and not trigger:
            truncated = True
        else:
            value = MessageToDict(entry.payload, preserving_proto_field_name=False,
                                  always_print_fields_with_no_presence=not omit_default_fields)
            if _encoded_size(value) > max_payload_bytes:
                value, truncated = _preview(value, max_payload_bytes) if trigger else None, True
    assessment = getattr(source, "assessment", None)
    expiry = getattr(entry, "expires_at_unix_ms", 0)
    expired = bool(expiry and expiry <= int(time.time() * 1000))
    if expired:
        value, truncated = None, True
    if assessment is not None:
        assessment = assessment.model_dump(mode="json") if hasattr(assessment, "model_dump") else dict(assessment)
        if _encoded_size(assessment) > 2048:
            assessment = {key: assessment[key] for key in ("relevance", "urgency", "reason_code", "source")
                          if key in assessment}
    age = round(max(0.0, now - entry.received_at), 3)
    return PayloadSnapshot(capability_id=entry.capability_id[:256], entity_id=entry.entity_id[:128],
                           type_url=url[:512], sequence=entry.sequence, revision=entry.revision,
                           age_seconds=age, stale=age > stale_after_seconds,
                           opaque=opaque, truncated=truncated, value=value,
                           event_id=getattr(source, "id", None),
                           extension_id=getattr(source, "extension_id", None),
                           extension_version=getattr(source, "extension_version", None),
                           assessment=assessment, aggregate_count=getattr(source, "aggregate_count", 1),
                           data_policy=getattr(entry, "data_policy", "public"), expired=expired)


def _identity(source):
    entry = getattr(source, "entry", source)
    return entry.sequence, entry.revision, entry.entity_id, entry.capability_id


def _words(value):
    return set(re.findall(r"\w{3,}", str(value).casefold().replace("_", " ")))


def _memory_record(memory, now_wall, max_payload_bytes):
    item = memory.model_dump(mode="json") if hasattr(memory, "model_dump") else dict(memory)
    content = item.get("content")
    truncated = item.get("content_truncated", False) or _encoded_size(content) > max_payload_bytes
    return MemorySnapshot(id=item["id"][:128], evidence_kind=item["evidence_kind"][:64],
                          source_event_id=item.get("source_event_id", "")[:512],
                          source_session_id=item.get("session_id", "")[:128],
                          capability_id=item.get("capability_id", "")[:256], type_url=item.get("type_url", "")[:512],
                          summary=item.get("summary", "")[:256],
                          content=_preview(content, max_payload_bytes) if truncated else content,
                          age_seconds=round(max(0.0, now_wall - item["created_at"]), 3),
                          importance=float(item.get("importance", 0)), truncated=truncated)


def build_decision_context(session, objective: Objective, catalog: list[dict], *, decision_index=1,
                           event_cursor=0, previous_skill_result=None, validation_error=None,
                           now=None, max_observations=64, max_events=32,
                           max_payload_bytes=8192, max_external_bytes=65536,
                           trigger_events=(), routed_events=None, memories=(), decision_reason=None,
                           cognitive_revision=0, current_skill=None, max_memories=8, planning=None,
                           max_event_payload_bytes=None, stale_after_seconds=3.0,
                           working_knowledge=(), max_working_knowledge=24, max_working_knowledge_bytes=8192,
                           selection: ContextSelection | None = None) -> DecisionContext:
    if (not all(type(value) is int and value > 0 for value in (
            max_observations, max_events, max_payload_bytes, max_external_bytes)) or
            type(max_memories) is not int or max_memories < 0 or
            type(max_working_knowledge) is not int or not 0 <= max_working_knowledge <= 128 or
            type(max_working_knowledge_bytes) is not int or max_working_knowledge_bytes < 256 or
            type(cognitive_revision) is not int or cognitive_revision < 0 or
            not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0 or
            (max_event_payload_bytes is not None and (type(max_event_payload_bytes) is not int or
                                                      max_event_payload_bytes <= 0))):
        raise ValueError("Decision snapshot limits must be positive")
    now = time.monotonic() if now is None else now
    if not math.isfinite(now):
        raise ValueError("Decision snapshot time must be finite")
    max_event_payload_bytes = max_event_payload_bytes or max_payload_bytes
    if selection is not None and not isinstance(selection, ContextSelection):
        raise ValueError("Installed context selection must use ContextSelection")
    selection = selection or ContextSelection()
    world = session.world
    if (previous_skill_result and previous_skill_result.get("data_policy") in ("session_only", "sensitive") and
            previous_skill_result.get("expires_at_unix_ms", 0) and
            previous_skill_result["expires_at_unix_ms"] <= int(time.time() * 1000)):
        previous_skill_result = {**previous_skill_result, "output": {"expired": True}, "details": {"expired": True}}
    if isinstance(current_skill, str):
        current_skill = {"skill_id": current_skill}
    elif hasattr(current_skill, "model_dump"):
        current_skill = current_skill.model_dump(mode="json")
    current_skill = _preview(current_skill, max_payload_bytes) if current_skill is not None else None
    skill_id = (current_skill or previous_skill_result or {}).get("skill_id")
    skill = next((item for item in catalog if item.get("id") == skill_id), {})
    relevant_caps = set(skill.get("required_capabilities", ())) | set(skill.get("owned_command_capabilities", ()))
    keywords = _words(objective.text) | _words(current_skill) | _words(previous_skill_result)
    def overlap(item):
        text = item.capability_id + " " + item.entity_id
        if not isinstance(item.payload, OpaquePayload) and item.payload.ByteSize() <= max_payload_bytes:
            text += " " + json.dumps(MessageToDict(item.payload, preserving_proto_field_name=False))
        return len(keywords & _words(text))
    all_observations = list(world.observations.values())
    candidates = [entry for entry in all_observations if selection.observation_capability_ids is None or
                  entry.capability_id in selection.observation_capability_ids]
    observations = sorted(candidates,
                          key=lambda item: (item.capability_id not in relevant_caps,
                                            now - item.received_at > stale_after_seconds,
                                            -overlap(item), item.entity_id != world.self_actor_id,
                                            item.entity_id, item.capability_id))
    triggers = list({_identity(event): event for event in trigger_events}.values())
    trigger_keys = {_identity(event) for event in triggers}
    events = [event for event in (world.events if routed_events is None else routed_events)
              if _identity(event) not in trigger_keys and (routed_events is not None or
                                                         getattr(event, "entry", event).sequence > event_cursor)]
    events = list({_identity(event): event for event in events}.values())
    if routed_events is None:
        events.sort(key=lambda item: _identity(item))
        selected_events = events[-max_events:]
    else:
        def event_priority(event):
            assessment = getattr(event, "assessment", None)
            router = getattr(session, "event_router", None)
            priority = router.context_priority(event, {"current_skill": current_skill,
                "previous_skill_result": previous_skill_result}) if router is not None else 0
            return ({"routine": 0, "important": 1, "critical": 2}.get(getattr(assessment, "urgency", "routine"), 0),
                    priority,
                    {"unrelated": 0, "uncertain": 1, "relevant": 2}.get(
                        getattr(assessment, "relevance", "uncertain"), 1),
                    overlap(event.entry), event.entry.sequence)
        selected_events = sorted(events, key=event_priority, reverse=True)[:max_events]
        selected_events.sort(key=_identity)
    observation_records = [_record(entry, now, max_payload_bytes, stale_after_seconds=stale_after_seconds,
                                   omit_default_fields=selection.omit_default_fields)
                           for entry in observations[:max_observations]]
    event_records = [_record(entry, now, max_event_payload_bytes, stale_after_seconds=stale_after_seconds)
                     for entry in selected_events]
    trigger_records = [_record(entry, now, max_event_payload_bytes, trigger=True,
                               stale_after_seconds=stale_after_seconds) for entry in sorted(triggers, key=_identity)]
    now_wall = time.time()
    memory_values = list(memories)
    unexpired_memories = [entry for entry in memory_values if (
        entry.expires_at if hasattr(entry, "expires_at") else entry.get("expires_at", now_wall + 1)) > now_wall]
    memory_records = [_memory_record(entry, now_wall, max_payload_bytes) for entry in unexpired_memories[:max_memories]]
    if previous_skill_result is not None and _encoded_size(previous_skill_result) > max_payload_bytes:
        previous_skill_result = {key: previous_skill_result[key]
                                 for key in ("skill_id", "status", "reason") if key in previous_skill_result}
        previous_skill_result["output_omitted"] = True
    manifest = session.manifest
    from .knowledge import select_knowledge
    knowledge_values = list(working_knowledge)
    selected_knowledge = select_knowledge(knowledge_values, max_entries=max_working_knowledge,
                                         max_bytes=max_working_knowledge_bytes)
    context = DecisionContext(
        objective=objective.model_copy(deep=True), decision_index=decision_index,
        game=GameMetadata(game_id=manifest.game_id[:160], game_version=manifest.game_version[:80],
                          protocol_major=manifest.protocol_version.major,
                          protocol_minor=manifest.protocol_version.minor),
        actor_id=world.self_actor_id, observations=observation_records, recent_events=event_records,
        previous_skill_result=previous_skill_result, available_skills=catalog,
        validation_error=validation_error,
        observations_omitted=max(0, len(all_observations) - len(observation_records)),
        events_omitted=max(0, len(events) - len(event_records)),
        trigger_events=trigger_records, memories=memory_records,
        memories_omitted=max(0, len(memory_values) - len(memory_records)),
        decision_reason=(decision_reason or ("objective_started" if previous_skill_result is None else
            "skill_succeeded" if previous_skill_result.get("status") == "succeeded" else "skill_failed"))[:128],
        cognitive_revision=cognitive_revision, current_skill=current_skill,
        current_plan=(session.executable_plan.snapshot() if getattr(session, "executable_plan", None) is not None else None),
        planning=planning,
        working_knowledge=selected_knowledge, knowledge_omitted=len(knowledge_values)-len(selected_knowledge),
    )
    # Trigger data and the previous result survive ordinary budget pressure.
    # A budget unable to contain those mandatory facts fails explicitly.
    while _encoded_size(context.external_state()) > max_external_bytes:
        if context.recent_events:
            if routed_events is not None:
                priority_by_identity = {_identity(event): event_priority(event) for event in selected_events}
                victim = min(context.recent_events, key=lambda entry: priority_by_identity[
                    (entry.sequence, entry.revision, entry.entity_id, entry.capability_id)])
            else:
                victim = context.recent_events[0]
            context = context.model_copy(update={"recent_events": [entry for entry in context.recent_events if entry is not victim],
                                                  "events_omitted": context.events_omitted + 1})
            continue
        if context.memories:
            context = context.model_copy(update={"memories": context.memories[:-1],
                                                  "memories_omitted": context.memories_omitted + 1})
            continue
        if context.working_knowledge:
            retained = select_knowledge(context.working_knowledge[:-1], max_entries=max_working_knowledge,
                                         max_bytes=max_working_knowledge_bytes)
            context = context.model_copy(update={"working_knowledge": retained,
                "knowledge_omitted": context.knowledge_omitted + len(context.working_knowledge)-len(retained)})
            continue
        candidates = [("observations", i, _encoded_size(item.value))
                      for i, item in enumerate(context.observations) if item.value is not None]
        if candidates:
            field, index, _ = max(candidates, key=lambda item: item[1])
            values = list(getattr(context, field))
            values[index] = values[index].model_copy(update={"value": None, "truncated": True})
            context = context.model_copy(update={field: values})
        elif context.observations:
            context = context.model_copy(update={"observations": context.observations[:-1],
                                                  "observations_omitted": context.observations_omitted + 1})
        elif context.previous_skill_result and not context.previous_skill_result.get("output_omitted"):
            compact = {key: context.previous_skill_result[key] for key in ("skill_id", "status", "reason")
                       if key in context.previous_skill_result}
            compact["output_omitted"] = True
            context = context.model_copy(update={"previous_skill_result": compact})
        else:
            raise ValueError("Decision snapshot budget cannot hold required metadata, trigger data and previous result")
    return context
