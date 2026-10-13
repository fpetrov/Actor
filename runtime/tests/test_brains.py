import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import Field, ValidationError

from actor_runtime.brains import (BrainRegistry, DECISION_ADAPTER, FinishObjective,
                                  InvalidBrainDecision, SequenceBrain, SkillCall, build_decision_schema)
from actor_runtime.objectives import Objective, ObjectiveRunner, ObjectiveStatus
from actor_runtime.skills import (ActorSkill, CapabilityRequirement, SkillDefinition, SkillError,
                                  SkillModel, SkillRegistry)
from actor_runtime.builtin_skills import skill_package as builtin_skills
from actor_standard import LOCOMOTION, Locomotion2DCommand


class CountInput(SkillModel):
    amount: int = Field(ge=1, le=10)


class CountOutput(SkillModel):
    count: int


class CountSkill(ActorSkill):
    def __init__(self, *, fail=False, blocked=False):
        self.fail, self.blocked = fail, blocked
        self.called = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cleaned = False

    async def execute(self, context, arguments):
        self.called.append(arguments)
        self.started.set()
        if self.fail:
            raise SkillError("counter_failed", "Synthetic Skill failed")
        while self.blocked and not self.release.is_set():
            await context.sleep(0.01)
        return CountOutput(count=arguments.amount)

    async def cleanup(self, context):
        self.cleaned = True


def count_registry(skill):
    registry = SkillRegistry()
    registry.register(SkillDefinition("test.increment-counter", "1.0.0", "Increment a typed synthetic counter.",
                                     CountInput, CountOutput, lambda: skill))
    for definition in builtin_skills():
        registry.register(definition)
    return registry


def call(amount=2):
    return SkillCall(skill_id="test.increment-counter", arguments={"amount": amount})


class HangingBrain(SequenceBrain):
    def __init__(self):
        super().__init__([])
        self.started_turn = asyncio.Event()
        self.turn_cancelled = False

    async def decide(self, context):
        self.turn_count += 1
        self.started_turn.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.turn_cancelled = True
            raise


class OwnedDrive(ActorSkill):
    def __init__(self):
        self.started = asyncio.Event()
        self.cleaned = False

    async def execute(self, context, arguments):
        self.started.set()
        while True:
            context.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=1))
            await context.sleep(0.01)

    async def cleanup(self, context):
        self.cleaned = True
        try:
            context.command(LOCOMOTION, Locomotion2DCommand())
        except SkillError as error:
            if error.code != "session_disconnected":
                raise


def owned_drive_registry(skill):
    registry = SkillRegistry()
    registry.register(SkillDefinition("test.drive", "1.0.0", "Exercise owned movement cancellation.",
        CountInput, CountOutput, lambda: skill,
        (CapabilityRequirement(LOCOMOTION, Locomotion2DCommand, "command"),), (LOCOMOTION,)))
    return registry


def assert_zero_intent(session):
    item = next(iter(session.mailbox.commands.values()))
    value = Locomotion2DCommand()
    assert item.command.payload.Unpack(value)
    assert value.desired_move_x == value.desired_move_y == 0


def test_brain_plugin_is_discovered_without_core_branch():
    point = SimpleNamespace(name="synthetic", load=lambda: lambda **_: SequenceBrain([FinishObjective(status="success")]))
    registry = BrainRegistry.discover([point])
    assert isinstance(registry.create("synthetic"), SequenceBrain)
    with pytest.raises(Exception, match="brain_unavailable"):
        registry.create("missing")
    with pytest.raises(Exception, match="brain_plugin_invalid"):
        registry.register("synthetic", lambda: None)


def test_async_brain_factory_and_sync_lifecycle_are_rejected_without_start(session):
    async def async_factory():
        return SequenceBrain([])
    registry = BrainRegistry()
    with pytest.raises(Exception, match="brain_plugin_invalid"):
        registry.register("async-factory", async_factory)
    class SyncBrain(SequenceBrain):
        def close(self):
            return None
    registry.register("sync-lifecycle", lambda: SyncBrain([]))
    with pytest.raises(Exception, match="brain_plugin_invalid"):
        registry.create("sync-lifecycle")
    with pytest.raises(Exception, match="brain_plugin_invalid"):
        ObjectiveRunner(session, count_registry(CountSkill()), SyncBrain([]))


