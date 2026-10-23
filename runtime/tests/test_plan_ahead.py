"""Plan execution and Brain overlap through the public ObjectiveRunner interface."""
import asyncio

import pytest

from actor_runtime.brains import FinishObjective, SequenceBrain, SkillPlan
from actor_runtime.config import CognitiveConfig
from actor_runtime.objectives import Objective, ObjectiveRunner, ObjectiveStatus
from actor_runtime.skills import SkillScheduler
from test_plans import Recorder, call
from test_events import spin, station
from test_plans import ObserveClassifier
from test_plans import body_condition, observe_body
from actor_agents_arena import ROUTINE_NOTICE


async def test_continuation_is_prepared_while_the_last_step_keeps_running(session):
    recorder = Recorder(block=True)

    class Brain(SequenceBrain):
        def __init__(self):
            super().__init__([
                SkillPlan(steps=[call(1)]),
                SkillPlan(steps=[call(2)]),
                FinishObjective(status="success"),
            ])
            self.preparing = asyncio.Event()
            self.release = asyncio.Event()
            self.prepared = asyncio.Event()

        async def decide(self, context):
            answer = await super().decide(context)
            if self.turn_count == 2:
                await recorder.started.wait()
                assert not recorder.cleanup_entered.is_set()
                self.preparing.set()
                await self.release.wait()
                self.prepared.set()
            return answer

    brain = Brain()
    config = CognitiveConfig(brain={"plan_ahead": True})
    runner = ObjectiveRunner(session, recorder.registry(), brain, config=config, run_finalizers=False)
    task = asyncio.create_task(runner.run(Objective(text="Keep working while preparing the next plan")))
    try:
        await asyncio.wait_for(recorder.started.wait(), 1)
        await asyncio.wait_for(brain.preparing.wait(), .5)
        assert not recorder.cleanup_entered.is_set()
        brain.release.set()
        await asyncio.wait_for(brain.prepared.wait(), 1)
        assert ("execute", 2) not in recorder.calls
        recorder.block = False
        result = await asyncio.wait_for(task, 2)
        assert result.status == ObjectiveStatus.SUCCEEDED
        assert [value for kind, value in recorder.calls if kind == "execute"] == [1, 2]
        assert recorder.calls.index(("cleanup", 1)) < recorder.calls.index(("execute", 2))
        assert brain.turn_count == 3
    finally:
        brain.release.set()
        recorder.block = False
        if not task.done():
            await runner.cancel()
            await task


@pytest.mark.parametrize("answer_ready", [False, True])
@pytest.mark.parametrize("source", ["recall", "discussion", "game_over"])
async def test_critical_event_stops_work_and_discards_its_continuation(session, answer_ready, source):
    recorder = Recorder(block=True)

    class Brain(SequenceBrain):
        def __init__(self):
            super().__init__([SkillPlan(steps=[call(1)]), SkillPlan(steps=[call(2)]),
                              FinishObjective(status="success")], stop_on_skill_failure=False)
            self.preparing = asyncio.Event()
            self.release = asyncio.Event()
            self.returned = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def decide(self, context):
            answer = await super().decide(context)
            if self.turn_count == 2:
                self.preparing.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    raise
                self.returned.set()
            return answer

    brain = Brain()
    runner = ObjectiveRunner(session, recorder.registry(), brain,
        config=CognitiveConfig(brain={"plan_ahead": True}), run_finalizers=False)
    task = asyncio.create_task(runner.run(Objective(text="Stop obsolete work on urgent recall")))
    try:
        await asyncio.wait_for(recorder.started.wait(), 1)
        await asyncio.wait_for(brain.preparing.wait(), 1)
        if answer_ready:
            brain.release.set()
            await asyncio.wait_for(brain.returned.wait(), 1)
        if source == "recall":
            session.event_router.ingest(station())
        else:
            from actor_agents_arena import SOCIAL_MATCH_CHANGED
            from extensions.agents_arena.v1 import social_pb2 as pb
            from test_social_runtime import event
            phase = pb.SOCIAL_PHASE_DISCUSSION if source == "discussion" else pb.SOCIAL_PHASE_GAME_OVER
            await event(session, SOCIAL_MATCH_CHANGED, pb.SocialMatchChangedEvent(
                state=pb.SocialMatchStateObservation(match_id="plan-ahead-match", phase=phase, phase_revision=2)))
        result = await asyncio.wait_for(task, 2)
        assert result.status == ObjectiveStatus.SUCCEEDED
        assert recorder.cleanup_entered.is_set()
        assert [value for kind, value in recorder.calls if kind == "execute"] == [1]
        assert answer_ready or brain.cancelled.is_set()
        assert runner.report()["pending_cleanup_tasks"] == 0
        assert not brain.interrupted
        assert brain.closed
    finally:
        brain.release.set()
        recorder.block = False
        if not task.done():
            await runner.cancel()
            await task


