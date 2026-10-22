"""Public-stream telemetry fakes; no SDK extra, authentication or model calls."""
import asyncio
from types import SimpleNamespace

import pytest

from actor_codex.adapter import OfficialCodexAdapter
from actor_codex.brain import CodexBrain
from actor_codex.stream import ToolObservation, collect_turn
from actor_runtime.errors import ActorError
from actor_runtime.objectives import Objective
from actor_runtime.snapshots import build_decision_context
from actor_runtime.telemetry import provider_telemetry
from test_codex import FakeSDK, registry, fake_codex_launch


async def start(session,sdk):
    adapter=sdk.adapter();brain=CodexBrain(adapter=adapter)
    objective=Objective(text="Use only the supplied game context")
    catalog=registry().catalog(session)
    await brain.start(objective,catalog)
    return adapter,brain,build_decision_context(session,objective,catalog)


async def test_completed_started_and_completed_tool_deduplicates_joined_sdk_handles(session):
    tool=SimpleNamespace(id="tool-one",type="commandExecution")
    sdk=FakeSDK(started_items=[tool],completed_items=[tool])
    adapter,brain,context=await start(session,sdk)
    try:
        await brain.decide(context)
        assert brain.turns[0]["tool_calls"]==1 and brain.turns[0]["tool_count_scope"]=="sdk_item_lifecycle"
        assert adapter.last_decision_tool_telemetry()["tool_count_complete"]
        assert adapter.report()["unique_model_turns"]==1 and sdk.streams_closed==2
    finally:
        await brain.close()


async def test_external_message_output_receipt_is_passive_without_hiding_tool_start(session):
    receipt=SimpleNamespace(id="external-state",type="functionCallOutput",name="game-state",namespace=None,
        output=SimpleNamespace(root="untrusted state"))
    tool=SimpleNamespace(id="actual-tool",type="dynamicToolCall")
    sdk=FakeSDK(started_items=[receipt,tool],completed_items=[receipt])
    adapter,brain,context=await start(session,sdk)
    try:
        await brain.decide(context)
        assert brain.turns[0]["tool_calls"]==1
        assert adapter.last_decision_tool_telemetry()["tool_count_complete"]
        assert sdk.streams_closed==2
    finally:
        await brain.close()


@pytest.mark.parametrize("started_tool",[False,True])
async def test_interruption_counts_actual_started_items_even_without_item_completed(session,started_tool):
    tool=SimpleNamespace(id="inflight-tool",type="webSearch")
    sdk=FakeSDK(blocked=True,started_items=[tool] if started_tool else [])
    adapter,brain,context=await start(session,sdk)
    pending=asyncio.create_task(brain.decide(context))
    await sdk.started.wait()
    await brain.interrupt_decision()
    with pytest.raises(asyncio.CancelledError):await pending
    assert brain.turns[0]["status"]=="interrupted"
    assert brain.turns[0]["tool_calls"]==int(started_tool)
    assert adapter.last_decision_tool_telemetry()["tool_count_complete"] and sdk.streams_closed==2
    assert provider_telemetry([{"brain":brain.report()}])["tool_calls"]==int(started_tool)
    await brain.close()


async def test_missing_terminal_result_keeps_tool_coverage_unknown_and_closes_consumers(session):
    sdk=FakeSDK(terminal=False,started_items=[SimpleNamespace(id="started",type="imageView")])
    adapter,brain,context=await start(session,sdk)
    with pytest.raises(ActorError,match="codex_provider_error"):await brain.decide(context)
    assert "tool_calls"not in brain.turns[0]
    assert adapter.last_decision_tool_telemetry()["tool_calls"]is None
    assert adapter.last_decision_tool_telemetry()["observed_tool_items"]==1
    assert not provider_telemetry([{"brain":brain.report()}])["tool_count_complete"]
    assert sdk.streams_closed==2
    await brain.close()


async def test_python_cancellation_before_sdk_terminal_never_manufactures_zero(session):
    sdk=FakeSDK(blocked=True)
    adapter,brain,context=await start(session,sdk)
    pending=asyncio.create_task(brain.decide(context));await sdk.started.wait();pending.cancel()
    with pytest.raises(asyncio.CancelledError):await pending
    assert "tool_calls"not in brain.turns[0]
    assert not adapter.last_decision_tool_telemetry()["tool_count_complete"]
    assert sdk.streams_closed==2
    await brain.close();assert sdk.clients[0].closed


