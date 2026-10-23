"""Versioned input and labelled-example contracts for the neural review classifier."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Event(Record):
    kind: str
    source: Literal["direct_vision", "server_public", "server_private", "own_action_result"]
    payload: dict[str, JsonValue]


class Observation(Record):
    kind: str
    source: Literal["direct_vision", "server_public", "own_state", "memory"]
    age_ms: int = Field(ge=0)
    value: JsonValue


class Plan(Record):
    action: str
    target: str | None
    conditions: list[str]
    next_action: str | None
    fallback: str | None


class ClassifierInput(Record):
    schema_version: Literal["0.1", "0.2"]
    game_id: str
    ruleset: str
    own_role: Literal["crew", "impostor"] | None
    phase: Literal["tasks", "meeting"] | None
    objective: str
    plan: Plan
    event: Event
    observations: list[Observation] = Field(max_length=12)


class Example(Record):
    id: str
    family: str
    split: Literal["train", "validation", "test"]
    source: Literal["synthetic"]
    annotation_status: Literal["draft", "reviewed", "derived_from_reviewed_seed"]
    review_reference: str | None = None
    parent_seed_id: str | None = None
    episode_id: str | None = None
    template_id: str | None = None
    integration_status: Literal["proposed_among_us_contract"]
    label: Literal[0, 1]
    description_ru: str
    reason_ru: str
    input: ClassifierInput


def compact(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def render_input(value: ClassifierInput) -> str:
    """The identical renderer must be used during training and live inference."""
    plan = value.plan
    lines = [
        f"Game: {value.game_id}. Rules: {value.ruleset}.",
        f"Role: {value.own_role or 'unknown'}. Phase: {value.phase or 'unknown'}.",
        f"Goal: {value.objective}",
        f"Action: {plan.action}. Target: {plan.target or 'unspecified'}.",
        f"Conditions: {'; '.join(plan.conditions) or 'unspecified'}.",
        f"Next: {plan.next_action or 'unspecified'}. Fallback: {plan.fallback or 'unspecified'}.",
        f"Event: {value.event.kind}. Source: {value.event.source}. {compact(value.event.payload)}",
    ]
    for observation in value.observations:
        lines.append(
            f"Observed {observation.kind}: {compact(observation.value)} "
            f"({observation.source}, age {observation.age_ms} ms)."
        )
    return "\n".join(lines)


def load_examples(path):
    return [Example.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]
