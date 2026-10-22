#!/usr/bin/env python3
"""Launch 4–6 isolated, role-agnostic SocialDeduction match participants.

The lobby must already use SocialDeduction with MinPlayers equal to --players.
This supervisor reads readiness and terminal proof only. It never reads current
alignment, positions, claims or model context, and never submits a game action.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit


TRUSTED_OBJECTIVE = (
    "Play the match autonomously. Preserve your life, pursue your own side's victory conditions, "
    "and make decisions based only on observations, memory and communication legitimately available to your character. "
    "A finished Objective ends one bounded episode; only the authoritative game determines the match outcome."
)
COMPLETION_SCHEMA = "actor.social-client-completion.v1"
TOKEN = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")
PROVIDERS = ("social-scripted", "codex", "codex-threat-audit", "social-fixture-vote", "social-fixture-parity",
    "social-fixture-sabotage", "social-fixture-tasks", "social-privacy-fixture")
PLAYER_NAMES = ("Аня", "Борис", "Вера", "Глеб", "Даша", "Егор", "Жанна", "Зоя",
    "Илья", "Кира", "Лена", "Мира", "Нина", "Олег", "Павел", "Рита",
    "Саша", "Тимур", "Уля", "Фёдор", "Элла", "Юля", "Яна", "Лев")


def participant_names(args, *, sample=None):
    """Resolve public names once; the OS name picker never touches game RNG."""
    if getattr(args, "player_names", None) is None:
        args.player_names = list((sample or secrets.SystemRandom().sample)(PLAYER_NAMES, args.players))
    return args.player_names


class _RegisteredSpawn:
    """Defer an interrupt only until a newly created group is owned by cleanup."""

    def __init__(self):
        self._registering = False
        self._pending = False

    def interrupted(self, _signal, _frame):
        if self._registering:
            self._pending = True
        else:
            raise KeyboardInterrupt

    def spawn(self, command, *, register, **kwargs):
        if self._pending:
            raise KeyboardInterrupt
        self._registering = True
        try:
            process = subprocess.Popen(command, **kwargs)
            register(process)
        finally:
            self._registering = False
        if self._pending:
            raise KeyboardInterrupt
        return process


def read_report(path):
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def token(value):
    return isinstance(value, str) and TOKEN.fullmatch(value) is not None


def winning_side(value):
    if not isinstance(value, str):
        return None
    return {"Crew": "Crew", "Impostor": "Impostor", "Unspecified": "Unspecified",
        "SOCIAL_ALIGNMENT_CREW": "Crew", "SOCIAL_ALIGNMENT_IMPOSTOR": "Impostor",
        "SOCIAL_ALIGNMENT_UNSPECIFIED": "Unspecified"}.get(value)


def match_outcome(runtime):
    match = runtime.get("match")
    if not isinstance(match, dict) or not isinstance(match.get("result"), dict):
        return None
    result = match["result"]
    if result.get("status") not in ("completed", "failed", "cancelled"):
        return None
    return (match.get("passed") is True and result.get("status") == "completed"
        and result.get("authoritative_terminal") is True)


def completion_snapshot(runtime, client):
    """No live private state is inspected; terminal identity must match both sides."""
    if match_outcome(runtime) is not True:
        return None
    result = runtime["match"]["result"]
    outcome = result.get("outcome")
    accepted = client.get("lastAcceptedSessionId") or client.get("sessionId")
    actor, match_id = client.get("actorId"), client.get("socialMatchId")
    if (client.get("gameMode") != "SocialDeduction" or client.get("socialPhase") != "GameOver"
            or client.get("socialSummaryPresent") is not True or client.get("socialAssigned") is not True or not isinstance(outcome, dict)
            or not all(token(value) for value in (actor, accepted, match_id))
            or result.get("scope_id") != match_id or outcome.get("match_id") != match_id
            or winning_side(client.get("socialWinningSide")) is None
            or winning_side(outcome.get("winning_side")) != winning_side(client.get("socialWinningSide"))
            or not token(client.get("socialWinReason")) or outcome.get("win_reason") != client["socialWinReason"]
            or type(client.get("socialPhaseRevision")) is not int or client["socialPhaseRevision"] <= 0):
        return None
    metadata = runtime.get("runtime")
    sessions = metadata.get("sessions", []) if isinstance(metadata, dict) else []
    if not isinstance(sessions, list) or len(sessions) > 256:
        return None
    matched = [s for s in sessions if isinstance(s, dict) and s.get("session_id") == accepted]
    if len(matched) != 1 or matched[0].get("actor_id") != actor:
        return None
    return {"schema": COMPLETION_SCHEMA, "actorId": actor, "acceptedSessionId": accepted,
        "socialMatchId": match_id, "socialPhase": "GameOver", "socialPhaseRevision": client["socialPhaseRevision"],
        "socialWinningSide": winning_side(client["socialWinningSide"]), "socialWinReason": client["socialWinReason"],
        "socialSummaryPresent": True, "runtimeAuthoritativeTerminal": True, "runtimeMatchPassed": True}


def completed_match(snapshots, expected):
    if len(snapshots) != expected or any(not isinstance(s, dict) for s in snapshots):
        return False
    if any(s.get("schema") != COMPLETION_SCHEMA or s.get("socialPhase") != "GameOver"
            or s.get("socialSummaryPresent") is not True or s.get("runtimeAuthoritativeTerminal") is not True
            or s.get("runtimeMatchPassed") is not True or not token(s.get("actorId"))
            or not token(s.get("acceptedSessionId")) or not token(s.get("socialMatchId"))
            or winning_side(s.get("socialWinningSide")) is None or not token(s.get("socialWinReason")) for s in snapshots):
        return False
    return (len({s.get("actorId") for s in snapshots}) == expected
        and len({s.get("acceptedSessionId") for s in snapshots}) == expected
        and all(token(s.get("actorId")) and token(s.get("acceptedSessionId")) for s in snapshots)
        and len({s.get("socialMatchId") for s in snapshots}) == 1 and token(snapshots[0].get("socialMatchId"))
        and len({(s.get("socialWinningSide"), s.get("socialWinReason")) for s in snapshots}) == 1)


def plans(root, args):
    result = []
    public_names = participant_names(args)
    for index in range(args.players):
        name = f"agent-{index + 1:02d}"
        output = args.output / name
        brain = args.brains[index] if args.brains else args.brain
        command = [sys.executable, str(root / "scripts/launch-agent.py"), "--operation", "match",
            "--match-policy", getattr(args, "match_policy", "agents-arena"), "--room", args.room, "--brain", brain,
            "--objective", getattr(args, "objective", TRUSTED_OBJECTIVE), "--player-name", public_names[index],
            "--port", str(args.base_port + index), "--output", str(output),
            "--memory", "sqlite", "--agent-profile", name, "--memory-database", str(output / "memory.sqlite3"),
            "--objective-timeout", str(min(args.timeout, 3600)), "--max-decisions", str(args.max_decisions)]
        for flag, value in (("--model", args.model), ("--client", args.client), ("--config", args.config)):
            if value is not None:
                command += [flag, str(value)]
        if getattr(args, "api_url", None) is not None:
            command += ["--api-url", args.api_url]
        if args.headless_client:
            command += ["--headless-client"]
        result.append((name, output, command))
    return result


def process_metadata(launches, processes):
    by_name = dict(processes)
    rows = []
    for name, output, command in launches:
        launch = read_report(output / "launch.json")
        row = {"agent": name, "launcher_pid": by_name[name].pid if name in by_name else None}
        row["player_name"] = command[command.index("--player-name") + 1] if "--player-name" in command else None
        for key in ("runtime_pid", "client_pid", "bridge_port"):
            value = launch.get(key)
            row[key] = value if type(value) is int and value > 0 else None
        row["launcher_identity_matches"] = launch.get("launcher_pid") == row["launcher_pid"] if row["launcher_pid"] is not None else False
        if name in by_name:
            row["launcher_exit_code"] = by_name[name].poll()
        rows.append(row)
    return {"schema": "actor.social-processes.v1", "agents": rows,
        "observed_unix_ms": int(time.time() * 1000)}


def isolation_checks(launches, processes):
    metadata = process_metadata(launches, processes)
    rows = metadata["agents"]
    pids = [row[key] for row in rows for key in ("launcher_pid", "runtime_pid", "client_pid")]
    launches_metadata = [read_report(output / "launch.json") for _, output, _ in launches]
    paths = [item.get("memory_database") for item in launches_metadata]
    profiles = [item.get("agent_profile") for item in launches_metadata]
    hashes = [item.get("bridge_credential_sha256") for item in launches_metadata]
    ports = [row["bridge_port"] for row in rows]
    checks = {"three_distinct_owned_pids_per_agent": all(type(pid) is int and pid > 0 for pid in pids) and len(set(pids)) == len(pids),
        "launcher_identities_match": all(row["launcher_identity_matches"] for row in rows),
        "distinct_bridge_ports": all(type(port) is int and 1024 <= port <= 65535 for port in ports) and len(set(ports)) == len(ports),
        "independent_bridge_credentials": all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes) and len(set(hashes)) == len(hashes),
        "distinct_memory_databases": all(isinstance(value, str) and value for value in paths) and len(set(paths)) == len(paths),
        "distinct_agent_profiles": all(token(value) for value in profiles) and len(set(profiles)) == len(profiles),
        "match_operation": all(item.get("operation") == "match" for item in launches_metadata)}
    return checks


def pid_alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


def stop_owned(processes):
    # Only session groups created by this invocation are signalled. Each child
    # launcher first drains its Runtime/Skill cleanup before closing its client.
    for _, process in processes:
        if process.poll() is None:
            try:
                process.terminate()
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
        for _, process in processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def arguments(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room", required=True)
    parser.add_argument("--players", type=int, choices=(4, 5, 6), default=4,
        help="Independent participants; the lobby must use this same MinPlayers")
    parser.add_argument("--brain", choices=PROVIDERS, default="social-scripted")
    parser.add_argument("--match-policy", default="agents-arena",
        help="Installed lifecycle policy; optional fixture policies must be installed explicitly")
    parser.add_argument("--brains", nargs="+", choices=PROVIDERS, help="One provider per numbered participant, never a role assignment")
    parser.add_argument("--player-names", nargs="+",
        help="Exactly --players distinct public names; default samples short Russian names without replacement")
    parser.add_argument("--objective", default=TRUSTED_OBJECTIVE,
        help="One shared trusted goal, identical for every participant; does not assign hidden roles")
    parser.add_argument("--base-port", type=int, default=50051)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--client", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--api-url", help="Optional absolute HTTP(S) lobby API base URL, passed only to each ordinary Unity client")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--timeout", type=float, default=1800, help="Overall supervisor deadline; configure Runtime participation limits with --config")
    parser.add_argument("--max-decisions", type=int, default=24, help="Per-episode decision limit")
    rendering = parser.add_mutually_exclusive_group()
    rendering.add_argument("--headless-client", dest="headless_client", action="store_true", default=True)
    rendering.add_argument("--windowed", dest="headless_client", action="store_false", help="Visual acceptance only; all clients remain 800x600 windowed")
    args = parser.parse_args(argv)
    if (not isinstance(args.match_policy, str) or len(args.match_policy) > 128 or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.match_policy) is None):
        parser.error("--match-policy must be a bounded installed registry name")
    args.room = args.room.upper()
    if not args.objective.strip() or len(args.objective) > 4096 or "\0" in args.objective:
        parser.error("--objective must contain 1–4096 characters and nonblank text, without NUL")
    if args.api_url is not None:
        try:
            url = urlsplit(args.api_url)
            valid_url = (0 < len(args.api_url) <= 2048 and url.scheme in ("http", "https") and url.hostname
                and url.username is None and url.password is None and not url.query and not url.fragment
                and all(ord(c) >= 33 for c in args.api_url) and (url.port is None or 1 <= url.port <= 65535))
        except ValueError:
            valid_url = False
        if not valid_url:
            parser.error("--api-url must be an absolute HTTP(S) base URL without credentials, query or fragment")
    if not re.fullmatch(r"[A-Z0-9]{6}", args.room):
        parser.error("A six-character room code is required")
    if not 1024 <= args.base_port <= 65536 - args.players:
        parser.error("Bridge ports must be distinct valid unprivileged ports")
    if not math.isfinite(args.timeout) or not 60 <= args.timeout <= 14400:
        parser.error("--timeout must be finite and between 60 and 14400 seconds")
    if not 1 <= args.max_decisions <= 64:
        parser.error("--max-decisions must be between 1 and 64")
    if args.brains and len(args.brains) != args.players:
        parser.error("--brains must contain exactly --players providers")
    if args.player_names is not None:
        if (len(args.player_names) != args.players
                or any(not 2 <= len(name) <= 24 or name != name.strip()
                    or any(not (char.isalpha() or char.isdecimal() or char in " _-.") or ord(char) > 0xffff for char in name)
                    for name in args.player_names)
                or len({name.casefold() for name in args.player_names}) != args.players):
            parser.error("--player-names requires distinct names of 2–24 letters/digits/spaces/_/./-, without edge whitespace")
    if args.output is None:
        args.output = root / "build/social-deduction-validation" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    args.output = args.output.resolve()
    for field in ("client", "config"):
        value = getattr(args, field)
        if value is not None:
            setattr(args, field, value.resolve())
    if args.config is not None and not args.config.is_file():
        parser.error("--config must name an existing cognitive configuration")
    if args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
        parser.error("Choose an empty output directory for an independent match")
    return root, args


def main(argv=None):
    root, args = arguments(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    launches = plans(root, args); processes = []; snapshots = {}; exit_code = 1; result = "not_started"; isolation = {}
    spawning = _RegisteredSpawn()
    signal.signal(signal.SIGTERM, spawning.interrupted); signal.signal(signal.SIGINT, spawning.interrupted)
    try:
        for name, output, command in launches:
            output.mkdir(parents=True, exist_ok=True)
            with (output / "launcher.log").open("w") as log:
                process = spawning.spawn(command, register=lambda child: processes.append((name, child)),
                    cwd=root, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=os.name == "posix")
            print(f"Started {name}; launcher PID={process.pid}", flush=True)
        deadline = time.monotonic() + args.timeout
        ready_deadline = time.monotonic() + min(args.timeout, 150)
        ready = False
        while True:
            for name, output, _ in launches:
                if name in snapshots:
                    continue
                snapshot = completion_snapshot(read_report(output / "runtime.json"), read_report(output / "client.json"))
                if snapshot is not None:
                    snapshots[name] = snapshot
                    write_json(output / "client-social-completed.json", snapshot)
            if completed_match(list(snapshots.values()), args.players):
                isolation = isolation_checks(launches, processes)
                result, exit_code = ("authoritative_match_completed", 0) if all(isolation.values()) else ("process_isolation_unverified", 1)
                break
            if any(match_outcome(read_report(output / "runtime.json")) is False for _, output, _ in launches):
                result = "runtime_match_failed"
                break
            if any(process.poll() is not None for _, process in processes):
                result = "owned_launcher_stopped"
                break
            if not ready:
                ready = all((client := read_report(output / "client.json")).get("state") == "Connected"
                    and token(client.get("actorId")) for _, output, _ in launches)
                if ready:
                    print("All independent game/bridge clients are ready.", flush=True)
                elif time.monotonic() >= ready_deadline:
                    result = "startup_deadline"
                    break
            if time.monotonic() >= deadline:
                result = "match_deadline"
                break
            time.sleep(.2)
    except KeyboardInterrupt:
        result, exit_code = "interrupted", 130
    except (OSError, RuntimeError, ValueError):
        result = "supervisor_error"
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            write_json(args.output / "processes.json", process_metadata(launches, processes))
        except OSError:
            result, exit_code = "report_write_failed", 1
        finally:
            stop_owned(processes)
        after = process_metadata(launches, processes)
        for row in after["agents"]:
            row["runtime_alive_after_cleanup"] = pid_alive(row["runtime_pid"])
            row["client_alive_after_cleanup"] = pid_alive(row["client_pid"])
        clean = not any(row["runtime_alive_after_cleanup"] or row["client_alive_after_cleanup"] for row in after["agents"])
        if not clean:
            result, exit_code = "owned_process_cleanup_incomplete", 1
        try:
            write_json(args.output / "processes-after-cleanup.json", after)
            write_json(args.output / "supervisor.json", {"schema": "actor.social-supervisor.v1", "status": result,
                "passed": exit_code == 0, "players": args.players, "completed_own_reports": len(snapshots),
                "headless_clients": args.headless_client, "window_width": 800, "window_height": 600,
                "process_isolation": isolation, "owned_process_cleanup_complete": clean,
                "authoritative_terminal_agreement": completed_match(list(snapshots.values()), args.players)})
        except OSError:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
