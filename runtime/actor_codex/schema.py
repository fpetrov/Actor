"""Convert the provider-independent Skill catalog into Codex's supported subset."""
from __future__ import annotations

from actor_runtime.brains import build_decision_schema
from actor_runtime.errors import ActorError


def _strict(value):
    if isinstance(value, list):
        return [_strict(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {("anyOf" if key == "oneOf" else key): _strict(item)
              for key, item in value.items() if key not in ("default", "$schema", "discriminator")}
    if result.get("type") == "object":
        if result.get("additionalProperties") is True or isinstance(result.get("additionalProperties"), dict):
            raise ActorError("codex_schema_unsupported", "Codex structured Skills require closed argument objects")
        result["additionalProperties"] = False
        result["required"] = list(result.get("properties", {}))
    return result


def decision_schema(catalog: list[dict]) -> dict:
    source = build_decision_schema(catalog)
    # Codex forbids root unions. Nested anyOf retains each Skill's exact input schema.
    return _strict({"type": "object", "additionalProperties": False,
        "properties": {"decision": {"anyOf": source["oneOf"]}}, "required": ["decision"],
        **({"$defs": source["$defs"]} if "$defs" in source else {})})
