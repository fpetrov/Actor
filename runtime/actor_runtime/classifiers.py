"""Provider-neutral System-1 assessments. Providers never execute game actions."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from typing import Literal, Protocol, Callable

from pydantic import BaseModel, ConfigDict, Field

from .errors import ActorError


class EventAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    relevance: Literal["unrelated", "relevant", "uncertain"] = "uncertain"
    urgency: Literal["routine", "important", "critical"] = "routine"
    should_buffer: bool = True
    should_remember: bool = False
    should_interrupt_skill: bool = False
    should_request_brain_review: bool = False
    confidence: float = Field(default=0.0, ge=0, le=1)
    provider: str = Field(default="rules", min_length=1, max_length=128)
    reason_code: str = Field(default="unknown_event", min_length=1, max_length=128)


@dataclass(frozen=True)
class ClassificationContext:
    objective: object
    current_skill: object = None
    relevant_state: tuple = ()
    policy_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventHints:
    """Only trusted installed game code supplies these, never payload prose."""
    priority: int = 0
    potentially_critical: bool = False
    mandatory_review: bool = False
    semantic_id: str = ""
    aggregation_key: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventRule:
    capability_id: str
    extension_id: str
    type_url: str
    hints: Callable
    assess: Callable


class EventRuleRegistry:
    def __init__(self, rules=()):
        self.rules = {}
        for rule in rules:
            self.register(rule)

    def register(self, rule):
        key = rule.capability_id, rule.extension_id, rule.type_url
        if key in self.rules:
            raise ActorError("event_rule_duplicate", "Duplicate installed event rule")
        self.rules[key] = rule

    def lookup(self, event):
        return self.rules.get((event.entry.capability_id, event.extension_id, event.type_url))

    @classmethod
    def discover(cls):
        registry = cls()
        for point in sorted(metadata.entry_points(group="actor.event_rules"), key=lambda p: p.name):
            for rule in point.load()():
                registry.register(rule)
        return registry


class EventClassifier(Protocol):
    async def classify(self, context: ClassificationContext, event) -> EventAssessment: ...
    async def close(self): ...


class RuleBasedEventClassifier:
    def __init__(self, rules=None):
        self.rules = rules or EventRuleRegistry.discover()
        self.calls = 0

    async def classify(self, context, event):
        self.calls += 1
        rule = self.rules.lookup(event)
        if rule is None:
            return EventAssessment(reason_code="unregistered_optional_event")
        return EventAssessment.model_validate(rule.assess(context, event))

    async def close(self):
        pass

    def report(self):
        return {"provider": "rules", "calls": self.calls, "external_requests": 0}


class ClassifierRegistry:
    """Future local/ONNX providers use the same async surface and entry point."""
    @staticmethod
    def create(name, **options):
        points = metadata.entry_points(group="actor.classifiers", name=name)
        if len(points) != 1:
            raise ActorError("classifier_missing", "Exactly one installed classifier must match", {"provider": name})
        return next(iter(points)).load()(**options)


def rules_factory(**options):
    return RuleBasedEventClassifier(options.get("rules"))
