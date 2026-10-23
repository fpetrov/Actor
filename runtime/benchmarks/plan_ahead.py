"""Compare inter-action waits through the real Runner with controlled Brain latency.

No game, credentials or external model calls. Run from the repository root with
PYTHONPATH=runtime:generated/python python runtime/benchmarks/plan_ahead.py.
"""
import argparse
import asyncio
import json
from pathlib import Path
import statistics
import time

from actor.core.v1 import actor_pb2 as core
from actor.core.v1 import bridge_pb2 as wire
from actor_runtime.brains import FinishObjective, SequenceBrain, SkillCall, SkillPlan
from actor_runtime.config import CognitiveConfig
from actor_runtime.extensions import ExtensionRegistry
from actor_runtime.objectives import Objective, ObjectiveRunner
from actor_runtime.session import RuntimeSession
from actor_runtime.skills import ActorSkill, SkillDefinition, SkillModel, SkillRegistry


class Input(SkillModel):
    pass


class Output(SkillModel):
    pass


async def probe(enabled):
    timeline = []

    class Work(ActorSkill):
        async def execute(self, context, arguments):
            timeline.append(("start", time.monotonic()))
            await context.sleep(.2)
            timeline.append(("end", time.monotonic()))
            return Output()

    class Brain(SequenceBrain):
        def __init__(self):
            plan = SkillPlan(steps=[SkillCall(skill_id="benchmark.work", arguments={})])
            super().__init__([plan, plan, FinishObjective(status="success")])
            self.active = self.peak_active = 0

        async def decide(self, context):
            self.active += 1
            self.peak_active = max(self.active, self.peak_active)
            try:
                await asyncio.sleep(.12)
                return await super().decide(context)
            finally:
                self.active -= 1

    session = RuntimeSession(ExtensionRegistry.discover())
    await session.accept(wire.GameToRuntime(session_id="benchmark", sequence=1,
        hello=core.GameHello(game_id="benchmark", game_version="0.1.0", self_actor_id="actor-1",
                             protocol_version=core.ProtocolVersion(major=1))))
    await session.accept(wire.GameToRuntime(session_id="benchmark", sequence=2,
        manifest=core.CapabilityManifest(game_id="benchmark", game_version="0.1.0", self_actor_id="actor-1",
                                        protocol_version=core.ProtocolVersion(major=1))))
    registry = SkillRegistry()
    registry.register(SkillDefinition("benchmark.work", "1.0.0", "Measured bounded work", Input, Output, Work))
    brain = Brain()
    runner = ObjectiveRunner(session, registry, brain,
        config=CognitiveConfig(brain={"plan_ahead": enabled}), run_finalizers=False)
    started = time.monotonic()
    try:
        result = await runner.run(Objective(text="Complete both benchmark actions"))
    finally:
        session.close()
    elapsed = time.monotonic() - started
    starts = [stamp for kind, stamp in timeline if kind == "start"]
    ends = [stamp for kind, stamp in timeline if kind == "end"]
    assert result.status == "succeeded" and len(starts) == len(ends) == 2
    assert brain.turn_count == 3 and brain.peak_active == 1
    return {"elapsed_ms": elapsed * 1000, "inter_action_wait_ms": (starts[1] - ends[0]) * 1000,
            "decisions": brain.turn_count, "actions": len(starts), "peak_concurrent_decisions": brain.peak_active,
            "status": result.status}


async def main(output, repeats):
    result = {"scope": "Real ObjectiveRunner and SkillScheduler, simulated 120ms Brain and 200ms work; no live game",
              "repeats": repeats, "modes": {}}
    for enabled in (False, True):
        samples = [await probe(enabled) for _ in range(repeats)]
        result["modes"]["plan_ahead" if enabled else "sequential"] = {
            "median_inter_action_wait_ms": statistics.median(s["inter_action_wait_ms"] for s in samples),
            "median_elapsed_ms": statistics.median(s["elapsed_ms"] for s in samples), "samples": samples}
    sequential = result["modes"]["sequential"]["median_inter_action_wait_ms"]
    ahead = result["modes"]["plan_ahead"]["median_inter_action_wait_ms"]
    result["inter_action_wait_reduction_percent"] = 100 * (1 - ahead / sequential)
    text = json.dumps(result, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, choices=range(1, 11), default=3)
    args = parser.parse_args()
    asyncio.run(main(args.output, args.repeats))