@pytest.mark.parametrize("source", ["station", "own_work"])
async def test_optional_review_keeps_work_and_inflight_continuation_running(session, source):
    recorder = Recorder(block=True)
    classifier = ObserveClassifier(review=True)
    if source == "own_work":
        from test_social_progress_events import baseline, own_event
        await baseline(session, classifier=classifier)
    else:
        session.event_router.configure(classifier=classifier)

    class Brain(SequenceBrain):
        def __init__(self):
            super().__init__([SkillPlan(steps=[call(1)]), SkillPlan(steps=[call(2)]),
                              FinishObjective(status="success")])
            self.preparing = asyncio.Event()
            self.release = asyncio.Event()

        async def decide(self, context):
            answer = await super().decide(context)
            if self.turn_count == 2:
                self.preparing.set()
                await self.release.wait()
            return answer

    brain = Brain()
    runner = ObjectiveRunner(session, recorder.registry(), brain,
        config=CognitiveConfig(brain={"plan_ahead": True}), run_finalizers=False,
        manage_router=source != "own_work")
    task = asyncio.create_task(runner.run(Objective(text="Continue useful work during ordinary updates")))
    try:
        await asyncio.wait_for(recorder.started.wait(), 1)
        await asyncio.wait_for(brain.preparing.wait(), 1)
        if source == "own_work":
            await own_event(session, can_move=False)
            await own_event(session, can_move=True)
        else:
            session.event_router.ingest(station(kind=ROUTINE_NOTICE))
        await spin(lambda: session.event_router.replan.is_set())
        assert not session.event_router.preempt.is_set()
        assert not recorder.cleanup_entered.is_set()
        assert brain.turn_count == 2
        brain.release.set()
        recorder.block = False
        result = await asyncio.wait_for(task, 2)
        assert result.status == ObjectiveStatus.SUCCEEDED
        assert [value for kind, value in recorder.calls if kind == "execute"] == [1, 2]
        assert brain.turn_count == 3
        # The new event was not in the pending continuation's snapshot. The
        # following decision must still receive it instead of losing it.
        assert any(c.trigger_events for c in brain.contexts[2:])
    finally:
        brain.release.set()
        recorder.block = False
        if not task.done():
            await runner.cancel()
            await task


async def test_cancel_discards_inflight_continuation_without_starting_its_actions(session):
    recorder = Recorder(block=True)

    class Brain(SequenceBrain):
        def __init__(self):
            super().__init__([SkillPlan(steps=[call(1)]), SkillPlan(steps=[call(2)])])
            self.preparing = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def decide(self, context):
            answer = await super().decide(context)
            if self.turn_count == 2:
                self.preparing.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    raise
            return answer

    brain = Brain()
    runner = ObjectiveRunner(session, recorder.registry(), brain,
        config=CognitiveConfig(brain={"plan_ahead": True}), run_finalizers=False)
    task = asyncio.create_task(runner.run(Objective(text="Stop both planning and work when cancelled")))
    await asyncio.wait_for(brain.preparing.wait(), 1)
    await runner.cancel()
    result = await asyncio.wait_for(task, 2)
    assert result.status == ObjectiveStatus.CANCELLED
    assert brain.cancelled.is_set()
    assert brain.closed
    assert [value for kind, value in recorder.calls if kind == "execute"] == [1]
    assert runner.report()["pending_cleanup_tasks"] == 0


async def test_prefetched_calls_count_towards_the_same_decision_limit(session):
    recorder = Recorder()
    brain = SequenceBrain([SkillPlan(steps=[call(2)]), SkillPlan(steps=[call(3)]),
                           FinishObjective(status="success")])
    runner = ObjectiveRunner(session, recorder.registry(), brain,
        config=CognitiveConfig(brain={"plan_ahead": True, "max_decisions": 2}), run_finalizers=False)
    result = await runner.run(Objective(text="Respect the total Brain budget"))
    assert result.reason == "decision_limit"
    assert brain.turn_count == result.decision_count == 2
    assert [value for kind, value in recorder.calls if kind == "execute"] == [2, 3]


async def test_continuation_is_revalidated_against_current_skill_availability(session):
    recorder = Recorder(block=True)
    registry = recorder.registry()
    allowed = True
    registry.availability_filter = lambda context, definition: allowed

    class Brain(SequenceBrain):
        def __init__(self):
            super().__init__([SkillPlan(steps=[call(1)]), SkillPlan(steps=[call(2)])])
            self.prepared = asyncio.Event()

        async def decide(self, context):
            answer = await super().decide(context)
            if self.turn_count == 2:
                self.prepared.set()
            return answer

    brain = Brain()
    runner = ObjectiveRunner(session, registry, brain, repair_limit=0,
        config=CognitiveConfig(brain={"plan_ahead": True}), run_finalizers=False)
    task = asyncio.create_task(runner.run(Objective(text="Never dispatch a now-unavailable action")))
    await asyncio.wait_for(brain.prepared.wait(), 1)
    allowed = False
    recorder.block = False
    result = await asyncio.wait_for(task, 2)
    assert result.status == ObjectiveStatus.FAILED
    assert [value for kind, value in recorder.calls if kind == "execute"] == [1]
    assert brain.turn_count == 2


