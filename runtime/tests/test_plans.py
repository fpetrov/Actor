import asyncio
from copy import deepcopy

import pytest
from pydantic import Field

from actor_runtime.brains import FinishObjective, PlanReaction, SequenceBrain, SkillCall, SkillPlan
from actor_runtime.classifiers import EventAssessment
from actor_runtime.objectives import Objective, ObjectiveRunner, ObjectiveStatus
from actor_runtime.skills import ActorSkill, SkillDefinition, SkillError, SkillModel, SkillRegistry
from actor_agents_arena import SOCIAL_VISIBLE_WORLD, SOCIAL_MATCH_CHANGED
from extensions.agents_arena.v1 import social_pb2 as pb
from actor.core.v1 import actor_pb2 as core
from conftest import envelope, message


class Input(SkillModel):
    amount: int = Field(ge=1, le=10)


class Output(SkillModel):
    amount: int


class Recorder:
    def __init__(self, *, block=False):
        self.calls = []
        self.started = asyncio.Event()
        self.block = block
        self.cleanup_entered = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_release.set()

    def registry(self):
        owner = self

        class Work(ActorSkill):
            async def execute(self, context, arguments):
                self.amount = arguments.amount
                owner.calls.append(("execute", arguments.amount))
                if arguments.amount == 1:
                    owner.started.set()
                    while owner.block:
                        await context.sleep(.01)
                if arguments.amount == 5:
                    raise SkillError("fixture_failure", "This task could not finish")
                return Output(amount=arguments.amount)

            async def cleanup(self, context):
                owner.calls.append(("cleanup", getattr(self, "amount", None)))
                owner.cleanup_entered.set()
                await owner.cleanup_release.wait()

        registry = SkillRegistry()
        registry.register(SkillDefinition("test.work", "1.0.0", "A bounded test action", Input, Output, Work))
        return registry


def call(amount):
    return SkillCall(skill_id="test.work", arguments={"amount": amount})


def body_condition(response=None):
    return PlanReaction(capability_id=SOCIAL_VISIBLE_WORLD, field="bodies", response=response or call(9))


class ObserveClassifier:
    def __init__(self, *, review=False, gated=False):
        self.inputs = []
        self.review = review
        self.release = asyncio.Event()
        if not gated:
            self.release.set()

    async def classify(self, context, event):
        self.inputs.append(deepcopy(context.current_plan))
        await self.release.wait()
        return EventAssessment(should_request_brain_review=self.review, should_interrupt_skill=self.review)

    async def close(self):
        pass


async def observe_body(session, sequence=3, *, actor="actor-1"):
    value = pb.SocialVisibleWorldObservation(match_id="match-plan", recipient_actor_id=actor,
        bodies=[pb.SocialVisibleBody(body_id="seen-body", victim_actor_id="peer")])
    await session.accept(message(sequence, observation=core.ObservationFrame(revision=sequence,
        observations=[envelope(SOCIAL_VISIBLE_WORLD, "agents-arena", value, entity=actor)])))


