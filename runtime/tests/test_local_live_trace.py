import asyncio
from copy import deepcopy

from actor_runtime.brains import FinishObjective, SequenceBrain, SkillPlan
from actor_runtime.objectives import Objective, ObjectiveRunner, ObjectiveStatus
from test_plans import Recorder, call


async def test_private_trace_links_accepted_plan_and_completed_actions(session):
    records = []
    session.trace_sink = lambda phase, fields: records.append((phase, deepcopy(fields)))
    recorder = Recorder()
    brain = SequenceBrain([SkillPlan(steps=[call(1), call(2)]), FinishObjective(status="success")])
    result = await ObjectiveRunner(session, recorder.registry(), brain,
        run_finalizers=False).run(Objective(text="Complete the work"))
    assert result.status == ObjectiveStatus.SUCCEEDED
    accepted = [v for phase, v in records if phase == "decision_accepted"]
    plan = next(v["plan"] for phase, v in records if phase == "plan_accepted")
    steps = [v["plan"] for phase, v in records if phase == "plan_step"]
    outcomes = [v for phase, v in records if phase == "skill_result"]
    assert accepted[0]["decision"]["steps"] == [call(1).model_dump(), call(2).model_dump()]
    assert [v["id"] for v in steps] == [plan["id"], plan["id"]]
    assert [v["step_index"] for v in steps] == [0, 1]
    assert len(outcomes) == 2
    assert all(v["objective_id"] == accepted[0]["objective_id"] for v in outcomes)
    assert session.trace_errors == 0


async def test_trace_write_failure_does_not_break_gameplay(session):
    def unavailable(phase, fields):
        raise OSError("disk unavailable")
    session.trace_sink = unavailable
    recorder = Recorder()
    result = await ObjectiveRunner(session, recorder.registry(),
        SequenceBrain([call(1), FinishObjective(status="success")]),
        run_finalizers=False).run(Objective(text="Complete one action"))
    assert result.status == ObjectiveStatus.SUCCEEDED
    assert session.trace_errors > 0


async def test_provider_failure_retains_class_but_not_arbitrary_error_details(session):
    from actor_runtime.errors import ActorError
    records = []
    session.trace_sink = lambda phase, fields: records.append((phase, deepcopy(fields)))

    class FailedBrain(SequenceBrain):
        public_error_codes = frozenset({"codex_provider_error"})

        async def decide(self, context):
            raise ActorError("codex_provider_error", "Provider failed",
                {"exception_type": "RpcError", "raw_prompt": "untrusted captured context"})

    result = await ObjectiveRunner(session, Recorder().registry(), FailedBrain([]),
        run_finalizers=False).run(Objective(text="Complete the work"))
    assert result.status == ObjectiveStatus.FAILED
    failure = next(v for phase, v in records if phase == "provider_failure")
    assert failure["provider_code"] == "codex_provider_error"
    assert failure["exception_type"] == "RpcError"
    assert "raw_prompt" not in failure


async def test_critical_observer_failure_does_not_change_mandatory_review(session):
    from actor_agents_arena import SOCIAL_MATCH_CHANGED
    from extensions.agents_arena.v1 import social_pb2 as pb
    from test_social_runtime import observe, event
    from actor_runtime.classifiers import RuleBasedEventClassifier

    class FailedObserver(RuleBasedEventClassifier):
        def observe_rule_assessment(self, *_):
            raise OSError("capture unavailable")

    await observe(session)
    router = session.event_router
    router.configure(classifier=FailedObserver(router.rules))
    await router.start(Objective(text="Play the match"))
    try:
        await event(session, SOCIAL_MATCH_CHANGED, pb.SocialMatchChangedEvent(
            state=pb.SocialMatchStateObservation(match_id="match-social",
                phase=pb.SOCIAL_PHASE_DISCUSSION, phase_revision=5)))
        assert router.replan.is_set()
        assert router.preempt.is_set()
        assert router._counts["observer_errors"] == 1
        assert not router.degraded
    finally:
        await router.close()


async def test_private_trace_links_skill_command_and_server_command_id(session):
    from test_social_runtime import observe, engine, command
    from actor_standard import Locomotion2DCommand
    records = []
    session.trace_sink = lambda phase, fields: records.append((phase, deepcopy(fields)))
    await observe(session)
    # This failure still runs the existing movement cleanup command.
    result = await engine(session).run("agents-arena.report-visible-body", {})
    stop = await command(session, Locomotion2DCommand)
    assert result.status == "failed" and stop.desired_move_x == stop.desired_move_y == 0
    owned = [v for phase, v in records if phase == "skill_command"]
    queued = [v for phase, v in records if phase == "command_queued"]
    assert owned[0]["skill_id"] == "agents-arena.report-visible-body"
    assert owned[0]["command_id"] == queued[0]["command_id"]
    assert owned[0]["cleanup"] is True
