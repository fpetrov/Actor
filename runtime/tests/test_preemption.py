"""Strategic events reuse the production Runner/Scheduler, never a second loop."""
import asyncio

from google.protobuf.wrappers_pb2 import StringValue
import pytest

from actor.core.v1 import actor_pb2 as core
from actor_runtime.brains import FinishObjective, SequenceBrain, SkillCall
from actor_runtime.classifiers import EventAssessment, EventHints, EventRule, EventRuleRegistry
from actor_runtime.config import RouterConfig
from actor_runtime.events import EventRouter
from actor_runtime.extensions import type_url
from actor_runtime.objectives import Objective, ObjectiveRunner
from actor_runtime.world import CachedPayload
from test_brains import (CountSkill, OwnedDrive, assert_zero_intent, call,
                         count_registry, owned_drive_registry)


def routing(session, *, review_only=False):
    session.capabilities["test.signal"] = core.CapabilityDefinition(
        capability_id="test.signal", extension_id="actor.standard", kind=core.CAPABILITY_KIND_EVENT,
        output_type_url=type_url(StringValue))

    def hints(event):
        urgent = event.entry.payload.value.startswith("urgent:")
        return EventHints(priority=100 if urgent else 0, potentially_critical=urgent,
                          semantic_id=event.entry.payload.value,
                          aggregation_key="" if urgent else "routine")

    def assess(context, event):
        urgent = event.hints.potentially_critical
        return EventAssessment(relevance="relevant" if urgent else "unrelated",
            urgency="critical" if urgent else "routine", should_buffer=True,
            should_interrupt_skill=urgent and not review_only, should_request_brain_review=urgent,
            confidence=1.0, reason_code="typed_urgent" if urgent else "typed_routine")

    rules = EventRuleRegistry([EventRule("test.signal", "actor.standard", type_url(StringValue), hints, assess)])
    return EventRouter(session, rules=rules, config=RouterConfig(decision_cooldown=0))


def emit(router, number, value):
    router.ingest(CachedPayload("actor-1", "test.signal", number, number, StringValue(value=value)))


async def until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_wait_preemption_continues_objective_once_after_noisy_duplicate_burst(session):
    router = routing(session)
    brain = SequenceBrain([SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 20.0}),
                           FinishObjective(status="success")], stop_on_skill_failure=False)
    runner = ObjectiveRunner(session, count_registry(CountSkill()), brain, event_router=router)
    task = asyncio.create_task(runner.run(Objective(text="Wait until a relevant signal arrives.")))
    await until(lambda: runner.scheduler.report()["active_skill_id"] == "actor.wait")
    for index in range(1000):
        emit(router, index + 3, f"routine:{index}")
    await asyncio.sleep(0.02)
    assert brain.turn_count == 1 and not task.done()
    emit(router, 1004, "urgent:one")
    for index in range(25):
        emit(router, index + 1005, "urgent:one")
    result = await asyncio.wait_for(task, timeout=1)
    assert result.status == "succeeded" and result.decision_count == 2
    assert result.skill_results[0]["status"] == "cancelled"
    assert result.skill_results[0]["reason"] == "preempted_by_event"
    trigger_ids = result.skill_results[0]["details"]["trigger_event_ids"]
    assert len(trigger_ids) == 1
    context = brain.contexts[-1]
    assert context.decision_reason == "critical_event"
    assert [event.event_id for event in context.trigger_events] == trigger_ids
    assert context.trigger_events[0].value == "urgent:one"
    assert runner.report()["replan_count"] == 1
    assert router.report()["counts"]["events_duplicated"] == 25
    assert router.report()["queue_size"] <= router.config.max_queue_size


async def test_preemption_zeroes_owned_commands_before_next_decision_and_releases_lease(session):
    router, drive = routing(session), OwnedDrive()
    registry = owned_drive_registry(drive)

    class CheckCleanupBrain(SequenceBrain):
        async def decide(self, context):
            if self.turn_count:
                assert drive.cleaned
                assert_zero_intent(session)
                assert runner.scheduler.report()["active_skill_id"] == ""
            return await super().decide(context)

    brain = CheckCleanupBrain([SkillCall(skill_id="test.drive", arguments={"amount": 1}),
                               FinishObjective(status="success")], stop_on_skill_failure=False)
    runner = ObjectiveRunner(session, registry, brain, event_router=router)
    task = asyncio.create_task(runner.run(Objective(text="Drive until recalled.")))
    await drive.started.wait()
    emit(router, 3, "urgent:stop")
    result = await asyncio.wait_for(task, timeout=1)
    assert result.status == "succeeded" and drive.cleaned
    assert result.skill_results[0]["reason"] == "preempted_by_event"
    # A new scheduler for the same Session can reserve the released foreground.
    from actor_runtime.skills import SkillScheduler
    assert (await SkillScheduler(session, count_registry(CountSkill())).run("actor.wait", {"duration_seconds": 0.0})).status == "succeeded"


