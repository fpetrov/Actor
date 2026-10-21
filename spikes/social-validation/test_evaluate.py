"""Offline evaluator rejection cases; no Unity, network, SDK or model calls."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("social_evaluate", Path(__file__).with_name("evaluate.py"))
EVALUATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATOR)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.match = "a" * 32
        self.summary = {"match_id": self.match, "mode": "SocialDeduction", "winning_side": "SOCIAL_ALIGNMENT_IMPOSTOR",
            "win_reason": "parity", "duration_ms": "12345", "kills": 2,
            "players": [{"actor_id": f"actor-{i}", "display_name": f"Player {i}",
                "alignment": "SOCIAL_ALIGNMENT_IMPOSTOR" if i == 0 else "SOCIAL_ALIGNMENT_CREW",
                "life": "SOCIAL_LIFE_ALIVE" if i < 2 else "SOCIAL_LIFE_DEAD", "survival_ms": "12345",
                "team_won": i == 0, "survived": i < 2} for i in range(4)],
            "replay": [{"sequence": str(i + 1), "kind": kind, "outcome": outcome} for i, (kind, outcome) in enumerate(
                (("match_started", "assigned"), ("kill", "dead"), ("kill", "dead"), ("match_ended", "parity"), ("phase", "GameOver")))]}
        self.write("supervisor.json", {"schema": "actor.social-supervisor.v1", "passed": True,
            "status": "authoritative_match_completed", "players": 4, "completed_own_reports": 4,
            "process_isolation": {key: True for key in EVALUATOR.ISOLATION}, "owned_process_cleanup_complete": True,
            "authoritative_terminal_agreement": True, "headless_clients": True})
        before, after = [], []
        for i in range(4):
            name = f"agent-{i + 1:02d}"; actor = f"actor-{i}"; session = f"session-{i}"
            self.write(name + "/runtime.json", {"match": {"passed": True, "result": {"status": "completed",
                "authoritative_terminal": True, "scope_id": self.match, "episodes": 1, "decisions": 2,
                "outcome": {"match_id": self.match, "phase_revision": 8, "winning_side": "SOCIAL_ALIGNMENT_IMPOSTOR", "win_reason": "parity",
                    "authoritative_summary": copy.deepcopy(self.summary)}}},
                "runtime": {"sessions": [{"session_id": session, "actor_id": actor}]},
                "cognitive": {"cleanup_errors": [], "episodes": [{"brain": {"provider": "social-fixture-parity", "closed": True},
                    "cleanup_errors": [], "pending_cleanup_tasks": 0,
                    "result": {"skill_results": [{"skill_id": "[private_activity]", "status": "succeeded"}]}}]}})
            self.write(name + "/client.json", {"actorId": actor, "lastAcceptedSessionId": session,
                "socialMatchId": self.match, "gameMode": "SocialDeduction", "socialPhase": "GameOver",
                "socialWinningSide": "Impostor", "socialWinReason": "parity", "socialSummaryPresent": True,
                "meanFps": 60, "bytesSent": 200, "bytesReceived": 100, "commReceivedCount": 1})
            self.write(name + "/client-social-completed.json", {"schema": "actor.social-client-completion.v1",
                "actorId": actor, "acceptedSessionId": session, "socialMatchId": self.match, "socialPhase": "GameOver",
                "socialPhaseRevision": 8, "socialSummaryPresent": True, "runtimeAuthoritativeTerminal": True,
                "runtimeMatchPassed": True, "socialWinningSide": "Impostor", "socialWinReason": "parity"})
            launch = {"launcher_pid": 100 + i * 3, "runtime_pid": 101 + i * 3, "client_pid": 102 + i * 3,
                "bridge_port": 50051 + i, "brain": "social-fixture-parity", "operation": "match", "headless_client": True,
                "memory_database": f"/local/{name}/memory.sqlite3", "agent_profile": name,
                "bridge_credential_sha256": str(i + 1) * 64}
            self.write(name + "/launch.json", launch)
            row = {"agent": name, "launcher_identity_matches": True, **{k: launch[k] for k in
                ("launcher_pid", "runtime_pid", "client_pid", "bridge_port")}}
            before.append(row); after.append(dict(row, runtime_alive_after_cleanup=False, client_alive_after_cleanup=False))
        self.write("processes.json", {"schema": "actor.social-processes.v1", "agents": before})
        self.write("processes-after-cleanup.json", {"schema": "actor.social-processes.v1", "agents": after})

    def read(self, name):
        return json.loads((self.folder / name).read_text())

    def write(self, name, value):
        path = self.folder / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def change(self, name, update):
        value = self.read(name); update(value); self.write(name, value)

    def evaluate(self, **options):
        return EVALUATOR.evaluate(self.folder, **options)

    def assert_rejected(self):
        self.assertIs(self.evaluate()["passed"], False)

    def change_summary(self, update):
        for i in range(4):
            self.change(f"agent-{i + 1:02d}/runtime.json", lambda value: update(value["match"]["result"]["outcome"]["authoritative_summary"]))

    def codex(self, i):
        name = f"agent-{i + 1:02d}"
        self.change(name + "/launch.json", lambda value: value.update(brain="codex"))
        self.change(name + "/runtime.json", lambda value: value["cognitive"]["episodes"][0].update(brain={
            "provider": "codex", "decision_count": 99,
            "adapter": {"unique_model_turns": 2, "sdk_version": "0.162.1", "sandbox": "read-only", "ephemeral": True,
                "closed": True, "decision_active": False},
            "usage_totals": {"input_tokens": 30, "output_tokens": 4, "total_tokens": 34},
            "turns": [{"duration_seconds": 1.5, "tool_calls": 0}]}))

    def threat_codex(self):
        self.codex(1)  # A fixed postgame Crew in this synthetic offline fixture.
        self.change("agent-02/launch.json", lambda value: value.update(brain="codex-threat-audit"))
        row = {"episode": 1, "decision_index": 1, "cognitive_revision": 3, "coherent_context": True,
            "own_crew_alive_context": True, "fresh_threat_before_decide": True, "fresh_attack_count": 1,
            "fresh_visible_body_count": 0, "historical_attack_count": 0, "candidate_returned": True,
            "returned_skill_id": "agents-arena.report-body", "target_matches_permitted_fact": True}
        self.change("agent-02/runtime.json", lambda value: value["match"]["result"]["outcome"].update(threat_audit={
            "schema": "actor.social-permitted-threat-audit.v1", "postgame_own_crew_verified": True,
            "bounded": True, "decisions_observed": 1, "episodes": 1, "records": [row]}))
        self.change("agent-02/runtime.json", lambda value: value["cognitive"]["episodes"][0].update(
            decisions=[{"index": 1, "cognitive_revision": 3, "status": "accepted", "skill_id": "agents-arena.report-body"}],
            result={"skill_results": [{"skill_id": "agents-arena.report-body", "status": "succeeded"}]}))

    def test_audit_alias_requires_original_official_provider_and_completed_reaction(self):
        self.threat_codex()
        result = self.evaluate()
        self.assertTrue(result["passed"], result["checks"])
        self.assertEqual(result["participants"][1]["codex_turns"], 2)
        self.assertEqual(result["participants"][1]["permitted_threat_reaction"]["succeeded_reactions"], 1)
        self.change("agent-02/runtime.json", lambda value: value["cognitive"]["episodes"][0]["brain"].update(provider="codex-threat-audit"))
        self.assert_rejected()

    def test_audit_alias_cannot_fall_back_to_decision_count_or_fake_adapter(self):
        self.threat_codex()
        original = self.read("agent-02/runtime.json")
        for key, bad in (("unique_model_turns", 0), ("sdk_version", "fake"), ("sandbox", "workspace-write"),
                         ("ephemeral", False), ("closed", False), ("decision_active", True)):
            with self.subTest(key=key):
                value = copy.deepcopy(original)
                value["cognitive"]["episodes"][0]["brain"]["adapter"][key] = bad
                self.write("agent-02/runtime.json", value)
                self.assert_rejected()
        value = copy.deepcopy(original)
        value["cognitive"]["episodes"][0]["brain"]["turns"][0]["tool_calls"] = 1
        self.write("agent-02/runtime.json", value); self.assert_rejected()

    def test_audit_candidate_without_dispatch_or_exact_postgame_crew_fails(self):
        self.threat_codex()
        original = self.read("agent-02/runtime.json")
        for status in ("stale", "rejected", "interrupted"):
            with self.subTest(status=status):
                value = copy.deepcopy(original); value["cognitive"]["episodes"][0]["decisions"][0]["status"] = status
                self.write("agent-02/runtime.json", value); self.assert_rejected()
        value = copy.deepcopy(original)
        value["match"]["result"]["outcome"]["threat_audit"]["postgame_own_crew_verified"] = False
        self.write("agent-02/runtime.json", value); self.assert_rejected()

    def test_strict_crew_discussion_vote_requirement_does_not_infer_missing_participation(self):
        self.assertFalse(self.evaluate(require_crew_communication_vote=True)["passed"])
        self.threat_codex()
        self.assertTrue(self.evaluate()["passed"])
        result = self.evaluate(require_crew_communication_vote=True)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["required_crew_communication_and_vote"])

    def test_valid_terminal_independent_match_and_protobuf_uint64_strings(self):
        result = self.evaluate(expected_reason="parity")
        self.assertTrue(result["passed"], result["checks"])
        self.assertEqual(result["gameplay_counts"]["kills"], 2)
        self.assertEqual(result["duration_ms"], 12345)
        self.assertFalse(result["real_codex_on_both_sides"])

    def test_normal_post_despawn_field_clear_keeps_saved_terminal_identity(self):
        for i in range(4):
            self.change(f"agent-{i + 1:02d}/client.json", lambda value: value.update(socialMatchId="", socialPhase="", socialSummaryPresent=False))
        self.assertTrue(self.evaluate()["passed"])

    def test_supervisor_alone_cannot_establish_gameover(self):
        for path in self.folder.glob("agent-*"):
            for child in path.iterdir(): child.unlink()
        self.assert_rejected()

    def test_missing_any_terminal_proof_or_process_report_rejects(self):
        for path in ("agent-01/client-social-completed.json", "agent-04/runtime.json", "processes.json", "processes-after-cleanup.json"):
            with self.subTest(path=path):
                value = self.read(path); (self.folder / path).unlink(); self.assert_rejected(); self.write(path, value)

    def test_finishobjective_without_authoritative_match_is_insufficient(self):
        self.change("agent-01/runtime.json", lambda value: value.update(match={"passed": True, "result": {"status": "succeeded"}}))
        self.assert_rejected()

    def test_nonterminal_and_identity_mismatch_cannot_be_overruled_by_supervisor(self):
        path = "agent-01/client-social-completed.json"; original = self.read(path)
        for key, bad in (("socialPhase", "Exploration"), ("actorId", "foreign"), ("acceptedSessionId", "foreign"),
                ("socialMatchId", "foreign"), ("socialSummaryPresent", False), ("socialPhaseRevision", 0),
                ("runtimeAuthoritativeTerminal", False), ("socialWinningSide", "Crew"), ("socialWinReason", "tasks_completed")):
            with self.subTest(key=key):
                self.write(path, dict(original, **{key: bad})); self.assert_rejected()
        self.write(path, original)
        self.change("agent-01/client.json", lambda value: value.update(socialPhase="Exploration")); self.assert_rejected()

    def test_runtime_accepted_actor_mapping_must_be_unique_and_match(self):
        path = "agent-01/runtime.json"; original = self.read(path)
        for sessions in ([{"session_id": "session-0", "actor_id": "foreign"}],
                [{"session_id": "session-0", "actor_id": "actor-0"}] * 2, []):
            with self.subTest(sessions=sessions):
                self.write(path, original); self.change(path, lambda value: value["runtime"].update(sessions=sessions)); self.assert_rejected()

    def test_all_four_independent_actor_and_session_identities_required(self):
        first_runtime = self.read("agent-01/runtime.json")
        for suffix in ("runtime.json", "client.json", "client-social-completed.json"):
            self.write("agent-02/" + suffix, self.read("agent-01/" + suffix))
        self.assert_rejected()
        self.assertFalse(self.evaluate()["checks"]["distinct_actor_sessions"])

    def test_duplicate_or_missing_summary_roster_actor_rejected(self):
        self.change_summary(lambda summary: summary["players"][3].update(actor_id="actor-2")); self.assert_rejected()

    def test_different_participant_summaries_rejected(self):
        self.change("agent-04/runtime.json", lambda value: value["match"]["result"]["outcome"]["authoritative_summary"].update(duration_ms="999"))
        self.assert_rejected()

    def test_forged_isolation_flag_does_not_replace_required_checks(self):
        self.change("supervisor.json", lambda value: value.update(process_isolation={"looks_good": True})); self.assert_rejected()

    def test_reused_launch_resources_rejected(self):
        path = "agent-02/launch.json"; original = self.read(path); first = self.read("agent-01/launch.json")
        for key in ("launcher_pid", "runtime_pid", "client_pid", "bridge_port", "bridge_credential_sha256", "memory_database", "agent_profile"):
            with self.subTest(key=key):
                self.write(path, dict(original, **{key: first[key]})); self.assert_rejected()

    def test_process_cleanup_claim_requires_matching_owned_pid_records(self):
        path = "processes-after-cleanup.json"; original = self.read(path)
        for key, value in (("runtime_alive_after_cleanup", True), ("client_alive_after_cleanup", True),
                ("runtime_pid", 999), ("launcher_identity_matches", False)):
            with self.subTest(key=key):
                self.write(path, original); self.change(path, lambda report: report["agents"][0].update(**{key: value})); self.assert_rejected()

    def test_unknown_reason_wrong_winner_and_absent_real_actions_rejected(self):
        original = copy.deepcopy(self.summary)
        for mutation in (lambda s: s.update(win_reason="invented"), lambda s: s.update(winning_side="SOCIAL_ALIGNMENT_CREW"),
                lambda s: s.update(kills=0), lambda s: s.update(replay=[r for r in s["replay"] if r["kind"] != "kill"])):
            with self.subTest(mutation=mutation):
                self.change_summary(lambda s: (s.clear(), s.update(copy.deepcopy(original)))); self.change_summary(mutation); self.assert_rejected()

    def test_tasks_use_actual_task_replay_and_crew_tasks_counter(self):
        self.set_reason("tasks_completed", "Crew")
        self.change_summary(lambda s: s.update(crew_tasks_completed=1, replay=[
            {"sequence": "1", "kind": "task", "outcome": "completed"}, {"sequence": "2", "kind": "match_ended", "outcome": "tasks_completed"}]))
        self.assertTrue(self.evaluate()["passed"])
        self.change_summary(lambda s: (s.pop("crew_tasks_completed"), s.update(tasks_completed=10))); self.assert_rejected()

    def set_reason(self, reason, winner):
        for i in range(4):
            name = f"agent-{i + 1:02d}"
            self.change(name + "/runtime.json", lambda r: r["match"]["result"]["outcome"].update(win_reason=reason, winning_side="SOCIAL_ALIGNMENT_" + winner.upper()))
            for suffix in ("client.json", "client-social-completed.json"):
                self.change(name + "/" + suffix, lambda v: v.update(socialWinReason=reason, socialWinningSide=winner))
        self.change_summary(lambda s: s.update(win_reason=reason, winning_side="SOCIAL_ALIGNMENT_" + winner.upper()))

    def test_sabotage_expired_needs_real_sabotage_not_similar_reason_text(self):
        self.set_reason("sabotage_expired", "Impostor")
        self.change_summary(lambda s: s.update(sabotages=1, replay=[{"sequence": "1", "kind": "sabotage", "outcome": "started"},
            {"sequence": "2", "kind": "match_ended", "outcome": "sabotage_expired"}]))
        self.assertTrue(self.evaluate()["passed"])
        self.change_summary(lambda s: s.update(sabotages=0)); self.assert_rejected()

    def test_official_unique_model_turns_required_not_decision_count_fallback(self):
        self.codex(0)
        path = "agent-01/runtime.json"; original = self.read(path)
        for adapter in ({}, {"unique_model_turns": 0}, {"unique_model_turns": 2, "sdk_version": "fake"}):
            with self.subTest(adapter=adapter):
                self.write(path, original); self.change(path, lambda r: r["cognitive"]["episodes"][0]["brain"].update(adapter=adapter))
                self.assert_rejected()

    def test_both_sides_model_requirement_is_explicit_and_cannot_be_scripted(self):
        self.assertFalse(self.evaluate(require_codex_both_sides=True)["passed"])
        self.codex(0)
        self.assertTrue(self.evaluate()["passed"])
        self.assertFalse(self.evaluate(require_codex_both_sides=True)["passed"])
        self.codex(1)
        result = self.evaluate(require_codex_both_sides=True)
        self.assertTrue(result["passed"], result["checks"]); self.assertTrue(result["real_codex_on_both_sides"])
        self.assertEqual(result["participants"][0]["codex_turns"], 2)

    def test_model_direct_tool_or_provider_mismatch_rejected(self):
        self.codex(0)
        self.change("agent-01/runtime.json", lambda r: r["cognitive"]["episodes"][0]["brain"]["turns"][0].update(tool_calls=1)); self.assert_rejected()

    def test_declared_scripted_provider_cannot_hide_codex_or_unclosed_provider(self):
        path = "agent-01/runtime.json"; original = self.read(path)
        for change in ({"provider": "codex"}, {"provider": "wrong-provider"}, {"closed": False}):
            with self.subTest(change=change):
                self.write(path, original)
                self.change(path, lambda r: r["cognitive"]["episodes"][0]["brain"].update(change))
                self.assert_rejected()

    def test_cleanup_errors_or_pending_tasks_rejected(self):
        path = "agent-01/runtime.json"; original = self.read(path)
        for key, value in (("cleanup_errors", ["failed"]), ("pending_cleanup_tasks", 1)):
            self.write(path, original); self.change(path, lambda r: r["cognitive"]["episodes"][0].update(**{key: value})); self.assert_rejected()

    def test_fractional_boolean_negative_nan_counts_fail_closed(self):
        for value in (1.5, True, -1, "1.5", "-1", float("nan"), "9" * 100):
            with self.subTest(value=value):
                with self.assertRaises(ValueError): EVALUATOR.count(value)
        self.change_summary(lambda s: s.update(kills=1.9)); self.assert_rejected()

    def test_replay_order_no_postterminal_actions_and_bounds_required(self):
        original = copy.deepcopy(self.summary)
        for mutation in (lambda s: s["replay"][1].update(sequence="1"),
                lambda s: s["replay"].append({"sequence": "9", "kind": "kill", "outcome": "dead"}),
                lambda s: s.update(replay=s["replay"] * 60)):
            self.change_summary(lambda s: (s.clear(), s.update(copy.deepcopy(original)))); self.change_summary(mutation); self.assert_rejected()

    def test_private_source_payloads_never_copied_to_public_output(self):
        secret = "PRIVATE_CHAT_PROMPT_POSITION_REASONING_SENTINEL"
        for i in range(4):
            name = f"agent-{i + 1:02d}"
            self.change(name + "/runtime.json", lambda r: r.update(raw_context=secret, model_reasoning=secret))
            self.change(name + "/runtime.json", lambda r: r["cognitive"]["episodes"][0]["result"]["skill_results"].append(
                {"skill_id": secret, "output": {"chat": secret}}))
            self.change(name + "/client.json", lambda r: r.update(position={"x": secret}, private_chat=secret, meanFps=float("nan")))
        self.change_summary(lambda s: (s.update(raw_reasoning=secret), s["players"][0].update(display_name=secret)))
        result = self.evaluate(); self.assertTrue(result["passed"], result["checks"])
        public = json.dumps(result, allow_nan=False); self.assertNotIn(secret, public); self.assertNotIn("raw_context", public)
        self.assertIn('"other": 1', public)

    def test_malformed_or_missing_inputs_have_bounded_safe_error(self):
        secret = "PRIVATE_FILE_CONTENT_SENTINEL"
        (self.folder / "agent-01/runtime.json").write_text(secret)
        result = self.evaluate(); self.assertFalse(result["passed"]); self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result["error_code"], "invalid_or_missing_evidence")

    def test_missing_participants_and_untrusted_directory_names_fail_closed(self):
        (self.folder / "agent-04").rename(self.folder / "agent-PRIVATE_SECRET")
        result = self.evaluate(); self.assertFalse(result["passed"])
        self.assertNotIn("PRIVATE_SECRET", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
