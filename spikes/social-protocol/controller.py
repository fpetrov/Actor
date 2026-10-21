#!/usr/bin/env python3
"""Supervise four ordinary clients for opt-in owned-RPC protocol validation."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

from verify_report import project

ROOT = Path(__file__).resolve().parents[2]


def runtime_entrypoint(python):
    """Use the distribution's supported console script; no __main__ is shipped."""
    executable = python.with_name("actor-runtime")
    if not executable.is_file():
        raise RuntimeError("Install the ordinary actor-runtime console entrypoint first")
    return executable


def read(path):
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp"); temporary.write_text(json.dumps(value, indent=2) + "\n"); temporary.replace(path)


def summarize(output):
    proof = project(read(output / "agent04/protocol.json"))
    scopes, complete = [], []
    for index in range(1, 5):
        match = read(output / f"agent{index:02}/runtime.json").get("match")
        result = match.get("result") if isinstance(match, dict) else None
        valid = isinstance(result, dict)
        scope = result.get("scope_id") if valid else None
        scopes.append(scope if isinstance(scope, str) and 0 < len(scope) <= 128 else None)
        complete.append(valid and match.get("passed") is True and result.get("status") == "completed" and result.get("authoritative_terminal") is True)
    cleanup = read(output / "cleanup.json")
    remaining = cleanup.get("remaining")
    stopped = type(cleanup.get("started")) is int and cleanup["started"] == 8 and type(remaining) is int and remaining == 0
    launch = read(output / "launch.json").get("agents")
    isolated = isinstance(launch, list) and len(launch) == 4 and all(isinstance(row, dict) for row in launch)
    if isolated:
        for key in ("bridge_port", "credential_sha256", "profile", "database"):
            values = [row.get(key) for row in launch]
            expected = int if key == "bridge_port" else str
            isolated &= all(type(value) is expected for value in values) and len(set(value for value in values if type(value) is expected)) == 4
    same_scope = scopes[0] is not None and all(scope == scopes[0] for scope in scopes)
    result = {"schema": "actor.social-protocol-supervision.v1", "passed": proof["passed"] and all(complete) and stopped and isolated and same_scope,
        "protocol": proof, "ordinary_participations_completed": sum(complete), "same_authoritative_terminal_scope": same_scope,
        "independent_runtime_credentials_profiles_ports": bool(isolated), "owned_processes_remaining": remaining if type(remaining) is int else None,
        "scope": "Optional local fixture plugin and ordinary owned RPC. No forced outcome, hidden roles, poses, IDs or payloads in this projection."}
    write(output / "acceptance.json", result); return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room")
    parser.add_argument("--api-url")
    parser.add_argument("--client", type=Path, default=ROOT / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--base-port", type=int, default=18600)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args(); output = args.output.resolve()
    if args.summarize_only:
        result = summarize(output); print(json.dumps({"passed": result["passed"]})); return 0 if result["passed"] else 1
    if not args.room or len(args.room) != 6 or not args.room.isascii() or not args.room.isalnum(): parser.error("--room needs six ASCII letters/digits")
    if not math.isfinite(args.timeout) or not 60 <= args.timeout <= 600: parser.error("--timeout must be 60–600 seconds")
    if not 1024 <= args.base_port <= 65532: parser.error("Four valid independent bridge ports are required")
    if args.api_url:
        try:
            url = urlsplit(args.api_url)
            valid = url.scheme in ("http", "https") and url.hostname and url.username is None and url.password is None and not url.query and not url.fragment
        except ValueError: valid = False
        if not valid: parser.error("Invalid public game API URL")
    client = args.client.resolve(); python = ROOT / "build/runtime-venv/bin/python"
    if not client.is_file() or not python.is_file(): parser.error("Build ordinary client and install Runtime first")
    runtime_executable = runtime_entrypoint(python)
    if output.exists() and any(output.iterdir()): parser.error("A fresh empty output is required")
    from importlib import metadata
    if not metadata.entry_points(group="actor.brains", name="social-protocol-fixture"):
        parser.error("Install the optional local fixture plugin with pip -e spikes/social-protocol --no-deps --no-build-isolation")
    for port in range(args.base_port, args.base_port + 4):
        with socket.socket() as probe:
            try: probe.bind(("127.0.0.1", port))
            except OSError: parser.error("A requested bridge port is occupied")
    from actor_runtime.episodes import TRUSTED_MATCH_OBJECTIVE
    output.mkdir(parents=True, exist_ok=True)
    processes, runtimes, games, logs, rows = [], [], [], [], []
    interrupted = False; forced = 0
    def interrupt(_signal, _frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    try:
        # The opt-in owner joins fourth, beside its public Main Hall task.
        for index in range(1, 5):
            folder = output / f"agent{index:02}"; folder.mkdir()
            credential = secrets.token_hex(32); profile = "social-protocol-" + secrets.token_hex(12)
            environment = dict(os.environ, ACTOR_BRIDGE_TOKEN=credential)
            provider = "social-protocol-fixture" if index == 4 else "social-fixture-vote"
            policy = "social-protocol-fixture" if index == 4 else "agents-arena"
            port = args.base_port + index - 1
            runtime_log = (folder / "runtime.log").open("w"); logs.append(runtime_log)
            command = [str(runtime_executable), "match", "--brain", provider, "--match-policy", policy,
                "--objective", TRUSTED_MATCH_OBJECTIVE, "--port", str(port), "--report", str(folder / "runtime.json"),
                "--memory", "sqlite", "--memory-database", str(folder / "memory.sqlite3"), "--agent-profile", profile,
                "--max-decisions", "32", "--measure-allocations", "--exit-on-complete"]
            if args.config: command += ["--config", str(args.config.resolve())]
            runtime = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=runtime_log, stderr=subprocess.STDOUT)
            processes.append(runtime); runtimes.append(runtime)
            startup = time.monotonic() + 15
            while True:
                if runtime.poll() is not None: raise RuntimeError("An ordinary Runtime failed to start")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2): break
                except OSError:
                    if time.monotonic() >= startup: raise TimeoutError("Runtime startup deadline elapsed")
                    time.sleep(.1)
            client_log = (folder / "client-stdout.log").open("w"); logs.append(client_log)
            command = [str(client), "--auto-room", args.room.upper(), "--player-name", f"Protocol{index:02}", "--control-mode", "actor",
                "--actor-endpoint", f"http://127.0.0.1:{port}", "--actor-report", str(folder / "client.json"),
                "-batchmode", "-nographics", "-screen-fullscreen", "0", "-screen-width", "800", "-screen-height", "600", "-logFile", str(folder / "client.log")]
            if args.api_url: command += ["--api-url", args.api_url]
            if index == 4: command += ["--social-protocol-fault-report", str(folder / "protocol.json")]
            game = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=client_log, stderr=subprocess.STDOUT)
            games.append(game); processes.append(game)
            rows.append({"participant": index, "runtime_pid": runtime.pid, "client_pid": game.pid, "brain": provider,
                "bridge_port": port, "credential_sha256": hashlib.sha256(credential.encode()).hexdigest(),
                "profile": profile, "database": str(folder / "memory.sqlite3")})
            write(output / "launch.json", {"agents": rows})
            ready = time.monotonic() + 45
            while True:
                if runtime.poll() is not None or game.poll() is not None: raise RuntimeError("A participant exited before its readiness barrier")
                state = read(folder / "client.json")
                if state.get("state") == "Connected" and isinstance(state.get("actorId"), str) and state["actorId"]:
                    break
                if time.monotonic() >= ready: raise TimeoutError("Ordinary client readiness deadline elapsed")
                time.sleep(.1)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            if all(runtime.poll() is not None for runtime in runtimes): break
            if any(game.poll() is not None and runtime.poll() is None for game, runtime in zip(games, runtimes)):
                raise RuntimeError("An ordinary client exited before authoritative termination")
            time.sleep(.2)
        if not all(runtime.poll() == 0 for runtime in runtimes): raise RuntimeError("Participation did not complete within its bounded deadline")
    except KeyboardInterrupt:
        interrupted = True
    except (RuntimeError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
    finally:
        for process in runtimes + games:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=16)
                except subprocess.TimeoutExpired:
                    forced += 1; process.kill(); process.wait(timeout=5)
        for log in logs: log.close()
        write(output / "cleanup.json", {"started": len(processes), "remaining": sum(process.poll() is None for process in processes), "forced_kills": forced})
    result = summarize(output)
    if interrupted: result["passed"] = False; write(output / "acceptance.json", result)
    print(json.dumps({"passed": result["passed"], "protocol_checks_passed": sum(check["passed"] for check in result["protocol"]["checks"]),
                      "ordinary_participations_completed": result["ordinary_participations_completed"], "owned_processes_remaining": result["owned_processes_remaining"]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
