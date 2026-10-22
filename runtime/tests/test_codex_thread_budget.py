"""Fresh ordinary decision threads preserve the measured episode budget.

Public SDK fakes only: no SDK client, authentication, process or model starts.
"""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from actor_codex.brain import CodexBrain
from actor_runtime.errors import ActorError
from actor_runtime.objectives import Objective
from actor_runtime.snapshots import build_decision_context
from test_codex import FakeSDK,TextInput,ExternalMessage,registry,fake_codex_launch


def usage(total):
    value={"input_tokens":total-10,"cached_input_tokens":max(0,total-30),
        "output_tokens":10,"total_tokens":total}
    return NS(model_dump=lambda **_: {"last":dict(value),"total":dict(value)})


class TrackedSDK(FakeSDK):
    def __init__(self,**kwargs):
        super().__init__(outputs=[{"decision":{"kind":"finish","status":"success"}}]*8,**kwargs)
        self.thread_inputs=[];self.rotation_entered=asyncio.Event();self.rotation_release=asyncio.Event()
        self.block_rotation=False
    def AsyncCodex(self,config):
        client=super().AsyncCodex(config);start=client.thread_start
        async def thread_start(**options):
            if self.thread_inputs and self.block_rotation:
                self.rotation_entered.set();await self.rotation_release.wait()
            thread=await start(**options);inputs=[];self.thread_inputs.append(inputs)
            turn=thread.turn
            async def tracked(input,*,output_schema):
                inputs.append(input);return await turn(input,output_schema=output_schema)
            thread.turn=tracked
            return thread
        client.thread_start=thread_start
        return client


async def test_each_decision_uses_one_snapshot_and_fresh_thread_with_same_trusted_policy(session):
    sdk=TrackedSDK(usage=usage(100));adapter=sdk.adapter(model="gpt-6-luna")
    catalog=registry().catalog(session)
    await adapter.start_episode("Trusted game objective",catalog)
    try:
        for number in range(3):
            await adapter.decide(json.dumps({"decision_index":number+1,"working_knowledge":[{"summary":"Own bounded history"}]}))
        assert len(sdk.clients)==1 and len(sdk.config_clients)==1 and sdk.config_clients[0].closed
        assert len(sdk.thread_inputs)==3 and all(len(items)==2 for items in sdk.thread_inputs)
        for number,items in enumerate(sdk.thread_inputs):
            assert isinstance(items[0],TextInput) and items[0].text=="Trusted game objective"
            assert isinstance(items[1],ExternalMessage)
            assert json.loads(items[1].content)["decision_index"]==number+1
            assert sdk.thread_options[number]==sdk.thread_options[0]
            assert sdk.thread_options[number]["ephemeral"] is True
            assert sdk.thread_options[number]["sandbox"]=="read-only"
        assert adapter.report()["thread_rotations"]==2
        assert adapter.report()["unique_model_turns"]==3
        # Both initiating/joined subscriptions report100 for each thread.
        assert adapter.report()["usage_totals"]["total_tokens"]==300
        assert adapter.report()["usage_totals"]["input_tokens"]==270
        assert adapter.report()["usage_totals"]["cached_input_tokens"]==210
    finally:await adapter.close()
    assert sdk.clients[0].closed and adapter.report()["closed"]


async def test_fresh_threads_do_not_reset_brain_or_participation_token_gate(session):
    sdk=TrackedSDK(usage=usage(100));adapter=sdk.adapter()
    brain=CodexBrain(adapter=adapter,usage_warning_tokens=150,usage_limit_tokens=200)
    objective=Objective(text="Bounded objective");catalog=registry().catalog(session)
    await brain.start(objective,catalog)
    context=build_decision_context(session,objective,catalog)
    try:
        await brain.decide(context);await brain.decide(context)
        assert brain.report()["usage_totals"]["total_tokens"]==200
        with pytest.raises(ActorError,match="codex_usage_limit"):await brain.decide(context)
        assert len(sdk.thread_inputs)==2 and len(brain.turns)==2
        assert brain.usage_warning
    finally:await brain.close()


async def test_late_owned_thread_usage_deduplicates_against_its_original_thread(session):
    sdk=TrackedSDK(usage=usage(100));adapter=sdk.adapter()
    await adapter.start_episode("Bounded objective",registry().catalog(session))
    try:
        first=await adapter.decide("{}");second=await adapter.decide("{}")
        late=NS(id=first.turn_id,status="completed",usage=usage(150))
        adapter._record_result(late);adapter._record_result(late)
        assert adapter.report()["usage_totals"]["total_tokens"]==250
        adapter._record_result(NS(id=second.turn_id,status="completed",usage=usage(200)))
        assert adapter.report()["usage_totals"]["total_tokens"]==350
        with pytest.raises(ActorError,match="codex_turn_incomplete"):
            adapter._record_result(NS(id="unowned",status="completed",usage=usage(999999)))
        assert adapter.report()["usage_totals"]["total_tokens"]==350
    finally:await adapter.close()


async def test_interrupted_thread_usage_is_retained_when_next_decision_starts_fresh(session):
    sdk=TrackedSDK(blocked=True,usage=usage(100));adapter=sdk.adapter()
    await adapter.start_episode("Bounded objective",registry().catalog(session))
    try:
        task=asyncio.create_task(adapter.decide("{}"));await sdk.started.wait()
        await adapter.interrupt_decision()
        with pytest.raises(asyncio.CancelledError):await task
        assert adapter.report()["usage_totals"]["total_tokens"]==100
        sdk.blocked=False;await adapter.decide("{}")
        assert adapter.report()["usage_totals"]["total_tokens"]==200
        assert len(sdk.thread_inputs)==2 and adapter.report()["interrupted_turns"]==1
    finally:await adapter.close()


async def test_close_during_late_rotation_cannot_start_a_stale_model_turn(session):
    sdk=TrackedSDK();adapter=sdk.adapter()
    await adapter.start_episode("Bounded objective",registry().catalog(session));await adapter.decide("{}")
    sdk.block_rotation=True
    task=asyncio.create_task(adapter.decide("{}"));await sdk.rotation_entered.wait()
    with pytest.raises(ActorError,match="codex_turn_busy"):await adapter.decide("{}")
    closing=asyncio.create_task(adapter.close())
    for _ in range(4):await asyncio.sleep(0)
    sdk.rotation_release.set()
    with pytest.raises(asyncio.CancelledError):await task
    await asyncio.wait_for(closing,1)
    assert adapter.closed and adapter.thread is None and adapter.workspace is None
    assert sdk.clients[0].closed and adapter.report()["unique_model_turns"]==1
    assert len(sdk.inputs)==2  # Only the first decision reached a model turn.
