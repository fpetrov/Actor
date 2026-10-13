"""Manual real Unity/VPS safety checks; model execution is never part of CI."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

from actor_codex.adapter import OfficialCodexAdapter
from actor_codex.brain import CodexBrain
from actor_runtime.brains import BrainRegistry
from actor_runtime.extensions import ExtensionRegistry
from actor_runtime.objectives import Objective, ObjectiveRunner
from actor_runtime.server import RuntimeServer
from actor_runtime.skills import SkillRegistry


async def wait_until(predicate, timeout=30):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.025)


async def main(args):
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    server = RuntimeServer(ExtensionRegistry.discover(), token, port=50051)
    await server.start()
    client = None
    runner_task = None
    report = {"scenario": args.scenario, "room": args.room, "passed": False}
    try:
        with (output / "client-stdout.log").open("w") as stream:
            client = subprocess.Popen([str(root / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena"),
                "--auto-room", args.room, "--player-name", "Safety Pilot", "--control-mode", "actor",
                "--actor-endpoint", "http://127.0.0.1:50051", "--actor-report", str(output / "client.json"),
                "-logFile", str(output / "client.log")], cwd=root,
                env=dict(os.environ, ACTOR_BRIDGE_TOKEN=token), stdout=stream, stderr=subprocess.STDOUT)
        session = await server.next_session(timeout=30)
        await session.world.wait_until(lambda world: len(world.observations) >= 3, timeout=10)
        registry = SkillRegistry.discover()
        adapter = OfficialCodexAdapter() if args.scenario == "codex-interrupt" else None
        brain = CodexBrain(adapter=adapter) if adapter else BrainRegistry.discover().create("scripted")
        runner = ObjectiveRunner(session, registry, brain)
        runner_task = asyncio.create_task(runner.run(Objective(
            text="Go to Engineering, wait there for 2 seconds, then return to Main Hall and finish.")))
        if adapter:
            await wait_until(lambda: bool(adapter.handles), timeout=30)
            report["active_sdk_turn_ids"] = sorted({handle.id for handle in adapter.handles})
            await runner.cancel()
            result = await runner_task
            report["sdk_turn_statuses"] = list(adapter.completed_turns.values())
            report["sdk_interrupted"] = adapter.report()["interrupted"]
            assert result.status == "cancelled"
            assert runner.report()["scheduler"]["skill_count"] == 0
            assert session.commands_sent == 0
            assert report["sdk_interrupted"] and "interrupted" in report["sdk_turn_statuses"]
            await server.stop()
        else:
            await wait_until(lambda: session.commands_sent >= 12)
            report["commands_before_disconnect"] = session.commands_sent
            await server.stop()
            result = await runner_task
            assert result.status == "failed" and result.reason == "session_disconnected"
            history = runner.report()["scheduler"]["skills"]
            assert history and history[-1]["status"] in ("failed", "cancelled")
        report["objective"] = result.model_dump(mode="json")
        report["cognitive"] = runner.report()
        # Unity keeps exchanging authoritative multiplayer snapshots after bridge loss.
        stopped_report = None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            if (output / "client.json").exists():
                stopped_report = json.loads((output / "client.json").read_text())
                velocity = stopped_report["velocity"]
                if (stopped_report["state"] == "Error" and abs(velocity["x"]) + abs(velocity["y"]) < 0.05):
                    break
        assert client.poll() is None and stopped_report is not None
        assert stopped_report["state"] == "Error"
        assert stopped_report["meanFps"] > 30 and not stopped_report["errors"]
        assert not stopped_report["keyboardEnabled"]
        assert abs(stopped_report["velocity"]["x"]) + abs(stopped_report["velocity"]["y"]) < 0.05
        report["unity_continues_running"] = True
        report["unity_safe_stop"] = stopped_report
        report["runtime"] = server.report()
        report["passed"] = True
        (output / "safety.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"passed": True, "scenario": args.scenario, "objective_status": result.status,
                          "sdk_turn_statuses": report.get("sdk_turn_statuses"), "client_pid": client.pid,
                          "report": str(output / "safety.json")}), flush=True)
        if args.keep_client:
            await asyncio.to_thread(client.wait)
    finally:
        if runner_task and not runner_task.done():
            runner_task.cancel()
            await asyncio.gather(runner_task, return_exceptions=True)
        await server.stop()
        if client and client.poll() is None:
            client.terminate()
            await asyncio.to_thread(client.wait)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("codex-interrupt", "disconnect"))
    parser.add_argument("--room", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-client", action="store_true")
    asyncio.run(main(parser.parse_args()))
