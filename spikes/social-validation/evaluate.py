#!/usr/bin/env python3
"""Check independently recorded postgame artifacts and publish whitelisted counts.

This is artifact consistency, not signed attestation against a malicious local
writer. No game/model launches; no prompts, chat, poses, contexts or reasoning.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re

SCHEMA = "actor.social-match-evaluation.v1"
IDENTITY = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")
PROVIDERS = {"social-scripted", "codex", "codex-threat-audit", "social-fixture-vote", "social-fixture-parity",
    "social-fixture-sabotage", "social-fixture-tasks", "social-privacy-fixture"}
ISOLATION = {"three_distinct_owned_pids_per_agent", "launcher_identities_match", "distinct_bridge_ports",
    "independent_bridge_credentials", "distinct_memory_databases", "distinct_agent_profiles", "match_operation"}
REPLAY_KINDS = {"match_started", "phase", "kill", "meeting", "vote", "ejection", "sabotage", "repair", "task", "disconnect", "match_ended"}
SIDES = {"SOCIAL_ALIGNMENT_CREW", "SOCIAL_ALIGNMENT_IMPOSTOR", "SOCIAL_ALIGNMENT_UNSPECIFIED"}
LIVES = {"SOCIAL_LIFE_ALIVE", "SOCIAL_LIFE_DEAD", "SOCIAL_LIFE_EJECTED", "SOCIAL_LIFE_DISCONNECTED"}
WINNERS = {"parity": "SOCIAL_ALIGNMENT_IMPOSTOR", "impostors_eliminated": "SOCIAL_ALIGNMENT_CREW",
    "sabotage_expired": "SOCIAL_ALIGNMENT_IMPOSTOR", "tasks_completed": "SOCIAL_ALIGNMENT_CREW",
    "match_timeout": "SOCIAL_ALIGNMENT_UNSPECIFIED", "waiting_timeout": "SOCIAL_ALIGNMENT_UNSPECIFIED",
    "all_players_lost": "SOCIAL_ALIGNMENT_UNSPECIFIED"}
SKILLS = {"[private_activity]", "actor.wait", "actor.send-message", "actor.wait-for-message", "actor.remember-hypothesis"} | {
    "agents-arena." + name for name in ("navigate-social-room", "ready-for-match", "perform-task", "fake-task",
    "kill-visible-actor", "report-body", "call-meeting", "cast-vote", "sabotage", "repair-emergency",
    "approach-visible-actor", "evade-threat", "wait-for-match-event")}
CODEX_PROVIDERS = {"codex", "codex-threat-audit"}
THREAT_REACTIONS = {"agents-arena.report-body", "agents-arena.evade-threat",
    "agents-arena.call-meeting", "actor.send-message", "agents-arena.cast-vote"}


def read(path):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("oversized report")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("invalid report")
    return value


def count(value):
    # Official protobuf JSON uses decimal strings for uint64/int64. No floats,
    # bools, signs or lossy int(1.9) conversion can manufacture a positive count.
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]{0,12}", value):
        number = int(value)
    else:
        raise ValueError("invalid count")
    if not 0 <= number <= 10**12:
        raise ValueError("invalid count")
    return number


def array(value, limit):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(x, dict) for x in value):
        raise ValueError("invalid bounded records")
    return value


def token(value):
    return isinstance(value, str) and IDENTITY.fullmatch(value) is not None


def side(value):
    return {"Crew": "SOCIAL_ALIGNMENT_CREW", "Impostor": "SOCIAL_ALIGNMENT_IMPOSTOR", "Unspecified": "SOCIAL_ALIGNMENT_UNSPECIFIED"}.get(
        value, value if value in SIDES else None)


def terminal_identity(runtime, client, proof, summary):
    result = runtime.get("match", {}).get("result", {}); outcome = result.get("outcome", {})
    actor, accepted, match = proof.get("actorId"), proof.get("acceptedSessionId"), proof.get("socialMatchId")
    matched = [s for s in array(runtime.get("runtime", {}).get("sessions", []), 256) if s.get("session_id") == accepted]
    # Normal despawn may clear current phase fields. It cannot replace the
    # frozen client proof or the retained last accepted Runtime identity.
    current_match = client.get("socialMatchId")
    current_agrees = not current_match or (current_match == match and client.get("socialPhase") == "GameOver"
        and client.get("gameMode") == "SocialDeduction" and client.get("socialSummaryPresent") is True
        and side(client.get("socialWinningSide")) == side(summary.get("winning_side"))
        and client.get("socialWinReason") == summary.get("win_reason"))
    return (runtime.get("match", {}).get("passed") is True and result.get("status") == "completed"
        and result.get("authoritative_terminal") is True and result.get("scope_id") == match
        and outcome.get("match_id") == match == summary.get("match_id")
        and proof.get("schema") == "actor.social-client-completion.v1"
        and all(token(value) for value in (actor, accepted, match))
        and proof.get("socialPhase") == "GameOver" and type(proof.get("socialPhaseRevision")) is int
        and proof["socialPhaseRevision"] > 0 and outcome.get("phase_revision") == proof["socialPhaseRevision"]
        and proof.get("socialSummaryPresent") is True
        and proof.get("runtimeAuthoritativeTerminal") is True and proof.get("runtimeMatchPassed") is True
        and side(proof.get("socialWinningSide")) == side(summary.get("winning_side")) is not None
        and side(outcome.get("winning_side")) == side(summary.get("winning_side"))
        and proof.get("socialWinReason") == outcome.get("win_reason") == summary.get("win_reason")
        and len(matched) == 1 and matched[0].get("actor_id") == actor
        and client.get("actorId") == actor and client.get("lastAcceptedSessionId") == accepted and current_agrees)


def process_proof(folder, names, launches):
    valid = True
    for filename, cleaned in (("processes.json", False), ("processes-after-cleanup.json", True)):
        snapshot = read(folder / filename); rows = array(snapshot.get("agents", []), 6)
        valid &= snapshot.get("schema") == "actor.social-processes.v1" and len(rows) == len(names)
        valid &= {row.get("agent") for row in rows} == set(names)
        for name, launch in zip(names, launches):
            selected = [row for row in rows if row.get("agent") == name]
            valid &= len(selected) == 1
            if len(selected) != 1:
                continue
            row = selected[0]
            valid &= row.get("launcher_identity_matches") is True
            valid &= all(row.get(key) == launch.get(key) for key in ("launcher_pid", "runtime_pid", "client_pid", "bridge_port"))
            if cleaned:
                valid &= row.get("runtime_alive_after_cleanup") is False and row.get("client_alive_after_cleanup") is False
    return bool(valid)


def threat_proof(runtime, own):
    """A returned candidate is not proof that the Runner dispatched a Skill.

    Audit rows contain no arguments/identifiers. Exact matching happened in RAM
    before/after the actual Codex call; this joins the resulting booleans with
    production accepted decisions and ordered actual Skill results postgame.
    """
    audit = runtime.get("match", {}).get("result", {}).get("outcome", {}).get("threat_audit", {})
    episodes = array(runtime.get("cognitive", {}).get("episodes", []), 256)
    rows = array(audit.get("records", []), 512)
    valid = (audit.get("schema") == "actor.social-permitted-threat-audit.v1"
        and audit.get("postgame_own_crew_verified") is True and audit.get("bounded") is True
        and own.get("alignment") == "SOCIAL_ALIGNMENT_CREW"
        and count(audit.get("decisions_observed", 0)) == len(rows)
        and count(audit.get("episodes", 0)) == len(episodes) > 0)
    candidates = dispatched = succeeded = confirmed = completed = fresh = communication = votes = 0
    keys = set()
    for row in rows:
        episode, index, revision = (count(row.get(key, 0)) for key in ("episode", "decision_index", "cognitive_revision"))
        key = (episode, index, revision)
        valid &= key not in keys and 1 <= episode <= len(episodes) and index > 0
        keys.add(key)
        source = (row.get("coherent_context") is True and row.get("own_crew_alive_context") is True
            and row.get("fresh_threat_before_decide") is True and
            (count(row.get("fresh_attack_count", 0)) > 0 or count(row.get("fresh_visible_body_count", 0)) > 0))
        fresh += bool(source)
        selected = (source and row.get("candidate_returned") is True and
            row.get("returned_skill_id") in THREAT_REACTIONS and row.get("target_matches_permitted_fact") is True)
        candidate = (row.get("candidate_returned") is True and row.get("coherent_context") is True
            and row.get("own_crew_alive_context") is True and row.get("returned_skill_id") in THREAT_REACTIONS)
        if not candidate or not 1 <= episode <= len(episodes):
            continue
        candidates += bool(selected)
        report = episodes[episode - 1]
        calls = [decision for decision in array(report.get("decisions", []), 4096)
                 if decision.get("status") == "accepted" and isinstance(decision.get("skill_id"), str)]
        results = array(report.get("result", {}).get("skill_results", []), 4096)
        # If positional association cannot be proven, do not guess from a later
        # unrelated result with the same Skill ID.
        if len(calls) != len(results):
            continue
        matched = [(call, result) for call, result in zip(calls, results)
            if count(call.get("index", 0)) == index and count(call.get("cognitive_revision", 0)) == revision
            and call.get("skill_id") == result.get("skill_id")
            and call.get("skill_id") in (row["returned_skill_id"], "[private_activity]")]
        if len(matched) == 1:
            local_success = matched[0][1].get("status") == "succeeded"
            communication += (local_success and row["returned_skill_id"] == "actor.send-message"
                and row.get("in_discussion_context") is True and row.get("discussion_channel_selected") is True)
            votes += local_success and row["returned_skill_id"] == "agents-arena.cast-vote"
            if not selected:
                continue
            dispatched += 1
            authoritative = (row.get("postgame_action_confirmed") is True and row["returned_skill_id"] in (
                "agents-arena.report-body", "agents-arena.call-meeting")
                and matched[0][1].get("status") in ("succeeded", "cancelled"))
            succeeded += local_success
            confirmed += authoritative
            completed += local_success or authoritative
    return {"valid": bool(valid), "fresh_threat_decisions": fresh,
        "selected_reactions": candidates, "dispatched_reactions": dispatched, "succeeded_reactions": succeeded,
        "server_confirmed_reactions": confirmed, "completed_reactions": completed,
        "discussion_communications_succeeded": communication, "votes_succeeded": votes,
        "full_participation_passed": bool(valid and fresh > 0 and completed > 0 and communication > 0 and votes > 0),
        "passed": bool(valid and fresh > 0 and completed > 0),
        "scope": "permitted threat preceded an actual Codex selection and dispatched Skill, completed locally or by exact own postgame phase-action proof; not proof of internal motivation or general intelligence"}


def evaluate(folder, expected_reason=None, *, require_codex_both_sides=False, require_crew_communication_vote=False):
    try:
        return _evaluate(Path(folder), expected_reason, require_codex_both_sides, require_crew_communication_vote)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Never expose raw errors, report content or environment paths.
        return {"schema": SCHEMA, "passed": False, "checks": {"input_reports_valid": False}, "error_code": "invalid_or_missing_evidence"}


def _evaluate(folder, expected_reason, require_codex_both_sides, require_crew_communication_vote=False):
    supervisor = read(folder / "supervisor.json")
    checks = {"supervisor_completed": supervisor.get("schema") == "actor.social-supervisor.v1"
            and supervisor.get("passed") is True and supervisor.get("status") == "authoritative_match_completed",
        "isolated_processes": ISOLATION.issubset(supervisor.get("process_isolation", {}))
            and all(supervisor["process_isolation"][key] is True for key in ISOLATION),
        "owned_process_cleanup": supervisor.get("owned_process_cleanup_complete") is True,
        "client_runtime_terminal_agreement": supervisor.get("authoritative_terminal_agreement") is True}
    outputs = sorted(p for p in folder.glob("agent-*") if p.is_dir()); names = [p.name for p in outputs]
    checks["all_participants_present"] = (type(supervisor.get("players")) is int
        and len(outputs) == supervisor.get("players") == supervisor.get("completed_own_reports") and 4 <= len(outputs) <= 6
        and names == [f"agent-{i + 1:02d}" for i in range(len(outputs))])
    if not checks["all_participants_present"]:
        return {"schema": SCHEMA, "passed": False, "checks": checks}
    reports = [(read(p / "runtime.json"), read(p / "client.json"), read(p / "launch.json"), read(p / "client-social-completed.json")) for p in outputs]
    launches = [r[2] for r in reports]
    pids = [launch.get(k) for launch in launches for k in ("launcher_pid", "runtime_pid", "client_pid")]
    ports = [launch.get("bridge_port") for launch in launches]
    checks["recorded_process_identities"] = process_proof(folder, names, launches)
    checks["independent_launches"] = (all(type(p) is int and p > 0 for p in pids) and len(set(pids)) == len(pids)
        and all(type(p) is int and 1024 <= p <= 65535 for p in ports) and len(set(ports)) == len(ports)
        and all(launch.get("operation") == "match" and launch.get("brain") in PROVIDERS
            and type(launch.get("headless_client")) is bool and launch["headless_client"] is supervisor.get("headless_clients") for launch in launches))
    for key in ("memory_database", "agent_profile", "bridge_credential_sha256"):
        values = [launch.get(key) for launch in launches]
        valid = all(isinstance(v, str) and 0 < len(v) <= 2048 for v in values)
        if key == "bridge_credential_sha256":
            valid &= all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) is not None for v in values)
        checks["independent_" + key] = valid and len(set(values)) == len(values)
    summaries, participants, actors, sessions = [], [], [], []
    for name, (runtime, client, launch, proof) in zip(names, reports):
        result = runtime.get("match", {}).get("result", {}); summary = result.get("outcome", {}).get("authoritative_summary", {})
        summaries.append(summary); checks[name + "_terminal_identity"] = terminal_identity(runtime, client, proof, summary)
        actor = proof.get("actorId"); actors.append(actor); sessions.append(proof.get("acceptedSessionId"))
        own_rows = [p for p in array(summary.get("players", []), 6) if p.get("actor_id") == actor]
        checks[name + "_postgame_identity"] = len(own_rows) == 1; own = own_rows[0] if len(own_rows) == 1 else {}
        cognition = runtime.get("cognitive", {}); episodes = array(cognition.get("episodes", []), 256)
        turns, usage, tools, skills, latencies = 0, Counter(), 0, Counter(), []; provider_valid = bool(episodes)
        for episode in episodes:
            brain = episode.get("brain", {})
            if launch.get("brain") in CODEX_PROVIDERS:
                adapter = brain.get("adapter") or {}
                provider_valid &= (brain.get("provider") == "codex" and "unique_model_turns" in adapter
                    and adapter.get("sdk_version") == "0.162.1" and adapter.get("sandbox") == "read-only"
                    and adapter.get("ephemeral") is True and adapter.get("closed") is True and adapter.get("decision_active") is False)
                turns += count(adapter.get("unique_model_turns", 0))
            else:
                provider_valid &= brain.get("provider") == launch.get("brain") and brain.get("closed") is True
            for key, value in brain.get("usage_totals", {}).items():
                if key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"):
                    usage[key] += count(value)
            for turn in array(brain.get("turns", []), 4096):
                tools += count(turn.get("tool_calls", 0)); seconds = turn.get("duration_seconds")
                if type(seconds) in (int, float) and math.isfinite(seconds) and 0 <= seconds <= 14400:
                    latencies.append(seconds)
            for item in array(episode.get("result", {}).get("skill_results", []), 4096):
                identifier = item.get("skill_id"); skills[identifier if identifier in SKILLS else "other"] += 1
        checks[name + "_no_cleanup_errors"] = cognition.get("cleanup_errors") == [] and all(
            e.get("cleanup_errors") == [] and e.get("pending_cleanup_tasks", 0) == 0 for e in episodes)
        checks[name + "_no_direct_model_tools"] = tools == 0
        checks[name + ("_official_model_turns" if launch.get("brain") in CODEX_PROVIDERS else "_provider_identity")] = provider_valid and (turns > 0 if launch.get("brain") in CODEX_PROVIDERS else True)
        threat = threat_proof(runtime, own) if launch.get("brain") == "codex-threat-audit" else None
        if threat is not None:
            checks[name + "_permitted_crew_threat_reaction"] = threat["passed"]
        life = own.get("life") if own.get("life") in LIVES else None
        alignment = own.get("alignment") if own.get("alignment") in SIDES else None
        checks[name + "_postgame_fields"] = life is not None and alignment in ("SOCIAL_ALIGNMENT_CREW", "SOCIAL_ALIGNMENT_IMPOSTOR")
        fps = client.get("meanFps")
        participants.append({"participant": name, "brain": launch.get("brain") if launch.get("brain") in PROVIDERS else "unknown",
            "alignment_disclosed_postgame": alignment, "life": life, "survival_ms": count(own.get("survival_ms", 0)),
            "team_won": own.get("team_won") is True, "survived": own.get("survived") is True,
            "episodes": count(result.get("episodes", 0)), "decisions": count(result.get("decisions", 0)),
            "codex_turns": turns, "codex_usage": dict(usage), "model_tool_calls": tools, "skills": dict(skills),
            "turn_latency_seconds": {"count": len(latencies), "mean": sum(latencies) / len(latencies) if latencies else 0, "max": max(latencies, default=0)},
            "headless_loop_rate": fps if type(fps) in (int, float) and math.isfinite(fps) and fps >= 0 else None,
            "grpc_bytes_sent": count(client.get("bytesSent", 0)), "grpc_bytes_received": count(client.get("bytesReceived", 0)),
            "delivered_messages_received": count(client.get("commReceivedCount", 0)),
            **({"permitted_threat_reaction": threat} if threat is not None else {})})
    summary = summaries[0]; players = array(summary.get("players", []), 6)
    checks["distinct_actor_sessions"] = len(set(actors)) == len(actors) and len(set(sessions)) == len(sessions)
    checks["identical_authoritative_summaries"] = bool(summary) and all(s == summary for s in summaries)
    checks["composition"] = (summary.get("mode") == "SocialDeduction" and len(players) == len(reports)
        and len({p.get("actor_id") for p in players}) == len(players) and {p.get("actor_id") for p in players} == set(actors)
        and sum(p.get("alignment") == "SOCIAL_ALIGNMENT_IMPOSTOR" for p in players) == 1)
    reason = summary.get("win_reason"); winner = side(summary.get("winning_side"))
    checks["known_terminal_outcome"] = reason in WINNERS and winner == WINNERS.get(reason)
    if expected_reason is not None:
        checks["expected_authoritative_reason"] = reason == expected_reason
    rows = array(summary.get("replay", []), 256); kinds = [r.get("kind") for r in rows]; seq = [count(r.get("sequence", 0)) for r in rows]
    ended = [i for i, row in enumerate(rows) if row.get("kind") == "match_ended"]
    checks["bounded_ordered_terminal_replay"] = (bool(rows) and all(k in REPLAY_KINDS for k in kinds)
        and all(a < b for a, b in zip([0] + seq, seq)) and len(ended) == 1
        and rows[ended[0]].get("outcome") == reason and all(
            row.get("kind") == "phase" and row.get("outcome") == "GameOver" for row in rows[ended[0] + 1:]))
    replay = Counter(k for k in kinds if k in REPLAY_KINDS)
    counters = {key: count(summary.get(key, 0)) for key in ("kills", "body_reports", "meetings", "votes", "crew_tasks_completed", "sabotages")}
    required = {"parity": (("kill", "kills", 2),), "impostors_eliminated": (("ejection", None, 1), ("meeting", "meetings", 1), ("vote", "votes", 1)),
        "sabotage_expired": (("sabotage", "sabotages", 1),), "tasks_completed": (("task", "crew_tasks_completed", 1),)}
    if reason in required:
        checks["required_real_gameplay"] = all(replay[kind] >= n and (counter is None or counters[counter] >= n) for kind, counter, n in required[reason])
    model_sides = {p["alignment_disclosed_postgame"] for p in participants if p["brain"] in CODEX_PROVIDERS and p["codex_turns"] > 0}
    both = {"SOCIAL_ALIGNMENT_CREW", "SOCIAL_ALIGNMENT_IMPOSTOR"}.issubset(model_sides)
    if require_codex_both_sides:
        checks["required_real_codex_on_both_sides"] = both
    if require_crew_communication_vote:
        audited = [p.get("permitted_threat_reaction", {}) for p in participants if p["brain"] == "codex-threat-audit"]
        checks["required_crew_communication_and_vote"] = len(audited) == 1 and audited[0].get("full_participation_passed") is True
    return {"schema": SCHEMA, "passed": all(checks.values()), "checks": checks,
        "scope": "consistent dedicated-server postgame summaries, ordinary client terminal proofs and independent Runtime reports",
        "headless_clients": supervisor.get("headless_clients") is True, "winner": winner, "reason": reason if reason in WINNERS else "unknown",
        "duration_ms": count(summary.get("duration_ms", 0)), "gameplay_counts": counters, "replay_kind_counts": dict(replay), "participants": participants,
        "real_codex_on_both_sides": both, "display_fps_measured": False, "privacy": "postgame outcomes and whitelisted aggregate counters only"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-reason", choices=sorted(WINNERS)); parser.add_argument("--require-codex-both-sides", action="store_true")
    parser.add_argument("--require-crew-communication-vote", action="store_true")
    args = parser.parse_args(); value = evaluate(args.input, args.expected_reason,
        require_codex_both_sides=args.require_codex_both_sides, require_crew_communication_vote=args.require_crew_communication_vote)
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"passed": value["passed"], "reason": value.get("reason"), "checks": value["checks"]}))
    return 0 if value["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
