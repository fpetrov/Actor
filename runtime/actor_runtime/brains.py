"""Provider-independent structured decisions and installed Brain discovery."""

from __future__ import annotations

from copy import deepcopy
from importlib import metadata
import inspect
import json
from typing import Annotated, Any, Literal, Protocol, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from .errors import ActorError

if TYPE_CHECKING:
    from .objectives import Objective
    from .snapshots import DecisionContext


class SkillCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False,
                             revalidate_instances="always")
    kind: Literal["skill"] = "skill"
    skill_id: str = Field(min_length=1, max_length=160)
    arguments: dict[str, JsonValue]


class FinishObjective(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False,
                             revalidate_instances="always")
    kind: Literal["finish"] = "finish"
    status: Literal["success", "failure"]
    reason: str | None = Field(default=None, max_length=512)


BrainDecision = Annotated[SkillCall | FinishObjective, Field(discriminator="kind")]
DECISION_ADAPTER = TypeAdapter(BrainDecision)


class InvalidBrainDecision(ActorError):
    """Provider parsing failed; the Runner may request a bounded repair decision."""
    def __init__(self, message="Provider output did not match the decision schema", details=None):
        super().__init__("invalid_brain_decision", message, details or {})


class Brain(Protocol):
    async def start(self, objective: Objective, catalog: list[dict]) -> None: ...
    async def decide(self, context: DecisionContext) -> BrainDecision: ...
    async def interrupt(self) -> None: ...
    async def close(self) -> None: ...


def build_decision_schema(catalog: list[dict]) -> dict:
    """Specialize each SkillCall branch from its typed input model's JSON Schema."""
    branches, definitions, seen = [], {}, set()
    for index, skill in enumerate(sorted(catalog, key=lambda item: item["id"])):
        skill_id = skill["id"]
        if not isinstance(skill_id, str) or not skill_id or skill_id in seen:
            raise ActorError("skill_catalog_invalid", "Skill IDs must be nonempty and unique")
        seen.add(skill_id)
        arguments = deepcopy(skill.get("input_schema"))
        if not isinstance(arguments, dict) or arguments.get("type") != "object":
            raise ActorError("skill_catalog_invalid", "Skill input schema must describe an object",
                             {"skill_id": skill_id})
        local_definitions = arguments.pop("$defs", {})
        if not isinstance(local_definitions, dict):
            raise ActorError("skill_catalog_invalid", "Input schema definitions must be an object")
        renamed = {name: f"skill_{index}_{name}" for name in local_definitions}

        def relocate(value):
            if isinstance(value, list):
                return [relocate(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {key: relocate(item) for key, item in value.items()}
            reference = result.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                name_and_tail = reference[len("#/$defs/"):].split("/", 1)
                name = name_and_tail[0].replace("~1", "/").replace("~0", "~")
                if name not in renamed:
                    raise ActorError("skill_catalog_invalid", "Input schema contains an unresolved local reference",
                                     {"skill_id": skill_id})
                escaped = renamed[name].replace("~", "~0").replace("/", "~1")
                suffix = "/" + name_and_tail[1] if len(name_and_tail) == 2 else ""
                result["$ref"] = "#/$defs/" + escaped + suffix
            return result

        for name, definition in local_definitions.items():
            definitions[renamed[name]] = relocate(definition)
        branches.append({
            "type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"type": "string", "const": "skill"},
                "skill_id": {"type": "string", "const": skill_id},
                "arguments": relocate(arguments),
            },
            "required": ["kind", "skill_id", "arguments"],
        })
    finish = FinishObjective.model_json_schema()
    finish["required"] = ["kind", "status"]
    branches.append(finish)
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
              "title": "ACTOR BrainDecision", "type": "object", "oneOf": branches}
    if definitions:
        schema["$defs"] = definitions
    return schema


class BrainRegistry:
    def __init__(self):
        self.factories = {}

    @classmethod
    def discover(cls, entry_points=None):
        registry = cls()
        points = metadata.entry_points(group="actor.brains") if entry_points is None else entry_points
        for point in sorted(points, key=lambda item: item.name):
            try:
                registry.register(point.name, point.load())
            except ActorError:
                raise
            except Exception as error:
                raise ActorError("brain_plugin_invalid", "Installed Brain factory could not load",
                                 {"brain_id": point.name, "reason": str(error)}) from error
        return registry

    def register(self, brain_id, factory):
        if (not brain_id or brain_id in self.factories or not callable(factory) or
                inspect.iscoroutinefunction(factory) or inspect.iscoroutinefunction(getattr(factory, "__call__", None))):
            raise ActorError("brain_plugin_invalid", "Brain IDs must be unique and factories callable",
                             {"brain_id": str(brain_id)})
        self.factories[brain_id] = factory

    def create(self, brain_id: str, **options) -> Brain:
        if brain_id not in self.factories:
            raise ActorError("brain_unavailable", "Requested Brain is not installed", {"brain_id": brain_id})
        brain = self.factories[brain_id](**options)
        if inspect.iscoroutine(brain):
            brain.close()
            raise ActorError("brain_plugin_invalid", "Brain factories must return a provider synchronously")
        if any(not inspect.iscoroutinefunction(getattr(brain, name, None))
               for name in ("start", "decide", "interrupt", "close")):
            raise ActorError("brain_plugin_invalid", "Brain does not implement its lifecycle interface")
        return brain


class SequenceBrain:
    """A deterministic provider that still uses the production ObjectiveRunner."""

    def __init__(self, decisions: list[Any], *, stop_on_skill_failure=True):
        self.decisions = list(decisions)
        self.stop_on_skill_failure = stop_on_skill_failure
        self.index = 0
        self.turn_count = 0
        self.contexts = []
        self.interrupted = False
        self.closed = False

    async def start(self, objective, catalog):
        self.index = 0
        self.turn_count = 0
        self.contexts.clear()
        self.interrupted = False
        self.closed = False

    async def decide(self, context):
        if self.interrupted:
            raise ActorError("brain_interrupted", "SequenceBrain was interrupted")
        self.turn_count += 1
        self.contexts.append(context.model_copy(deep=True))
        previous = context.previous_skill_result
        if self.stop_on_skill_failure and previous and previous.get("status") != "succeeded":
            return FinishObjective(status="failure", reason=(
                f"Skill {previous.get('skill_id', 'unknown')} ended {previous.get('status', 'failed')}"
            )[:512])
        if self.index >= len(self.decisions):
            raise ActorError("brain_sequence_exhausted", "Deterministic decision sequence ended without Finish")
        value = self.decisions[self.index]
        self.index += 1
        return value

    async def interrupt(self):
        self.interrupted = True

    async def close(self):
        self.closed = True

    def report(self):
        return {"provider": "sequence", "decisions": self.turn_count,
                "interrupted": self.interrupted, "closed": self.closed}