@pytest.mark.parametrize("candidate", [
    {"kind": "other"}, {"kind": "skill", "skill_id": "actor.wait"},
    {"kind": "finish", "status": "maybe"},
    {"kind": "finish", "status": "success", "arbitrary_action": "bad"},
    {"kind": "skill", "skill_id": 42, "arguments": {}},
])
def test_structured_decision_rejects_unknown_or_invalid_shape(candidate):
    with pytest.raises(ValidationError):
        DECISION_ADAPTER.validate_python(candidate)


def test_schema_generation_is_deterministic_and_uses_typed_models(session):
    registry = count_registry(CountSkill())
    catalog = registry.catalog(session)
    first = build_decision_schema(catalog)
    assert first == build_decision_schema(list(reversed(catalog)))
    counter = next(branch for branch in first["oneOf"] if branch["properties"].get("skill_id", {}).get("const") == "test.increment-counter")
    assert counter["properties"]["arguments"]["properties"]["amount"]["maximum"] == 10
    assert counter["properties"]["arguments"]["required"] == ["amount"]
    with pytest.raises(Exception, match="skill_catalog_invalid"):
        build_decision_schema([catalog[0], catalog[0]])


def test_nested_schema_definitions_are_relocated_without_collision():
    schema = {"type": "object", "$defs": {"Nested": {"type": "object", "properties": {"number": {"type": "integer"}}}},
              "properties": {"item": {"$ref": "#/$defs/Nested"}}}
    result = build_decision_schema([{"id": "test.a", "input_schema": schema},
                                    {"id": "test.b", "input_schema": schema}])
    assert set(result["$defs"]) == {"skill_0_Nested", "skill_1_Nested"}
    assert result["oneOf"][0]["properties"]["arguments"]["properties"]["item"]["$ref"] == "#/$defs/skill_0_Nested"
    assert "$defs" in schema  # caller's installed metadata is untouched