async def test_expired_prepared_answer_is_replaced_by_a_fresh_decision(session):
    recorder = Recorder(block=True)

    class Brain(SequenceBrain):
        def __init__(self):
            super().__init__([SkillPlan(steps=[call(1)]), SkillPlan(steps=[call(2)]),
                              FinishObjective(status="success")])
            self.prepared = asyncio.Event()

        async def decide(self, context):
            answer = await super().decide(context)
            if self.turn_count == 2:
                self.prepared.set()
            return answer

    brain = Brain()
    runner = ObjectiveRunner(session, recorder.registry(), brain,
        config=CognitiveConfig(brain={"plan_ahead": True, "plan_ahead_max_age": .01}), run_finalizers=False)
    task = asyncio.create_task(runner.run(Objective(text="Do not keep a prepared answer indefinitely")))
    await asyncio.wait_for(brain.prepared.wait(), 1)
    await asyncio.sleep(.04)
    recorder.block = False
    result = await asyncio.wait_for(task, 2)
    assert result.status == ObjectiveStatus.SUCCEEDED
    assert [value for kind, value in recorder.calls if kind == "execute"] == [1]
    assert brain.turn_count == 3


async def test_invalid_provider_output_in_a_continuation_uses_the_normal_repair_budget(session):
    from actor_runtime.brains import InvalidBrainDecision

    class Brain(SequenceBrain):
        async def decide(self, context):
            answer = await super().decide(context)
            if self.turn_count == 2:
                raise InvalidBrainDecision()
            return answer

    recorder = Recorder()
    brain = Brain([SkillPlan(steps=[call(2)]), None, FinishObjective(status="success")])
    runner = ObjectiveRunner(session, recorder.registry(), brain,
        config=CognitiveConfig(brain={"plan_ahead": True}), run_finalizers=False)
    result = await runner.run(Objective(text="Repair one invalid prepared answer"))
    assert result.status == ObjectiveStatus.SUCCEEDED
    assert brain.turn_count == 3
    assert brain.contexts[-1].validation_error is not None
    assert [value for kind, value in recorder.calls if kind == "execute"] == [2]


async def test_failed_background_interruption_still_releases_the_active_plan(session):
    recorder = Recorder(block=True)

    class Brain(SequenceBrain):
        def __init__(self):
            super().__init__([SkillPlan(steps=[call(1)]), SkillPlan(steps=[call(2)])])
            self.preparing = asyncio.Event()

        async def decide(self, context):
            answer = await super().decide(context)
            if self.turn_count == 2:
                self.preparing.set()
                await asyncio.Event().wait()
            return answer

        async def interrupt_decision(self):
            raise RuntimeError("fixture interruption failed")

    brain = Brain()
    runner = ObjectiveRunner(session, recorder.registry(), brain,
        config=CognitiveConfig(brain={"plan_ahead": True}), run_finalizers=False)
    task = asyncio.create_task(runner.run(Objective(text="Release execution even when Brain cleanup fails")))
    await asyncio.wait_for(brain.preparing.wait(), 1)
    session.event_router.ingest(station())
    result = await asyncio.wait_for(task, 2)
    assert result.status == ObjectiveStatus.FAILED
    assert session.executable_plan is None
    assert recorder.cleanup_entered.is_set()
    assert [value for kind, value in recorder.calls if kind == "execute"] == [1]


async def test_condition_at_step_completion_discards_the_old_continuation(session):
    recorder = Recorder()

    class Brain(SequenceBrain):
        def __init__(self):
            super().__init__([SkillPlan(steps=[call(1)], conditions=[body_condition()]),
                              SkillPlan(steps=[call(2)]), FinishObjective(status="success")])
            self.prepared = asyncio.Event()

        async def decide(self, context):
            answer = await super().decide(context)
            if self.turn_count == 2:
                self.prepared.set()
            return answer

    brain = Brain()
    registry = recorder.registry()

    class Scheduler(SkillScheduler):
        async def run(self, skill_id, arguments):
            result = await super().run(skill_id, arguments)
            if arguments["amount"] == 1:
                await brain.prepared.wait()
                # Queue the real permitted observation at the completion seam:
                # both the Skill result and its condition are now available.
                await observe_body(session)
            return result

    runner = ObjectiveRunner(session, registry, brain, scheduler=Scheduler(session, registry),
        config=CognitiveConfig(brain={"plan_ahead": True}), run_finalizers=False)
    result = await asyncio.wait_for(runner.run(Objective(text="Follow the reaction instead of an obsolete continuation")), 2)
    assert result.status == ObjectiveStatus.SUCCEEDED
    assert [value for kind, value in recorder.calls if kind == "execute"] == [1, 9]
