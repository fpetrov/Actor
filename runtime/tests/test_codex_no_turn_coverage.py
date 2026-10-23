"""Public SDK fakes: distinguish no request from an unknown request result."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from actor_codex.brain import CodexBrain
from actor_runtime.errors import ActorError
from actor_runtime.objectives import Objective
from actor_runtime.telemetry import provider_telemetry
from test_codex import fake_codex_launch
from test_codex_thread_budget import TrackedSDK, usage


async def started():
    sdk = TrackedSDK(usage=usage(100))
    adapter = sdk.adapter()
    brain = CodexBrain(adapter=adapter)
    await brain.start(Objective(text="Authored game objective"), [])
    return sdk, adapter, brain


def context(index):
    return NS(decision_index=index, external_json=lambda: "{}")


async def test_interrupted_fresh_thread_wait_proves_no_model_request_in_brain_and_aggregate():
    sdk, adapter, brain = await started()
    pending = stopping = None
    try:
        await brain.decide(context(1))
        sdk.block_rotation = True
        pending = asyncio.create_task(brain.decide(context(2)))
        await asyncio.wait_for(sdk.rotation_entered.wait(), 1)
        stopping = asyncio.create_task(brain.interrupt_decision())
        await asyncio.sleep(0)
        sdk.rotation_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 1)
        await asyncio.wait_for(stopping, 1)
        assert len(sdk.inputs) == 2 and len(sdk.thread_inputs) == 2
        assert sdk.thread_inputs[-1] == []  # Empty ThreadStart is not a Turn request.
        assert adapter.report()["unique_model_turns"] == 1
        assert adapter.report()["thread_rotations"] == 0
        assert adapter.report()["usage_totals"]["total_tokens"] == 100
        assert brain.turns[-1]["status"] == "interrupted"
        assert "turn_id" not in brain.turns[-1]
        assert brain.turns[-1]["tool_calls"] == 0
        assert brain.turns[-1]["tool_count_scope"] == "sdk_item_lifecycle"
        assert adapter.last_decision_tool_telemetry()["tool_count_complete"] is True
    finally:
        sdk.rotation_release.set()
        for task in (pending, stopping):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (pending, stopping) if task is not None), return_exceptions=True)
        await brain.close()
    aggregate = provider_telemetry([{"brain": brain.report()}])
    assert aggregate["tool_count_complete"] is True and aggregate["tool_calls"] == 0
    assert aggregate["unique_model_turns"] == 1 and aggregate["closed_providers"] == 1


@pytest.mark.parametrize("cancel", [True, False])
async def test_turn_request_before_handle_stays_unknown_on_cancel_or_error(cancel):
    sdk, adapter, brain = await started()
    entered, release = asyncio.Event(), asyncio.Event()
    attempts = []
    async def unresolved(input, *, output_schema):
        attempts.append(1)
        entered.set()
        await release.wait()
        raise RuntimeError("Authored request failed before handle")
    adapter.thread.turn = unresolved
    pending = asyncio.create_task(brain.decide(context(1)))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if cancel:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 1)
        else:
            release.set()
            with pytest.raises(ActorError, match="codex_provider_error"):
                await asyncio.wait_for(pending, 1)
        assert attempts == [1] and adapter.report()["unique_model_turns"] == 0
        assert "tool_calls" not in brain.turns[-1]
        assert adapter.last_decision_tool_telemetry()["tool_count_complete"] is False
        assert adapter.last_decision_tool_telemetry()["tool_calls"] is None
        assert provider_telemetry([{"brain": brain.report()}])["tool_count_complete"] is False
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await brain.close()
    assert sdk.clients[0].closed and adapter.report()["closed"]


async def test_failed_thread_creation_cannot_erase_prior_usage_or_invent_a_model_turn():
    sdk, adapter, brain = await started()
    try:
        await brain.decide(context(1))
        async def failed_thread(**options):
            raise RuntimeError("Authored ThreadStart failure")
        adapter.client.thread_start = failed_thread
        with pytest.raises(ActorError, match="codex_provider_error"):
            await brain.decide(context(2))
        assert len(sdk.inputs) == 2 and adapter.report()["unique_model_turns"] == 1
        assert adapter.report()["usage_totals"]["total_tokens"] == 100
        assert brain.turns[-1]["status"] == "failed" and brain.turns[-1]["tool_calls"] == 0
        assert provider_telemetry([{"brain": brain.report()}])["tool_calls"] == 0
    finally:
        await brain.close()