async def test_brain_skill_brain_finish_uses_real_scheduler(session):
    skill = CountSkill()
    brain = SequenceBrain([call(), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, count_registry(skill), brain)
    result = await runner.run(Objective(text="Increment once and finish."))
    assert result.status == ObjectiveStatus.SUCCEEDED and result.decision_count == 2
    assert result.skill_results[0]["output"] == {"count": 2}
    assert skill.cleaned and isinstance(skill.called[0], CountInput)
    assert brain.contexts[1].previous_skill_result["status"] == "succeeded"
    assert runner.report()["brain"]["decisions"] == 2
    assert brain.closed


async def test_no_brain_invocations_while_skill_is_running(session):
    skill = CountSkill(blocked=True)
    brain = SequenceBrain([call(), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, count_registry(skill), brain)
    task = asyncio.create_task(runner.run(Objective(text="Complete the blocked counter.")))
    await asyncio.wait_for(skill.started.wait(), timeout=1)
    for _ in range(10):
        await session.world.notify()
        await asyncio.sleep(0.005)
    assert brain.turn_count == 1
    assert runner.scheduler.report()["active_skill_id"] == "test.increment-counter"
    skill.release.set()
    assert (await task).status == ObjectiveStatus.SUCCEEDED
    assert brain.turn_count == 2 and skill.cleaned


async def test_invalid_skill_call_gets_one_bounded_repair(session):
    skill = CountSkill()
    brain = SequenceBrain([SkillCall(skill_id="missing", arguments={}), call(), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, count_registry(skill), brain)
    result = await runner.run(Objective(text="Increment once."))
    assert result.status == ObjectiveStatus.SUCCEEDED and result.decision_count == 3
    assert len(skill.called) == 1
    assert brain.contexts[1].validation_error["code"] == "unknown_skill"
    assert "validation_error" in brain.contexts[1].external_state()


async def test_invalid_decision_shape_repair_is_json_serializable(session):
    brain = SequenceBrain([{"kind": "unsupported"}, FinishObjective(status="success")])
    runner = ObjectiveRunner(session, count_registry(CountSkill()), brain)
    assert (await runner.run(Objective(text="Finish safely."))).status == ObjectiveStatus.SUCCEEDED
    context = brain.contexts[1]
    assert context.validation_error["code"] == "invalid_brain_decision"
    json.loads(context.external_json())


async def test_provider_parse_error_uses_same_bounded_repair(session):
    class ParseErrorBrain(SequenceBrain):
        async def decide(self, context):
            if self.turn_count == 0:
                self.turn_count += 1
                raise InvalidBrainDecision()
            return await super().decide(context)
    brain = ParseErrorBrain([FinishObjective(status="success")])
    result = await ObjectiveRunner(session, count_registry(CountSkill()), brain).run(Objective(text="Finish."))
    assert result.status == ObjectiveStatus.SUCCEEDED and result.decision_count == 2
    assert brain.contexts[0].validation_error["code"] == "invalid_brain_decision"


async def test_repeated_invalid_arguments_fail_without_skill_implementation(session):
    skill = CountSkill()
    brain = SequenceBrain([call("bad"), call(999), FinishObjective(status="success")])
    result = await ObjectiveRunner(session, count_registry(skill), brain).run(Objective(text="Increment."))
    assert result.status == ObjectiveStatus.FAILED and result.reason == "brain_failure"
    assert result.decision_count == 2 and not skill.called


async def test_sequence_brain_does_not_report_success_after_failed_skill(session):
    skill = CountSkill(fail=True)
    brain = SequenceBrain([call(), FinishObjective(status="success")])
    result = await ObjectiveRunner(session, count_registry(skill), brain).run(Objective(text="Increment."))
    assert result.status == ObjectiveStatus.FAILED and result.reason == "brain_reported_failure"
    assert result.skill_results[0]["reason"] == "counter_failed"
    assert result.decision_count == 2 and skill.cleaned


async def test_brain_timeout_interrupts_turn_and_closes_provider(session):
    brain = HangingBrain()
    runner = ObjectiveRunner(session, count_registry(CountSkill()), brain, decision_timeout_seconds=0.03)
    result = await asyncio.wait_for(runner.run(Objective(text="Finish.")), timeout=1)
    assert result.status == ObjectiveStatus.FAILED and result.reason == "brain_timeout"
    assert result.decision_count == 1
    assert brain.interrupted and brain.closed and brain.turn_cancelled


async def test_objective_deadline_cancels_foreground_skill_before_next_brain_call(session):
    skill = CountSkill(blocked=True)
    brain = SequenceBrain([call(), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, count_registry(skill), brain, objective_timeout_seconds=0.05)
    result = await asyncio.wait_for(runner.run(Objective(text="Increment.")), timeout=1)
    assert result.status == ObjectiveStatus.FAILED and result.reason == "objective_timeout"
    assert result.skill_results[-1]["status"] == "cancelled"
    assert skill.cleaned and brain.turn_count == 1


async def test_cancel_during_brain_turn_has_no_skill_start(session):
    skill = CountSkill()
    brain = HangingBrain()
    runner = ObjectiveRunner(session, count_registry(skill), brain)
    task = asyncio.create_task(runner.run(Objective(text="Increment.")))
    await brain.started_turn.wait()
    await asyncio.wait_for(runner.cancel(), timeout=1)
    result = await task
    assert result.status == ObjectiveStatus.CANCELLED
    assert not skill.called and brain.turn_cancelled and brain.closed


async def test_interrupt_retrieves_a_provider_exception_completed_during_abort(session):
    from actor_runtime.errors import ActorError
    class InterruptedProvider(SequenceBrain):
        def __init__(self):
            super().__init__([])
            self.started_turn = asyncio.Event()
            self.interrupt_requested = asyncio.Event()
            self.decide_finished = asyncio.Event()

        async def decide(self, context):
            self.started_turn.set()
            try:
                await self.interrupt_requested.wait()
                raise ActorError("provider_turn_incomplete", "Turn ended interrupted")
            finally:
                self.decide_finished.set()

        async def interrupt(self):
            self.interrupted = True
            self.interrupt_requested.set()
            await self.decide_finished.wait()

    brain = InterruptedProvider()
    skill = CountSkill()
    runner = ObjectiveRunner(session, count_registry(skill), brain)
    task = asyncio.create_task(runner.run(Objective(text="Increment safely.")))
    await brain.started_turn.wait()
    provider_task = runner._active_task
    assert provider_task is not None
    await asyncio.wait_for(runner.cancel(), timeout=1)
    result = await task
    assert result.status == ObjectiveStatus.CANCELLED and not skill.called
    assert provider_task.done()
    was_retrieved = not provider_task._log_traceback
    assert isinstance(provider_task.exception(), ActorError)
    assert was_retrieved  # cleanup consumed it before the test inspected the exception


async def test_cancellation_during_skill_waits_for_cleanup(session):
    skill = CountSkill(blocked=True)
    brain = SequenceBrain([call(), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, count_registry(skill), brain)
    task = asyncio.create_task(runner.run(Objective(text="Increment.")))
    await skill.started.wait()
    await asyncio.wait_for(runner.cancel(), timeout=1)
    assert (await task).status == ObjectiveStatus.CANCELLED
    assert skill.cleaned and brain.turn_count == 1
    assert len(runner.report()["skills"]) == 1


@pytest.mark.parametrize("ending", ["cancel", "deadline", "disconnect"])
async def test_objective_terminal_paths_clean_owned_movement(session, ending):
    skill = OwnedDrive()
    brain = SequenceBrain([SkillCall(skill_id="test.drive", arguments={"amount": 1}),
                           FinishObjective(status="success")])
    runner = ObjectiveRunner(session, owned_drive_registry(skill), brain,
                             objective_timeout_seconds=0.05 if ending == "deadline" else 10)
    task = asyncio.create_task(runner.run(Objective(text="Drive safely.")))
    await skill.started.wait()
    await asyncio.sleep(0.02)
    assert brain.turn_count == 1
    if ending == "cancel":
        await runner.cancel()
    elif ending == "disconnect":
        session.close()
    result = await asyncio.wait_for(task, timeout=1)
    assert result.status == (ObjectiveStatus.CANCELLED if ending == "cancel" else ObjectiveStatus.FAILED)
    assert skill.cleaned and brain.turn_count == 1
    assert runner.scheduler.report()["active_skill_id"] == ""
    assert result.skill_results[-1]["status"] in ("cancelled", "failed")
    if ending == "disconnect":
        assert not session.mailbox.commands
    else:
        assert_zero_intent(session)


async def test_disconnect_wins_a_race_with_completed_brain_decision(session):
    class DisconnectBrain(SequenceBrain):
        async def decide(self, context):
            session.close()
            return call()
    skill = CountSkill()
    brain = DisconnectBrain([])
    result = await ObjectiveRunner(session, count_registry(skill), brain).run(Objective(text="Increment."))
    assert result.status == ObjectiveStatus.FAILED and result.reason == "session_disconnected"
    assert not skill.called and brain.closed


async def test_external_task_cancellation_returns_structured_cancelled_result(session):
    brain = HangingBrain()
    runner = ObjectiveRunner(session, count_registry(CountSkill()), brain)
    task = asyncio.create_task(runner.run(Objective(text="Finish.")))
    await brain.started_turn.wait()
    task.cancel()
    result = await task
    assert result.status == ObjectiveStatus.CANCELLED and brain.turn_cancelled


async def test_runner_can_execute_a_new_episode_in_the_same_caller_task(session):
    skill = CountSkill()
    brain = SequenceBrain([call(), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, count_registry(skill), brain)
    first = await runner.run(Objective(text="Increment first."))
    second = await runner.run(Objective(text="Increment second."))
    assert first.status == second.status == ObjectiveStatus.SUCCEEDED
    assert len(skill.called) == 2 and first.objective_id != second.objective_id
    await asyncio.wait_for(runner.cancel(), timeout=0.1)  # never waits for the long-lived caller task


async def test_decision_limit_bounds_a_brain_that_never_finishes(session):
    brain = SequenceBrain([SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 0.0})] * 5)
    result = await ObjectiveRunner(session, count_registry(CountSkill()), brain, max_decisions=2).run(Objective(text="Wait."))
    assert result.status == ObjectiveStatus.FAILED and result.reason == "decision_limit"
    assert result.decision_count == 2 and len(result.skill_results) == 2


@pytest.mark.parametrize("option,value", [("decision_timeout_seconds", float("nan")),
                                         ("objective_timeout_seconds", float("inf")),
                                         ("objective_timeout_seconds", 0), ("max_decisions", True)])
def test_runner_deadlines_and_limits_are_finite(session, option, value):
    with pytest.raises(ValueError):
        ObjectiveRunner(session, count_registry(CountSkill()), SequenceBrain([]), **{option: value})


async def test_provider_close_failure_is_controlled_and_does_not_leak_exception_text(session):
    class BadCloseBrain(SequenceBrain):
        async def close(self):
            raise RuntimeError("private provider detail")
    runner = ObjectiveRunner(session, count_registry(CountSkill()), BadCloseBrain([FinishObjective(status="success")]))
    result = await runner.run(Objective(text="Finish."))
    assert result.status == ObjectiveStatus.FAILED and result.reason == "cleanup_failed"
    assert runner.report()["cleanup_errors"] == ["brain_close_failed"]
    assert "private provider detail" not in json.dumps(runner.report())