async def test_actual_navigation_skill_preemption_stops_and_allows_new_skill(session):
    from actor_agents_arena.skills import skill_package
    from actor_runtime.builtin_skills import skill_package as builtin_skills
    from actor_runtime.skills import SkillRegistry
    from test_navigation_skill import observe, zero_pending
    await observe(session, 3, x=-1.8)
    router, registry = routing(session), SkillRegistry()
    for definition in (*skill_package(), *builtin_skills()):
        registry.register(definition)
    brain = SequenceBrain([
        SkillCall(skill_id="agents-arena.navigate-to-room", arguments={"target_room_id": "engineering"}),
        SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 0.0}),
        FinishObjective(status="success")], stop_on_skill_failure=False)
    runner = ObjectiveRunner(session, registry, brain, event_router=router)
    task = asyncio.create_task(runner.run(Objective(text="Navigate, react to recall and stop.")))
    await until(lambda: session.next_command_id > 0)
    emit(router, 4, "urgent:navigation-stop")
    result = await asyncio.wait_for(task, timeout=1)
    assert result.status == "succeeded"
    assert [item["status"] for item in result.skill_results] == ["cancelled", "succeeded"]
    assert result.skill_results[0]["reason"] == "preempted_by_event"
    zero_pending(session)


async def test_critical_burst_during_reasoning_discards_stale_skill_and_stays_single_flight(session):
    router, skill = routing(session), CountSkill()

    class ReasoningBrain(SequenceBrain):
        def __init__(self):
            super().__init__([])
            self.entered, self.release = asyncio.Event(), asyncio.Event()
            self.active, self.max_active, self.soft_interrupts = 0, 0, 0

        async def decide(self, context):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                self.contexts.append(context)
                if len(self.contexts) == 1:
                    self.entered.set()
                    await self.release.wait()
                    return call()  # completed stale SDK output is never dispatched
                return FinishObjective(status="success")
            finally:
                self.active -= 1

        async def interrupt_decision(self):
            self.soft_interrupts += 1
            self.release.set()
            await asyncio.sleep(0)

    brain = ReasoningBrain()
    runner = ObjectiveRunner(session, count_registry(skill), brain, event_router=router)
    task = asyncio.create_task(runner.run(Objective(text="Respond to relevant signals.")))
    await brain.entered.wait()
    for index in range(5):
        emit(router, index + 3, f"urgent:{index}")
    result = await asyncio.wait_for(task, timeout=1)
    assert result.status == "succeeded" and not skill.called
    assert brain.max_active == brain.soft_interrupts == 1
    assert not brain.interrupted and brain.closed
    assert runner.report()["decisions"][0]["status"] == "stale"
    assert len(brain.contexts[-1].trigger_events) == 5
    assert brain.contexts[-1].cognitive_revision == 5
    assert runner.report()["replan_count"] == 1


async def test_new_event_during_brain_waiter_cleanup_invalidates_completed_finish(session):
    router = routing(session)
    class CleanupEvent(asyncio.Event):
        fired = False
        async def wait(self):
            try:
                return await super().wait()
            finally:
                if not self.fired:
                    self.fired = True
                    emit(router, 3, "urgent:late-finish")
    router.replan = CleanupEvent()
    brain = SequenceBrain([FinishObjective(status="success", reason="Obsolete finish"),
                           FinishObjective(status="failure", reason="Fresh deliberate result")])
    runner = ObjectiveRunner(session, count_registry(CountSkill()), brain, event_router=router)
    result = await asyncio.wait_for(runner.run(Objective(text="Assess the newest urgent event.")), timeout=1)
    assert result.status == "failed" and result.reason == "brain_reported_failure"
    assert result.message == "Fresh deliberate result" and result.decision_count == 2
    assert runner.report()["decisions"][0]["status"] == "stale"
    assert brain.contexts[-1].trigger_events[0].value == "urgent:late-finish"


@pytest.mark.parametrize("ending,expected", [("cancel", "objective_cancelled"),
    ("disconnect", "session_disconnected"), ("degraded", "event_routing_degraded")])
async def test_terminal_change_during_waiter_cleanup_overrides_completed_finish(session, ending, expected):
    router = routing(session)
    class CleanupEvent(asyncio.Event):
        fired = False
        async def wait(self):
            try:
                return await super().wait()
            finally:
                if not self.fired:
                    self.fired = True
                    if ending == "cancel": runner.cancel_requested.set()
                    elif ending == "disconnect": session.close()
                    else: router._fail_safe("fixture_failure")
    router.replan = CleanupEvent()
    runner = ObjectiveRunner(session, count_registry(CountSkill()),
        SequenceBrain([FinishObjective(status="success")]), event_router=router)
    result = await asyncio.wait_for(runner.run(Objective(text="Finish if still safe.")), timeout=1)
    assert result.reason == expected and result.status != "succeeded"
    assert runner.report()["decisions"][0]["status"] != "running"


