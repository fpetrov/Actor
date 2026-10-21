#!/usr/bin/env python3
"""Opt-in ordinary-client lifecycle checks; no server mutation or hidden-role input.

Only this invocation's Popen client is stopped. Reports contain selected own
identities, statuses and counters, never contexts, prompts, messages or reasoning.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "actor.social-lifecycle-acceptance.v1"
CASES = ("objective-cancel", "codex-turn-budget", "server-loss-observer")
TOKEN = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")


def read(path):
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def number(value, minimum=0, maximum=1_000_000_000):
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


def token(value):
    return isinstance(value, str) and TOKEN.fullmatch(value) is not None


def client_projection(value):
    velocity = value.get("velocity", {})
    speed = math.hypot(velocity.get("x"), velocity.get("y")) if isinstance(velocity, dict) and all(
        number(velocity.get(axis), -10000, 10000) for axis in ("x", "y")) else None
    return {"actor_id": value.get("actorId") if token(value.get("actorId")) else None,
        "accepted_session_id": value.get("lastAcceptedSessionId") if token(value.get("lastAcceptedSessionId")) else None,
        "connected": value.get("state") == "Connected", "keyboard_disabled": value.get("keyboardEnabled") is False,
        "speed": speed, "loop_rate": value.get("meanFps") if number(value.get("meanFps")) else None,
        "observations": value.get("observations") if type(value.get("observations")) is int else None}


class BrainProbe:
    """Observe actual public provider calls; never synthesize a denial/result."""
    def __init__(self, delegate):
        self.delegate = delegate
        self.denials, self.attempts, self.active, self.max_active = [], 0, 0, 0

    async def start(self, objective, catalog):
        await self.delegate.start(objective, catalog)

    async def decide(self, context):
        from actor_runtime.errors import ActorError
        self.attempts += 1; self.active += 1; self.max_active = max(self.max_active, self.active)
        try:
            return await self.delegate.decide(context)
        except ActorError as error:
            if error.code in ("codex_turn_limit", "codex_usage_limit"):
                self.denials.append(error.code)
            raise
        finally:
            self.active -= 1

    async def interrupt(self):
        await self.delegate.interrupt()

    async def interrupt_decision(self):
        await self.delegate.interrupt_decision()

    async def close(self):
        await self.delegate.close()

    def report(self):
        return self.delegate.report()


def evaluate(value):
    """Fail closed on absent/partial execution; safe aggregate output only."""
    case = value.get("case"); live = value.get("live", {}); after = value.get("after", {})
    checks = {"ordinary_live_owned_client": case in CASES and value.get("executed") is True
        and live.get("client_owned") is True and live.get("client_connected") is True
        and all(token(live.get(key)) for key in ("actor_id", "session_id", "match_id")),
        "foreground_released": after.get("active_skill") is False and after.get("runner_running") is False,
        "bounded_cleanup_complete": after.get("cleanup_errors") == 0 and after.get("pending_cleanup_tasks") == 0
            and after.get("provider_closed") is True,
        "no_commands_after_cleanup": type(after.get("commands_at_cleanup")) is int
            and after["commands_at_cleanup"] >= 0 and after.get("commands_later") == after["commands_at_cleanup"]
            and number(after.get("observation_seconds"), 2, 10),
        "owned_client_cleanup": value.get("owned_client_cleanup") is True}
    if case == "objective-cancel":
        checks.update(explicit_cancel_invoked=value.get("cancel_api_called") is True,
            cancelled_result=value.get("result_status") == "cancelled" and value.get("result_reason") == "objective_cancelled",
            cancelled_foreground_skill=value.get("controlled_skill_started") is True and value.get("controlled_skill_cancelled") is True,
            bridge_remained_connected=after.get("session_closed") is False and after.get("client_alive") is True
                and after.get("client_connected") is True and after.get("keyboard_disabled") is True,
            lease_reacquired=after.get("lease_reacquired") is True,
            physically_stopped=number(after.get("speed"), 0, .05))
    elif case == "codex-turn-budget":
        model = value.get("model", {})
        checks.update(actual_official_model_turn=type(model.get("unique_model_turns")) is int and model["unique_model_turns"] > 0
                and model.get("sdk_version") == "0.162.1" and model.get("sandbox") == "read-only" and model.get("ephemeral") is True,
            actual_codex_turn_limit=model.get("denials") == ["codex_turn_limit"] and model.get("configured_max_turns") == 1
                and model.get("provider_decisions") == 1 and model.get("attempts") == 2,
            first_real_wait_completed=value.get("real_wait_succeeded") is True,
            controlled_failure=value.get("result_status") == "failed" and value.get("result_reason") == "brain_failure",
            singleflight_no_tools=model.get("max_active") == 1 and model.get("active") == 0 and model.get("tool_calls") == 0,
            bridge_remained_connected=after.get("session_closed") is False and after.get("client_alive") is True and after.get("client_connected") is True)
    elif case == "server-loss-observer":
        checks.update(observed_authoritative_motion_expiry=value.get("motion_became_stale") is True,
            observed_bridge_disconnect=after.get("session_closed") is True,
            controlled_session_failure=value.get("result_status") == "failed" and (
                value.get("result_reason") == "session_disconnected" or value.get("result_reason") == "brain_reported_failure"
                and value.get("controlled_skill_failure_reason") in ("stale_world_state", "session_disconnected", "command_rejected")),
            client_survived_stopped=after.get("client_alive") is True and after.get("keyboard_disabled") is True
                and number(after.get("speed"), 0, .05),
            bounded_transport_loss=number(value.get("stale_to_terminal_seconds"), 0, 45))
    else:
        checks["known_case"] = False
    status = value.get("result_status") if value.get("result_status") in ("succeeded", "failed", "cancelled") else None
    reason = value.get("result_reason") if value.get("result_reason") in ("objective_cancelled", "brain_failure", "session_disconnected", "brain_reported_failure") else None
    return {"schema": SCHEMA, "case": case if case in CASES else None,
        "passed": all(checks.values()) and value.get("harness_error") is None, "checks": checks,
        "result_status": status, "result_reason": reason,
        "identity": {key: live.get(key) if token(live.get(key)) else None for key in ("actor_id", "session_id", "match_id")},
        "cleanup_seconds": value.get("cleanup_seconds") if number(value.get("cleanup_seconds"), 0, 60) else None,
        "stale_to_terminal_seconds": value.get("stale_to_terminal_seconds") if number(value.get("stale_to_terminal_seconds"), 0, 45) else None,
        "commands_at_cleanup": after.get("commands_at_cleanup") if type(after.get("commands_at_cleanup")) is int else None,
        "commands_later": after.get("commands_later") if type(after.get("commands_later")) is int else None,
        "model_turns": value.get("model", {}).get("unique_model_turns") if type(value.get("model", {}).get("unique_model_turns")) is int else None,
        "server_mutation_performed": False, "model_provider_requested": case == "codex-turn-budget",
        "limits": ["Controlled lifecycle objective, not autonomous match intelligence or a match win.",
            "Server-loss observer requires separate exact-match SSH fault identity evidence; it never kills a server.",
            "Headless client loop rate is not display FPS or keyboard latency."]}


async def stop_owned_client(process, expected_pid):
    if process is None:
        return True
    if type(expected_pid) is not int or process.pid != expected_pid:
        return False
    if process.poll() is None:
        process.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=8)
        except TimeoutError:
            if process.poll() is None: process.kill()
            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=5)
    return process.poll() is not None


async def drain(task, timeout=5):
    if task is None:
        return True
    done, _ = await asyncio.wait((task,), timeout=timeout)
    if task in done:
        if not task.cancelled(): task.exception()
        return True
    task.cancel()
    task.add_done_callback(lambda finished: None if finished.cancelled() else finished.exception())
    return False


async def until(predicate, *, timeout, stopping):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stopping.is_set(): raise RuntimeError("harness_interrupted")
        if predicate(): return
        await asyncio.sleep(.05)
    raise TimeoutError("bounded_condition_timeout")


async def wait_completion(task, *, timeout, stopping):
    stopped = asyncio.create_task(stopping.wait())
    try:
        done, _ = await asyncio.wait((task, stopped), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if stopping.is_set(): raise RuntimeError("harness_interrupted")
        if task not in done: raise TimeoutError("objective_completion_deadline")
        return await task
    finally:
        stopped.cancel()
        await asyncio.gather(stopped, return_exceptions=True)


async def run(args):
    from actor_runtime.brains import SequenceBrain, SkillCall, FinishObjective
    from actor_runtime.config import RouterConfig
    from actor_runtime.extensions import ExtensionRegistry
    from actor_runtime.objectives import Objective, ObjectiveRunner
    from actor_runtime.server import RuntimeServer
    from actor_runtime.skills import SkillRegistry, SkillScheduler
    from actor_runtime.builtin_skills import skill_package
    from actor.standard.communication.v1.communication_pb2 import CommunicationPeersObservation
    from actor.standard.v1.transform2d_pb2 import Transform2DObservation

    class ObservedScheduler(SkillScheduler):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw); self.outcomes = []
        async def run(self, identifier, arguments):
            result = await super().run(identifier, arguments)
            self.outcomes.append({"requested_id": identifier, "status": result.status, "reason": result.reason})
            return result

    args.output.mkdir(parents=True)
    deadline = time.monotonic() + args.timeout
    def budget(maximum):
        return max(.01, min(maximum, deadline - time.monotonic()))
    state = {"case": args.case, "executed": False, "harness_error": None}
    stopping = asyncio.Event(); loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(signum, stopping.set)
    credential = secrets.token_hex(32)
    server = RuntimeServer(ExtensionRegistry.discover(), credential, port=args.port)
    client = task = runner = session = scheduler = brain = None
    readiness = None
    client_pid = None
    try:
        await server.start()
        with (args.output / "client-stdout.log").open("w") as out:
            command = [str(args.client.resolve()), "--auto-room", args.room, "--player-name", "Lifecycle Participant",
                "--control-mode", "actor", "--actor-endpoint", f"http://127.0.0.1:{server.port}",
                "--actor-report", str(args.output / "client.json"), "--api-url", args.api_url,
                "-batchmode", "-nographics", "-screen-fullscreen", "0", "-screen-width", "800", "-screen-height", "600",
                "-logFile", str(args.output / "client.log")]
            client = subprocess.Popen(command, cwd=ROOT, env=dict(os.environ, ACTOR_BRIDGE_TOKEN=credential), stdout=out, stderr=subprocess.STDOUT)
        client_pid = client.pid
        write(args.output / "owned-client.json", {"schema": "actor.lifecycle-owned-client.v1", "pid": client_pid, "bridge_port": server.port})
        session = await server.next_session(timeout=budget(120))
        await until(lambda: (c := client_projection(read(args.output / "client.json")))["connected"]
            and c["actor_id"] == session.world.self_actor_id and c["accepted_session_id"] == session.session_id,
            timeout=budget(10), stopping=stopping)
        await until(lambda: session.world.latest(CommunicationPeersObservation) is not None
            and bool(session.world.latest(CommunicationPeersObservation).match_id)
            and session.world.latest_entry(Transform2DObservation) is not None, timeout=budget(10), stopping=stopping)
        match = session.world.latest(CommunicationPeersObservation).match_id
        if args.match_id is not None and args.match_id != match: raise RuntimeError("match_identity_mismatch")
        registry = SkillRegistry.discover()
        if args.ready_social:
            from extensions.agents_arena.v1 import social_pb2 as pb
            prep = SkillScheduler(session, registry)
            ready = await asyncio.wait_for(prep.run("agents-arena.ready-for-match", {}), timeout=budget(12))
            if ready.status != "succeeded": raise RuntimeError("ordinary_ready_failed")
            await until(lambda: (own := session.world.latest(pb.SocialOwnStateObservation)) is not None and own.assigned
                and own.life == pb.SOCIAL_LIFE_ALIVE and (phase := session.world.latest(pb.SocialMatchStateObservation)) is not None
                and phase.phase == pb.SOCIAL_PHASE_EXPLORATION, timeout=budget(150), stopping=stopping)
        # Deliberately isolated cognitive procedure: game observations remain
        # ordinary; phase noise cannot substitute an unrelated event failure for
        # the explicit API cancellation or provider-cap denial being measured.
        session.event_router.configure(config=RouterConfig(enabled=False))
        if args.case == "codex-turn-budget":
            from actor_codex.brain import CodexBrain
            brain = BrainProbe(CodexBrain(max_turns=1, usage_warning_tokens=100000, usage_limit_tokens=200000))
            registry = SkillRegistry()
            for definition in skill_package(): registry.register(definition)
            objective = "Use actor.wait to wait for 1 second, then finish this Objective successfully."
        else:
            identifier = "actor.wait" if args.skill == "wait" else "agents-arena.navigate-social-room"
            arguments = {"duration_seconds": 300.0} if args.skill == "wait" else {"target_room_id": args.target_room}
            brain = SequenceBrain([SkillCall(skill_id=identifier, arguments=arguments), FinishObjective(status="success")])
            objective = "Execute the selected ordinary Skill while the independent lifecycle monitor observes cancellation or connection loss."
        scheduler = ObservedScheduler(session, registry)
        runner = ObjectiveRunner(session, registry, brain, scheduler=scheduler, max_decisions=4,
            decision_timeout_seconds=120, objective_timeout_seconds=budget(600), run_finalizers=False)
        task = asyncio.create_task(runner.run(Objective(text=objective)))
        client_before = client_projection(read(args.output / "client.json"))
        state["live"] = {"client_owned": client.pid == client_pid and client.poll() is None,
            "client_connected": client_before["connected"], "actor_id": session.world.self_actor_id,
            "session_id": session.session_id, "match_id": match}
        if args.case != "codex-turn-budget":
            await until(lambda: scheduler.working_skill_metadata() is not None or task.done(), timeout=budget(20), stopping=stopping)
            if task.done(): raise RuntimeError("controlled_skill_did_not_remain_active")
            if args.skill == "navigate":
                await until(lambda: session.commands_sent >= 3 or task.done(), timeout=budget(10), stopping=stopping)
                if task.done(): raise RuntimeError("navigation_ended_before_fault")
            state["controlled_skill_started"] = True
            state["executed"] = True
            readiness = {"schema": SCHEMA, "ready": True, "case": args.case,
                "actor_id": session.world.self_actor_id, "session_id": session.session_id, "match_id": match,
                "room_code": args.room, "created_at_unix_ms": int(time.time() * 1000),
                "client_pid": client_pid, "commands": session.commands_sent}
            write(args.output / "ready.json", readiness)
            print(json.dumps({"ready": True, "case": args.case, "match_id": match}), flush=True)
        if args.case == "objective-cancel":
            started = time.monotonic(); state["cancel_api_called"] = True
            await asyncio.wait_for(runner.cancel(), timeout=20)
            result = await asyncio.wait_for(asyncio.shield(task), timeout=2)
            state["cleanup_seconds"] = time.monotonic() - started
        elif args.case == "codex-turn-budget":
            result = await wait_completion(task, timeout=budget(240), stopping=stopping)
            report = brain.report(); adapter = report.get("adapter") or {}
            state["executed"] = type(adapter.get("unique_model_turns")) is int and adapter["unique_model_turns"] > 0
            state["model"] = {key: adapter.get(key) for key in ("unique_model_turns", "sdk_version", "sandbox", "ephemeral")}
            state["model"].update(denials=list(brain.denials), configured_max_turns=brain.delegate.max_turns,
                provider_decisions=report.get("decision_count"), attempts=brain.attempts, max_active=brain.max_active,
                active=brain.active, tool_calls=sum(turn.get("tool_calls", 0) for turn in report.get("turns", [])))
        else:
            stale_at = None; next_ready = time.monotonic() + 1
            while (not task.done() or not session.closed.is_set()) and time.monotonic() < deadline:
                entry = session.world.latest_entry(Transform2DObservation)
                if entry is not None and time.monotonic() - entry.received_at > .8 and stale_at is None:
                    stale_at = time.monotonic()
                if stale_at is not None and time.monotonic() - stale_at > 45: raise TimeoutError("transport_loss_cleanup_deadline")
                if stopping.is_set(): raise RuntimeError("harness_interrupted")
                if time.monotonic() >= next_ready:
                    readiness.update(ready=not task.done() and not session.closed.is_set() and client.poll() is None
                        and entry is not None and time.monotonic() - entry.received_at <= .8,
                        created_at_unix_ms=int(time.time() * 1000), commands=session.commands_sent)
                    write(args.output / "ready.json", readiness)
                    next_ready = time.monotonic() + 1
                await asyncio.sleep(.05)
            if not task.done() or not session.closed.is_set(): raise TimeoutError("external_server_fault_not_observed")
            result = await task
            state["motion_became_stale"] = stale_at is not None
            state["stale_to_terminal_seconds"] = time.monotonic() - stale_at if stale_at is not None else None
        state.update(result_status=str(result.status), result_reason=result.reason,
            controlled_skill_cancelled=any(r["status"] == "cancelled" for r in scheduler.outcomes),
            controlled_skill_failure_reason=next((r["reason"] for r in scheduler.outcomes if r["status"] == "failed"), None),
            real_wait_succeeded=any(r["requested_id"] == "actor.wait" and r["status"] == "succeeded" for r in scheduler.outcomes))
        if args.case != "server-loss-observer":
            probe = await SkillScheduler(session, registry).run("actor.wait", {"duration_seconds": 0.0})
            lease_reacquired = probe.status == "succeeded"
        else: lease_reacquired = False
        commands = session.commands_sent; observed_at = time.monotonic()
        await asyncio.sleep(2.2)
        after_client = client_projection(read(args.output / "client.json")); report = runner.report()
        provider = brain.report(); adapter = provider.get("adapter") or {}
        state["after"] = {"commands_at_cleanup": commands, "commands_later": session.commands_sent,
            "observation_seconds": time.monotonic() - observed_at, "active_skill": scheduler.working_skill_metadata() is not None,
            "runner_running": runner._running, "cleanup_errors": len(report["cleanup_errors"]),
            "pending_cleanup_tasks": report["pending_cleanup_tasks"], "provider_closed": adapter.get("closed") is True if args.case == "codex-turn-budget" else provider.get("closed") is True,
            "session_closed": session.closed.is_set(), "client_alive": client.poll() is None,
            "client_connected": after_client["connected"], "keyboard_disabled": after_client["keyboard_disabled"],
            "speed": after_client["speed"], "lease_reacquired": lease_reacquired}
    except (Exception, asyncio.CancelledError) as error:
        state["harness_error"] = type(error).__name__
    finally:
        if readiness is not None:
            readiness.update(ready=False, created_at_unix_ms=int(time.time() * 1000))
            try:
                write(args.output / "ready.json", readiness)
            except OSError:
                state["harness_error"] = "readiness_close_failed"
        if runner is not None:
            try: await asyncio.wait_for(runner.cancel(), timeout=16)
            except (Exception, asyncio.CancelledError): state["harness_error"] = "cleanup_incomplete"
        if task is not None:
            if not task.done(): task.cancel()
            if not await drain(task): state["harness_error"] = "objective_drain_incomplete"
        try:
            await server.stop()
        except (Exception, asyncio.CancelledError):
            state["harness_error"] = "server_close_incomplete"
        finally:
            state["owned_client_cleanup"] = await stop_owned_client(client, client_pid)
        write(args.output / "evidence-private.json", state)
    result = evaluate(state); write(args.output / "acceptance.json", result)
    print(json.dumps({"passed": result["passed"], "case": result["case"], "checks": result["checks"]}), flush=True)
    return 0 if result["passed"] else 1


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True); parser.add_argument("--room", required=True)
    parser.add_argument("--output", type=Path, required=True); parser.add_argument("--port", type=int, default=18750)
    parser.add_argument("--client", type=Path, default=ROOT / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena")
    parser.add_argument("--api-url", default="http://127.0.0.1:5081"); parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--match-id"); parser.add_argument("--ready-social", action="store_true")
    parser.add_argument("--skill", choices=("wait", "navigate"), default="wait")
    parser.add_argument("--target-room", choices=("main-hall", "engineering", "security", "storage"), default="security")
    args = parser.parse_args(argv); args.room = args.room.upper(); args.output = args.output.resolve()
    if not re.fullmatch(r"[A-Z0-9]{6}", args.room): parser.error("Use an ordinary six-character room code")
    if not 1024 <= args.port <= 65535: parser.error("Use an unprivileged bridge port")
    if not number(args.timeout, 30, 900): parser.error("Timeout must be finite and between 30 and 900 seconds")
    if args.output.exists(): parser.error("Use a fresh output directory")
    if args.match_id is not None and not token(args.match_id): parser.error("Use a bounded match ID")
    if args.case == "codex-turn-budget" and args.skill != "wait": parser.error("The controlled Codex cap catalog contains only actor.wait")
    try:
        parsed = urlsplit(args.api_url)
        valid_url = (len(args.api_url) <= 2048 and parsed.scheme in ("http", "https") and parsed.hostname
            and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
            and (parsed.port is None or 1 <= parsed.port <= 65535) and all(ord(c) >= 33 for c in args.api_url))
    except ValueError:
        valid_url = False
    if not valid_url:
        parser.error("Use an absolute ordinary lobby API URL without credentials")
    if not args.client.is_file(): parser.error("Build the ordinary game client first")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(arguments())))