async def test_unknown_item_kind_makes_complete_looking_stream_tool_coverage_unknown(session):
    sdk=FakeSDK(started_items=[SimpleNamespace(id="new-item",type="futureToolType")])
    adapter,brain,context=await start(session,sdk)
    await brain.decide(context)
    assert "tool_calls"not in brain.turns[0]
    assert adapter.last_decision_tool_telemetry()["tool_calls"]is None
    await brain.close()


async def test_item_overflow_fails_bounded_without_a_zero_tool_claim(session,monkeypatch):
    monkeypatch.setattr("actor_codex.stream.MAX_ITEMS",2)
    sdk=FakeSDK(completed_items=[SimpleNamespace(id=str(i),type="reasoning") for i in range(3)])
    adapter,brain,context=await start(session,sdk)
    with pytest.raises(ActorError,match="codex_stream_limit"):await brain.decide(context)
    assert "tool_calls"not in brain.turns[0]
    assert adapter.last_decision_tool_telemetry()["tool_calls"]is None and sdk.streams_closed==2
    await brain.close()


async def test_public_turn_result_preserves_final_answer_items_usage_timing_and_close():
    items=[SimpleNamespace(id="legacy",type="agentMessage",phase=None,text="legacy"),
        SimpleNamespace(id="final",type="agentMessage",phase="final_answer",text="structured-final"),
        SimpleNamespace(id="comment",type="agentMessage",phase="commentary",text="commentary")]
    usage=object();closed=[]
    turn=SimpleNamespace(id="turn",status="completed",error=None,started_at=10,completed_at=20,duration_ms=100)
    async def stream():
        try:
            for item in items:
                yield SimpleNamespace(method="item/completed",payload=SimpleNamespace(turn_id="turn",item=item))
            yield SimpleNamespace(method="thread/tokenUsage/updated",payload=SimpleNamespace(turn_id="turn",token_usage=usage))
            yield SimpleNamespace(method="turn/completed",payload=SimpleNamespace(turn=turn))
        finally:closed.append(True)
    observation=ToolObservation()
    result=await collect_turn(SimpleNamespace(TurnResult=SimpleNamespace),SimpleNamespace(id="turn",stream=stream),observation,primary=True)
    assert result.final_response=="structured-final" and result.items==items and result.usage is usage
    assert result.started_at==10 and result.completed_at==20 and result.duration_ms==100
    assert observation.complete and observation.count==0 and closed==[True]


async def test_failed_sdk_terminal_preserves_run_failure_and_unknown_tool_coverage():
    closed=[]
    async def stream():
        try:
            turn=SimpleNamespace(id="turn",status="failed",error=SimpleNamespace(message="synthetic provider failure"))
            yield SimpleNamespace(method="turn/completed",payload=SimpleNamespace(turn=turn))
        finally:closed.append(True)
    observation=ToolObservation()
    with pytest.raises(RuntimeError):
        await collect_turn(SimpleNamespace(TurnResult=SimpleNamespace),SimpleNamespace(id="turn",stream=stream),observation,primary=True)
    assert not observation.complete and closed==[True]


async def test_late_joined_stream_cannot_certify_missing_initiating_stream_coverage():
    observation=ToolObservation();tool=SimpleNamespace(id="tool",type="commandExecution")
    async def joined():
        yield SimpleNamespace(method="item/completed",payload=SimpleNamespace(turn_id="turn",item=tool))
        turn=SimpleNamespace(id="turn",status="completed",error=None,started_at=None,completed_at=None,duration_ms=None)
        yield SimpleNamespace(method="turn/completed",payload=SimpleNamespace(turn=turn))
    await collect_turn(SimpleNamespace(TurnResult=SimpleNamespace),SimpleNamespace(id="turn",stream=joined),observation,primary=False)
    assert observation.count==1 and not observation.complete
    async def incomplete_primary():
        yield SimpleNamespace(method="item/started",payload=SimpleNamespace(turn_id="turn",item=tool))
    with pytest.raises(RuntimeError):
        await collect_turn(SimpleNamespace(TurnResult=SimpleNamespace),SimpleNamespace(id="turn",stream=incomplete_primary),observation,primary=True)
    assert observation.count==1 and not observation.complete
