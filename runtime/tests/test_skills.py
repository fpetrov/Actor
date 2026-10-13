"""Typed plugin discovery and foreground lifecycle without an LLM or game transport."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from actor_runtime.skills import (ActorSkill, CapabilityRequirement, SkillDefinition, SkillError,
                                  SkillModel, SkillRegistry, SkillScheduler)
from actor_runtime.builtin_skills import skill_package as builtins
from actor_standard import LOCOMOTION, Locomotion2DCommand


class Empty(SkillModel):
    pass


class Done(SkillModel):
    done: bool


class CounterInput(SkillModel):
    amount: int = Field(ge=1, le=10)


class CounterOutput(SkillModel):
    count: int


class Counter(ActorSkill):
    called = []
    async def execute(self, context, arguments: CounterInput):
        self.called.append(arguments)
        return CounterOutput(count=arguments.amount)


def counter_definition():
    return SkillDefinition("test.increment-counter", "1.0.0", "Increment a synthetic typed counter.",
                           CounterInput, CounterOutput, Counter)


class Drive(ActorSkill):
    def __init__(self, mode="success"):
        self.mode = mode
        self.context = None
        self.cleanups = 0
        self.started = asyncio.Event()

    async def execute(self, context, arguments):
        self.context = context
        context.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=1))
        self.started.set()
        if self.mode == "failure":
            raise SkillError("route_failed", "Synthetic route failed")
        if self.mode == "exception":
            raise RuntimeError("Not a machine-readable status")
        if self.mode == "invalid_result":
            return {"done": True}
        await context.sleep(0.01 if self.mode == "success" else 20)
        return Done(done=True)

    async def cleanup(self, context):
        self.cleanups += 1
        try:
            context.command(LOCOMOTION, Locomotion2DCommand())
        except SkillError as error:
            if error.code != "session_disconnected": raise


def drive_registry(skill, timeout=0.2):
    registry = SkillRegistry()
    registry.register(SkillDefinition("test.drive", "1.0.0", "Exercise owned intent lifecycle.", Empty, Done,
        lambda: skill, (CapabilityRequirement(LOCOMOTION, Locomotion2DCommand, "command"),),
        (LOCOMOTION,), timeout_seconds=timeout, cleanup_timeout_seconds=0.1))
    return registry


def pending_intent(session):
    outgoing = next(iter(session.mailbox.commands.values()))
    value = Locomotion2DCommand(); assert outgoing.command.payload.Unpack(value)
    return value


def point(name, factory):
    return SimpleNamespace(name=name, load=lambda: factory)


def test_synthetic_skill_plugin_discovery_without_core_branch():
    registry = SkillRegistry.discover([point("synthetic", lambda: (counter_definition(),)), point("generic", builtins)])
    assert list(sorted(registry.definitions)) == ["actor.wait", "test.increment-counter"]
    assert isinstance(registry.create("test.increment-counter"), Counter)


def test_duplicates_and_conflicting_versions_reject():
    registry = SkillRegistry()
    registry.register(counter_definition())
    with pytest.raises(SkillError, match="duplicate_skill"):
        registry.register(counter_definition())
    with pytest.raises(SkillError, match="skill_version_conflict"):
        registry.register(replace(counter_definition(), version="2.0.0"))


def test_invalid_model_schema_and_undeclared_command_ownership_reject():
    class Lax(BaseModel):
        value: int
    registry = SkillRegistry()
    with pytest.raises(SkillError, match="skill_schema_invalid"):
        registry.register(replace(counter_definition(), input_model=Lax))
    with pytest.raises(SkillError, match="skill_definition_invalid"):
        registry.register(replace(counter_definition(), owned_command_capabilities=(LOCOMOTION,)))


async def test_catalog_deterministic_and_excludes_unavailable_capability(session):
    registry = drive_registry(Drive())
    registry.register(builtins()[0])
    first = registry.catalog(session)
    assert first == registry.catalog(session)
    assert [item["id"] for item in first] == ["actor.wait", "test.drive"]
    assert first[0]["input_schema"]["additionalProperties"] is False
    assert first[1]["owned_command_capabilities"] == [LOCOMOTION]
    session.capabilities.pop(LOCOMOTION)
    assert [item["id"] for item in registry.catalog(session)] == ["actor.wait"]


@pytest.mark.parametrize("arguments", [{}, {"amount": "2"}, {"amount": True}, {"amount": 0}, {"amount": 11},
                                       {"amount": 2, "unknown": 1}])
async def test_invalid_typed_arguments_never_reach_implementation(session, arguments):
    Counter.called.clear()
    registry = SkillRegistry(); registry.register(counter_definition())
    scheduler = SkillScheduler(session, registry)
    result = await scheduler.run("test.increment-counter", arguments)
    assert result.status == "failed" and result.reason == "invalid_arguments"
    assert Counter.called == []
    assert scheduler.report()["events"] == []


@pytest.mark.parametrize("duration", ["2", True, -0.1, 301, float("nan"), float("inf")])
async def test_wait_arguments_finite_strict_and_bounded(session, duration):
    registry = SkillRegistry(); registry.register(builtins()[0])
    result = await SkillScheduler(session, registry).run("actor.wait", {"duration_seconds": duration})
    assert result.status == "failed" and result.reason == "invalid_arguments"


async def test_wait_monotonic_duration_and_generic_success(session):
    registry = SkillRegistry(); registry.register(builtins()[0])
    result = await SkillScheduler(session, registry).run("actor.wait", {"duration_seconds": 0.02})
    assert result.status == "succeeded"
    assert result.output["waited_seconds"] >= 0.02
    assert result.commands_sent == 0


@pytest.mark.parametrize("mode,status,reason", [("success", "succeeded", ""), ("failure", "failed", "route_failed"),
    ("exception", "failed", "skill_exception"), ("invalid_result", "failed", "invalid_skill_result"),
    ("hang", "timed_out", "timeout")])
async def test_all_execution_paths_cleanup_zero_and_revoke_rights(session, mode, status, reason):
    skill = Drive(mode)
    scheduler = SkillScheduler(session, drive_registry(skill, timeout=0.03))
    result = await scheduler.run("test.drive", {})
    assert (result.status, result.reason) == (status, reason)
    assert skill.cleanups == 1
    assert pending_intent(session).desired_move_x == 0
    assert result.commands_sent == 2
    with pytest.raises(SkillError, match="command_ownership_lost"):
        skill.context.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=1))
    assert scheduler.report()["active_skill_id"] == ""


async def test_cancel_waits_cleanup_and_releases_foreground(session):
    skill = Drive("hang")
    scheduler = SkillScheduler(session, drive_registry(skill))
    task = asyncio.create_task(scheduler.run("test.drive", {}))
    await skill.started.wait()
    await scheduler.cancel()
    result = await task
    assert result.status == "cancelled"
    assert skill.cleanups == 1 and pending_intent(session).desired_move_x == 0
    registry = SkillRegistry(); registry.register(builtins()[0])
    assert (await SkillScheduler(session, registry).run("actor.wait", {"duration_seconds": 0})).status == "succeeded"


async def test_direct_run_task_cancellation_returns_controlled_result_after_cleanup(session):
    skill = Drive("hang")
    scheduler = SkillScheduler(session, drive_registry(skill))
    task = asyncio.create_task(scheduler.run("test.drive", {}))
    await skill.started.wait()
    task.cancel()
    result = await task
    assert result.status == "cancelled" and not task.cancelled()
    assert pending_intent(session).desired_move_x == 0


async def test_cancel_before_execute_begins_cannot_leak_foreground_lease(session):
    skill = Drive("hang")
    scheduler = SkillScheduler(session, drive_registry(skill))
    task = asyncio.create_task(scheduler.run("test.drive", {}))
    await asyncio.sleep(0)
    await scheduler.cancel()
    assert (await task).status == "cancelled"
    registry = SkillRegistry(); registry.register(builtins()[0])
    assert (await SkillScheduler(session, registry).run("actor.wait", {"duration_seconds": 0})).status == "succeeded"


async def test_foreground_ownership_shared_across_schedulers(session):
    skill = Drive("hang")
    first = SkillScheduler(session, drive_registry(skill))
    task = asyncio.create_task(first.run("test.drive", {})); await skill.started.wait()
    second = SkillScheduler(session, drive_registry(Drive()))
    result = await second.run("test.drive", {})
    assert result.status == "failed" and result.reason == "command_ownership_conflict"
    await first.cancel(); await task


async def test_session_disconnect_fails_and_cleanup_executes(session):
    skill = Drive("hang")
    scheduler = SkillScheduler(session, drive_registry(skill))
    task = asyncio.create_task(scheduler.run("test.drive", {})); await skill.started.wait()
    session.close()
    result = await task
    assert result.status == "failed" and result.reason == "session_disconnected"
    assert skill.cleanups == 1
    with pytest.raises(SkillError): skill.context.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=1))


async def test_prerequisite_failure_does_not_start_skill_or_issue_commands(session):
    skill = Drive()
    scheduler = SkillScheduler(session, drive_registry(skill))
    session.capabilities.pop(LOCOMOTION)
    result = await scheduler.run("test.drive", {})
    assert result.reason == "required_capability_missing"
    assert skill.cleanups == 0 and session.next_command_id == 0
    assert scheduler.report()["events"] == []


async def test_command_owner_without_explicit_cleanup_cannot_start(session):
    class UnsafeDrive(ActorSkill):
        async def execute(self, context, arguments):
            context.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=1))
            return Done(done=True)
    definition = replace(drive_registry(Drive()).definition("test.drive"), factory=UnsafeDrive)
    registry = SkillRegistry(); registry.register(definition)
    result = await SkillScheduler(session, registry).run("test.drive", {})
    assert result.reason == "skill_cleanup_missing" and session.next_command_id == 0


@pytest.mark.parametrize("method", ["execute", "cleanup", "preflight"])
async def test_invalid_lifecycle_signature_cannot_start_or_leak_foreground(session, method):
    class InvalidLifecycle(ActorSkill):
        async def execute(self, context, arguments):
            return CounterOutput(count=arguments.amount)
    if method == "execute":
        InvalidLifecycle.execute = lambda self, context, arguments: CounterOutput(count=arguments.amount)
    elif method == "cleanup":
        InvalidLifecycle.cleanup = lambda self, context: None
    else:
        async def async_preflight(self, context, arguments): pass
        InvalidLifecycle.preflight = async_preflight
    registry = SkillRegistry()
    registry.register(replace(counter_definition(), factory=InvalidLifecycle))
    scheduler = SkillScheduler(session, registry)
    result = await scheduler.run("test.increment-counter", {"amount": 1})
    assert result.reason == "skill_plugin_invalid" and scheduler.report()["events"] == []
    generic = SkillRegistry(); generic.register(builtins()[0])
    assert (await SkillScheduler(session, generic).run("actor.wait", {"duration_seconds": 0})).status == "succeeded"
