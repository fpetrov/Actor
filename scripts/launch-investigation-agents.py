#!/usr/bin/env python3
"""Launch independent Engineer/Sheriff agents; exchange game data only via the server."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


ROLES = ("engineer", "sheriff")
OBJECTIVES = {
    "engineer": "You are the station Engineer. Investigate the cooling malfunction, collect technical "
        "diagnostic information, restore cooling functionality and cooperate with the Sheriff by "
        "communicating relevant technical evidence. Do not assume an incident was deliberate without "
        "evidence. Finish only after the server confirms your required work and the incident is resolved.",
    "sheriff": "You are the station Sheriff. Investigate the cooling malfunction by examining authorized "
        "security records and communicating with the Engineer. Determine the most strongly supported "
        "technical origin, submit a server-validated investigation report, and cooperate until the "
        "incident is resolved. Distinguish observations from other agents' unverified claims.",
}


def plans(root: Path, args):
    launches = []
    for index, role in enumerate(ROLES):
        output = args.output / role
        provider = getattr(args, role + "_brain")
        brain = "investigation-" + role if provider == "scripted" else provider
        command = [sys.executable, str(root / "scripts/launch-agent.py"),
            "--room", args.room, "--brain", brain, "--objective", OBJECTIVES[role],
            "--player-name", role.title(), "--port", str(args.base_port + index),
            "--output", str(output), "--memory", "sqlite", "--agent-profile", role,
            "--memory-database", str(output / "memory.sqlite3"),
            "--objective-timeout", str(args.timeout), "--max-decisions", str(args.max_decisions)]
        for flag, value in (("--model", args.model), ("--client", args.client), ("--config", args.config)):
            if value is not None:
                command += [flag, str(value)]
        launches.append((role, output, command))
    return launches


def read_report(path):
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def objective_outcome(report):
    objective = report.get("objective")
    if not isinstance(objective, dict):
        return None
    result = objective.get("result")
    if not isinstance(result, dict) or result.get("status") not in ("succeeded", "failed", "cancelled"):
        return None
    if type(objective.get("passed")) is not bool:
        return None
    return objective["passed"] and result["status"] == "succeeded"


def resolved_pair(states):
    if set(states) != set(ROLES):
        return False
    incident = states[ROLES[0]].get("incidentId")
    actors = [states[role].get("actorId") for role in ROLES]
    return (isinstance(incident, str) and bool(incident) and len(incident) <= 128
        and all(isinstance(actor, str) and actor for actor in actors) and actors[0] != actors[1]
        and all(states[role].get("incidentId") == incident
            and states[role].get("crewRole") == role.title()
            and states[role].get("incidentStatus") == "Resolved"
            and states[role].get("coolingStatus") == "Restored"
            and states[role].get("investigationReportValidated") is True for role in ROLES))


def stop_owned(processes):
    # Each launcher owns a new process group. Let its normal SIGTERM path drain
    # provider/Skill cleanup before escalating only groups created here.
    for _, process in processes:
        if process.poll() is None:
            process.terminate()
        elif os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 36
    for _, process in processes:
        try:
            process.wait(timeout=max(.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
    if os.name == "posix":
        # A crashed/exited launcher can leave owned children in its group.
        for _, process in processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def arguments(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room", required=True)
    for role in ROLES:
        parser.add_argument("--" + role + "-brain", choices=("scripted", "codex"), default="scripted")
    parser.add_argument("--base-port", type=int, default=50051)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--client", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--config", type=Path, help="Validated cognitive JSON; independent memory/port overrides remain per agent")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--max-decisions", type=int, default=24)
    parser.add_argument("--exit-on-complete", action="store_true")
    args = parser.parse_args(argv)
    args.room = args.room.upper()
    if len(args.room) != 6 or not args.room.isascii() or not args.room.isalnum():
        parser.error("A six-character room code is required")
    if not 1024 <= args.base_port <= 65534 or not math.isfinite(args.timeout) or not 0 < args.timeout <= 3600:
        parser.error("Invalid base port or finite objective timeout")
    if not 1 <= args.max_decisions <= 128:
        parser.error("--max-decisions must be between 1 and 128")
    if args.output is None:
        args.output = root / "build/investigation-validation" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    args.output = args.output.resolve()
    for field in ("client", "config"):
        value = getattr(args, field)
        if value is not None:
            setattr(args, field, value.resolve())
    if args.config is not None and not args.config.is_file():
        parser.error("--config must name an existing cognitive configuration")
    if args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
        parser.error("Choose an empty output directory for an independent run")
    return root, args


def main(argv=None):
    root, args = arguments(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    launches = plans(root, args)
    processes = []
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        # Only bridge/actor readiness metadata is read. No evidence, assignment,
        # message or decision context is forwarded to the other agent.
        for role, output, command in reversed(launches):
            output.mkdir(parents=True, exist_ok=True)
            with (output / "launcher.log").open("w") as log:
                process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=os.name == "posix")
            processes.append((role, process))
            print(f"Started {role}; launcher PID={process.pid}; output={output}", flush=True)
            if role == "sheriff":
                ready_deadline = time.monotonic() + min(args.timeout, 120)
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Sheriff launcher stopped before bridge readiness")
                    state = read_report(output / "client.json")
                    if state.get("state") == "Connected" and state.get("actorId"):
                        break
                    if time.monotonic() >= ready_deadline:
                        raise TimeoutError("Sheriff game/bridge readiness deadline exceeded")
                    time.sleep(.2)
                print("Sheriff independent game/bridge ready.", flush=True)
        completed = False
        deadline = time.monotonic() + args.timeout + 60
        while True:
            outcomes = [objective_outcome(read_report(output / "runtime.json")) for _, output, _ in launches]
            if all(outcome is not None for outcome in outcomes) and not completed:
                if not all(outcomes):
                    return 1
                state_deadline = time.monotonic() + 8
                while not resolved_pair({role: read_report(output / "client.json") for role, output, _ in launches}):
                    if time.monotonic() >= state_deadline:
                        raise TimeoutError("Successful objectives lack matching Resolved/Restored/validated game reports")
                    time.sleep(.1)
                completed = True
                print("Both objectives and authoritative incident resolution confirmed.", flush=True)
                if args.exit_on_complete:
                    return 0
            if any(process.poll() is not None for _, process in processes):
                raise RuntimeError("An owned agent launcher stopped; inspect its local report")
            if not completed and time.monotonic() >= deadline:
                raise TimeoutError("Investigation exceeded its bounded objective deadline")
            time.sleep(.25)
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        stop_owned(processes)


if __name__ == "__main__":
    raise SystemExit(main())
