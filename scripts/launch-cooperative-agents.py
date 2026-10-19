#!/usr/bin/env python3
"""Launch two isolated ACTOR agents in one ordinary game lobby.

This supervisor supplies objectives and owns process cleanup. It never transfers
game information between agents. Cooperation uses the authoritative game server.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
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
        completed = False
        deadline = time.monotonic() + args.timeout + 60
        while True:
            results = []
            for role, output, _ in launches:
                try:
                    report = json.loads((output / "runtime.json").read_text())
                    result = report.get("objective", {})
                    if "passed" in result:
                        results.append(bool(result["passed"]))
                except (OSError, json.JSONDecodeError):
                    pass
            if len(results) == 2 and not completed:
                completed = True
                print(f"Both objectives terminated; successful={all(results)}. Reports: {args.output}", flush=True)
                if args.exit_on_complete or not all(results):
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
