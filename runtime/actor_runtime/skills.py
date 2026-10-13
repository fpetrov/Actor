"""Typed Runtime procedures over negotiated capabilities; no game or Brain ontology."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from importlib import metadata
import inspect
import json
import logging
import math
import re
import time
from typing import Callable, Literal, TypeVar
import weakref

from google.protobuf.message import Message
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from actor.core.v1 import actor_pb2 as core
from .errors import ActorError
from .extensions import type_url
from .session import RuntimeSession
from .world import CachedPayload

LOG = logging.getLogger("actor_runtime")
T = TypeVar("T", bound=Message)
SkillStatus = Literal["succeeded", "failed", "cancelled", "timed_out"]


class SkillModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False, frozen=True)


class SkillResult(SkillModel):
    skill_id: str
    skill_version: str = ""
    status: SkillStatus
    reason: str = ""
    output: dict = Field(default_factory=dict)
    duration_seconds: float = Field(default=0, ge=0)
    commands_sent: int = Field(default=0, ge=0)
    details: dict = Field(default_factory=dict)


class SkillError(ActorError):
    """Machine-readable failure; exception text never supplies semantic decisions."""


@dataclass(frozen=True)
class CapabilityRequirement:
    capability_id: str
    message_type: type[Message]
    kind: Literal["observation", "command", "event"]
    max_age_seconds: float | None = None


class ActorSkill:
    def preflight(self, context: SkillContext, arguments: BaseModel) -> None:
        """Optional synchronous, read-only game-specific preconditions."""

    async def execute(self, context: SkillContext, arguments: BaseModel) -> BaseModel:
        raise NotImplementedError

    async def cleanup(self, context: SkillContext) -> None:
        """Always called after execution rights are revoked, including cancellation."""


@dataclass(frozen=True)
class SkillDefinition:
    skill_id: str
    version: str
    description: str
    input_model: type[BaseModel]
    result_model: type[BaseModel]
    factory: Callable[[], ActorSkill]
    required_capabilities: tuple[CapabilityRequirement, ...] = ()
    owned_command_capabilities: tuple[str, ...] = ()
    timeout_seconds: float = 60
    cleanup_timeout_seconds: float = 1


class SkillRegistry:
    def __init__(self):
        self.definitions: dict[str, SkillDefinition] = {}

    @classmethod
    def discover(cls, entry_points=None) -> SkillRegistry:
        registry = cls()
        points = metadata.entry_points(group="actor.skills") if entry_points is None else entry_points
        for point in sorted(points, key=lambda item: item.name):
            try:
                factory = point.load()
                if inspect.iscoroutinefunction(factory):
                    raise SkillError("skill_plugin_invalid", "Skill package factory must be synchronous")
                for definition in factory():
                    registry.register(definition)
            except SkillError:
                raise
            except Exception as error:
                raise SkillError("skill_plugin_invalid", "Installed skill plugin could not load",
                                 {"plugin": point.name, "exception_type": type(error).__name__}) from error
        return registry

    def register(self, definition: SkillDefinition):
        if not isinstance(definition, SkillDefinition):
            raise SkillError("skill_definition_invalid", "Plugin must supply SkillDefinition instances")
        if definition.skill_id in self.definitions:
            previous = self.definitions[definition.skill_id]
            code = "duplicate_skill" if previous.version == definition.version else "skill_version_conflict"
            raise SkillError(code, "A skill ID must be registered once", {"skill_id": definition.skill_id})
        if (not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", definition.skill_id) or
                not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", definition.version) or
                not definition.description or len(definition.description) > 2000 or not callable(definition.factory) or
                inspect.iscoroutinefunction(definition.factory)):
            raise SkillError("skill_definition_invalid", "Invalid skill identity/version/description/factory")
        for model in (definition.input_model, definition.result_model):
            if (not isinstance(model, type) or not issubclass(model, BaseModel) or
                    model.model_config.get("strict") is not True or model.model_config.get("extra") != "forbid" or
                    model.model_config.get("allow_inf_nan") is not False):
                raise SkillError("skill_schema_invalid", "Skill models require strict, finite, extra-forbid validation")
            try:
                schema = model.model_json_schema()
                if schema.get("type") != "object":
                    raise ValueError("Object model required")
                json.dumps(schema, allow_nan=False)
            except Exception as error:
                raise SkillError("skill_schema_invalid", "Skill model cannot produce an object JSON Schema") from error
        ids = set()
        for requirement in definition.required_capabilities:
            if (not isinstance(requirement, CapabilityRequirement) or not requirement.capability_id or
                    requirement.capability_id in ids or requirement.kind not in ("observation", "command", "event") or
                    not isinstance(requirement.message_type, type) or not issubclass(requirement.message_type, Message)):
                raise SkillError("skill_definition_invalid", "Invalid or duplicate capability requirement")
            ids.add(requirement.capability_id)
            if requirement.max_age_seconds is not None and (
                requirement.kind != "observation" or not math.isfinite(requirement.max_age_seconds) or requirement.max_age_seconds <= 0
            ):
                raise SkillError("skill_definition_invalid", "Freshness applies only to observations and must be positive")
        commands = {item.capability_id for item in definition.required_capabilities if item.kind == "command"}
        if (len(set(definition.owned_command_capabilities)) != len(definition.owned_command_capabilities) or
                not set(definition.owned_command_capabilities) <= commands):
            raise SkillError("skill_definition_invalid", "Owned commands must be unique declared command requirements")
        if any(not math.isfinite(value) or value <= 0 or value > 3600
               for value in (definition.timeout_seconds, definition.cleanup_timeout_seconds)):
            raise SkillError("skill_definition_invalid", "Skill lifecycle deadlines must be finite and bounded")
        self.definitions[definition.skill_id] = definition

    def definition(self, skill_id: str) -> SkillDefinition:
        try:
            return self.definitions[skill_id]
        except KeyError as error:
            raise SkillError("unknown_skill", "Requested skill is not installed", {"skill_id": skill_id}) from error

    def create(self, skill_id: str) -> ActorSkill:
        definition = self.definition(skill_id)
        skill = definition.factory()
        if not isinstance(skill, ActorSkill):
            if inspect.iscoroutine(skill): skill.close()
            raise SkillError("skill_plugin_invalid", "Skill factory returned an invalid implementation")
        if (not inspect.iscoroutinefunction(skill.execute) or not inspect.iscoroutinefunction(skill.cleanup) or
                inspect.iscoroutinefunction(skill.preflight)):
            raise SkillError("skill_plugin_invalid", "Skills need async execute/cleanup and synchronous preflight")
        if definition.owned_command_capabilities and type(skill).cleanup is ActorSkill.cleanup:
            raise SkillError("skill_cleanup_missing", "A skill owning commands must implement explicit cleanup")
        return skill

    @staticmethod
    def _compatible(session: RuntimeSession, definition: SkillDefinition):
        if not session.ready.is_set() or session.closed.is_set() or session.mailbox.closed:
            raise SkillError("session_disconnected", "A negotiated live session is required")
        for requirement in definition.required_capabilities:
            capability = session.capabilities.get(requirement.capability_id)
            kind = {"observation": core.CAPABILITY_KIND_OBSERVATION,
                    "command": core.CAPABILITY_KIND_COMMAND, "event": core.CAPABILITY_KIND_EVENT}[requirement.kind]
            if capability is None or capability.kind != kind:
                raise SkillError("required_capability_missing", "Required capability is unavailable",
                                 {"capability_id": requirement.capability_id})
            expected = capability.input_type_url if requirement.kind == "command" else capability.output_type_url
            if expected != type_url(requirement.message_type) or capability.extension_id not in session.negotiated.available:
                raise SkillError("required_capability_incompatible", "Capability type/extension is not decoded",
                                 {"capability_id": requirement.capability_id})

    def catalog(self, session: RuntimeSession) -> list[dict]:
        result = []
        for skill_id, definition in sorted(self.definitions.items()):
            try:
                self._compatible(session, definition)
            except SkillError:
                continue
            result.append({"id": skill_id, "version": definition.version, "description": definition.description,
                "input_schema": definition.input_model.model_json_schema(),
                "result_schema": definition.result_model.model_json_schema(),
                "required_capabilities": [item.capability_id for item in definition.required_capabilities],
                "owned_command_capabilities": list(definition.owned_command_capabilities),
                "timeout_seconds": definition.timeout_seconds})
        return result

    def validate_call(self, session: RuntimeSession, skill_id: str, arguments: dict) -> BaseModel:
        definition = self.definition(skill_id)
        self._compatible(session, definition)
        try:
            if not isinstance(arguments, dict):
                raise TypeError("Arguments must be an object")
            typed = definition.input_model.model_validate(arguments, strict=True)
            json.dumps(typed.model_dump(mode="json"), allow_nan=False)
            return typed
        except (ValidationError, TypeError, ValueError) as error:
            details = {"skill_id": skill_id}
            if isinstance(error, ValidationError):
                details["validation"] = json.dumps(error.errors(include_input=False, include_url=False), default=str)[:2000]
            raise SkillError("invalid_arguments", "Skill arguments do not match the typed input schema", details) from error


@dataclass
class _Lease:
    active: bool = False
    cleanup_task: asyncio.Task | None = None
    commands_sent: int = 0
    cancellation: asyncio.Event = field(default_factory=asyncio.Event)


_FOREGROUND: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


class SkillContext:
    """A revocable capability view. Skills receive no raw transport or mutable world cache."""
    def __init__(self, session: RuntimeSession, definition: SkillDefinition, lease: _Lease,
                 clock: Callable[[], float] = time.monotonic):
        self._session, self._definition, self._lease, self._clock = session, definition, lease, clock
        self._requirements = {item.capability_id: item for item in definition.required_capabilities}

    @property
    def actor_id(self) -> str:
        return self._session.world.self_actor_id

    def now(self) -> float:
        return self._clock()

    @property
    def cancelled(self) -> bool:
        return self._lease.cancellation.is_set()

    def _live(self):
        if self._session.closed.is_set() or self._session.mailbox.closed:
            raise SkillError("session_disconnected", "Game session disconnected")

    def _require(self, capability_id: str, message_type: type[Message], kind: str) -> CapabilityRequirement:
        requirement = self._requirements.get(capability_id)
        if requirement is None or requirement.kind != kind or type_url(requirement.message_type) != type_url(message_type):
            raise SkillError("capability_access_denied", "Skill did not declare this typed capability",
                             {"capability_id": capability_id})
        return requirement

    def observation_entry(self, capability_id: str, message_type: type[T], *, max_age_seconds: float | None = None) -> CachedPayload:
        self._live()
        requirement = self._require(capability_id, message_type, "observation")
        entry = self._session.world.latest_entry(message_type, capability_id=capability_id)
        if entry is None:
            raise SkillError("required_observation_missing", "Required observation is not available", {"capability_id": capability_id})
        maximum = requirement.max_age_seconds if max_age_seconds is None else max_age_seconds
        if maximum is not None and (not math.isfinite(maximum) or maximum <= 0):
            raise SkillError("invalid_freshness", "Observation age limit must be finite and positive")
        if requirement.max_age_seconds is not None and maximum is not None:
            maximum = min(maximum, requirement.max_age_seconds)
        if maximum is not None and self.now() - entry.received_at > maximum:
            raise SkillError("stale_world_state", "Required observation is stale", {"capability_id": capability_id})
        return entry

    def observation(self, capability_id: str, message_type: type[T], *, max_age_seconds: float | None = None) -> T:
        return self.observation_entry(capability_id, message_type, max_age_seconds=max_age_seconds).payload

    def events(self, capability_id: str, message_type: type[T], *, after_sequence: int = 0) -> list[CachedPayload]:
        self._live()
        self._require(capability_id, message_type, "event")
        result = []
        for event in self._session.world.events:
            if (event.capability_id == capability_id and event.entity_id == self.actor_id and event.sequence > after_sequence
                    and isinstance(event.payload, Message) and type_url(type(event.payload)) == type_url(message_type)):
                copy = message_type(); copy.CopyFrom(event.payload)
                result.append(CachedPayload(event.entity_id, event.capability_id, event.sequence, event.revision, copy, event.received_at))
        return result

    def command(self, capability_id: str, payload: Message, *, ttl_ms: int | None = None) -> int:
        self._require(capability_id, type(payload), "command")
        cleanup = self._lease.cleanup_task is asyncio.current_task()
        if (capability_id not in self._definition.owned_command_capabilities or
                (not self._lease.active and not cleanup) or (self.cancelled and not cleanup)):
            raise SkillError("command_ownership_lost", "Skill does not own this command capability")
        self._live()
        try:
            result = self._session.command(capability_id, payload, ttl_ms=ttl_ms)
        except ActorError as error:
            raise SkillError("session_disconnected" if error.code == "SessionUnavailable" else "command_failed",
                             "Typed command could not be issued", {"capability_id": capability_id, "code": error.code}) from error
        self._lease.commands_sent += 1
        return result

    async def sleep(self, seconds: float):
        if not math.isfinite(seconds) or seconds < 0:
            raise SkillError("invalid_duration", "Sleep duration must be finite and nonnegative")
        self._live()
        if self.cancelled:
            raise asyncio.CancelledError
        sleepers = [asyncio.create_task(asyncio.sleep(seconds)), asyncio.create_task(self._session.closed.wait()),
                    asyncio.create_task(self._lease.cancellation.wait())]
        try:
            done, _ = await asyncio.wait(sleepers, return_when=asyncio.FIRST_COMPLETED)
            if self._session.closed.is_set():
                raise SkillError("session_disconnected", "Game session disconnected")
            if self.cancelled:
                raise asyncio.CancelledError
        finally:
            for task in sleepers: task.cancel()
            await asyncio.gather(*sleepers, return_exceptions=True)

    async def wait_until(self, predicate: Callable[[SkillContext], bool], *, timeout_seconds: float, interval_seconds: float = 0.05):
        if any(not math.isfinite(value) or value <= 0 for value in (timeout_seconds, interval_seconds)):
            raise SkillError("invalid_duration", "Condition deadlines/intervals must be finite and positive")
        deadline = self.now() + timeout_seconds
        while not predicate(self):
            if self.now() >= deadline:
                raise SkillError("condition_timeout", "World condition did not become true before its deadline")
            await self.sleep(min(interval_seconds, max(0, deadline - self.now())))


class SkillScheduler:
    def __init__(self, session: RuntimeSession, registry: SkillRegistry, *, clock: Callable[[], float] = time.monotonic):
        self.session, self.registry, self._clock = session, registry, clock
        self._task: asyncio.Task | None = None
        self._lease: _Lease | None = None
        self._history = deque(maxlen=64)
        self._lifecycle = deque(maxlen=256)
        self._active_id = ""

    def report(self) -> dict:
        return {"active_skill_id": self._active_id, "skills": list(self._history), "events": list(self._lifecycle),
                "skill_count": sum(item.get("started", False) for item in self._history),
                "failed_skill_count": sum(item["status"] != "succeeded" for item in self._history)}

    def _event(self, event: str, skill_id: str):
        self._lifecycle.append({"event": event, "skill_id": skill_id, "at_monotonic": self._clock()})
        LOG.info("%s skill=%s", event, skill_id)

    def _result(self, skill_id: str, status: SkillStatus, reason: str, *, version="", started=0.0,
                output=None, commands=0, details=None, did_start=False) -> SkillResult:
        result = SkillResult(skill_id=skill_id, skill_version=version, status=status, reason=reason,
            output=output or {}, duration_seconds=max(0, self._clock() - started) if did_start else 0.0,
            commands_sent=commands, details=details or {})
        record = result.model_dump(mode="json"); record["started"] = did_start
        self._history.append(record)
        return result

    async def run(self, skill_id: str, arguments: dict) -> SkillResult:
        try:
            typed = self.registry.validate_call(self.session, skill_id, arguments)
            definition = self.registry.definition(skill_id)
            if self._task is not None and not self._task.done() or self.session in _FOREGROUND:
                raise SkillError("command_ownership_conflict", "A foreground skill already owns this session")
            lease = _Lease()
            context = SkillContext(self.session, definition, lease, self._clock)
            for requirement in definition.required_capabilities:
                if requirement.kind == "observation":
                    context.observation_entry(requirement.capability_id, requirement.message_type)
            skill = self.registry.create(skill_id)
            skill.preflight(context, typed)
        except SkillError as error:
            known = self.registry.definitions.get(skill_id)
            return self._result(skill_id, "failed", error.code, version=known.version if known else "", details=error.details)
        except Exception as error:
            return self._result(skill_id, "failed", "skill_precondition_failed", details={"exception_type": type(error).__name__})
        lease.active = True
        _FOREGROUND[self.session] = lease
        self._lease, self._active_id = lease, skill_id
        self._task = asyncio.create_task(self._perform(skill, definition, context, typed, lease))
        try:
            return await asyncio.shield(self._task)
        except asyncio.CancelledError:
            lease.cancellation.set()
            current = asyncio.current_task()
            if current is not None: current.uncancel()
            return await asyncio.shield(self._task)

    async def _perform(self, skill: ActorSkill, definition: SkillDefinition, context: SkillContext,
                       typed: BaseModel, lease: _Lease) -> SkillResult:
        started = self._clock()
        self._event("SkillStarted", definition.skill_id)
        status, reason, output, details = "succeeded", "", {}, {}
        execute = asyncio.create_task(skill.execute(context, typed))
        disconnected = asyncio.create_task(self.session.closed.wait())
        cancelled = asyncio.create_task(lease.cancellation.wait())
        timeout = asyncio.create_task(asyncio.sleep(definition.timeout_seconds))
        try:
            done, _ = await asyncio.wait((execute, disconnected, cancelled, timeout), return_when=asyncio.FIRST_COMPLETED)
            if self.session.closed.is_set():
                raise SkillError("session_disconnected", "Game session disconnected during skill")
            if lease.cancellation.is_set():
                status, reason = "cancelled", "cancelled"
            elif timeout in done and execute not in done:
                status, reason = "timed_out", "timeout"
            else:
                value = await execute
                if not isinstance(value, definition.result_model):
                    raise SkillError("invalid_skill_result", "Implementation did not return its typed result model")
                output = definition.result_model.model_validate(value.model_dump(), strict=True).model_dump(mode="json")
                json.dumps(output, allow_nan=False)
        except asyncio.CancelledError:
            status, reason = "cancelled", "cancelled"
            current = asyncio.current_task()
            if current is not None: current.uncancel()
        except SkillError as error:
            status, reason, details = "failed", error.code, error.details
        except Exception as error:
            status, reason, details = "failed", "skill_exception", {"exception_type": type(error).__name__}
        finally:
            lease.active = False
            lease.cancellation.set()
            execute.cancel(); disconnected.cancel(); cancelled.cancel(); timeout.cancel()
            ended, pending = await asyncio.wait((execute, disconnected, cancelled, timeout),
                                               timeout=definition.cleanup_timeout_seconds)
            for task in ended:
                if not task.cancelled(): task.exception()
            for task in pending:
                task.add_done_callback(lambda finished: None if finished.cancelled() else finished.exception())
            if pending: details["execution_cleanup_reason"] = "task_did_not_stop"
            cleanup = asyncio.create_task(skill.cleanup(context))
            lease.cleanup_task = cleanup
            try:
                await asyncio.wait_for(asyncio.shield(cleanup), timeout=definition.cleanup_timeout_seconds)
            except SkillError as error:
                details["cleanup_reason"] = error.code
                if status == "succeeded": status, reason = "failed", "cleanup_failed"
            except Exception as error:
                cleanup.cancel()
                cleanup.add_done_callback(lambda finished: None if finished.cancelled() else finished.exception())
                details["cleanup_reason"] = "cleanup_timeout" if isinstance(error, TimeoutError) else "cleanup_exception"
                if status == "succeeded": status, reason = "failed", "cleanup_failed"
            finally:
                lease.cleanup_task = None
                if _FOREGROUND.get(self.session) is lease: _FOREGROUND.pop(self.session, None)
                if self._lease is lease: self._lease, self._active_id = None, ""
        self._event({"succeeded": "SkillSucceeded", "failed": "SkillFailed", "cancelled": "SkillCancelled", "timed_out": "SkillTimedOut"}[status], definition.skill_id)
        return self._result(definition.skill_id, status, reason, version=definition.version, started=started,
                            output=output, commands=lease.commands_sent, details=details, did_start=True)

    async def cancel(self):
        task, lease = self._task, self._lease
        if task is not None and not task.done():
            if lease is not None: lease.cancellation.set()
            await asyncio.shield(task)
