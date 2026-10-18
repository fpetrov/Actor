"""Manual real-provider soft-interrupt proof; no Unity or gameplay claims.

Run from Actor/: build/runtime-venv/bin/python spikes/codex/event_replan.py
Uses managed Codex sign-in. Never writes prompts, SDK items or hidden reasoning.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time
from types import SimpleNamespace

from google.protobuf.wrappers_pb2 import StringValue

from actor.core.v1 import actor_pb2 as core
from actor_codex.adapter import OfficialCodexAdapter, SDK_VERSION
from actor_codex.brain import CodexBrain
from actor_runtime.brains import FinishObjective, SkillCall
from actor_runtime.builtin_skills import skill_package
from actor_runtime.classifiers import EventAssessment, EventHints
from actor_runtime.objectives import Objective
from actor_runtime.snapshots import build_decision_context
from actor_runtime.world import CachedPayload, WorldState


def wait_catalog():
    definition = skill_package()[0]
    return [{"id": definition.skill_id, "version": definition.version,
        "description": definition.description, "input_schema": definition.input_model.model_json_schema(),
        "result_schema": definition.result_model.model_json_schema(), "required_capabilities": [],
        "owned_command_capabilities": [], "timeout_seconds": definition.timeout_seconds}]


async def main(output, model):
    report = {"sdk_version": SDK_VERSION, "fixture": "real_provider_only_no_unity",
              "game_commands": 0, "passed": False}
    adapter = OfficialCodexAdapter(model=model)
    brain = CodexBrain(adapter=adapter)
    objective = Objective(text=("Choose actor.wait for 30 seconds. If an urgent recall event arrives, "
        "finish successfully immediately. Do not wait again after the urgent recall. Use no tools."))
    catalog = wait_catalog()
    session = SimpleNamespace(world=WorldState("spike-actor"), manifest=core.CapabilityManifest(
        game_id="provider-spike", game_version="0", self_actor_id="spike-actor",
        protocol_version=core.ProtocolVersion(major=1, minor=0)))
    pending = None
    active, peak_active = 0, 0
    async def decide(context):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        try:
            return await brain.decide(context)
        finally:
            active -= 1
    started = time.monotonic()
    try:
        async with asyncio.timeout(120):
            await brain.start(objective, catalog)
            thread_id = adapter.thread.id
            client = adapter.client
            context = build_decision_context(session, objective, catalog)
            pending = asyncio.create_task(decide(context))
            # Wait for public turn handles for user and external input, then
            # interrupt while the decision operation is still active.
            async with asyncio.timeout(30):
                while len(adapter.handles) < 2 and not pending.done():
                    await asyncio.sleep(0.01)
            if pending.done():
                raise RuntimeError("Initial turn completed before interruption fixture could act")
            first_ids = {handle.id for handle in adapter.handles}
            interruption = time.monotonic()
            await brain.interrupt_decision()
            cancelled = False
            try:
                await pending
            except asyncio.CancelledError:
                cancelled = True
            report["old_decision_cancelled"] = cancelled
            report["interrupt_seconds"] = time.monotonic() - interruption
            report["initial_turn_statuses"] = {key: adapter.completed_turns.get(key) for key in first_ids}
            report["old_decision_drained_before_resume"] = pending.done() and not adapter.handles
            trigger = SimpleNamespace(id="provider-spike:urgent-recall", session_id="provider-spike",
                entry=CachedPayload("spike-actor", "spike.urgent-recall", 1, 1,
                    StringValue(value="Urgent recall received. Return immediately; this fixture has no game commands.")),
                extension_id="spike", extension_version="1", aggregate_count=1,
                hints=EventHints(potentially_critical=True),
                assessment=EventAssessment(relevance="relevant", urgency="critical", confidence=1.0,
                    should_interrupt_skill=True, should_request_brain_review=True, reason_code="fixture_recall"))
            updated = build_decision_context(session, objective, catalog, decision_index=2,
                cognitive_revision=1, decision_reason="critical_event", trigger_events=[trigger],
                previous_skill_result={"skill_id": "actor.wait", "status": "cancelled",
                    "reason": "preempted_by_event", "details": {"trigger_event_ids": [trigger.id]}})
            decision = await decide(updated)
            report["same_thread"] = adapter.thread.id == thread_id
            report["same_client"] = adapter.client is client
            report["continuation_decision"] = decision.model_dump(mode="json")
            report["peak_active_decisions"] = peak_active
            report["single_flight"] = report["old_decision_drained_before_resume"] and peak_active == 1
            report["passed"] = (cancelled and report["same_thread"] and report["single_flight"] and
                "interrupted" in report["initial_turn_statuses"].values() and
                isinstance(decision, (FinishObjective, SkillCall)))
    except Exception as error:
        report.update(error_type=type(error).__name__, error_code=getattr(error, "code", None))
    finally:
        if pending is not None and not pending.done():
            await brain.interrupt()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        if pending is not None and pending.done() and not pending.cancelled():
            pending.exception()
        await brain.close()
        report["duration_seconds"] = time.monotonic() - started
        report["brain"] = brain.report()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("build/event-validation/codex-soft-interrupt.json"))
    parser.add_argument("--model", default=None)
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(main(arguments.output, arguments.model)))
