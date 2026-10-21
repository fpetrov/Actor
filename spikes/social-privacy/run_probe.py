#!/usr/bin/env python3
"""Launch an ordinary four-player privacy fixture and project only bounded audit counters."""
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

ROOT = Path(__file__).resolve().parents[2]
CLIENT_COUNTS = tuple("incomingHookCalls adapterHookCalls incomingWorldFrames adapterWorldFrames spawnAuditSamples ownStateSamples foreignComponentSamples initialLegitimateHallActors farWorldFrames farAdapterFrames endpointCheckedCalls wrongRecipientIncoming wrongRecipientAdapter foreignEndpointIncoming foreignPrivateComponentSamples legacyInvestigationSamples legacyCalibrationSamples legacyAdapterPayloads persistentOutOfSightActors persistentOutOfSightBodies farForbiddenIncomingEntries farForbiddenAdapterEntries farForbiddenAttackEvents earlyPostgamePayloads probeFailures".split())
CLIENT_ZEROS = CLIENT_COUNTS[11:]
CHECKS = {"actual_incoming_and_adapter_hooks_exercised", "actual_ngo_spawn_audit_exercised", "only_own_private_rpc_and_adapter_values",
    "remote_endpoints_never_contain_private_state", "social_has_no_legacy_private_or_roster_state", "no_sustained_out_of_sight_network_objects",
    "far_security_receives_no_out_of_sight_facts", "postgame_disclosure_is_terminal", "auditor_is_bounded_and_operational"}
WIRE_COUNTS = tuple("streams wire_messages manifests observation_frames world_frames events own_payloads self_envelopes far_security_frames foreign_entities foreign_recipients legacy_capabilities legacy_payloads type_mismatches far_forbidden_world_entries far_forbidden_attack_events early_postgame_payloads probe_failures".split())
WIRE_ZEROS = WIRE_COUNTS[9:]


