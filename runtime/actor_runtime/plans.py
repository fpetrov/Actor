"""Bounded accepted plans, executed through the existing foreground Scheduler."""
from copy import deepcopy
import asyncio
import time
from uuid import uuid4

from google.protobuf import descriptor_pool
from actor.core.v1 import actor_pb2 as core

from .brains import SkillPlan
from .errors import ActorError


def validate_plan(plan, registry, session):
    if getattr(session, "executable_plan", None) is not None:
        raise ActorError("plan_busy", "A plan already owns this session")
    if plan.conditions and (session.event_router is None or not session.event_router.config.enabled):
        raise ActorError("plan_condition_invalid", "Conditional plans require the event router")
    for call in [*plan.steps, *(r.response for r in plan.conditions), *([plan.fallback] if plan.fallback else [])]:
        registry.validate_call(session, call.skill_id, call.arguments)
    for condition in plan.conditions:
        capability = session.capabilities.get(condition.capability_id)
        if capability is None or capability.kind not in (core.CAPABILITY_KIND_EVENT, core.CAPABILITY_KIND_OBSERVATION):
            raise ActorError("plan_condition_invalid", "Plan conditions require a negotiated observation or event")
        if capability.extension_id not in session.negotiated.available:
            raise ActorError("plan_condition_invalid", "Opaque data cannot supply a plan condition")
        descriptor = descriptor_pool.Default().FindMessageTypeByName(capability.output_type_url.split("/", 1)[-1])
        field = descriptor.fields_by_name.get(condition.field)
        if field is None or not (field.is_repeated or field.type == field.TYPE_STRING):
            raise ActorError("plan_condition_invalid", "Nonempty conditions require a declared repeated or string field")


class ExecutablePlan:
    def __init__(self, decision: SkillPlan):
        self.decision = SkillPlan.model_validate(decision.model_dump())
        self.id = uuid4().hex
        self.index = 0
        self.state = "active"
        self.current = self.decision.steps[0]
        self.used_conditions = set()
        self.pending = None
        self.triggered = None
        self.changed = asyncio.Event()
        self.fallback_used = False
        self._observations_offered = set()

    def observation_candidates(self, entries, own_actor_id):
        if self.state != "active":
            return []
        selected = {}
        for index, condition in enumerate(self.decision.conditions):
            if index in self.used_conditions or index in self._observations_offered:
                continue
            for entry in entries:
                if (entry.entity_id == own_actor_id and entry.capability_id == condition.capability_id
                        and time.monotonic() - entry.received_at <= 2
                        and condition.field in entry.payload.DESCRIPTOR.fields_by_name
                        and bool(getattr(entry.payload, condition.field))):
                    self._observations_offered.add(index)
                    selected[entry.capability_id] = entry
                    break
        return list(selected.values())

    def snapshot(self):
        return deepcopy({"schema_version": "actor.executable-plan.v1", "id": self.id, "state": self.state,
            "step_index": self.index, "current": self.current.model_dump(mode="json"),
            "remaining_steps": [s.model_dump(mode="json") for s in self.decision.steps[self.index + 1:]] if self.state == "active" else [],
            "conditions": [c.model_dump(mode="json") for i, c in enumerate(self.decision.conditions) if i not in self.used_conditions],
            "fallback": self.decision.fallback.model_dump(mode="json") if self.decision.fallback and not self.fallback_used else None,
            "triggered_condition": self.triggered})

    def covers(self, capability_id, field):
        return any(c.capability_id == capability_id and c.field == field and
                   (i not in self.used_conditions or self.triggered == i)
                   for i, c in enumerate(self.decision.conditions))

    def offer(self, event, own_actor_id):
        if (self.pending is not None or self.state != "active" or event.hints.potentially_critical
                or event.hints.mandatory_review or event.entry.entity_id != own_actor_id
                or time.monotonic() - event.entry.received_at > 2):
            return False
        for index, condition in enumerate(self.decision.conditions):
            if index in self.used_conditions or event.entry.capability_id != condition.capability_id:
                continue
            payload = event.entry.payload
            if condition.field in payload.DESCRIPTOR.fields_by_name and bool(getattr(payload, condition.field)):
                self.pending = index
                self.used_conditions.add(index)
                self.changed.set()
                return True
        return False

    def take_reaction(self):
        if self.pending is None:
            return False
        self.triggered = self.pending
        self.current = self.decision.conditions[self.pending].response
        self.pending = None
        self.changed.clear()
        self.state = "reaction"
        return True

    def advance(self, result):
        if self.take_reaction():
            return True
        if self.state != "active":
            self.state = "completed" if result.status == "succeeded" else "failed"
            return False
        if result.status != "succeeded":
            if self.decision.fallback is not None and not self.fallback_used:
                self.current = self.decision.fallback
                self.fallback_used = True
                self.state = "fallback"
                return True
            self.state = "failed"
            return False
        self.index += 1
        if self.index >= len(self.decision.steps):
            self.state = "completed"
            return False
        self.current = self.decision.steps[self.index]
        return True
