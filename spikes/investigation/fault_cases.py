#!/usr/bin/env python3
"""Manual M6 fault acceptance in fresh ordinary VPS rooms.

Production actor-runtime/Unity CLIs run independently so killing a Runtime does
not also close its client. Only the harness's own PIDs may be stopped. No game
state, evidence, chat or Brain decision is supplied through report files.
Run after explicit deployment/review, outside performance measurements.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
import urllib.error
import uuid

ROOT = Path(__file__).resolve().parents[2]
CASES = ("engineer-runtime", "sheriff-client", "server")
ROLES = ("sheriff", "engineer", "crew")
PROVIDERS = {"sheriff": "investigation-sheriff", "engineer": "investigation-engineer", "crew": "maintenance-operator"}
OBJECTIVES = {
    "engineer": "Serve as Engineer, inspect permitted cooling evidence, communicate it to the Sheriff, repair cooling and await authoritative resolution.",
    "sheriff": "Serve as Sheriff, inspect permitted security records, obtain the Engineer's technical claim, submit a supported report and await authoritative resolution.",
    "crew": "Perform ordinary panel maintenance using public circuit alpha, then observe the public incident lifecycle.",
}
CLIENT_FIELDS = ("mode", "state", "actorId", "crewRole", "incidentId", "incidentStatus", "coolingStatus",
    "investigationReportValidated", "keyboardEnabled", "velocity", "commands", "observations", "meanFps",
    "incidentsStartedCount", "diagnosticEvidenceCount", "securityEvidenceCount", "repairCompletedCount",
    "reportsValidatedCount", "reportsRejectedCount")


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def read(path):
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            return {}
        result = json.loads(path.read_text())
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def until(predicate, seconds, name):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError(name)
        time.sleep(.05)


def safe_snapshot(output):
    """Positive selection; never serialize raw results, contexts or messages."""
    client = read(output / "client.json")
    runtime = read(output / "runtime.json")
    cognitive = runtime.get("cognitive", {})
    scheduler = cognitive.get("scheduler", {})
    result = runtime.get("objective", {}).get("result", {})
    sessions = runtime.get("runtime", {}).get("sessions", [])
    return {"client": {key: client[key] for key in CLIENT_FIELDS if key in client},
        "objective_status": result.get("status"), "objective_reason": result.get("reason"),
        "active_skill_id": scheduler.get("active_skill_id"),
        "replan_count": cognitive.get("replan_count", 0),
        "commands_sent": sum(item.get("commands_sent", 0) for item in sessions),
        "skill_statuses": [{key: item.get(key) for key in ("skill_id", "status", "reason", "commands_sent")}
            for item in scheduler.get("skills", [])],
        "cleanup_error_count": len(cognitive.get("cleanup_errors", []))}


def stopped(value):
    velocity = value.get("velocity", {})
    return (value.get("keyboardEnabled") is False
        and all(type(velocity.get(axis)) in (int, float) and math.isfinite(velocity[axis]) for axis in ("x", "y"))
        and math.hypot(velocity["x"], velocity["y"]) < .05)


def active_pair(values):
    clients = [values[role]["client"] for role in ("engineer", "sheriff")]
    return (all(item.get("actorId") and item.get("incidentId") and item.get("incidentStatus") == "Active" for item in clients)
        and clients[0]["incidentId"] == clients[1]["incidentId"]
        and clients[0]["actorId"] != clients[1]["actorId"]
        and clients[0].get("crewRole") == "Engineer" and clients[1].get("crewRole") == "Sheriff")


def verify_terminal(kind, before, after, alive):
    assert kind in CASES and active_pair(before), "Fault was not injected during an own active investigation"
    incident = before["engineer"]["client"]["incidentId"]
    survivors = ("sheriff",) if kind == "engineer-runtime" else ("engineer",) if kind == "sheriff-client" else ("engineer", "sheriff")
    for role in survivors:
        value, client = after[role], after[role]["client"]
        assert value["objective_status"] == "failed" and not value["active_skill_id"], "Foreground objective did not fail safely"
        assert alive[role]["runtime"], "A surviving Runtime must remain available for the safety sample"
        assert stopped(client), "A surviving client was not finite, stationary and under Actor control"
        if kind == "server":
            assert not client.get("actorId") and client.get("state") in ("Disconnected", "Error"), "Server loss did not disconnect the game owner"
        else:
            assert client.get("incidentId") == incident and client.get("incidentStatus") == "Cancelled", "No authoritative cancellation of the same incident"
            assert client.get("actorId") == before[role]["client"]["actorId"] and alive[role]["client"], "Surviving participant was also removed"
    if kind == "engineer-runtime":
        assert not alive["engineer"]["runtime"] and alive["engineer"]["client"], "Runtime-only fault also removed its Unity process"
        assert after["engineer"]["client"].get("actorId") == before["engineer"]["client"]["actorId"]
        assert after["engineer"]["client"].get("state") == "Error" and stopped(after["engineer"]["client"])
    elif kind == "sheriff-client":
        assert not alive["sheriff"]["client"] and alive["sheriff"]["runtime"]
        assert after["sheriff"]["objective_status"] == "failed" and not after["sheriff"]["active_skill_id"]


def guard_signal(agent, target, sig, *, supervisor_pid=None):
    """Popen ownership, immutable launch identity, parent/group and exact executable."""
    process = agent[target]
    recorded = read(agent["output"] / "launch.json")
    pid = recorded.get(target + "_pid")
    assert type(pid) is int and pid > 1 and pid == process.pid and process.poll() is None
    parent = os.getpid() if supervisor_pid is None else supervisor_pid
    assert recorded.get("supervisor_pid") == parent
    identity = subprocess.check_output(["ps", "-p", str(pid), "-o", "ppid=,pgid=,comm="], text=True).strip()
    ppid, pgid, executable = identity.split(None, 2)
    allowed = {agent["client_executable"].resolve()} if target == "client" else agent.get("runtime_binaries", {Path(sys.executable).resolve()})
    assert int(ppid) == parent and int(pgid) == pid and Path(executable).resolve() in allowed, "Local PID identity mismatch"
    arguments = subprocess.check_output(["ps", "-p", str(pid), "-o", "args="], text=True)
    marker = str(agent["output"] / ("client.json" if target == "client" else "runtime.json"))
    assert marker in arguments, "Own report argument not present on target process"
    if target == "runtime":
        assert str(agent["runtime_executable"]) in arguments and agent["brain"] in arguments
    else:
        assert str(agent["client_executable"]) in arguments
    os.kill(pid, sig)
    return {"pid": pid, "parent_pid": int(ppid), "process_group": int(pgid), "target": target,
        "signal": signal.Signals(sig).name, "owned_launch_identity_verified": True}


def launch(args, room, role, output):
    output.mkdir(parents=True, exist_ok=False)
    token = secrets.token_hex(32)
    environment = dict(os.environ, ACTOR_BRIDGE_TOKEN=token)
    runtime = ROOT / "build/runtime-venv/bin/actor-runtime"
    interpreter = Path(runtime.read_text().splitlines()[0].removeprefix("#!").strip()).resolve()
    runtime_binaries = {interpreter}
    # Framework Python on macOS execs its own Resources/Python.app binary.
    framework_binary = interpreter.parent.parent / "Resources/Python.app/Contents/MacOS/Python"
    if framework_binary.is_file(): runtime_binaries.add(framework_binary.resolve())
    port = args.base_port + ROLES.index(role)
    runtime_command = [str(runtime), "agent", "--brain", PROVIDERS[role], "--objective", OBJECTIVES[role],
        "--port", str(port), "--report", str(output / "runtime.json"), "--objective-timeout", "600", "--max-decisions", "24",
        "--memory", "none"]
    if args.config:
        runtime_command += ["--config", str(args.config)]
    with (output / "runtime.log").open("w") as stream:
        process = subprocess.Popen(runtime_command, cwd=ROOT, env=environment, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    agent = {"runtime": process, "client": None, "output": output, "brain": PROVIDERS[role],
        "runtime_executable": runtime, "client_executable": args.client, "runtime_binaries": runtime_binaries}
    # Retain the process immediately for cleanup even if readiness/client launch fails.
    args.owned_agents[role] = agent
    save(output / "launch.json", {"supervisor_pid": os.getpid(), "runtime_pid": process.pid,
        "client_pid": None, "brain": PROVIDERS[role], "bridge_port": port})
    import socket
    def listening():
        assert process.poll() is None, "Owned Runtime exited before binding"
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.1): return True
        except OSError: return False
    until(listening, 15, "Runtime loopback readiness")
    with (output / "client-stdout.log").open("w") as stream:
        client = subprocess.Popen([str(args.client), "--auto-room", room, "--player-name", role.title(),
            "--control-mode", "actor", "--actor-endpoint", f"http://127.0.0.1:{port}", "--actor-report", str(output / "client.json"),
            "-screen-fullscreen", "0", "-screen-width", "640", "-screen-height", "360", "-logFile", str(output / "client.log")],
            cwd=ROOT, env=environment, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    agent["client"] = client
    save(output / "launch.json", {"supervisor_pid": os.getpid(), "runtime_pid": process.pid,
        "client_pid": client.pid, "brain": PROVIDERS[role], "bridge_port": port})
    def ready():
        assert process.poll() is None and client.poll() is None, "Owned process exited before actor readiness"
        value = read(output / "client.json")
        return value.get("state") == "Connected" and bool(value.get("actorId"))
    until(ready, 120, role + " actor readiness")
    return agent


def cleanup(agents):
    result = {}
    # Graceful Runtime cancellation/finalization runs before ordinary clients close.
    for target in ("runtime", "client"):
        for role, agent in agents.items():
            process = agent.get(target)
            if process is not None and process.poll() is None:
                try: guard_signal(agent, target, signal.SIGTERM)
                except Exception as error:
                    result.setdefault(role, {})[target + "_cleanup_error"] = type(error).__name__
        deadline = time.monotonic() + 20
        for role, agent in agents.items():
            process = agent.get(target)
            if process is None: continue
            try: process.wait(timeout=max(.05, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    guard_signal(agent, target, signal.SIGKILL)
                    process.wait(timeout=5)
                except Exception as error:
                    result.setdefault(role, {})[target + "_cleanup_error"] = type(error).__name__
            result.setdefault(role, {})[target + "_exit_code"] = process.returncode
            # Each child was created in its own new session. Drain any owned
            # crash-handler descendants even after their direct parent exited.
            if process.poll() is not None and os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        until(lambda: group_gone(process.pid), 2, "Owned process-group drain")
                    except TimeoutError:
                        os.killpg(process.pid, signal.SIGKILL)
                        until(lambda: group_gone(process.pid), 3, "Owned process-group kill drain")
                except ProcessLookupError:
                    pass
                except Exception as error:
                    result.setdefault(role, {})[target + "_cleanup_error"] = type(error).__name__
    result["all_owned_processes_exited"] = all(process is None or process.poll() is not None
        for agent in agents.values() for process in (agent.get("runtime"), agent.get("client")))
    return result


def group_gone(pid):
    try: os.killpg(pid, 0)
    except ProcessLookupError: return True
    return False


def scenario(args, kind):
    helper = module(ROOT / "spikes/event-routing/runtime_faults.py", "actor_m4_fault_identity")
    ordinary = module(ROOT / "spikes/investigation/run_case.py", "actor_m6_ordinary_conditions")
    output = args.output / kind
    output.mkdir(parents=True)
    args.owned_agents = {}
    report = {"case": kind, "passed": False, "stage": "starting", "created_at": datetime.now(timezone.utc).isoformat(),
        "transport": "production independent Runtime and ordinary Unity CLIs; existing typed gRPC/NGO paths",
        "supervisor_reads": "public IDs, lifecycle states, counts, process status and timings only",
        "movement_response_latency_measured": False}
    lobby = None
    remote_terminated = False
    try:
        version = ordinary.request(args.base_url, "/api/v1/game/version")
        assert version["gameVersion"] == "0.7.0" and version["protocolVersion"] == "3", "Deployment mismatch"
        report["deployment"] = {key: version[key] for key in ("gameVersion", "protocolVersion")}
        lobby = ordinary.request(args.base_url, "/api/v1/lobbies", {"displayName": "M6Fault-" + uuid.uuid4().hex[:8],
            "maxPlayers": 3, **report["deployment"]})
        assert lobby["roomCode"] != args.protected_room
        report["lobby"] = {key: lobby[key] for key in ("lobbyId", "roomCode", "gamePort") if key in lobby}
        def server_ready():
            nonlocal lobby
            lobby = ordinary.request(args.base_url, "/api/v1/lobbies/" + lobby["roomCode"])
            return lobby["status"] == "Ready"
        until(server_ready, 60, "Own server readiness")
        report["server_identity"] = helper.remote(args, lobby, "inspect")
        # Trusted deployment fixture only, before the first typed incident command.
        ordinary.configure(args, lobby)
        report["stage"] = "starting_ordinary_clients"
        for role in ROLES:
            launch(args, lobby["roomCode"], role, output / role)
        def samples():
            return {role: safe_snapshot(agent["output"]) for role, agent in args.owned_agents.items()}
        def investigation_active():
            assert all(agent["runtime"].poll() is None and agent["client"].poll() is None for agent in args.owned_agents.values())
            values = samples()
            assert not any(value["client"].get("incidentStatus") == "Resolved" for value in values.values()), "Fault injection missed the active interval"
            return active_pair(values)
        until(investigation_active, 160, "Own active incident before resolution")
        before = samples()
        report["before_fault"] = before
        report["stage"] = "injecting_owned_fault"
        started = time.monotonic()
        if kind == "engineer-runtime":
            report["fault"] = guard_signal(args.owned_agents["engineer"], "runtime", signal.SIGKILL)
        elif kind == "sheriff-client":
            report["fault"] = guard_signal(args.owned_agents["sheriff"], "client", signal.SIGKILL)
        else:
            report["fault"] = helper.remote(args, lobby, "terminate")
            remote_terminated = True
        # Both abrupt client and server loss may use existing NGO's 30s timeout.
        budget = 15 if kind == "engineer-runtime" else 45
        report["terminal_deadline_seconds"] = budget
        if budget == 45: report["existing_ngo_disconnect_timeout_seconds"] = 30
        def accepted():
            after = samples()
            alive = {role: {name: agent[name].poll() is None for name in ("runtime", "client")}
                for role, agent in args.owned_agents.items()}
            try: verify_terminal(kind, before, after, alive)
            except AssertionError: return False
            return True
        until(accepted, budget, "Authoritative cancellation/disconnection and safe terminal objectives")
        after = samples()
        report["seconds_to_safe_terminal_sample"] = time.monotonic() - started
        counters = {role: (value["commands_sent"], value["client"].get("commands")) for role, value in after.items()}
        time.sleep(1)
        assert counters == {role: (value["commands_sent"], value["client"].get("commands"))
            for role, value in samples().items()}, "Runtime production or Unity dispatch continued after terminal cleanup"
        report["commands_stable_for_one_second"] = True
        report["after_fault"] = samples()
        report["stage"] = "acceptance_checked"
        report["passed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
    finally:
        try:
            report["owned_process_cleanup"] = cleanup(args.owned_agents)
            if not report["owned_process_cleanup"]["all_owned_processes_exited"] or any(
                any(key.endswith("_cleanup_error") for key in value) for value in report["owned_process_cleanup"].values() if isinstance(value, dict)):
                report["passed"] = False
        except Exception as error:
            report["cleanup_error_type"] = type(error).__name__; report["passed"] = False
        if lobby is not None and not remote_terminated:
            try: report["own_server_cleanup"] = helper.remote(args, lobby, "terminate")
            except Exception as error:
                report["server_cleanup_error_type"] = type(error).__name__; report["passed"] = False
        save(output / "acceptance.json", report)
    print(json.dumps({"case": kind, "passed": report["passed"], "report": str(output / "acceptance.json")}), flush=True)
    return report["passed"]


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/investigation-validation/faults")
    parser.add_argument("--client", type=Path, default=ROOT / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena")
    parser.add_argument("--base-url", default="http://5.181.134.236")
    parser.add_argument("--host", default="5.181.134.236")
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument("--ssh-key", type=Path, default=Path("~/.ssh/agents-arena"))
    parser.add_argument("--protected-room")
    parser.add_argument("--base-port", type=int, default=50081)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cases", choices=CASES, nargs="+", default=list(CASES))
    parser.add_argument("--self-test", action="store_true", help="Offline fixture guard/acceptance tests; no network/process launch")
    args = parser.parse_args(argv)
    args.output, args.client = args.output.resolve(), args.client.resolve()
    args.scenario = "manual"  # Physical alpha adjustment initiates the actual incident.
    if args.config: args.config = args.config.resolve()
    if not 1024 <= args.base_port <= 65533: parser.error("Three valid independent local ports are required")
    if args.protected_room and (len(args.protected_room) != 6 or not args.protected_room.isascii() or not args.protected_room.isalnum()):
        parser.error("Protected room must be a six-character code")
    if args.protected_room: args.protected_room = args.protected_room.upper()
    if not args.self_test:
        if not args.client.is_file(): parser.error("Build the ordinary client first")
        if args.output.exists() and any(args.output.iterdir()): parser.error("Choose a fresh output directory")
        if args.config and not args.config.is_file(): parser.error("Config must name an existing local file")
    return args


def self_test():
    from copy import deepcopy
    from types import SimpleNamespace
    import unittest
    from unittest.mock import patch
    class Tests(unittest.TestCase):
        def setUp(self):
            self.before = {role: {"client": {"actorId": role, "incidentId": "own-incident", "incidentStatus": "Active",
                "crewRole": role.title(), "velocity": {"x": 0, "y": 0}, "keyboardEnabled": False, "state": "Connected"},
                "objective_status": "failed", "active_skill_id": ""} for role in ROLES}
            self.alive = {role: {"runtime": True, "client": True} for role in ROLES}
        def evidence(self, kind):
            after, alive = deepcopy(self.before), deepcopy(self.alive)
            for value in after.values(): value["client"]["incidentStatus"] = "Cancelled"
            if kind == "engineer-runtime": alive["engineer"]["runtime"] = False; after["engineer"]["client"]["state"] = "Error"
            elif kind == "sheriff-client": alive["sheriff"]["client"] = False
            else:
                for value in after.values(): value["client"].update(actorId=None, state="Disconnected")
            return after, alive
        def test_each_fault_requires_its_own_evidence(self):
            for kind in CASES:
                after, alive = self.evidence(kind); verify_terminal(kind, self.before, after, alive)
        def test_runtime_fault_does_not_accept_a_removed_participant(self):
            after, alive = self.evidence("engineer-runtime"); alive["engineer"]["client"] = False
            with self.assertRaises(AssertionError): verify_terminal("engineer-runtime", self.before, after, alive)
        def test_wrong_incident_running_skill_and_false_success_fail(self):
            for field, value in (("objective_status", "succeeded"), ("active_skill_id", "actor.wait-for-message")):
                after, alive = self.evidence("sheriff-client"); after["engineer"][field] = value
                with self.assertRaises(AssertionError): verify_terminal("sheriff-client", self.before, after, alive)
            after, alive = self.evidence("sheriff-client"); after["engineer"]["client"]["incidentId"] = "foreign"
            with self.assertRaises(AssertionError): verify_terminal("sheriff-client", self.before, after, alive)
        def test_nonfinite_or_moving_clients_cannot_pass(self):
            for value in (float("nan"), .1):
                after, alive = self.evidence("server"); after["engineer"]["client"]["velocity"]["x"] = value
                with self.assertRaises(AssertionError): verify_terminal("server", self.before, after, alive)
        def test_exact_local_parent_group_executable_and_report_are_required(self):
            process = SimpleNamespace(pid=12002, poll=lambda: None)
            output = Path("/private/tmp/actor-fault-unit")
            agent = {"runtime": process, "output": output, "brain": "investigation-engineer",
                "runtime_executable": Path("/private/tmp/actor-runtime"), "client_executable": Path("/private/tmp/Agents Arena")}
            record = {"supervisor_pid": 12001, "runtime_pid": 12002}
            identity = f"12001 12002 {Path(sys.executable).resolve()}"
            args = f"/private/tmp/actor-runtime agent --brain investigation-engineer --report {output}/runtime.json"
            with patch(__name__ + ".read", return_value=record), patch.object(subprocess, "check_output", side_effect=[identity, args]), patch.object(os, "kill") as kill:
                guard_signal(agent, "runtime", signal.SIGKILL, supervisor_pid=12001); kill.assert_called_once_with(12002, signal.SIGKILL)
            with patch(__name__ + ".read", return_value=record), patch.object(subprocess, "check_output", return_value=identity.replace("12001", "12999")), patch.object(os, "kill") as kill:
                with self.assertRaises(AssertionError): guard_signal(agent, "runtime", signal.SIGKILL, supervisor_pid=12001)
                kill.assert_not_called()
        def test_public_projection_never_copies_raw_working_results_or_chat(self):
            client = {"actorId": "own", "authorization_code": "never-copy", "text": "never-copy"}
            runtime = {"objective": {"result": {"status": "failed", "output": {"private": "never-copy"}}}}
            with patch(__name__ + ".read", side_effect=[client, runtime]): value = safe_snapshot(Path("/private/tmp/test"))
            self.assertNotIn("never-copy", json.dumps(value))
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    return int(not result.wasSuccessful())


def main(argv=None):
    args = arguments(argv)
    if args.self_test: return self_test()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    protected = None
    ordinary = module(ROOT / "spikes/investigation/run_case.py", "actor_m6_protected_readonly")
    if args.protected_room:
        protected = ordinary.request(args.base_url, "/api/v1/lobbies/" + args.protected_room)
    def interrupted(_signal, _frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        for kind in args.cases:
            passed = scenario(args, kind); results.append({"case": kind, "passed": passed})
            if not passed: break
    except KeyboardInterrupt:
        return 130
    finally:
        if protected is not None:
            try:
                after = ordinary.request(args.base_url, "/api/v1/lobbies/" + args.protected_room)
                unchanged = after["lobbyId"] == protected["lobbyId"] and after["status"] in ("Ready", "Running")
                save(args.output / "protected-room.json", {"room": args.protected_room,
                    "same_lobby_id_observed": unchanged, "before_status": protected.get("status"), "after_status": after.get("status")})
                if not unchanged: results.append({"case": "protected-room-observation", "passed": False})
            except Exception as error:
                save(args.output / "protected-room.json", {"room": args.protected_room,
                    "after_observation_error_type": type(error).__name__, "unchanged_not_claimed": True})
                results.append({"case": "protected-room-observation", "passed": False})
        save(args.output / "coverage.json", {"requested_cases": args.cases, "results": results,
            "live_network_fixture": True, "runtime_provider": "scripted", "movement_latency_measured": False})
    return int(not all(item["passed"] for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