async def test_event_scheduled_between_acceptance_and_dispatch_cannot_start_stale_skill(session):
    router, skill = routing(session), CountSkill()
    acknowledge = router.acknowledge_decision
    fired = False
    def acknowledge_then_receive(ids, revision):
        nonlocal fired
        result = acknowledge(ids, revision)
        if not fired:
            fired = True
            asyncio.get_running_loop().call_soon(emit, router, 3, "urgent:dispatch-race")
        return result
    router.acknowledge_decision = acknowledge_then_receive
    brain = SequenceBrain([call(), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, count_registry(skill), brain, event_router=router)
    result = await asyncio.wait_for(runner.run(Objective(text="Act on the current state.")), timeout=1)
    assert result.status == "succeeded" and not skill.called and not result.skill_results
    assert runner.report()["decisions"][0]["status"] == "stale"


async def test_noninterrupting_review_preserves_running_skill_and_real_success(session):
    router, skill = routing(session, review_only=True), CountSkill(blocked=True)
    brain = SequenceBrain([call(), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, count_registry(skill), brain, event_router=router)
    task = asyncio.create_task(runner.run(Objective(text="Finish current work then review.")))
    await skill.started.wait()
    emit(router, 3, "urgent:review-after-work")
    await asyncio.sleep(0.02)
    assert brain.turn_count == 1 and not skill.cleaned
    skill.release.set()
    result = await asyncio.wait_for(task, timeout=1)
    assert result.status == "succeeded" and result.skill_results[0]["status"] == "succeeded"
    assert brain.contexts[-1].trigger_events and runner.report()["replan_count"] == 0


async def test_degraded_routing_is_terminal_safe_stop_not_soft_success(session):
    router, drive = routing(session), OwnedDrive()
    brain = SequenceBrain([SkillCall(skill_id="test.drive", arguments={"amount": 1}),
                           FinishObjective(status="success")])
    runner = ObjectiveRunner(session, owned_drive_registry(drive), brain, event_router=router)
    task = asyncio.create_task(runner.run(Objective(text="Drive safely.")))
    await drive.started.wait()
    router._fail_safe("critical_queue_overflow")
    result = await asyncio.wait_for(task, timeout=1)
    assert result.status == "failed" and result.reason == "event_routing_degraded"
    assert drive.cleaned and brain.turn_count == 1
    assert_zero_intent(session)


async def test_terminal_cancellation_wins_pending_soft_replan(session):
    router, skill = routing(session), CountSkill(blocked=True)
    brain = SequenceBrain([call(), FinishObjective(status="success")], stop_on_skill_failure=False)
    runner = ObjectiveRunner(session, count_registry(skill), brain, event_router=router)
    task = asyncio.create_task(runner.run(Objective(text="Wait safely.")))
    await skill.started.wait()
    emit(router, 3, "urgent:cancel-race")
    await runner.cancel()
    result = await task
    assert result.status == "cancelled" and result.reason == "objective_cancelled"
    assert brain.turn_count == 1 and skill.cleaned


async def test_replan_count_bounds_a_perpetually_invalidating_brain(session):
    router = routing(session)
    class InvalidatingBrain(SequenceBrain):
        async def decide(self, context):
            self.turn_count += 1
            emit(router, self.turn_count + 2, f"urgent:{self.turn_count}")
            return call()
    skill, brain = CountSkill(), InvalidatingBrain([])
    runner = ObjectiveRunner(session, count_registry(skill), brain, event_router=router, max_replans=2)
    result = await asyncio.wait_for(runner.run(Objective(text="Do useful work.")), timeout=1)
    assert result.status == "failed" and result.reason == "replan_limit"
    assert result.decision_count == 3 and not skill.called


async def test_replan_limit_drains_a_still_active_provider_before_closing_episode(session):
    router = routing(session)
    class Brain(SequenceBrain):
        async def decide(self, context):
            self.task = asyncio.current_task()
            emit(router, 3, "urgent:over-limit")
            await asyncio.Event().wait()
    brain = Brain([])
    runner = ObjectiveRunner(session, count_registry(CountSkill()), brain,
                             event_router=router, max_replans=0)
    result = await asyncio.wait_for(runner.run(Objective(text="Finish safely.")), timeout=1)
    assert result.status == "failed" and result.reason == "replan_limit"
    assert brain.task.done() and brain.task.cancelled() and brain.closed
    assert runner.report()["pending_cleanup_tasks"] == 0


async def test_legacy_brain_drains_stale_decision_without_terminal_interrupt_then_continues(session):
    router, skill = routing(session), CountSkill()
    class LegacyBrain:
        def __init__(self):
            self.entered, self.release = asyncio.Event(), asyncio.Event()
            self.calls, self.interrupted = 0, False
        async def start(self, objective, catalog): pass
        async def decide(self, context):
            self.calls += 1
            if self.calls == 1:
                self.entered.set()
                await self.release.wait()
                return call()
            return FinishObjective(status="success")
        async def interrupt(self): self.interrupted = True
        async def close(self): pass
    brain = LegacyBrain()
    runner = ObjectiveRunner(session, count_registry(skill), brain, event_router=router)
    task = asyncio.create_task(runner.run(Objective(text="Respond safely.")))
    await brain.entered.wait()
    emit(router, 3, "urgent:legacy")
    await asyncio.sleep(0.01)
    assert brain.calls == 1 and not brain.interrupted
    brain.release.set()
    assert (await asyncio.wait_for(task, timeout=1)).status == "succeeded"
    assert brain.calls == 2 and not brain.interrupted and not skill.called


async def test_runner_retrieves_memory_and_persists_skill_and_objective_without_closing_store(session):
    from actor_runtime.memory import MemoryStore
    store = MemoryStore(game_id=session.manifest.game_id, session_id=session.session_id,
                        actor_id=session.world.self_actor_id)
    try:
        brain = SequenceBrain([call(), FinishObjective(status="success")])
        runner = ObjectiveRunner(session, count_registry(CountSkill()), brain, memory=store)
        result = await runner.run(Objective(text="Increment and remember the outcome."))
        assert result.status == "succeeded"
        assert brain.contexts[-1].memories[0].evidence_kind == "skill_result"
        remembered = await store.retrieve(objective="Increment outcome")
        assert {item.evidence_kind for item in remembered} == {"skill_result", "objective_result"}
    finally:
        await store.close()


async def test_memory_failure_stops_before_skill_dispatch_and_cancellation_stays_terminal(session):
    from actor_runtime.errors import ActorError
    class BrokenMemory:
        async def retrieve(self, **kwargs):
            raise ActorError("memory_unavailable", "Private database content is not diagnostics")
        async def remember_objective_result(self, result):
            raise ActorError("memory_unavailable", "Private database content is not diagnostics")
    skill = CountSkill()
    runner = ObjectiveRunner(session, count_registry(skill), SequenceBrain([call()]), memory=BrokenMemory())
    result = await runner.run(Objective(text="Increment."))
    assert result.status == "failed" and result.reason == "memory_failure"
    assert not skill.called and "Private" not in str(runner.report())


async def test_cancel_during_memory_finalization_returns_controlled_result(session):
    class Memory:
        def __init__(self): self.entered = asyncio.Event()
        async def retrieve(self, **kwargs): return []
        async def remember_objective_result(self, result):
            self.entered.set()
            await asyncio.Event().wait()
    memory = Memory()
    runner = ObjectiveRunner(session, count_registry(CountSkill()),
        SequenceBrain([FinishObjective(status="success")]), memory=memory, cleanup_timeout_seconds=0.03)
    task = asyncio.create_task(runner.run(Objective(text="Finish.")))
    await memory.entered.wait()
    task.cancel()
    result = await asyncio.wait_for(task, timeout=1)
    assert result.status == "cancelled" and result.reason == "objective_cancelled"
    assert not runner._running


async def test_late_cancel_reconciles_already_committed_sqlite_outcome(session):
    from actor_runtime.memory import MemoryStore
    class SlowMemory(MemoryStore):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.committed = asyncio.Event()
            self.writes = 0
        async def remember_objective_result(self, result):
            await super().remember_objective_result(result)
            self.writes += 1
            if self.writes == 1:
                self.committed.set()
                await asyncio.Event().wait()
    memory = SlowMemory(game_id=session.manifest.game_id, session_id=session.session_id,
                        actor_id=session.world.self_actor_id)
    runner = ObjectiveRunner(session, count_registry(CountSkill()),
        SequenceBrain([FinishObjective(status="success")]), memory=memory, cleanup_timeout_seconds=0.03)
    try:
        task = asyncio.create_task(runner.run(Objective(text="Finish.")))
        await memory.committed.wait()
        task.cancel()
        result = await asyncio.wait_for(task, timeout=1)
        assert result.status == "cancelled"
        entries = await memory.retrieve()
        outcomes = [item for item in entries if item.evidence_kind == "objective_result"]
        assert len(outcomes) == 1 and outcomes[0].content["status"] == "cancelled"
        assert memory.writes == 2
    finally:
        await memory.close()