async def test_sequential_plan_calls_brain_only_before_and_after_the_plan(session):
    recorder = Recorder()
    brain = SequenceBrain([SkillPlan(steps=[call(1), call(2)]), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, recorder.registry(), brain, run_finalizers=False)
    result = await runner.run(Objective(text="Complete both tasks"))
    assert result.status == ObjectiveStatus.SUCCEEDED
    assert [v for kind, v in recorder.calls if kind == "execute"] == [1, 2]
    assert brain.turn_count == 2
    assert len(result.skill_results) == 2
    assert runner.report()["plans"][0]["state"] == "completed"
    assert session.executable_plan is None


async def test_failed_step_runs_one_fallback_and_discards_remaining_steps(session):
    recorder = Recorder()
    brain = SequenceBrain([SkillPlan(steps=[call(5), call(2)], fallback=call(3)), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, recorder.registry(), brain, run_finalizers=False)
    result = await runner.run(Objective(text="Use the fallback if work fails"))
    assert result.status == ObjectiveStatus.SUCCEEDED
    assert [v for kind, v in recorder.calls if kind == "execute"] == [5, 3]
    assert runner.report()["plans"][0]["fallback_used"]
    assert brain.turn_count == 2


async def test_matching_observation_runs_planned_response_after_cleanup_without_a_brain_call(session):
    recorder = Recorder(block=True)
    classifier = ObserveClassifier(gated=True)
    session.event_router.configure(classifier=classifier)
    brain = SequenceBrain([SkillPlan(steps=[call(1), call(2)], conditions=[body_condition()]), FinishObjective(status="success")])
    runner = ObjectiveRunner(session, recorder.registry(), brain, run_finalizers=False)
    task = asyncio.create_task(runner.run(Objective(text="Work and handle a visible body as planned")))
    try:
        await asyncio.wait_for(recorder.started.wait(), 1)
        await observe_body(session)
        await observe_body(session, 4)
        classifier.release.set()
        result = await asyncio.wait_for(task, 2)
        assert result.status == ObjectiveStatus.SUCCEEDED
        assert [v for kind, v in recorder.calls if kind == "execute"] == [1, 9]
        assert recorder.calls.index(("cleanup", 1)) < recorder.calls.index(("execute", 9))
        assert brain.turn_count == 2
        assert runner.report()["replan_count"] == 0
        assert len(classifier.inputs) == 1
        snapshot = classifier.inputs[0]
        assert snapshot["current"]["arguments"] == {"amount": 1}
        assert snapshot["remaining_steps"][0]["arguments"] == {"amount": 2}
        assert snapshot["conditions"][0]["response"]["arguments"] == {"amount": 9}
        assert session.executable_plan is None
    finally:
        recorder.block = False
        classifier.release.set()
        if not task.done():
            task.cancel()
            await task


async def test_neural_request_for_review_supersedes_the_preselected_response(session):
    recorder = Recorder(block=True)
    classifier = ObserveClassifier(review=True)
    session.event_router.configure(classifier=classifier)
    brain = SequenceBrain([SkillPlan(steps=[call(1)], conditions=[body_condition()]), FinishObjective(status="success")],
                          stop_on_skill_failure=False)
    runner = ObjectiveRunner(session, recorder.registry(), brain, run_finalizers=False)
    task = asyncio.create_task(runner.run(Objective(text="Reassess if the classifier requests it")))
    await asyncio.wait_for(recorder.started.wait(), 1)
    await observe_body(session)
    result = await asyncio.wait_for(task, 2)
    assert result.status == ObjectiveStatus.SUCCEEDED
    assert [v for kind, v in recorder.calls if kind == "execute"] == [1]
    assert runner.report()["plans"][0]["state"] == "invalidated"
    assert runner.report()["replan_count"] == 1


async def test_authoritative_phase_change_invalidates_the_entire_plan(session):
    recorder = Recorder(block=True)
    brain = SequenceBrain([SkillPlan(steps=[call(1), call(2)], fallback=call(3), conditions=[body_condition()]),
                           FinishObjective(status="success")], stop_on_skill_failure=False)
    runner = ObjectiveRunner(session, recorder.registry(), brain, run_finalizers=False)
    task = asyncio.create_task(runner.run(Objective(text="Stop old work at the next meeting")))
    await asyncio.wait_for(recorder.started.wait(), 1)
    value = pb.SocialMatchChangedEvent(state=pb.SocialMatchStateObservation(match_id="match-plan",
        phase=pb.SOCIAL_PHASE_DISCUSSION, phase_revision=2))
    event = core.GameEventEnvelope(capability_id=SOCIAL_MATCH_CHANGED, extension_id="agents-arena",
        extension_version=session.extensions["agents-arena"].extension_version, entity_id="actor-1")
    event.payload.Pack(value)
    await session.accept(message(3, event=event))
    result = await asyncio.wait_for(task, 2)
    assert result.status == ObjectiveStatus.SUCCEEDED
    assert [v for kind, v in recorder.calls if kind == "execute"] == [1]
    assert runner.report()["plans"][0]["state"] == "invalidated"
    assert session.executable_plan is None


async def test_invalid_condition_rejects_the_whole_plan_before_any_action(session):
    recorder = Recorder()
    condition = PlanReaction(capability_id=SOCIAL_VISIBLE_WORLD, field="hidden_alignments", response=call(9))
    runner = ObjectiveRunner(session, recorder.registry(), SequenceBrain([SkillPlan(steps=[call(1)], conditions=[condition])]),
                             repair_limit=0, run_finalizers=False)
    result = await runner.run(Objective(text="Reject unsupported conditions"))
    assert result.status == ObjectiveStatus.FAILED
    assert recorder.calls == []
    assert runner.report()["decisions"][0]["status"] == "rejected"


async def test_phase_change_during_planned_cleanup_discards_the_queued_response(session):
    recorder = Recorder(block=True)
    recorder.cleanup_release.clear()
    session.event_router.configure(classifier=ObserveClassifier())
    brain = SequenceBrain([SkillPlan(steps=[call(1)], conditions=[body_condition()]), FinishObjective(status="success")],
                          stop_on_skill_failure=False)
    runner = ObjectiveRunner(session, recorder.registry(), brain, run_finalizers=False)
    task = asyncio.create_task(runner.run(Objective(text="Honor a phase transition while draining work")))
    try:
        await asyncio.wait_for(recorder.started.wait(), 1)
        await observe_body(session)
        await asyncio.wait_for(recorder.cleanup_entered.wait(), 1)
        from test_social_runtime import event
        await event(session, SOCIAL_MATCH_CHANGED, pb.SocialMatchChangedEvent(state=pb.SocialMatchStateObservation(
            match_id="match-plan", phase=pb.SOCIAL_PHASE_DISCUSSION, phase_revision=2)))
        recorder.cleanup_release.set()
        result = await asyncio.wait_for(task, 2)
        assert result.status == ObjectiveStatus.SUCCEEDED
        assert [v for kind, v in recorder.calls if kind == "execute"] == [1]
        assert runner.report()["plans"][0]["state"] == "invalidated"
    finally:
        recorder.cleanup_release.set()
        if not task.done():
            task.cancel()
            await task


async def test_other_actor_observation_cannot_activate_a_plan(session):
    recorder = Recorder(block=True)
    classifier = ObserveClassifier()
    session.event_router.configure(classifier=classifier)
    runner = ObjectiveRunner(session, recorder.registry(), SequenceBrain([SkillPlan(steps=[call(1)], conditions=[body_condition()])]),
                             run_finalizers=False)
    task = asyncio.create_task(runner.run(Objective(text="Use only my own permitted view")))
    await asyncio.wait_for(recorder.started.wait(), 1)
    await observe_body(session, actor="some-other-actor")
    await asyncio.sleep(.02)
    assert classifier.inputs == []
    await runner.cancel()
    result = await asyncio.wait_for(task, 2)
    assert result.status == ObjectiveStatus.CANCELLED
    assert [v for kind, v in recorder.calls if kind == "execute"] == [1]


@pytest.mark.parametrize("blocked", [False, True])
async def test_failed_cleanup_stops_plan_before_next_step_or_reaction(session, blocked):
    from dataclasses import replace
    recorder = Recorder(block=blocked)
    registry = recorder.registry()
    definition = registry.definition("test.work")

    class BrokenCleanup(definition.factory):
        async def cleanup(self, context):
            await super().cleanup(context)
            raise SkillError("cleanup_failed_fixture", "Foreground cleanup failed")

    registry.definitions[definition.skill_id] = replace(definition, factory=BrokenCleanup)
    session.event_router.configure(classifier=ObserveClassifier())
    brain = SequenceBrain([SkillPlan(steps=[call(1), call(2)], conditions=[body_condition()], fallback=call(3)),
                           FinishObjective(status="success")])
    runner = ObjectiveRunner(session, registry, brain, run_finalizers=False)
    task = asyncio.create_task(runner.run(Objective(text="Stop if foreground cleanup fails")))
    try:
        if blocked:
            await asyncio.wait_for(recorder.started.wait(), 1)
            await observe_body(session)
        result = await asyncio.wait_for(task, 2)
        assert result.status == ObjectiveStatus.FAILED
        assert result.reason == "plan_cleanup_failed"
        assert [v for kind, v in recorder.calls if kind == "execute"] == [1]
        assert brain.turn_count == 1
        assert session.executable_plan is None
    finally:
        recorder.block = False
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
