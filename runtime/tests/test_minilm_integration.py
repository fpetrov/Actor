"""Optional local-weight integration. No remote model or real game is started."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from actor_runtime.brains import FinishObjective, PlanReaction, SequenceBrain, SkillCall, SkillPlan
from actor_runtime.objectives import Objective, ObjectiveRunner, ObjectiveStatus
from actor_runtime.skills import SkillRegistry
from actor_agents_arena import SOCIAL_VISIBLE_WORLD, SOCIAL_ACTION_RESULT
from extensions.agents_arena.v1 import social_pb2 as pb
from test_social_runtime import observe, command, event

pytest.importorskip("actor_minilm")
from actor_minilm.provider import MiniLMClassifier
from actor_minilm.projection import project


async def test_real_neural_classifier_uses_executing_plan_and_logs_exact_input(session, tmp_path):
    classifier = MiniLMClassifier(session.event_router.rules, capture_root=tmp_path)
    session.event_router.configure(classifier=classifier)
    own_task = pb.SocialTask(task_id="task-1", room_id="main-hall", x=0, y=-.94,
        approach_x=0, approach_y=-1.4, work_duration_ms=10000)
    visible_bodies = []
    await observe(session, tasks=[own_task])
    async def fresh_frames():
        while True:
            await asyncio.sleep(.05)
            await observe(session, tasks=[own_task], bodies=visible_bodies)
    frames = asyncio.create_task(fresh_frames())
    call = lambda name, **args: SkillCall(skill_id=name, arguments=args)
    plan = SkillPlan(steps=[call("agents-arena.perform-task", task_id="task-1"), call("actor.wait", duration_seconds=.01)],
        conditions=[PlanReaction(capability_id=SOCIAL_VISIBLE_WORLD, field="bodies", response=call("agents-arena.report-visible-body"))],
        fallback=call("actor.wait", duration_seconds=.02))
    brain = SequenceBrain([plan, FinishObjective(status="success")], stop_on_skill_failure=False)
    runner = ObjectiveRunner(session, SkillRegistry.discover(), brain, run_finalizers=False)
    pending = asyncio.create_task(runner.run(Objective(text="Survive and complete crew tasks.")))
    try:
        await command(session, pb.SocialWorkTaskCommand)
        visible_bodies.append(pb.SocialVisibleBody(body_id="body-1", x=0, y=-.94))
        await observe(session, tasks=[own_task], bodies=visible_bodies)
        report = await command(session, pb.SocialReportBodyCommand)
        assert report.body_id == "body-1"
        assert brain.turn_count == 1  # No new strategic call was used to select the report.
        rows = [json.loads(line) for line in classifier.path.read_text().splitlines()]
        inputs = {r["call_id"]: r for r in rows if r["schema"] == "actor.local-classifier-input.v3"}
        outputs = [r for r in rows if r["schema"] == "actor.local-classifier-output.v4" and r.get("assessment_source") == "neural"]
        body_output = next(r for r in outputs if r["model_input"]["event"]["kind"] == "body_seen")
        raw = inputs[body_output["call_id"]]
        assert raw["input"]["context"]["current_plan"]["current"]["skill_id"] == "agents-arena.perform-task"
        assert body_output["model_input"] == project(raw).model_dump()
        assert body_output["neural_prediction"]["should_request_brain_review"] is False
        counterfactual = deepcopy(raw)
        counterfactual["input"]["context"]["current_plan"]["conditions"] = []
        without_condition = classifier.model.predict(project(counterfactual))
        assert without_condition["should_request_brain_review"] is True
        await event(session, SOCIAL_ACTION_RESULT, pb.SocialActionResultEvent(match_id="match-social",
            correlation_id=report.context.correlation_id, action=pb.SOCIAL_ACTION_REPORT_BODY, accepted=True, completed=True))
        result = await asyncio.wait_for(pending, 2)
        assert result.status == ObjectiveStatus.SUCCEEDED
        assert classifier.fallbacks == 0
        (tmp_path / "result.json").write_text(json.dumps({"scenario": "local typed Runtime fixture, not a completed game",
            "with_executable_condition": body_output["neural_prediction"], "without_condition_counterfactual": without_condition,
            "brain_calls_before_report": 1, "runner": runner.report(), "capture": str(classifier.path)}, indent=2))
    finally:
        frames.cancel()
        await asyncio.gather(frames, return_exceptions=True)
        if not pending.done():
            pending.cancel()
            await pending
        (tmp_path / "runner-diagnostic.json").write_text(json.dumps(runner.report(), indent=2))
