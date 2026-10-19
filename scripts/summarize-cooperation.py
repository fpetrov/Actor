#!/usr/bin/env python3
"""Summarize two-agent diagnostics without publishing retained private content.

Usage: python3 scripts/summarize-cooperation.py build/cooperative-validation/scripted
       --output build/cooperative-validation/scripted/summary.json --require-success

Reads existing reports/SQLite only. Does not launch clients, connect to a Runtime,
read process arguments, or reproduce logs, message bodies, Objective prose or codes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import statistics


ROLES = ("coordinator", "technician")
TOKEN = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")
PRIVATE = {"session_only", "sensitive"}
SECRET_KEYS = {"authorizationcode", "secret", "secrets", "password", "accesstoken",
               "refreshtoken", "apikey", "authtoken", "bridgeauth", "bridgekey"}
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")


def mapping(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return value if isinstance(value, list) else []


def token(value):
    return value if isinstance(value, str) and TOKEN.fullmatch(value) else None


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def count(value):
    return value if type(value) is int and value >= 0 else None


def local_endpoint(value):
    match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5})", value) if isinstance(value, str) else None
    return value if match and 1 <= int(match.group(1)) <= 65535 else None


def values(items):
    return [v for v in (number(item) for item in items) if v is not None and v >= 0]


def distribution(items):
    """p95 uses nearest-rank; no interpolation or fabricated missing samples."""
    samples = sorted(values(items))
    return {"samples": len(samples), "total": sum(samples) if samples else None,
            "mean": statistics.mean(samples) if samples else None,
            "median": statistics.median(samples) if samples else None,
            "p95": samples[math.ceil(len(samples) * .95) - 1] if samples else None,
            "max": samples[-1] if samples else None}


def artifact(path):
    if not path.is_file():
        return {"present": False}
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return {"present": True, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def read_report(path):
    try:
        # An invalid/oversized report is an input error, never an excuse to emit it.
        if path.stat().st_size > 32 * 1024 * 1024:
            return {}, "report_too_large"
        report = json.loads(path.read_text())
        return (report, None) if isinstance(report, dict) else ({}, "report_not_object")
    except FileNotFoundError:
        return {}, "missing"
    except (OSError, UnicodeError, ValueError):
        return {}, "unreadable_or_invalid_json"


def is_redacted(value):
    return value == "[redacted]" or isinstance(value, dict) and value == {"redacted": True}


def inspect_retention(value, result):
    """Structural check only: do not return values or infer arbitrary paraphrases."""
    if isinstance(value, dict):
        policy = value.get("data_policy")
        if isinstance(policy, str) and policy in PRIVATE:
            result["private_policy_nodes"] += 1
            for key in ("output", "details", "value", "content"):
                if key in value:
                    result["private_payload_fields"] += 1
                    result["redacted_private_payload_fields" if is_redacted(value[key])
                           else "unredacted_private_payload_fields"] += 1
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in SECRET_KEYS:
                result["secret_named_fields"] += 1
                result["redacted_secret_named_fields" if is_redacted(item)
                       else "unredacted_secret_named_fields"] += 1
            inspect_retention(item, result)
    elif isinstance(value, list):
        for item in value:
            inspect_retention(item, result)


def memory_metrics(path, role, runtime):
    result = {"artifact": artifact(path), "state": "missing"}
    reported = mapping(runtime.get("memory"))
    result["runtime_counters"] = {key: count(reported.get(key)) for key in (
        "entries", "global_entries", "retrieval_count", "retrieved_entries", "pending_operations")}
    configured = mapping(mapping(runtime.get("cognitive_config")).get("memory")).get("path")
    result["configured_path_matches_local_database"] = (
        Path(configured).expanduser().resolve() == path.resolve() if isinstance(configured, str) else None)
    if not path.is_file():
        return result
    result["wal_present"] = Path(str(path) + "-wal").is_file()
    audit = Counter({key: 0 for key in (
        "private_policy_nodes", "private_payload_fields", "redacted_private_payload_fields",
        "unredacted_private_payload_fields", "secret_named_fields", "redacted_secret_named_fields",
        "unredacted_secret_named_fields", "invalid_content_rows", "oversized_content_rows")})
    try:
        # mode=ro + query_only; a transaction gives a consistent SQLite snapshot,
        # including committed WAL rows. Do not use immutable=1 (would omit WAL).
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            result.update(state="read_only", entries=db.execute("SELECT count(*) FROM memories").fetchone()[0],
                own_profile_entries=db.execute("SELECT count(*) FROM memories WHERE profile=?", (role,)).fetchone()[0],
                profiles=db.execute("SELECT count(DISTINCT profile) FROM memories").fetchone()[0],
                actors=db.execute("SELECT count(DISTINCT actor) FROM memories").fetchone()[0],
                sessions=db.execute("SELECT count(DISTINCT session) FROM memories").fetchone()[0])
            kinds = {"observed_event", "observed_fact", "skill_result", "objective_result"}
            result["entries_by_kind"] = {key: 0 for key in sorted(kinds)}
            result["entries_by_kind"]["other"] = 0
            for kind, amount in db.execute("SELECT kind,count(*) FROM memories GROUP BY kind"):
                result["entries_by_kind"][kind if kind in kinds else "other"] += amount
            # Only inspect bounded content internally. Never select/dump prose,
            # summaries, tags or source IDs; no value escapes this function.
            for length, content in db.execute("SELECT length(content), CASE WHEN length(content)<=1048576 THEN content ELSE NULL END FROM memories"):
                if length is None or length > 1048576:
                    audit["oversized_content_rows"] += 1
                    continue
                try:
                    inspect_retention(json.loads(content), audit)
                except (ValueError, TypeError, RecursionError):
                    audit["invalid_content_rows"] += 1
            db.rollback()
        result["retention_audit"] = dict(audit)
        result["structural_redaction_passed"] = not any(audit[key] for key in (
            "unredacted_private_payload_fields", "unredacted_secret_named_fields",
            "invalid_content_rows", "oversized_content_rows"))
    except sqlite3.Error:
        result["state"] = "sqlite_read_failed"
    return result


def usage(report):
    return {key: count(mapping(report).get(key)) for key in USAGE_KEYS}


def summarize_agent(directory, role):
    runtime, runtime_error = read_report(directory / "runtime.json")
    client, client_error = read_report(directory / "client.json")
    core = mapping(runtime.get("runtime"))
    cognitive = mapping(runtime.get("cognitive"))
    objective = mapping(runtime.get("objective"))
    result = mapping(objective.get("result"))
    sessions = [mapping(item) for item in rows(core.get("sessions"))]
    actor_id = token(client.get("actorId"))
    session_id = token(client.get("lastAcceptedSessionId")) or token(client.get("sessionId"))
    matched = [item for item in sessions if item.get("session_id") == session_id]
    session = matched[0] if len(matched) == 1 else {}
    skills = [mapping(item) for item in rows(cognitive.get("skills"))]
    decisions = [mapping(item) for item in rows(cognitive.get("decisions"))]
    brain = mapping(cognitive.get("brain"))
    adapter = mapping(brain.get("adapter"))
    turns = [mapping(item) for item in rows(brain.get("turns"))]
    statuses = Counter(token(item.get("status")) or "unknown" for item in skills)
    privacy = mapping(session.get("privacy"))
    report = {
        "inputs": {"runtime": artifact(directory / "runtime.json"),
                   "client": artifact(directory / "client.json")},
        "input_errors": {"runtime": runtime_error, "client": client_error},
        "processes": {"runtime_expected": 1, "unity_client_expected": 1,
                      "total_expected": 2, "actual_pids_verified": False,
                      "launcher_and_codex_helper_processes_excluded": True},
        "identity": {"actor_id": actor_id, "session_id": session_id,
                     "local_runtime_endpoint": local_endpoint(core.get("endpoint")),
                     "runtime_session_count": len(sessions),
                     "matching_runtime_sessions": len(matched),
                     "actor_matches_runtime": bool(actor_id and actor_id == session.get("actor_id")),
                     "game_id": token(session.get("game_id")),
                     "game_version": token(session.get("game_version"))},
        "objective": {"passed": objective.get("passed") is True,
                      "status": token(result.get("status")),
                      "duration_seconds": number(result.get("duration_seconds")),
                      "decision_count": count(result.get("decision_count"))},
        "cognition": {"provider": token(brain.get("provider")), "decisions_recorded": len(decisions),
            "context_bytes": distribution(item.get("context_bytes") for item in decisions),
            "decision_duration_seconds": distribution(item.get("duration_seconds") for item in decisions),
            "cleanup_error_count": len(rows(cognitive.get("cleanup_errors"))),
            "pending_cleanup_tasks": count(cognitive.get("pending_cleanup_tasks"))},
        "codex": {"turns_recorded": len(turns), "unique_model_turns": count(adapter.get("unique_model_turns")),
            "interrupted_turns": count(adapter.get("interrupted_turns")),
            "usage_totals": usage(brain.get("usage_totals")),
            "turn_duration_seconds": distribution(item.get("duration_seconds") for item in turns),
            "turns": [{"index": count(item.get("decision_index")), "status": token(item.get("status")),
                       "duration_seconds": number(item.get("duration_seconds")),
                       "usage_last": usage(mapping(item.get("usage")).get("last")),
                       "tool_calls": count(item.get("tool_calls"))} for item in turns]},
        "skills": {"count": len(skills), "statuses": dict(sorted(statuses.items())),
            "failures": sum(item.get("status") in ("failed", "timed_out") for item in skills),
            "cancelled": sum(item.get("status") == "cancelled" for item in skills),
            "duration_seconds": distribution(item.get("duration_seconds") for item in skills),
            "items": [{"skill_id": token(item.get("skill_id")), "status": token(item.get("status")),
                       "duration_seconds": number(item.get("duration_seconds")),
                       "commands_sent": count(item.get("commands_sent")),
                       "data_policy": item.get("data_policy") if isinstance(item.get("data_policy"), str)
                           and item["data_policy"] in PRIVATE | {"public"} else None}
                      for item in skills]},
        "runtime": {"elapsed_seconds": number(core.get("elapsed_seconds")),
            "cpu_seconds": number(core.get("cpu_seconds")),
            "cpu_percent_one_core": number(core.get("cpu_percent_one_core")),
            "commands_sent": count(session.get("commands_sent")),
            "observation_frames": count(session.get("observation_frames")),
            "events_received": count(session.get("events_received")),
            "errors": len(rows(session.get("errors"))),
            "command_result_rtt_ms": distribution(rows(session.get("command_result_rtt_ms"))),
            "privacy": {"private_data_observed": privacy.get("private_data_observed") is True,
                        "registered_secrets": count(privacy.get("registered_secrets")),
                        "redaction_degraded": privacy.get("redaction_degraded") is True}},
        "client": {"state": token(client.get("state")), "mode": token(client.get("mode")),
            "mean_fps": number(client.get("meanFps")), "max_pump_ms": number(client.get("maxPumpMs")),
            "elapsed_seconds": number(client.get("elapsedSeconds")),
            "errors": len(rows(client.get("errors"))), "warnings": len(rows(client.get("warnings"))),
            "messages_received": count(client.get("commReceivedCount")),
            "receipts_server_accepted": count(client.get("receiptsServerAcceptedCount")),
            "receipts_client_received": count(client.get("receiptsClientReceivedCount")),
            "receipts_rejected": count(client.get("receiptsRejectedCount")),
            "mean_message_latency_ms": number(client.get("meanMessageLatencyMs")),
            "diagnostic_results": count(client.get("diagnosticResultsCount")),
            "task_id": token(client.get("calibrationTaskId")),
            "task_status": token(client.get("calibrationStatus")),
            "task_revision": count(client.get("calibrationRevision")),
            "public_task_status": token(client.get("publicCalibrationStatus")),
            "own_assignment_present": client.get("ownCalibrationAssignment") is True},
        "memory": memory_metrics(directory / "memory.sqlite3", role, runtime),
    }
    return report


def summarize(directory):
    agents = {role: summarize_agent(directory / role, role) for role in ROLES}
    pair = list(agents.values())
    paths = [(directory / role / "memory.sqlite3").resolve() for role in ROLES]
    distinct_paths = paths[0] != paths[1]
    distinct_files = all(path.is_file() for path in paths) and not paths[0].samefile(paths[1])
    checks = {
        "both_objectives_passed": all(a["objective"]["passed"] and a["objective"]["status"] == "succeeded" for a in pair),
        "both_authoritative_clients_completed": all(a["client"]["task_status"] == "Completed" for a in pair),
        "same_nonempty_task_id": bool(pair[0]["client"]["task_id"] and pair[0]["client"]["task_id"] == pair[1]["client"]["task_id"]),
        "distinct_actor_ids": bool(pair[0]["identity"]["actor_id"] and pair[1]["identity"]["actor_id"] and pair[0]["identity"]["actor_id"] != pair[1]["identity"]["actor_id"]),
        "distinct_session_ids": bool(pair[0]["identity"]["session_id"] and pair[1]["identity"]["session_id"] and pair[0]["identity"]["session_id"] != pair[1]["identity"]["session_id"]),
        "client_runtime_identities_match": all(a["identity"]["actor_matches_runtime"] for a in pair),
        "distinct_runtime_endpoints": bool(pair[0]["identity"]["local_runtime_endpoint"]
            and pair[1]["identity"]["local_runtime_endpoint"]
            and pair[0]["identity"]["local_runtime_endpoint"] != pair[1]["identity"]["local_runtime_endpoint"]),
        "distinct_database_paths": distinct_paths,
        "distinct_database_files": distinct_files,
        "database_configuration_matches": all(a["memory"]["configured_path_matches_local_database"] is True for a in pair),
        "sqlite_structural_redaction_passed": all(a["memory"].get("structural_redaction_passed") is True for a in pair),
    }
    return {"schema": "actor.cooperation-summary.v1", "passed": all(checks.values()),
            "processes": {"agent_count_expected": 2, "runtime_reports_present": sum(
                a["inputs"]["runtime"]["present"] for a in pair),
                "game_process_count_expected": 4, "actual_pids_verified": False},
            "acceptance": checks, "agents": agents,
            "measurement_notes": [
                "Success requires both Objective passed+succeeded and both authoritative client task statuses Completed; FinishObjective alone is insufficient.",
                "Two expected game processes per agent means one Runtime and one Unity client, excluding launcher/Codex helpers; PIDs are not verified by this offline summarizer.",
                "RTT p95 uses nearest rank over retained command-result samples; FPS is the client report mean, not a measured worst-frame percentile.",
                "Mean message latency uses receiver wall clock minus server Unix time and is approximate across hosts; it is not transport RTT.",
                "ClientReceived means recipient game client ACK, not Runtime consumption or Brain reading. Station requires all actual recipients ACK.",
                "SQLite audit checks explicit private data_policy payload redaction and secret-named fields structurally; it cannot prove absence of arbitrary paraphrased secrets.",
                "SQLite queries use a read-only transaction including committed WAL. Artifact hashes describe files read, not a frozen live-run snapshot; summarize after reports stop for reproducible evidence.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Run directory containing coordinator/ and technician/")
    parser.add_argument("--output", type=Path, help="Write this safe JSON in addition to stdout")
    parser.add_argument("--require-success", action="store_true", help="Exit 1 unless every acceptance check passes")
    args = parser.parse_args()
    report = summarize(args.directory.resolve())
    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        # Never overwrite the raw evidence or a persistent memory database.
        target = args.output.resolve()
        protected = {(args.directory.resolve() / role / name).resolve() for role in ROLES
                     for name in ("runtime.json", "client.json", "memory.sqlite3", "memory.sqlite3-wal", "memory.sqlite3-shm")}
        if target in protected or target.exists() and any(path.is_file() and target.samefile(path) for path in protected):
            parser.error("The output must not replace an input artifact")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded)
    print(encoded, end="")
    return 1 if args.require_success and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