def count(value):
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def duration(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 86400 else None


def milliseconds(value, *, maximum=10_000_000_000_000):
    # Protobuf JSON encodes int64 as decimal strings. Booleans/floats are never
    # timestamps, even though Python compares True equal to 1.
    if type(value) is str and value.isascii() and value.isdecimal() and len(value) <= 16:
        value = int(value)
    return value if type(value) is int and 0 <= value <= maximum else None


def interval_ranges(value, *, unity):
    rows = value.get("stableFarIntervals" if unity else "stable_far_intervals")
    if not isinstance(rows, list) or len(rows) > 16:
        return None
    result = []
    for row in rows:
        if not isinstance(row, dict): return None
        if unity:
            keys = ("incoming_first_unix_ms", "incoming_last_unix_ms", "adapter_first_unix_ms", "adapter_last_unix_ms")
            numbers = [milliseconds(row.get(key)) for key in keys]
            frames = [count(row.get(key)) for key in ("incoming_frames", "adapter_frames")]
            if any(number is None or number <= 0 for number in numbers) or any(number is None or number < 10 for number in frames):
                continue
            a, b, c, d = numbers
            if a > b or c > d: return None
            result.append((max(a, c), min(b, d)))
        else:
            a, b = milliseconds(row.get("first_unix_ms")), milliseconds(row.get("last_unix_ms"))
            frames = count(row.get("frames"))
            if a is None or b is None or not a or not b or frames is None or frames < 10: continue
            if a > b: return None
            result.append((a, b))
    return result


def temporal_kill_coverage(unity, wire, summary):
    started = milliseconds(unity.get("matchStartedAtUnixMs"))
    other_started = milliseconds(wire.get("match_started_at_unix_ms"))
    if started is None or not started or started != other_started:
        return False
    unity_ranges, wire_ranges = interval_ranges(unity, unity=True), interval_ranges(wire, unity=False)
    if unity_ranges is None or wire_ranges is None: return False
    replay = summary.get("replay")
    total_duration = milliseconds(summary.get("durationMs"), maximum=3_600_000)
    if not isinstance(replay, list) or len(replay) > 256 or total_duration is None: return False
    for event in replay:
        if not isinstance(event, dict) or event.get("kind") != "kill": continue
        elapsed = milliseconds(event.get("elapsedMs"), maximum=total_duration)
        if elapsed is None: continue
        killed_at = started + elapsed
        for a, b in unity_ranges:
            for c, d in wire_ranges:
                first, last = max(a, c), min(b, d)
                # Same continuous interval on all three actual surfaces, with
                # measured time before and after the real authoritative kill.
                if last - first >= 4000 and first + 1000 <= killed_at <= last - 1000:
                    return True
    return False


def temporal_instant_coverage(unity, wire, instant):
    a_start = milliseconds(unity.get("matchStartedAtUnixMs"))
    b_start = milliseconds(wire.get("match_started_at_unix_ms"))
    if a_start is None or not a_start or a_start != b_start: return False
    unity_ranges, wire_ranges = interval_ranges(unity, unity=True), interval_ranges(wire, unity=False)
    if unity_ranges is None or wire_ranges is None: return False
    return any(min(b, d)-max(a, c) >= 4000 and max(a, c)+1000 <= instant <= min(b, d)-1000
               for a, b in unity_ranges for c, d in wire_ranges)


def project_active_probe(reports, audits, agents, summary):
    result = {"enabled": True, "passed": False, "attempts": 0, "authoritative_rejections": 0,
              "accepted": 0, "stable_far_coverage_at_request": False, "no_server_kill_during_probe": False}
    if len(reports) != 4: return result
    eligible = []
    for index, report in enumerate(reports):
        if (report.get("schema") != "actor.social-out-of-sight-kill-probe.v1" or report.get("completed") is not True
                or report.get("passed") is not True or count(report.get("probe_failures")) != 0): return result
        numbers = [count(report.get(key)) for key in ("attempts", "authoritative_rejections", "accepted")]
        if any(number is None for number in numbers): return result
        for key, number in zip(("attempts", "authoritative_rejections", "accepted"), numbers): result[key] += number
        if report.get("eligible") is True:
            eligible.append((index, report))
        elif report.get("eligible") is not False or any(numbers) or report.get("outcome") != "own_participant_ineligible": return result
    if len(eligible) != 1 or (result["attempts"], result["authoritative_rejections"], result["accepted"]) != (1, 1, 0): return result
    index, report = eligible[0]
    started = milliseconds(report.get("match_started_at_unix_ms"))
    requested = milliseconds(report.get("requested_at_unix_ms"))
    captured = milliseconds(report.get("request_world_captured_at_unix_ms"))
    received = milliseconds(report.get("received_at_unix_ms"))
    if (any(number is None or not number for number in (started, requested, captured, received))
            or not 90000 <= requested-started <= 110000 or not 0 <= received-requested <= 10000
            or abs(requested-captured) > 400 or report.get("candidate_was_not_visible") is not True
            or count(report.get("initial_legitimate_candidates")) not in range(1, 7)
            or report.get("outcome") != "authoritative_rejection_verified"
            or started != milliseconds(audits[index][0].get("matchStartedAtUnixMs"))
            or started != milliseconds(audits[index][1].get("match_started_at_unix_ms"))): return result
    result["stable_far_coverage_at_request"] = any(
        other != index and agent["unity"]["far_security_coverage"] and agent["wire"]["far_security_coverage"]
        and temporal_instant_coverage(unity, wire, captured)
        for other, (agent, (unity, wire)) in enumerate(zip(agents, audits)))
    replay = summary.get("replay")
    duration_ms = milliseconds(summary.get("durationMs"), maximum=3_600_000)
    if not isinstance(replay, list) or len(replay) > 256 or duration_ms is None or summary.get("replayTruncated") is True: return result
    # Before this request no real elimination/disconnect may make a remembered
    # participant unavailable. No kill can occur in the rejected-request window.
    request_elapsed, receipt_elapsed = requested-started, received-started
    safe = True; actual_kills = 0
    for event in replay:
        if not isinstance(event, dict): return result
        if event.get("kind") not in ("kill", "disconnect", "ejection"): continue
        elapsed = milliseconds(event.get("elapsedMs"), maximum=duration_ms)
        if elapsed is None: return result
        if event["kind"] == "kill": actual_kills += 1
        if elapsed <= receipt_elapsed+1000: safe = False
    result["no_server_kill_during_probe"] = safe and actual_kills == count(summary.get("kills")) and actual_kills >= 1
    result["passed"] = result["stable_far_coverage_at_request"] and result["no_server_kill_during_probe"]
    return result


def read(path: Path):
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def project_client(value):
    metrics = {key: count(value.get(key)) for key in CLIENT_COUNTS}
    metrics.update({key: duration(value.get(key)) for key in ("durationSeconds", "farSecuritySeconds", "maxFarSecurityContinuousSeconds")})
    checks = value.get("checks")
    checked = (isinstance(checks, list) and len(checks) == len(CHECKS)
               and all(isinstance(item, dict) and type(item.get("name")) is str and item.get("passed") is True for item in checks)
               and {item.get("name") for item in checks} == CHECKS)
    passed = (value.get("schema") == "actor.social-privacy-probe.v1" and value.get("completed") is True
              and value.get("passed") is True and value.get("socialModeObserved") is True and checked
              and all(number is not None for number in metrics.values())
              and all(metrics[key] == 0 for key in CLIENT_ZEROS)
              and all(metrics[key] > 0 for key in ("incomingWorldFrames", "adapterWorldFrames", "spawnAuditSamples", "ownStateSamples", "foreignComponentSamples", "endpointCheckedCalls")))
    far = (passed and value.get("farSecurityCoverage") is True and metrics["maxFarSecurityContinuousSeconds"] >= 4
           and metrics["initialLegitimateHallActors"] >= 1 and metrics["farWorldFrames"] >= 10 and metrics["farAdapterFrames"] >= 10)
    return {"passed": passed, "completed": value.get("completed") is True, "far_security_coverage": far, "metrics": metrics}


def project_wire(value):
    metrics = {key: count(value.get(key)) for key in WIRE_COUNTS}
    metrics["initial_legitimate_hall_actors"] = count(value.get("initial_legitimate_hall_actors"))
    metrics["max_far_security_seconds"] = duration(value.get("max_far_security_seconds"))
    metrics["duration_seconds"] = duration(value.get("duration_seconds"))
    passed = (value.get("schema") == "actor.social-wire-privacy-probe.v1" and value.get("completed") is True
              and value.get("passed") is True and all(number is not None for number in metrics.values())
              and metrics["streams"] == 1 and metrics["manifests"] == 1
              and all(metrics[key] > 0 for key in ("observation_frames", "world_frames", "own_payloads", "self_envelopes"))
              and all(metrics[key] == 0 for key in WIRE_ZEROS))
    far = (passed and value.get("far_security_coverage") is True and metrics["max_far_security_seconds"] >= 4
           and metrics["far_security_frames"] >= 10 and metrics["initial_legitimate_hall_actors"] >= 1)
    return {"passed": passed, "completed": value.get("completed") is True, "far_security_coverage": far, "metrics": metrics}


def runtime_result(value):
    match = value.get("match", {})
    if not isinstance(match, dict): match = {}
    result = match.get("result", {})
    if not isinstance(result, dict): result = {}
    return {"participation_complete": match.get("passed") is True and result.get("status") == "completed"
            and result.get("authoritative_terminal") is True,
            "episodes": count(result.get("episodes")), "decisions": count(result.get("decisions"))}


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp"); temporary.write_text(json.dumps(value, indent=2) + "\n"); temporary.replace(path)


def independent_launches(rows):
    if not isinstance(rows, list) or len(rows) != 4 or not all(isinstance(row, dict) for row in rows):
        return False
    for row in rows:
        port = count(row.get("bridge_port"))
        fingerprint = row.get("bridge_credential_sha256")
        if port is None or not 1024 <= port <= 65535:
            return False
        if type(fingerprint) is not str or len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            return False
        if any(type(row.get(key)) is not str or not 1 <= len(row[key]) <= 4096 for key in ("agent_profile", "memory_database")):
            return False
    return all(len({row[key] for row in rows}) == 4 for key in ("bridge_port", "bridge_credential_sha256", "agent_profile", "memory_database"))


def summarize(output: Path, server_summary: Path | None):
    agents = []; scopes = []; audit_pairs = []
    for index in range(1, 5):
        folder = output / f"agent{index:02}"
        runtime = read(folder / "runtime.json")
        match = runtime.get("match", {})
        raw_result = match.get("result", {}) if isinstance(match, dict) else {}
        scope = raw_result.get("scope_id") if isinstance(raw_result, dict) else None
        scopes.append(scope if isinstance(scope, str) and 0 < len(scope) <= 128 else None)
        unity, wire = read(folder / "privacy-unity.json"), read(folder / "privacy-wire.json")
        audit_pairs.append((unity, wire))
        agents.append({"participant": index, "unity": project_client(unity),
                       "wire": project_wire(wire), "runtime": runtime_result(runtime)})
    summary = read(server_summary) if server_summary else {}
    kills = count(summary.get("kills"))
    same_match = scopes[0] is not None and all(scope == scopes[0] for scope in scopes) and summary.get("matchId") == scopes[0]
    server = {"available": bool(summary), "same_match": same_match, "social_mode": summary.get("mode") == "SocialDeduction", "kills": kills}
    launch = read(output / "launch.json"); rows = launch.get("agents", [])
    isolated = independent_launches(rows)
    cleanup = read(output / "cleanup.json")
    stopped = count(cleanup.get("started")) == 8 and count(cleanup.get("remaining")) == 0
    far = sum(agent["unity"]["far_security_coverage"] and agent["wire"]["far_security_coverage"] for agent in agents)
    temporal_far = sum(agent["unity"]["far_security_coverage"] and agent["wire"]["far_security_coverage"]
                       and temporal_kill_coverage(unity, wire, summary) for agent, (unity, wire) in zip(agents, audit_pairs))
    active_probe = {"enabled": False, "passed": None}
    if launch.get("active_out_of_sight_probe") is True:
        active_probe = project_active_probe([read(output / f"agent{index:02}/out-of-sight-kill.json") for index in range(1, 5)], audit_pairs, agents, summary)
    passed = (all(agent["unity"]["passed"] and agent["wire"]["passed"] and agent["runtime"]["participation_complete"] for agent in agents)
              and temporal_far >= 1 and same_match and server["social_mode"] and kills is not None and kills >= 1 and isolated and stopped
              and (not active_probe["enabled"] or active_probe["passed"]))
    result = {"schema": "actor.social-privacy-acceptance.v1", "passed": passed, "agents": agents,
              "far_participants_verified_on_both_surfaces": far, "server_postgame": server,
              "far_participants_verified_during_actual_kill": temporal_far,
              "active_out_of_sight_kill_probe": active_probe,
              "independent_runtime_credentials_profiles_and_ports": isolated, "owned_processes_remaining": count(cleanup.get("remaining")),
              "scope": "Actual Unity NGO objects + pre-filter owner RPC + actual adapter captures + authenticated raw gRPC before RuntimeSession.accept. Counts only; no roles, positions, prose, IDs or credentials."}
    write(output / "acceptance.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room")
    parser.add_argument("--api-url")
    parser.add_argument("--client", type=Path, default=ROOT / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--server-summary", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--base-port", type=int, default=18400)
    parser.add_argument("--timeout", type=float, default=260)
    parser.add_argument("--summarize-only", action="store_true")
    parser.add_argument("--probe-out-of-sight-kill", action="store_true", help="Also perform one explicit ACTIVE ordinary out-of-sight kill request, independently from passive auditing")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.summarize_only:
        result = summarize(output, args.server_summary.resolve() if args.server_summary else None)
        print(json.dumps({"passed": result["passed"], "far_participants_verified_on_both_surfaces": result["far_participants_verified_on_both_surfaces"]}))
        return 0 if result["passed"] else 1
    if not args.room or len(args.room) != 6 or not args.room.isascii() or not args.room.isalnum(): parser.error("--room must be six ASCII letters/digits")
    if not math.isfinite(args.timeout) or not 60 <= args.timeout <= 600: parser.error("--timeout must be 60–600 seconds")
    if not 1024 <= args.base_port <= 65532: parser.error("Four valid independent bridge ports are required")
    if args.api_url is not None:
        try:
            url = urlsplit(args.api_url)
            valid = url.scheme in ("http", "https") and url.hostname and url.username is None and url.password is None and not url.query and not url.fragment
        except ValueError: valid = False
        if not valid: parser.error("--api-url must have no credentials, query or fragment")
    client = args.client.resolve(); python = ROOT / "build/runtime-venv/bin/python"
    if not client.is_file() or not python.is_file(): parser.error("Build ordinary client and install Runtime first")
    if output.exists() and any(output.iterdir()): parser.error("Use a fresh empty output; never backfill a previous acceptance run")
    for port in range(args.base_port, args.base_port + 4):
        with socket.socket() as probe:
            try: probe.bind(("127.0.0.1", port))
            except OSError: parser.error("One requested bridge port is occupied")
    output.mkdir(parents=True, exist_ok=True)
    processes = []; runtime_processes = []; logs = []; launch_rows = []; interrupted = False; forced = 0
    def stop(_signal, _frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    from actor_runtime.episodes import TRUSTED_MATCH_OBJECTIVE
    try:
        for index in range(1, 5):
            folder = output / f"agent{index:02}"; folder.mkdir()
            credential = secrets.token_hex(32); profile = "social-privacy-" + secrets.token_hex(12)
            environment = dict(os.environ, ACTOR_BRIDGE_TOKEN=credential)
            port = args.base_port + index - 1
            runtime_log = (folder / "runtime.log").open("w"); logs.append(runtime_log)
            command = [str(python), str(ROOT / "spikes/social-privacy/runtime_audit.py"), "--privacy-wire-report", str(folder / "privacy-wire.json"),
                "match", "--brain", "social-privacy-fixture", "--match-policy", "agents-arena", "--objective", TRUSTED_MATCH_OBJECTIVE,
                "--port", str(port), "--report", str(folder / "runtime.json"), "--memory", "sqlite", "--memory-database", str(folder / "memory.sqlite3"),
                "--agent-profile", profile, "--measure-allocations", "--exit-on-complete"]
            if args.config: command += ["--config", str(args.config.resolve())]
            runtime = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=runtime_log, stderr=subprocess.STDOUT)
            processes.append(runtime); runtime_processes.append(runtime)
            deadline = time.monotonic() + 15
            while True:
                if runtime.poll() is not None: raise RuntimeError("Audited Runtime did not start")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2): break
                except OSError:
                    if time.monotonic() >= deadline: raise TimeoutError("Audited Runtime startup deadline elapsed")
                    time.sleep(.1)
            client_log = (folder / "client-stdout.log").open("w"); logs.append(client_log)
            command = [str(client), "--auto-room", args.room.upper(), "--player-name", f"Agent{index:02}", "--control-mode", "actor",
                "--actor-endpoint", f"http://127.0.0.1:{port}", "--actor-report", str(folder / "client.json"),
                "--social-privacy-report", str(folder / "privacy-unity.json"), "-batchmode", "-nographics",
                "-screen-fullscreen", "0", "-screen-width", "800", "-screen-height", "600", "-logFile", str(folder / "client.log")]
            if args.api_url: command += ["--api-url", args.api_url]
            if args.probe_out_of_sight_kill: command += ["--social-out-of-sight-kill-report", str(folder / "out-of-sight-kill.json")]
            game = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=client_log, stderr=subprocess.STDOUT); processes.append(game)
            launch_rows.append({"participant": index, "runtime_pid": runtime.pid, "client_pid": game.pid, "bridge_port": port,
                "bridge_credential_sha256": hashlib.sha256(credential.encode()).hexdigest(), "agent_profile": profile, "memory_database": str(folder / "memory.sqlite3")})
        write(output / "launch.json", {"agents": launch_rows, "active_out_of_sight_probe": args.probe_out_of_sight_kill})
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            if all(process.poll() is not None for process in runtime_processes): break
            if any(processes[index].poll() is not None and runtime_processes[index // 2].poll() is None for index in (1, 3, 5, 7)):
                raise RuntimeError("An ordinary game client exited before participation completed")
            time.sleep(.2)
        if not all(process.poll() == 0 for process in runtime_processes): raise RuntimeError("Privacy participation did not complete within its bounded deadline")
    except KeyboardInterrupt:
        interrupted = True
    except (RuntimeError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
    finally:
        # Provider/Skill cleanup happens while its ordinary client is still alive.
        for process in runtime_processes + [process for process in processes if process not in runtime_processes]:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=16)
                except subprocess.TimeoutExpired:
                    forced += 1; process.kill(); process.wait(timeout=5)
        for log in logs: log.close()
        write(output / "cleanup.json", {"started": len(processes), "remaining": sum(process.poll() is None for process in processes), "forced_kills": forced})
    result = summarize(output, args.server_summary.resolve() if args.server_summary else None)
    if interrupted:
        result["passed"] = False; write(output / "acceptance.json", result)
    print(json.dumps({"passed": result["passed"], "far_participants_verified_on_both_surfaces": result["far_participants_verified_on_both_surfaces"],
                      "server_postgame_available": result["server_postgame"]["available"], "owned_processes_remaining": result["owned_processes_remaining"]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
