"""Versioned model inputs; actor names carry identity, not strategic meaning."""
from __future__ import annotations

from .contract import ClassifierInput, render_input

CANONICAL_RENDERER = "features.render_canonical_v1"
ACTOR_TARGET_ACTIONS = {"attack", "approach_target"}


def _actor_slots(data):
    plan = data["plan"]
    if plan["action"] in ACTOR_TARGET_ACTIONS:
        yield plan, "target"
    event = data["event"]
    for key in ("actor", "body", "victim"):
        if key in event["payload"]:
            yield event["payload"], key
    if event["kind"] in {"attack_result", "target_moved"} and "target" in event["payload"]:
        yield event["payload"], "target"
    for observation in data["observations"]:
        if observation["kind"] in {"visible_body", "previous_body_observation"}:
            yield observation, "value"
        elif observation["kind"] == "visible_players" and isinstance(observation["value"], list):
            for index in range(len(observation["value"])):
                yield observation["value"], index


def actor_ids(value: ClassifierInput) -> list[str]:
    found = []
    for container, key in _actor_slots(value.model_dump()):
        identity = container[key]
        if isinstance(identity, str) and identity and identity not in found:
            found.append(identity)
    return found


def canonicalize_actors(value: ClassifierInput) -> ClassifierInput:
    data = value.model_dump()
    mapping = {identity: f"actor{index + 1}" for index, identity in enumerate(actor_ids(value))}
    for container, key in _actor_slots(data):
        identity = container[key]
        if isinstance(identity, str) and identity in mapping:
            container[key] = mapping[identity]
    return ClassifierInput.model_validate(data)


def render_canonical_v1(value: ClassifierInput) -> str:
    # Rewrite explicit 0.1 actor-reference fields only. Free text, rooms, tasks,
    # provenance, ages and missing values retain their original meaning.
    return render_input(canonicalize_actors(value))


def get_renderer(name: str):
    if name == "contract.render_input":
        return render_input
    if name == CANONICAL_RENDERER:
        return render_canonical_v1
    raise ValueError(f"Unsupported input renderer: {name}")
