#!/usr/bin/env python3
"""Read-only safe investigation aggregates; no clients, auth, model or network."""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import re


_SPEC = importlib.util.spec_from_file_location("actor_cooperation_metrics", Path(__file__).with_name("summarize-cooperation.py"))
BASE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(BASE)
ROLES = ("engineer", "sheriff")
STATUSES = {"succeeded", "failed", "cancelled", "timed_out", "pending", "running"}
INCIDENT_STATUSES = {"None", "Active", "Resolved", "Expired", "Cancelled", "Failed", "Unspecified"}
COOLING_STATUSES = {"Operational", "Failure", "Repairing", "Restored", "Unspecified"}
SKILLS = {"actor.wait", "actor.send-message", "actor.wait-for-message", "agents-arena.navigate-to-room",
    "agents-arena.select-role", "agents-arena.wait-for-incident", "agents-arena.inspect-cooling",
    "agents-arena.inspect-security-records", "agents-arena.repair-cooling", "agents-arena.submit-investigation-report",
    "agents-arena.wait-for-incident-resolved", "agents-arena.cancel-investigation", "agents-arena.share-investigation-evidence",
    "agents-arena.maintenance-override"}


def enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else None


def launch_metadata(directory, role):
    value, error = BASE.read_report(directory / "launch.json")
    return value, {"present": error is None,
        "database_matches": isinstance(value.get("memory_database"), str)
            and Path(value["memory_database"]).expanduser().resolve() == (directory / "memory.sqlite3").resolve(),
        "profile_matches": value.get("agent_profile") == role}


def agent(directory, role):
    # Reuse the tested M5 numeric metrics and read-only structural SQLite audit.
    # Only the explicit projection below is published; M5 task/session identifiers
    # and arbitrary token-like strings do not cross this boundary.
    base = BASE.summarize_agent(directory, role)
    runtime, _ = BASE.read_report(directory / "runtime.json")
    client, _ = BASE.read_report(directory / "client.json")
    cognitive = BASE.mapping(runtime.get("cognitive"))
    brain = BASE.mapping(cognitive.get("brain"))
    skills = [BASE.mapping(item) for item in BASE.rows(cognitive.get("skills"))]
    skill_counts = Counter(enum(item.get("skill_id"), SKILLS) or "other" for item in skills)
    status_counts = Counter(enum(item.get("status"), STATUSES) or "unknown" for item in skills)
    launch, launch_checks = launch_metadata(directory, role)
    report = {
        "inputs": base["inputs"], "input_errors": base["input_errors"],
        "identity": {"actor_id": base["identity"]["actor_id"], "role": enum(client.get("crewRole"), {"Engineer", "Sheriff", "Crew"}),
            "runtime_session_count": base["identity"]["runtime_session_count"],
            "matching_runtime_sessions": base["identity"]["matching_runtime_sessions"],
            "actor_matches_runtime": base["identity"]["actor_matches_runtime"]},
        "objective": {**base["objective"], "status": enum(base["objective"]["status"], STATUSES)},
        "cognition": {key: value for key, value in base["cognition"].items() if key != "provider"},
        "provider": enum(brain.get("provider"), {"codex", "scripted", "investigation-scripted"}),
        "codex": {key: value for key, value in base["codex"].items() if key != "turns"},
        "skills": {"count": len(skills), "by_id": dict(sorted(skill_counts.items())),
            "statuses": dict(sorted(status_counts.items())), "failures": base["skills"]["failures"],
            "cancelled": base["skills"]["cancelled"], "duration_seconds": base["skills"]["duration_seconds"],
            "successful_share_evidence_skill_calls": sum(item.get("skill_id") == "agents-arena.share-investigation-evidence"
                and item.get("status") == "succeeded" for item in skills)},
        "runtime": base["runtime"], "memory": base["memory"],
        "client": {key: value for key, value in base["client"].items() if key in {
            "mean_fps", "max_pump_ms", "elapsed_seconds", "errors", "warnings", "messages_received",
            "receipts_server_accepted", "receipts_client_received", "receipts_rejected", "mean_message_latency_ms"}},
        "incident": {"incident_id": BASE.token(client.get("incidentId")),
            "status": enum(client.get("incidentStatus"), INCIDENT_STATUSES),
            "cooling_status": enum(client.get("coolingStatus"), COOLING_STATUSES),
            "report_validated": client.get("investigationReportValidated") is True,
            "own_diagnostic_present": client.get("ownDiagnosticPresent") is True,
            "own_security_present": client.get("ownSecurityPresent") is True,
            "diagnostic_evidence_count": BASE.count(client.get("diagnosticEvidenceCount")),
            "security_evidence_count": BASE.count(client.get("securityEvidenceCount")),
            "repairs_completed": BASE.count(client.get("repairCompletedCount")),
            "reports_validated": BASE.count(client.get("reportsValidatedCount")),
            "reports_rejected": BASE.count(client.get("reportsRejectedCount")),
            "incidents_started": BASE.count(client.get("incidentsStartedCount")),
            "hypotheses_proposed_by_scripted_provider": BASE.count(brain.get("hypotheses_proposed"))},
        "launch_metadata": launch_checks,
    }
    # Return private validation inputs separately; callers never serialize them.
    return report, base["identity"], launch


