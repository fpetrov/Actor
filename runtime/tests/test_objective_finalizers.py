import asyncio
from types import SimpleNamespace

import pytest

from actor_runtime import objectives
from actor_runtime.brains import FinishObjective, SequenceBrain, SkillCall
from actor_runtime.builtin_skills import skill_package
from actor_runtime.objectives import Objective, ObjectiveRunner, ObjectiveStatus
from actor_runtime.skills import SkillRegistry


def install_finalizers(monkeypatch, **functions):
    original = objectives.metadata.entry_points
    points = [SimpleNamespace(name=name, load=lambda function=function: function)
              for name, function in functions.items()]
    monkeypatch.setattr(objectives.metadata, "entry_points", lambda *, group:
        points if group == "actor.objective_finalizers" else original(group=group))


def registry():
    value = SkillRegistry()
    for definition in skill_package():
        value.register(definition)
    return value


async def test_installed_finalizer_reuses_idle_scheduler_before_router_close(session, monkeypatch):
    received = []

    async def finalize(**arguments):
        received.append(arguments)
        assert not arguments["scheduler"].report()["active_skill_id"]
        assert not brain.closed
        assert not router_closed.is_set()
        result = await arguments["scheduler"].run("actor.wait", {"duration_seconds": 0.0})
        assert result.status == "succeeded"

    install_finalizers(monkeypatch, synthetic=finalize)
    brain = SequenceBrain([FinishObjective(status="success")])
    router_closed = asyncio.Event()
    close = session.event_router.close

    async def close_router():
        router_closed.set()
        await close()

    monkeypatch.setattr(session.event_router, "close", close_router)
    runner = ObjectiveRunner(session, registry(), brain)
    objective = Objective(text="Finish and release extension resources.")
    result = await runner.run(objective)
    assert result.status == ObjectiveStatus.SUCCEEDED
    assert received[0]["session"] is session
    assert received[0]["objective"].id == objective.id
    assert received[0]["status"] == ObjectiveStatus.SUCCEEDED
    assert received[0]["reason"] == "finished"
    assert received[0]["scheduler"] is runner.scheduler
    assert runner.report()["finalizers"] == [{"name": "synthetic", "status": "completed"}]
    assert router_closed.is_set() and brain.closed


async def test_live_session_cancel_runs_extension_cleanup_after_foreground_release(session, monkeypatch):
    called = asyncio.Event()

    async def finalize(**arguments):
        assert arguments["status"] == ObjectiveStatus.CANCELLED
        assert arguments["reason"] == "objective_cancelled"
        assert not session.closed.is_set()
        assert not arguments["scheduler"].report()["active_skill_id"]
        assert (await arguments["scheduler"].run("actor.wait", {"duration_seconds": 0.0})).status == "succeeded"
        called.set()

    install_finalizers(monkeypatch, synthetic=finalize)
    brain = SequenceBrain([SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 100.0})])
    runner = ObjectiveRunner(session, registry(), brain)
    task = asyncio.create_task(runner.run(Objective(text="Wait until cancelled.")))
    async with asyncio.timeout(1):
        while runner.scheduler.report()["active_skill_id"] != "actor.wait":
            await asyncio.sleep(0)
        await runner.cancel()
        result = await task
    assert result.status == ObjectiveStatus.CANCELLED and called.is_set()
    assert not session.closed.is_set()


@pytest.mark.parametrize("invalid", [False, True])
async def test_finalizer_failure_is_safe_and_other_finalizers_still_run(session, monkeypatch, caplog, invalid):
    called = asyncio.Event()

    async def bad(**arguments):
        raise RuntimeError("PRIVATE-DIAGNOSTIC-CONTENT")

    async def good(**arguments):
        called.set()

    install_finalizers(monkeypatch, a_bad=(lambda **_: None) if invalid else bad, b_good=good)
    runner = ObjectiveRunner(session, registry(), SequenceBrain([FinishObjective(status="success")]))
    result = await runner.run(Objective(text="Finish."))
    assert result.status == ObjectiveStatus.FAILED and result.reason == "cleanup_failed"
    assert called.is_set()
    assert runner.report()["cleanup_errors"] == ["objective_finalizer_failed"]
    assert "PRIVATE-DIAGNOSTIC-CONTENT" not in caplog.text


async def test_unresponsive_finalizer_is_bounded_and_delayed_exception_is_consumed(session, monkeypatch):
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def unresponsive(**arguments):
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
        raise RuntimeError("late extension failure")

    install_finalizers(monkeypatch, synthetic=unresponsive)
    runner = ObjectiveRunner(session, registry(), SequenceBrain([FinishObjective(status="success")]),
        cleanup_timeout_seconds=0.02)
    try:
        async with asyncio.timeout(0.5):
            result = await runner.run(Objective(text="Finish."))
        assert result.status == ObjectiveStatus.FAILED and result.reason == "cleanup_failed"
        assert cancelled.is_set()
        assert runner.report()["pending_cleanup_tasks"] == 1
        assert runner.report()["cleanup_errors"] == ["objective_finalizers_failed"]
    finally:
        release.set()
        async with asyncio.timeout(0.5):
            while runner.report()["pending_cleanup_tasks"]:
                await asyncio.sleep(0)
