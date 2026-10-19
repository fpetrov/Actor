#!/usr/bin/env python3
"""Launch two isolated ACTOR agents in one ordinary game lobby.

This supervisor supplies objectives and owns process cleanup. It never transfers
game information between agents. Cooperation uses the authoritative game server.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


OBJECTIVES = {
    "coordinator": "You are the diagnostic coordinator for the cooperative station calibration task. "
        "Find an available partner, initialize the task, obtain the authorization information from the "
        "Main Hall diagnostic terminal, communicate the required information privately to the assigned "
        "technician, and cooperate until the server confirms successful calibration.",
    "technician": "You are the technician for the cooperative station calibration task. Wait for your "
        "assignment, coordinate with the diagnostic coordinator, reach Engineering, obtain the required "
        "authorization information through in-game communication, perform calibration at the Engineering "
        "console, and finish only after the server confirms the task is complete.",
}

COMPLETION_SCHEMA = "actor.cooperation-client-completion.v1"
_completion_validator = None


def read_report(path):
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def completed_report(state):
    accepted = state.get("lastAcceptedSessionId") or state.get("sessionId")
    identifiers = (state.get("actorId"), accepted, state.get("calibrationTaskId"))
    if (not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value) for value in identifiers)
            or state.get("calibrationStatus") != "Completed" or state.get("publicCalibrationStatus") != "Completed"
            or state.get("ownCalibrationAssignment") is not True
            or type(state.get("calibrationRevision")) is not int or state["calibrationRevision"] <= 0):
        return None
    return {"schema": COMPLETION_SCHEMA, "actorId": identifiers[0], "acceptedSessionId": accepted,
        "calibrationTaskId": identifiers[2], "calibrationStatus": "Completed",
        "publicCalibrationStatus": "Completed", "calibrationRevision": state["calibrationRevision"],
        "ownCalibrationAssignment": True}


def latch_completed_reports(launches):
    for _, output, _ in launches:
        runtime = read_report(output / "runtime.json")
        objective = runtime.get("objective", {})
        if (not isinstance(objective, dict) or objective.get("passed") is not True
                or not isinstance(objective.get("result"), dict) or objective["result"].get("status") != "succeeded"):
            continue
        snapshot = completed_report(read_report(output / "client.json"))
        if snapshot is not None and not (output / "client-completed.json").exists():
            save_completed_reports([(output, snapshot)])


def completed_reports(launches):
    """Read one pair of authoritative own completions, without copying private data."""
    global _completion_validator
    if _completion_validator is None:
        spec = importlib.util.spec_from_file_location("actor_cooperation_completion_validator",
            Path(__file__).with_name("summarize-cooperation.py"))
        _completion_validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_completion_validator)
    completed = []
    for _, output, _ in launches:
        state, runtime = read_report(output / "client.json"), read_report(output / "runtime.json")
        sessions = runtime.get("runtime", {}).get("sessions", [])
        sessions = [item for item in sessions if isinstance(item, dict)] if isinstance(sessions, list) else []
        selected, _ = _completion_validator.completed_calibration(output, state, sessions)
        snapshot = completed_report(selected)
        if snapshot is None:
            return None
        completed.append((output, snapshot))
    if (len(completed) != 2 or completed[0][1]["calibrationTaskId"] != completed[1][1]["calibrationTaskId"]
            or completed[0][1]["actorId"] == completed[1][1]["actorId"]
            or completed[0][1]["acceptedSessionId"] == completed[1][1]["acceptedSessionId"]):
        return None
    return completed


def save_completed_reports(completed):
    # Persist the reports actually inspected at the barrier, before shutdown can
    # clear a player's private assignment. This file has only public proof fields.
    for output, snapshot in completed:
        target = output / "client-completed.json"
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(snapshot, indent=2, allow_nan=False) + "\n")
        temporary.replace(target)


def plans(root: Path, args):
    """Independent launch commands; no shared credentials, DB, port or report file."""
    result = []
    for index, role in enumerate(("coordinator", "technician")):
        output = args.output / role
        provider = getattr(args, f"{role}_brain")
        brain = f"calibration-{role}" if provider == "scripted" else provider
        command = [sys.executable, str(root / "scripts/launch-agent.py"),
            "--room", args.room, "--brain", brain, "--objective", OBJECTIVES[role],
            "--player-name", role.title(), "--port", str(args.base_port + index),
            "--output", str(output), "--memory", "sqlite", "--agent-profile", role,
            "--memory-database", str(output / "memory.sqlite3"),
            "--objective-timeout", str(args.timeout), "--max-decisions", "24"]
        if args.model:
            command += ["--model", args.model]
        if args.client:
            command += ["--client", str(args.client)]
        if getattr(args, "headless_client", False):
            command += ["--headless-client"]
        result.append((role, output, command))
    return result


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room", required=True)
    parser.add_argument("--coordinator-brain", choices=("scripted", "codex"), default="scripted")
    parser.add_argument("--technician-brain", choices=("scripted", "codex"), default="scripted")
    parser.add_argument("--base-port", type=int, default=50051)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--client", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--headless-client", action="store_true",
                        help="Run both ordinary Unity clients without graphics windows")
    parser.add_argument("--exit-on-complete", action="store_true",
                        help="Close both clients after both independent objectives terminate")
    args = parser.parse_args()
    args.room = args.room.upper()
    if len(args.room) != 6 or not args.room.isascii() or not args.room.isalnum():
        parser.error("A six-character room code is required")
    if not 1024 <= args.base_port <= 65534 or not 0 < args.timeout <= 3600:
        parser.error("Invalid base port or objective timeout")
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        args.output = root / "build/cooperative-validation" / stamp
    args.output = args.output.resolve()
    if args.client:
        args.client = args.client.resolve()
    # Fresh output is also a fresh pair of separate persistent memories.
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Choose an empty output directory for this independent run")
    args.output.mkdir(parents=True, exist_ok=True)
    launches = plans(root, args)
    processes = []

    def stop(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        for role, output, command in reversed(launches):
            output.mkdir(parents=True, exist_ok=True)
            with (output / "launcher.log").open("w") as log:
                process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=os.name == "posix")
            processes.append((role, process))
            print(f"Started {role}: launcher PID={process.pid}, output={output}", flush=True)
            if role == "technician":
                # Startup ordering uses readiness metadata only, never another
                # agent's assignment, messages, code or decision context.
                ready_deadline = time.monotonic() + min(args.timeout, 120)
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Technician launcher stopped before its bridge became ready")
                    try:
                        client_state = json.loads((output / "client.json").read_text())
                        if client_state.get("state") == "Connected" and client_state.get("actorId"):
                            break
                    except (OSError, json.JSONDecodeError):
                        pass
                    if time.monotonic() >= ready_deadline:
                        raise TimeoutError("Technician's game/bridge did not become ready")
                    time.sleep(.2)
                print("Technician game and independent local bridge ready.", flush=True)
        completed = False
        deadline = time.monotonic() + args.timeout + 60
        while True:
            latch_completed_reports(launches)
            results = []
            for role, output, _ in launches:
                try:
                    report = json.loads((output / "runtime.json").read_text())
                    result = report.get("objective", {})
                    status = result.get("result", {}).get("status")
                    if type(result.get("passed")) is bool and status in ("succeeded", "failed", "cancelled"):
                        results.append(result["passed"] and status == "succeeded")
                except (OSError, json.JSONDecodeError):
                    pass
            if len(results) == 2 and not completed:
                completed = True
                print(f"Both objectives terminated; successful={all(results)}. Reports: {args.output}", flush=True)
                if args.exit_on_complete or not all(results):
                    if all(results):
                        # Runtime completion can precede the next periodic game
                        # report. Wait for each client's own authoritative state
                        # before closing it; this barrier transfers no payload.
                        state_deadline = time.monotonic() + 8
                        while True:
                            captured = completed_reports(launches)
                            if captured is not None:
                                save_completed_reports(captured)
                                break
                            if time.monotonic() >= state_deadline:
                                raise TimeoutError("Successful objectives lack matching Completed game reports")
                            time.sleep(.1)
                    return 0 if all(results) else 1
            if any(process.poll() is not None for _, process in processes):
                raise RuntimeError("An agent launcher stopped; inspect its local report/log")
            if not completed and time.monotonic() >= deadline:
                raise TimeoutError("Cooperation exceeded its bounded objective deadline")
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 0
    except (RuntimeError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
        for _, process in processes:
            try:
                process.wait(timeout=22)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    # The group was created by this supervisor. Escalation
                    # cannot target another launch or the user's shell.
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