def summarize(directory, case_label=None):
    directory = Path(directory)
    collected = [agent(directory / role, role) for role in ROLES]
    agents = {role: collected[index][0] for index, role in enumerate(ROLES)}
    pair = list(agents.values()); identities = [item[1] for item in collected]; launches = [item[2] for item in collected]
    paths = [(directory / role / "memory.sqlite3").resolve() for role in ROLES]
    endpoints = [item["local_runtime_endpoint"] for item in identities]
    sessions = [item["session_id"] for item in identities]
    runtime_pids = [BASE.count(item.get("runtime_pid")) for item in launches]
    client_pids = [BASE.count(item.get("client_pid")) for item in launches]
    fingerprints = [item.get("bridge_credential_sha256") for item in launches]
    checks = {
        "both_objectives_passed": all(a["objective"]["passed"] and a["objective"]["status"] == "succeeded" for a in pair),
        "both_incidents_resolved": all(a["incident"]["status"] == "Resolved" for a in pair),
        "both_cooling_restored": all(a["incident"]["cooling_status"] == "Restored" for a in pair),
        "both_reports_validated": all(a["incident"]["report_validated"] for a in pair),
        "same_nonempty_incident_id": bool(pair[0]["incident"]["incident_id"] and pair[0]["incident"]["incident_id"] == pair[1]["incident"]["incident_id"]),
        "expected_distinct_duties": all(agents[role]["identity"]["role"] == role.title() for role in ROLES),
        "distinct_actor_ids": bool(pair[0]["identity"]["actor_id"] and pair[1]["identity"]["actor_id"] and pair[0]["identity"]["actor_id"] != pair[1]["identity"]["actor_id"]),
        "distinct_accepted_session_ids": bool(all(sessions) and sessions[0] != sessions[1]),
        "client_runtime_identities_match": all(item["actor_matches_runtime"] and item["matching_runtime_sessions"] == 1 for item in identities),
        "distinct_runtime_endpoints": bool(all(endpoints) and endpoints[0] != endpoints[1]),
        "distinct_database_paths": paths[0] != paths[1],
        "distinct_database_files": all(path.is_file() for path in paths) and not paths[0].samefile(paths[1]),
        "database_configuration_matches": all(a["memory"]["configured_path_matches_local_database"] is True for a in pair),
        "isolated_own_memory_profiles": all(a["memory"].get("profiles") == 1 and a["memory"].get("actors") == 1
            and a["memory"].get("entries", 0) > 0 and a["memory"].get("own_profile_entries") == a["memory"].get("entries") for a in pair),
        "sqlite_structural_redaction_passed": all(a["memory"].get("structural_redaction_passed") is True for a in pair),
        "role_private_evidence_separation": (agents["engineer"]["incident"]["diagnostic_evidence_count"] is not None
            and agents["engineer"]["incident"]["diagnostic_evidence_count"] > 0
            and agents["engineer"]["incident"]["security_evidence_count"] == 0
            and agents["sheriff"]["incident"]["security_evidence_count"] is not None
            and agents["sheriff"]["incident"]["security_evidence_count"] > 0
            and agents["sheriff"]["incident"]["diagnostic_evidence_count"] == 0),
        "engineer_repair_confirmed": bool((agents["engineer"]["incident"]["repairs_completed"] or 0) > 0),
        "sheriff_report_confirmed": bool((agents["sheriff"]["incident"]["reports_validated"] or 0) > 0),
        "recorded_four_distinct_processes": all(type(value) is int and value > 0 for value in (*runtime_pids, *client_pids))
            and len(set((*runtime_pids, *client_pids))) == 4,
        "recorded_distinct_bridge_credentials": all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in fingerprints)
            and fingerprints[0] != fingerprints[1],
        "launch_database_and_profiles_match": all(a["launch_metadata"]["present"] and a["launch_metadata"]["database_matches"]
            and a["launch_metadata"]["profile_matches"] for a in pair),
        "launch_ports_match_runtime_endpoints": all(type(item.get("bridge_port")) is int and endpoint == f"127.0.0.1:{item['bridge_port']}"
            for item, endpoint in zip(launches, endpoints)),
    }
    return {"schema": "actor.investigation-summary.v1", "passed": all(checks.values()),
        "validation_case": case_label, "acceptance": checks, "agents": agents,
        "processes": {"agents_expected": 2, "runtime_and_unity_processes_expected": 4,
            "recorded_launch_metadata_checked": True, "actual_live_pids_verified": False},
        "measurement_notes": [
            "Success requires both Objectives passed+succeeded and matching public Resolved/Restored/report-validated state; FinishObjective alone is insufficient.",
            "Recorded launch PIDs, ports, credential fingerprints and database paths are compared privately; no live process identity is inspected and those values are omitted.",
            "Evidence counters describe authorized client receipts; private payloads, reference IDs, hypotheses and messages are never reproduced.",
            "Successful share-evidence Skill calls and client ACK counts are separate metrics; neither claims a Brain understood a message or an exact reference count.",
            "SQLite audit checks explicit private-policy/secret-key structure, not arbitrary paraphrases. Read after reports stop for reproducible artifact hashes.",
            "RTT p95 is nearest-rank over retained samples; FPS is client mean; cross-host message latency is approximate wall-clock difference.",
            "The optional case label is supplied only to this offline validator and is never forwarded to a Brain.",
        ]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Run directory with engineer/ and sheriff/")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case-label", help="Public validation label only; never sent to agents")
    parser.add_argument("--require-success", action="store_true")
    args = parser.parse_args(argv)
    if args.case_label is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.case_label):
        parser.error("Case label must be a short public identifier")
    directory = args.directory.resolve()
    report = summarize(directory, args.case_label)
    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        target = args.output.resolve()
        protected = {(directory / role / name).resolve() for role in ROLES for name in (
            "runtime.json", "client.json", "launch.json", "memory.sqlite3", "memory.sqlite3-wal", "memory.sqlite3-shm")}
        if target in protected or target.exists() and any(path.is_file() and target.samefile(path) for path in protected):
            parser.error("Output must not overwrite a raw input or private launch artifact")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded)
    print(encoded, end="")
    return int(args.require_success and not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
