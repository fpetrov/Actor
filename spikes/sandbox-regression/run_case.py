#!/usr/bin/env python3
"""Opt-in ordinary Sandbox regressions on root's isolated staging API.

Default prepares a local plan only. --execute requires an already created,
fresh Sandbox room. No lobby creation, SSH, model, game debug API or geometry
change is performed. Existing legacy launch plans and completion validators
remain the source of truth.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
STAGING = "http://127.0.0.1:5081"
CASES = ("cooperation", "investigation", "movement")
PREDICTION_CHECKS = {
    "Client physics remains non-authoritative", "Local presentation responds within two frames",
    "Authoritative root still receives server movement", "Release decelerates locally without waiting for server",
    "Idle reconciles to the server", "Rapid reversals converge without accumulated drift",
    "Predicted and authoritative wall collision agree", "Holding into a wall does not drift or jitter",
    "Diagonal wall contact slides instead of sticking", "Can move away from a wall immediately",
    "Speed remains normalized", "Client root manipulation cannot become authoritative", "No runtime errors or warnings",
}


def load(name):
    spec = importlib.util.spec_from_file_location("sandbox_regression_" + name,
        ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def read(path):
    try:
        if path.stat().st_size > 32 * 1024 * 1024: return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError): return {}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n"); temporary.replace(path)


def wrapper_source(client, python=sys.executable):
    # The wrapper inherits the ordinary launcher's token environment without
    # reading or serializing it. execv preserves the owned child PID. No shell.
    return f'''#!{python}
import os, sys
client = {str(client)!r}
args = sys.argv[1:]
filtered = []
index = 0
while index < len(args):
    arg = args[index]
    if arg in ("--api-url", "-screen-fullscreen", "-screen-width", "-screen-height"):
        if index + 1 >= len(args):
            raise SystemExit("Missing client option value")
        index += 2
    elif arg in ("-batchmode", "-nographics"):
        index += 1
    else:
        filtered.append(arg)
        index += 1
os.execv(client, [client, *filtered, "--api-url", {STAGING!r}, "-batchmode", "-nographics",
    "-screen-fullscreen", "0", "-screen-width", "800", "-screen-height", "600"])
'''


def make_plans(args, wrapper):
    if args.case == "movement":
        command = [str(wrapper), "--auto-room", args.room, "--player-name", "Sandbox Prediction",
            "--control-mode", "human", "--probe-role", "P", "--probe-output", str(args.output / "prediction.json"),
            "--actor-report", str(args.output / "client.json"), "-logFile", str(args.output / "client.log")]
        return None, [("movement", args.output, command)]
    module = load("launch-cooperative-agents" if args.case == "cooperation" else "launch-investigation-agents")
    plan_args = SimpleNamespace(output=args.output, room=args.room, base_port=args.base_port,
        timeout=args.timeout, max_decisions=24, model=None, client=wrapper, config=None,
        coordinator_brain="scripted", technician_brain="scripted", engineer_brain="scripted", sheriff_brain="scripted",
        headless_client=True)
    return module, module.plans(ROOT, plan_args)


def objective_passed(value):
    objective = value.get("objective", {})
    return (isinstance(objective, dict) and objective.get("passed") is True
        and isinstance(objective.get("result"), dict) and objective["result"].get("status") == "succeeded")


def alive(pid):
    if type(pid) is not int or pid <= 0: return False
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False
    except PermissionError: return True


def prediction_projection(value, client):
    checks = value.get("checks", [])
    errors, warnings = value.get("errors"), value.get("warnings")
    valid = (value.get("role") == "P" and value.get("connected") is True and value.get("completed") is True
        and isinstance(checks, list) and len(checks) == 13 and all(isinstance(c, dict) and c.get("passed") is True for c in checks)
        and {c.get("name") for c in checks if isinstance(c, dict)} == PREDICTION_CHECKS
        and isinstance(errors, list) and not errors and isinstance(warnings, list) and not warnings
        and client.get("mode") == "Human" and client.get("keyboardEnabled") is True)
    metrics = {}
    for key in ("visualResponseMs", "authoritativeResponseMs", "releaseResponseMs", "idleError", "maxSpeed"):
        number = value.get(key)
        metrics[key] = number if type(number) in (int, float) and math.isfinite(number) and number >= 0 else None
    valid_metrics = (all(number is not None for number in metrics.values()) and metrics["visualResponseMs"] < 70
        and metrics["releaseResponseMs"] < 160 and metrics["idleError"] < .1 and 2.4 < metrics["maxSpeed"] < 2.65)
    return {"passed": valid and valid_metrics,
        "checks_passed": sum(c.get("passed") is True for c in checks if isinstance(c, dict)), "checks_expected": 13,
        "errors": len(errors) if isinstance(errors, list) else None, "warnings": len(warnings) if isinstance(warnings, list) else None,
        "metrics": metrics, "input": "existing synthetic owned-input prediction probe; no physical keyboard or rendered-display claim"}


def latch(args, module, plans):
    if args.case == "cooperation":
        module.latch_completed_reports(plans)
    elif args.case == "investigation":
        for role, folder, _ in plans:
            snapshot = module.completion_snapshot(read(folder / "runtime.json"), read(folder / "client.json"), role,
                observer="readonly_completion_watcher")
            if snapshot is not None and not (folder / "client-investigation-completed.json").exists():
                module.save_completion_snapshot(folder, snapshot)


def pair_result(args, module, plans, processes):
    objectives = [objective_passed(read(folder / "runtime.json")) for _, folder, _ in plans]
    authoritative = bool(module.completed_reports(plans)) if args.case == "cooperation" else module.completed_pair(plans)
    rows = [read(folder / "launch.json") for _, folder, _ in plans]
    pids = [row.get(key) for row in rows for key in ("launcher_pid", "runtime_pid", "client_pid")]
    by_role = {role: process.pid for role, process in processes}
    independent = (len(pids) == 6 and all(type(pid) is int and pid > 0 for pid in pids) and len(set(pids)) == 6
        and all(rows[index].get("launcher_pid") == by_role.get(role) for index, (role, _, _) in enumerate(plans))
        and all(type(row.get("bridge_port")) is int and 1024 <= row["bridge_port"] <= 65535
            and isinstance(row.get("bridge_credential_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", row["bridge_credential_sha256"])
            and all(isinstance(row.get(key), str) and row[key].strip() for key in ("memory_database", "agent_profile")) for row in rows)
        and all(len({row.get(key) for row in rows}) == 2 for key in (
            "bridge_port", "bridge_credential_sha256", "memory_database", "agent_profile")))
    return {"passed": all(objectives) and authoritative and independent, "both_objectives_passed": all(objectives),
        "both_own_authoritative_terminal_and_same_scope": authoritative, "independent_processes_credentials_and_memory": independent,
        "scope": ("Two ordinary calibration agents: same authoritative Completed task plus own Objective success."
            if args.case == "cooperation" else "Two ordinary investigation agents: same Resolved incident, Restored cooling, validated report and own Objective success. Natural mechanical clock; no manual-causation hard-negative claim.")}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--room", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--client", type=Path, default=ROOT / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena")
    parser.add_argument("--base-port", type=int, default=18800)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--execute", action="store_true", help="Only after staging lease is explicitly available; otherwise prepare without launching")
    args = parser.parse_args(argv); args.output = args.output.resolve(); args.client = args.client.resolve(); args.room = args.room.upper()
    if not re.fullmatch(r"[A-Z0-9]{6}", args.room): parser.error("Six ASCII letters/digits are required")
    if not 1024 <= args.base_port <= 65534 or not math.isfinite(args.timeout) or not 60 <= args.timeout <= 600: parser.error("Invalid ports/deadline")
    if args.output.exists() and any(args.output.iterdir()): parser.error("Use a fresh empty output")
    if args.execute and not args.client.is_file(): parser.error("Build the ordinary client first")
    return args


def main(argv=None):
    args = arguments(argv); args.output.mkdir(parents=True, exist_ok=True)
    wrapper = args.output / "client-staging-wrapper"
    wrapper.write_text(wrapper_source(args.client)); wrapper.chmod(0o700)
    module, plans = make_plans(args, wrapper)
    write(args.output / "plan.json", {"case": args.case, "execution_requested": args.execute, "api": STAGING,
        "headless": True, "fullscreen": False, "commands": [command for _, _, command in plans]})
    if not args.execute:
        print(json.dumps({"prepared": True, "executed": False, "case": args.case}))
        return 0
    processes = []; logs = []; result = {"passed": False}; reason = "not_started"; deadline = time.monotonic() + args.timeout
    def interrupted(_signal, _frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted); signal.signal(signal.SIGINT, interrupted)
    try:
        for role, folder, command in reversed(plans):
            folder.mkdir(parents=True, exist_ok=True)
            log = (folder / "regression-launcher.log").open("w"); logs.append(log)
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            processes.append((role, process))
            if args.case != "movement" and role in ("technician", "sheriff"):
                ready_until = min(deadline, time.monotonic() + 120)
                while True:
                    state = read(folder / "client.json")
                    if state.get("state") == "Connected" and state.get("actorId"): break
                    if process.poll() is not None: raise RuntimeError("startup_child_stopped")
                    if time.monotonic() >= ready_until: raise TimeoutError("startup_deadline")
                    time.sleep(.1)
        while time.monotonic() < deadline:
            if args.case == "movement":
                report = read(args.output / "prediction.json")
                if report.get("completed") is True:
                    result = prediction_projection(report, read(args.output / "client.json")); reason = "probe_completed"; break
            else:
                latch(args, module, plans)
                if all(objective_passed(read(folder / "runtime.json")) for _, folder, _ in plans):
                    result = pair_result(args, module, plans, processes)
                    if result["passed"]: reason = "authoritative_pair_completed"; break
                if any(read(folder / "runtime.json").get("objective", {}).get("passed") is False for _, folder, _ in plans):
                    raise RuntimeError("objective_failed")
            if any(process.poll() is not None for _, process in processes): raise RuntimeError("owned_launcher_stopped")
            time.sleep(.05)
        else: raise TimeoutError("regression_deadline")
    except KeyboardInterrupt: reason = "interrupted"
    except (OSError, RuntimeError, TimeoutError) as error:
        reason = str(error) if re.fullmatch(r"[A-Za-z_]{1,64}", str(error)) else type(error).__name__
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGINT, signal.SIG_IGN)
        # Only manifests whose launcher identity equals our actual Popen can
        # contribute a child PID. They are inspected for liveness, not signalled
        # individually; cleanup still addresses only our owned process groups.
        by_role = dict(processes); children = []
        for role, folder, _ in plans:
            row = read(folder / "launch.json")
            if role in by_role and row.get("launcher_pid") == by_role[role].pid:
                children.extend(row.get(key) for key in ("runtime_pid", "client_pid") if type(row.get(key)) is int and row[key] > 0)
        # Reuse the existing launcher group's exact owned cleanup semantics.
        load("launch-investigation-agents").stop_owned(processes)
        clean_until = time.monotonic() + 3
        while any(alive(pid) for pid in children) and time.monotonic() < clean_until:
            time.sleep(.05)
        children_remaining = sum(alive(pid) for pid in children)
        launchers_remaining = sum(process.poll() is None for _, process in processes)
        if children_remaining or launchers_remaining or args.case != "movement" and len(children) != 4:
            result["passed"] = False
        for log in logs: log.close()
    write(args.output / "acceptance.json", {"schema": "actor.sandbox-regression.v1", "case": args.case,
        "executed": True, "reason": reason, **result, "owned_launchers_remaining": launchers_remaining,
        "owned_runtime_client_pids_checked": len(children), "owned_runtime_clients_remaining": children_remaining,
        "limitations": ["Only the named case was run; no model intelligence or full M6 benchmark claim.",
            "Prediction uses synthetic owned input on a headless client, not native keyboard/display response.",
            "Raw local reports, plan and logs remain private; this acceptance publishes no authorization, chat, actor/session IDs or credentials."]})
    print(json.dumps({"passed": result["passed"], "case": args.case, "reason": reason}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
