#!/usr/bin/env python3
"""Manual M5 fault acceptance using two ordinary clients in isolated VPS lobbies.

Each worker owns a Runtime, Brain, Scheduler and Unity client. The supervisor
reads metadata-only reports and injects process faults; reports never carry
agent instructions, message text or authorization. Run only after deployment,
outside performance measurements. Remote termination reuses M4's exact own-PID
identity guard, and never targets a process by name or wildcard port.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
import uuid

from actor_agents_arena import CALIBRATION_STATE, ROOM_MEMBERSHIP, CalibrationTaskStateObservation
from actor_agents_arena.calibration import status_name
from actor_runtime.brains import FinishObjective, SkillCall
from actor_runtime.extensions import ExtensionRegistry
from actor_runtime.objectives import Objective, ObjectiveRunner
from actor_runtime.server import RuntimeServer
from actor_runtime.skills import SkillRegistry
from actor_standard import TRANSFORM, MOTION
from actor_standard_communication import PEERS, CommunicationPeersObservation


ROOT = Path(__file__).resolve().parents[2]
CASES = ("cancel", "bridge", "participant", "server")
ACTIVE_TASK_STATUSES = {"awaiting_diagnostic", "awaiting_authorization"}
SETUP_OBSERVATIONS = (PEERS, CALIBRATION_STATE, TRANSFORM, MOTION, ROOM_MEMBERSHIP)


def setup_ready(world):
    return all((world.self_actor_id, capability) in world.observations for capability in SETUP_OBSERVATIONS)


def terminal_budget(kind):
    if kind not in CASES:
        raise ValueError("Unknown fault case")
    # Abrupt process loss does not guarantee any graceful disconnect packet.
    # Both a killed client and a killed server may need NGO's 30-second timeout.
    return 45 if kind in ("participant", "server") else 15


def stopped_client(value):
    velocity = value.get("velocity", {})
    return (all(math.isfinite(velocity.get(axis, float("nan"))) for axis in ("x", "y"))
        and math.hypot(velocity["x"], velocity["y"]) < .05
        and value.get("keyboardEnabled") is False)


def verify_terminal(kind, before, after):
    for role, status in after.items():
        assert status["result"]["status"] in ("failed", "cancelled"), role
        assert not status["active_skill_id"] and not status["failed"], role
        assert status["task_id"] == before[role]["task_id"], "Task identity changed during fault"
        assert status["match_id"] == before[role]["match_id"], "Match identity changed during fault"
    technician, coordinator = after["technician"], after["coordinator"]
    if kind == "server":
        assert all(status["session_closed"] and status["result"]["reason"] == "session_disconnected"
            for status in after.values()), "Server loss did not end both bridges"
        return
    remaining = coordinator if kind == "cancel" else technician
    assert remaining["task_status"] == "cancelled"
    assert remaining["result"]["skill_results"][-1]["reason"] == "preempted_by_event"
    assert remaining["cognitive"]["replan_count"] == 1
    assert remaining["client_alive"] and not remaining["session_closed"]
    expected_reason = {"cancel": "participant_cancelled", "bridge": "participant_control_unavailable",
                       "participant": "participant_disconnected"}[kind]
    assert remaining["task_reason"] == expected_reason, "A different cancellation path produced this result"
    if kind == "cancel":
        assert technician["result"]["status"] == "cancelled"
        assert technician["task_status"] == "cancelled" and technician["task_reason"] == expected_reason
        assert not technician["session_closed"] and technician["client_alive"]
    elif kind == "bridge":
        assert coordinator["client_alive"], "Bridge case also terminated the participant process"
        assert coordinator["session_closed"]
        assert technician["peer_count"] == 2, "Bridge case also removed the participant from the game"
    else:
        assert not coordinator["client_alive"] and coordinator["session_closed"]
        assert technician["peer_count"] == 1, "Server did not remove the abruptly lost participant"


def settled_terminal(kind, after):
    if kind == "server":
        return True
    remaining = after["coordinator" if kind == "cancel" else "technician"]
    expected = {"cancel": "participant_cancelled", "bridge": "participant_control_unavailable",
                "participant": "participant_disconnected"}[kind]
    return (remaining.get("task_status") == "cancelled" and remaining.get("task_reason") == expected
        and (kind != "participant" or remaining.get("peer_count") == 1))


def old_fixture():
    path = ROOT / "spikes/event-routing/runtime_faults.py"
    spec = importlib.util.spec_from_file_location("actor_m4_fault_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


async def until(predicate, timeout):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.05)


class FaultSetupBrain:
    """Set up a real task without inspecting or transmitting its authorization."""
    def __init__(self, role):
        self.role = role
        self.step = self.turn_count = 0
        self.task_id = self.partner = ""
        self.closed = self.interrupted = False

    async def start(self, objective, catalog):
        self.step = self.turn_count = 0
        self.task_id = self.partner = ""
        self.closed = self.interrupted = False

    async def decide(self, context):
        self.turn_count += 1
        previous = context.previous_skill_result or {}
        if previous and previous.get("status") != "succeeded":
            return FinishObjective(status="failure", reason="A required task Skill ended without success")
        output = previous.get("output", {})
        step, self.step = self.step, self.step + 1
        if self.role == "coordinator":
            if step == 0:
                peers = [peer for observation in context.observations
                    if observation.capability_id == PEERS and isinstance(observation.value, dict)
                    for peer in observation.value.get("peers", [])
                    if peer.get("actorId") != context.actor_id and peer.get("displayName") == "Technician"]
                partner = peers[0]["actorId"] if len(peers) == 1 else ""
                return SkillCall(skill_id="agents-arena.start-calibration",
                    arguments={"technician_actor_id": partner})
            if step == 1:
                self.task_id = output["task_id"]
                return SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 240.0})
        else:
            if step == 0:
                return SkillCall(skill_id="agents-arena.wait-for-calibration-assignment", arguments={})
            if step == 1:
                self.task_id, self.partner = output["task_id"], output["coordinator_actor_id"]
                return SkillCall(skill_id="agents-arena.navigate-to-room", arguments={"target_room_id": "engineering"})
            if step == 2:
                return SkillCall(skill_id="actor.wait-for-message", arguments={"task_id": self.task_id,
                    "sender_actor_id": self.partner, "channel_id": "direct", "timeout_seconds": 240.0})
        return FinishObjective(status="failure", reason="Fault fixture did not produce task completion")

    async def interrupt(self):
        self.interrupted = True

    async def interrupt_decision(self):
        pass

    async def close(self):
        self.closed = True

    def report(self):
        return {"provider": "fault-setup", "role": self.role, "decisions": self.turn_count,
            "closed": self.closed, "interrupted": self.interrupted}


async def worker(args):
    """Signals control this test process only, never game state or another Brain."""
    token = secrets.token_hex(32)
    server = RuntimeServer(ExtensionRegistry.discover(), token, port=args.port)
    stop_runtime, cancel_objective, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    for number, event in ((signal.SIGTERM, stop_runtime), (signal.SIGUSR1, cancel_objective), (signal.SIGUSR2, release)):
        loop.add_signal_handler(number, event.set)
    client = runner = session = running = monitor = cancellation = stopping = None
    report = {"role": args.role, "worker_pid": os.getpid(), "phase": "starting", "failed": False}
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "status.json"

    def update_report():
        report["client_alive"] = client is not None and client.poll() is None
        report["session_closed"] = session.closed.is_set() if session else None
        if session is not None:
            state = session.world.latest(CalibrationTaskStateObservation, capability_id=CALIBRATION_STATE)
            peers = session.world.latest(CommunicationPeersObservation, capability_id=PEERS)
            report.update(actor_id=session.world.self_actor_id, commands_sent=session.commands_sent,
                task_id=state.task_id if state else "", task_status=status_name(state.status) if state else "",
                task_reason=state.reason_code if state else "", match_id=peers.match_id if peers else "",
                peer_count=len(peers.peers) if peers else 0)
        if runner is not None:
            report["active_skill_id"] = runner.scheduler.report()["active_skill_id"]
            report["objective_status"] = runner.result.status if runner.result else "running"

    async def snapshot():
        while True:
            update_report()
            save(path, report)
            await asyncio.sleep(0.1)

    async def cancel_on_signal():
        await cancel_objective.wait()
        await runner.cancel()

    async def stop_on_signal():
        await stop_runtime.wait()
        report["runtime_stop_requested"] = True
        await server.stop()

    try:
        await server.start()
        with (args.output / "client-stdout.log").open("w") as stream:
            client = subprocess.Popen([str(args.client), "--auto-room", args.room,
                "--player-name", args.role.title(), "--control-mode", "actor",
                "--actor-endpoint", f"http://127.0.0.1:{args.port}",
                "--actor-report", str(args.output / "client.json"),
                "-screen-fullscreen", "0", "-screen-width", "640", "-screen-height", "360",
                "-logFile", str(args.output / "client.log")], cwd=ROOT,
                env=dict(os.environ, ACTOR_BRIDGE_TOKEN=token), stdout=stream, stderr=subprocess.STDOUT)
        report["client_pid"] = client.pid
        monitor = asyncio.create_task(snapshot())
        session = await server.next_session(timeout=120)
        # Core keeps the existing transport and cache. This is ordinary first-
        # frame warmup, not a fixture channel for state/action exchange.
        await session.world.wait_until(setup_ready, timeout=12)
        runner = ObjectiveRunner(session, SkillRegistry.discover(), FaultSetupBrain(args.role),
            objective_timeout_seconds=300, max_decisions=8)
        cancellation = asyncio.create_task(cancel_on_signal())
        stopping = asyncio.create_task(stop_on_signal())
        report["phase"] = "running"
        running = asyncio.create_task(runner.run(Objective(text=
            "Initialize and hold the cooperative task for an isolated fault acceptance test."
            if args.role == "coordinator" else
            "Await my assignment, reach Engineering, and wait for my coordinator's private message.")))
        result = await running
        report.update(phase="terminal", result=session.privacy.public(result), cognitive=runner.report())
        update_report()
        save(path, report)
        # Keep the ordinary client alive so the supervisor can distinguish a
        # bridge loss from participant disconnect and sample stable cleanup.
        await asyncio.wait_for(release.wait(), timeout=45)
    except Exception as error:
        report.update(failed=True, error_type=type(error).__name__)
    finally:
        if runner is not None and running is not None and not running.done():
            await runner.cancel()
        for task in (cancellation, stopping, monitor):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(task for task in (cancellation, stopping, monitor) if task is not None),
            return_exceptions=True)
        await server.stop()
        await old_fixture().stop_client(client)
        report.update(phase="closed", owned_client_cleaned=client is None or client.poll() is not None)
        if runner is not None:
            report["cognitive"] = runner.report()
        save(path, report)


def own_client_signal(worker_process, status, client_path):
    pid = status["client_pid"]
    assert type(pid) is int and pid > 1 and worker_process.poll() is None
    # macOS comm reports the executable path without exposing its arguments.
    identity = subprocess.check_output(["ps", "-p", str(pid), "-o", "ppid=,comm="], text=True).strip()
    parent, command = identity.split(None, 1)
    assert int(parent) == worker_process.pid and Path(command).resolve() == client_path.resolve(), "Client PID identity mismatch"
    os.kill(pid, signal.SIGKILL)
    return {"pid": pid, "parent_pid": int(parent), "owned_child_verified": True,
        "signal": "SIGKILL", "kind": "abrupt_participant_loss"}


async def stop_worker(process):
    if process.poll() is None:
        process.send_signal(signal.SIGUSR2)
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=12)
        except TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=12)
            except TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await asyncio.to_thread(process.wait)
    # A worker that crashed before its finally block may leave its Unity child.
    # The supervisor created this isolated group and retains ownership of it;
    # never stop by a process-name search or by an unvalidated report PID.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"exit_code": process.poll(), "owned_group_drained": True}

    def drained():
        try:
            os.killpg(process.pid, 0)
            return False
        except ProcessLookupError:
            return True

    try:
        await until(drained, timeout=8)
    except TimeoutError:
        os.killpg(process.pid, signal.SIGKILL)
        await until(drained, timeout=3)
    return {"exit_code": process.poll(), "owned_group_drained": True}


async def scenario(args, kind):
    helper = old_fixture()
    output = args.output / kind
    output.mkdir(parents=True, exist_ok=True)
    report = {"scenario": kind, "passed": False, "stage": "starting",
        "transport": "ordinary Unity clients and ACTOR gRPC",
        "supervisor_data": "metadata reports only; no messages or authorization", "workers": {}}
    lobby = None
    processes = {}
    remote_terminated = False
    try:
        version = await helper.api(args, "/api/v1/game/version")
        assert version["gameVersion"] == "0.6.0" and version["protocolVersion"] == "3"
        lobby = await helper.api(args, "/api/v1/lobbies", {"displayName": f"M5Fault-{kind}-{uuid.uuid4().hex[:8]}",
            "maxPlayers": 2, "gameVersion": version["gameVersion"], "protocolVersion": version["protocolVersion"]})
        assert lobby["roomCode"] != args.protected_room
        report["lobby"] = lobby
        async with asyncio.timeout(50):
            while True:
                current = await helper.api(args, "/api/v1/lobbies/" + lobby["roomCode"])
                if current["status"] in ("Ready", "Running"):
                    break
                await asyncio.sleep(.5)
        report["server_identity"] = await asyncio.to_thread(helper.remote, args, lobby, "inspect")
        report["stage"] = "starting_workers"

        def state(role):
            return read(output / role / "status.json")

        for offset, role in enumerate(("technician", "coordinator")):
            role_output = output / role
            role_output.mkdir(parents=True, exist_ok=True)
            with (role_output / "worker.log").open("w") as stream:
                processes[role] = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker",
                    "--role", role, "--room", lobby["roomCode"], "--port", str(args.port + offset),
                    "--client", str(args.client), "--output", str(role_output)], cwd=ROOT,
                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            report["workers"][role] = {"pid": processes[role].pid}
            if role == "technician":
                def technician_ready():
                    assert processes[role].poll() is None and not state(role).get("failed"), "Technician setup failed"
                    return state(role).get("active_skill_id") == "agents-arena.wait-for-calibration-assignment"
                await until(technician_ready, timeout=135)

        def both_waiting():
            assert all(process.poll() is None for process in processes.values()), "Worker exited during setup"
            assert not any(state(role).get("failed") for role in processes), "Worker setup failed"
            return (state("coordinator").get("active_skill_id") == "actor.wait"
                and state("technician").get("active_skill_id") == "actor.wait-for-message")

        await until(both_waiting, timeout=150)
        before = {role: state(role) for role in processes}
        assert before["coordinator"]["task_id"] == before["technician"]["task_id"] != ""
        assert before["coordinator"]["match_id"] == before["technician"]["match_id"] != ""
        assert all(value["task_status"] in ACTIVE_TASK_STATUSES for value in before.values())
        report["before_fault"] = before
        report["stage"] = "fault_injected"
        started = time.monotonic()
        if kind == "cancel":
            processes["technician"].send_signal(signal.SIGUSR1)
            report["fault"] = {"target": "technician ObjectiveRunner.cancel", "signal": "SIGUSR1"}
        elif kind == "bridge":
            processes["coordinator"].terminate()
            report["fault"] = {"target": "coordinator RuntimeServer.stop", "signal": "SIGTERM"}
        elif kind == "participant":
            report["fault"] = own_client_signal(processes["coordinator"], before["coordinator"], args.client)
        else:
            report["fault"] = await asyncio.to_thread(helper.remote, args, lobby, "terminate")
            remote_terminated = True
        # Generic waits do not poll game-health observations. Abrupt remote
        # endpoint loss may need NGO's disconnect timeout even while the local
        # bridge heartbeat is healthy. This is separate from intent expiry.
        terminal_timeout = terminal_budget(kind)
        report["terminal_deadline_seconds"] = terminal_timeout
        if kind in ("participant", "server"):
            report["existing_ngo_disconnect_timeout_seconds"] = 30
        await until(lambda: all(state(role).get("phase") == "terminal" for role in processes),
            timeout=terminal_timeout)
        report["seconds_to_both_objectives_terminal"] = time.monotonic() - started
        # Changed events can preempt before the next 15-Hz observation frame.
        # Allow that separate authoritative state/roster frame to arrive rather
        # than reading an earlier cache value as the final server state.
        await until(lambda: settled_terminal(kind, {role: state(role) for role in processes}), timeout=3)
        report["seconds_to_authoritative_terminal_sample"] = time.monotonic() - started
        after = {role: state(role) for role in processes}
        report["after_fault"] = after
        verify_terminal(kind, before, after)
        commands = {role: status["commands_sent"] for role, status in after.items()}
        await asyncio.sleep(1)
        assert commands == {role: state(role)["commands_sent"] for role in processes}, "Commands continued after cleanup"
        report["commands_stable_after_one_second"] = True
        for role in processes:
            client = read(output / role / "client.json")
            if role == "coordinator" and kind == "participant":
                continue
            assert stopped_client(client), "Unity reported moving, nonfinite or keyboard-enabled state"
            if role == "coordinator" and kind == "bridge":
                assert client.get("actorId") == before[role]["actor_id"], "Bridge loss also removed the game actor"
        report["unity_stopped_at_post_cleanup_sample"] = True
        report["movement_fault_response_latency_measured"] = False
        report["stage"] = "acceptance_checked"
        report["passed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        report["last_worker_status_before_cleanup"] = {
            role: read(output / role / "status.json") for role in processes}
        worker_cleanup = {}
        for role, process in processes.items():
            try:
                worker_cleanup[role] = await stop_worker(process)
            except Exception as error:
                worker_cleanup[role] = {"exit_code": process.poll(), "owned_group_drained": False,
                    "error_type": type(error).__name__}
                report["passed"] = False
        report["owned_workers_cleaned"] = all(process.poll() is not None for process in processes.values())
        if lobby is not None and not remote_terminated:
            try:
                report["server_cleanup"] = await asyncio.to_thread(helper.remote, args, lobby, "terminate")
            except Exception as error:
                report["server_cleanup_error_type"] = type(error).__name__
                report["passed"] = False
        report["worker_cleanup"] = worker_cleanup
        save(output / "acceptance.json", report)
    assert report["passed"], "Fault acceptance or owned cleanup failed; inspect acceptance.json"
    print(json.dumps({"scenario": kind, "passed": report["passed"], "report": str(output / "acceptance.json")}), flush=True)


async def main(args):
    args.output = args.output.resolve()
    args.client = args.client.resolve()
    if args.worker:
        await worker(args)
        return
    assert args.protected_room and args.client.is_file()
    assert not args.output.exists() or not any(args.output.iterdir()), "Choose a fresh output directory"
    args.output.mkdir(parents=True, exist_ok=True)
    helper = old_fixture()
    before = await helper.api(args, "/api/v1/lobbies/" + args.protected_room)
    try:
        for case in args.cases:
            await scenario(args, case)
    finally:
        after = await helper.api(args, "/api/v1/lobbies/" + args.protected_room)
        assert before["lobbyId"] == after["lobbyId"] and after["status"] in ("Ready", "Running")
        save(args.output / "protected-room-unchanged.json", {"before": before, "after": after})
        save(args.output / "coverage.json", {"requested_cases": args.cases,
            "passed_cases": [case for case in args.cases if read(args.output / case / "acceptance.json").get("passed")],
            "new_match_stale_request_rejection": "separate deterministic CooperativeAuthorityTests; no live claim"})


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://5.181.134.236")
    parser.add_argument("--host", default="5.181.134.236")
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument("--ssh-key", type=Path, default=Path("~/.ssh/agents-arena"))
    parser.add_argument("--protected-room")
    parser.add_argument("--port", type=int, default=50071)
    parser.add_argument("--output", type=Path, default=Path("build/cooperative-validation/faults"))
    parser.add_argument("--client", type=Path,
        default=ROOT / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena")
    parser.add_argument("--cases", choices=CASES, nargs="+", default=list(CASES))
    parser.add_argument("--self-test", action="store_true", help="Run offline acceptance/identity checks only; no processes or network")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--role", choices=("coordinator", "technician"), help=argparse.SUPPRESS)
    parser.add_argument("--room", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65534 or args.worker and (not args.room or not args.role):
        parser.error("A valid endpoint port and worker identity are required")
    if not args.worker and not args.self_test and (not args.protected_room or len(args.protected_room) != 6
                            or not args.protected_room.isascii() or not args.protected_room.isalnum()):
        parser.error("--protected-room must identify the six-character existing lobby to leave unchanged")
    return args


def offline_tests():
    """Guard the fixture's claims without creating a Runtime or Unity process."""
    from copy import deepcopy
    from types import SimpleNamespace
    import unittest
    from unittest.mock import patch

    def evidence(kind):
        before = {role: {"task_id": "own-task", "match_id": "own-match"}
            for role in ("coordinator", "technician")}
        after = {role: {"task_id": "own-task", "match_id": "own-match", "failed": False,
            "active_skill_id": "", "client_alive": True, "session_closed": False,
            "task_status": "cancelled", "task_reason": {
                "cancel": "participant_cancelled", "bridge": "participant_control_unavailable",
                "participant": "participant_disconnected", "server": "participants_assigned"}[kind],
            "peer_count": 1 if kind == "participant" else 2,
            "result": {"status": "failed", "reason": "brain_reported_failure",
                "skill_results": [{"reason": "preempted_by_event"}]},
            "cognitive": {"replan_count": 1}}
            for role in before}
        if kind == "cancel":
            after["technician"]["result"].update(status="cancelled", reason="objective_cancelled")
        elif kind in ("bridge", "participant"):
            after["coordinator"]["session_closed"] = True
            if kind == "participant":
                after["coordinator"]["client_alive"] = False
        else:
            for item in after.values():
                item["session_closed"] = True
                item["result"]["reason"] = "session_disconnected"
        return before, after

    class AcceptanceTests(unittest.TestCase):
        def test_initial_warmup_requires_own_exact_skill_observations(self):
            world = SimpleNamespace(self_actor_id="own-actor", observations={
                ("different-actor", capability): object() for capability in SETUP_OBSERVATIONS})
            self.assertFalse(setup_ready(world))
            world.observations.update({("own-actor", capability): object() for capability in SETUP_OBSERVATIONS[:-1]})
            self.assertFalse(setup_ready(world))
            world.observations[("own-actor", SETUP_OBSERVATIONS[-1])] = object()
            self.assertTrue(setup_ready(world))

        def test_deadlines_include_abrupt_peer_timeout(self):
            self.assertEqual([terminal_budget(case) for case in CASES], [15, 15, 45, 45])
            with self.assertRaises(ValueError):
                terminal_budget("unknown")

        def test_zero_velocity_is_a_sample_not_invented_latency(self):
            self.assertTrue(stopped_client({"velocity": {"x": 0, "y": 0}, "keyboardEnabled": False}))
            for value in ({}, {"velocity": {"x": 0, "y": 0}},
                {"velocity": {"x": 0.1, "y": 0}, "keyboardEnabled": False},
                {"velocity": {"x": float("nan"), "y": 0}, "keyboardEnabled": False},
                {"velocity": {"x": 0, "y": 0}, "keyboardEnabled": True}):
                self.assertFalse(stopped_client(value))

        def test_all_four_cases_require_distinct_valid_evidence(self):
            for kind in CASES:
                before, after = evidence(kind)
                verify_terminal(kind, before, after)
                self.assertTrue(settled_terminal(kind, after))

        def test_participant_loss_cannot_pass_using_bridge_cancellation(self):
            before, after = evidence("participant")
            after["technician"]["task_reason"] = "participant_control_unavailable"
            self.assertFalse(settled_terminal("participant", after))
            with self.assertRaises(AssertionError):
                verify_terminal("participant", before, after)

        def test_unrelated_task_or_live_foreground_never_passes(self):
            before, original = evidence("bridge")
            for field, value in (("task_id", "different-task"), ("active_skill_id", "actor.wait-for-message"),
                                 ("client_alive", False)):
                after = deepcopy(original)
                after["technician"][field] = value
                with self.assertRaises(AssertionError):
                    verify_terminal("bridge", before, after)

        def test_own_client_guard_uses_parent_and_exact_executable(self):
            process = SimpleNamespace(pid=12001, poll=lambda: None)
            executable = Path("/private/tmp/fixture/Agents Arena")
            with patch.object(subprocess, "check_output", return_value=f"12001 {executable}\n"), \
                 patch.object(os, "kill") as kill:
                result = own_client_signal(process, {"client_pid": 12002}, executable)
                kill.assert_called_once_with(12002, signal.SIGKILL)
                self.assertEqual(result["kind"], "abrupt_participant_loss")
            for identity in (f"12009 {executable}", f"12001 {executable} Helper"):
                with patch.object(subprocess, "check_output", return_value=identity), patch.object(os, "kill") as kill:
                    with self.assertRaises(AssertionError):
                        own_client_signal(process, {"client_pid": 12002}, executable)
                    kill.assert_not_called()

    class BrainTests(unittest.IsolatedAsyncioTestCase):
        async def test_crashed_worker_does_not_skip_owned_orphan_cleanup(self):
            process = SimpleNamespace(pid=12001, poll=lambda: 1)
            with patch.object(os, "killpg", side_effect=[None, ProcessLookupError]) as killpg:
                result = await stop_worker(process)
            self.assertTrue(result["owned_group_drained"])
            self.assertEqual([call.args for call in killpg.call_args_list],
                [(12001, signal.SIGTERM), (12001, 0)])

        async def test_technician_uses_only_its_own_assignment_and_fails_after_preemption(self):
            brain = FaultSetupBrain("technician")
            await brain.start(None, [])
            context = SimpleNamespace(previous_skill_result=None)
            self.assertEqual((await brain.decide(context)).skill_id, "agents-arena.wait-for-calibration-assignment")
            context.previous_skill_result = {"status": "succeeded", "output": {
                "task_id": "own-observed-task", "coordinator_actor_id": "own-observed-coordinator"}}
            self.assertEqual((await brain.decide(context)).skill_id, "agents-arena.navigate-to-room")
            context.previous_skill_result = {"status": "succeeded", "output": {}}
            call = await brain.decide(context)
            self.assertEqual(call.skill_id, "actor.wait-for-message")
            self.assertEqual(call.arguments["task_id"], "own-observed-task")
            self.assertEqual(call.arguments["sender_actor_id"], "own-observed-coordinator")
            context.previous_skill_result = {"status": "cancelled", "reason": "preempted_by_event", "output": {}}
            self.assertEqual((await brain.decide(context)).status, "failure")

    suite = unittest.TestSuite((unittest.defaultTestLoader.loadTestsFromTestCase(AcceptanceTests),
        unittest.defaultTestLoader.loadTestsFromTestCase(BrainTests)))
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    options = arguments()
    if options.self_test:
        raise SystemExit(offline_tests())
    asyncio.run(main(options))
