"""Offline strict acceptance, privacy and independent launcher-plan regressions."""
import contextlib
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest


def load(name):
    path = Path(__file__).resolve().parents[1] / (name + ".py")
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SUMMARY = load("summarize-investigation")
LAUNCHER = load("launch-investigation-agents")


class InvestigationSummaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        for index, role in enumerate(SUMMARY.ROLES):
            directory = self.directory / role; directory.mkdir()
            memory = directory / "memory.sqlite3"
            with sqlite3.connect(memory) as db:
                db.execute("CREATE TABLE memories(profile,actor,session,kind,content)")
                db.execute("INSERT INTO memories VALUES(?,?,?,?,?)", (role, "actor-" + role, "PRIVATE-SESSION-" + role,
                    "skill_result", json.dumps({"data_policy": "session_only", "output": {"redacted": True}, "details": {"redacted": True}})))
            runtime = {"runtime": {"endpoint": f"127.0.0.1:{50051+index}", "sessions": [{
                    "session_id": "PRIVATE-SESSION-" + role, "actor_id": "actor-" + role,
                    "command_result_rtt_ms": [1, 2, 100], "private_evidence": "PRIVATE-EVIDENCE"}]},
                "objective": {"passed": True, "result": {"status": "succeeded", "duration_seconds": 8,
                    "decision_count": 4, "message": "PRIVATE-OBJECTIVE"}},
                "cognitive_config": {"memory": {"path": str(memory)}},
                "cognitive": {"brain": {"provider": "investigation-scripted", "hypotheses_proposed": int(role == "sheriff")},
                    "decisions": [{"context_bytes": 2048, "context": {"evidence_id": "PRIVATE-EVIDENCE-ID"}}],
                    "skills": [{"skill_id": "agents-arena.share-investigation-evidence", "status": "succeeded",
                        "data_policy": "session_only", "output": {"text": "PRIVATE-MESSAGE", "evidence_id": "PRIVATE-EVIDENCE-ID"}}]}}
            client = {"actorId": "actor-" + role, "sessionId": "PRIVATE-SESSION-" + role,
                "crewRole": role.title(), "incidentId": "public-incident", "incidentStatus": "Resolved", "coolingStatus": "Restored",
                "investigationReportValidated": True, "ownDiagnosticPresent": False, "ownSecurityPresent": False,
                "diagnosticEvidenceCount": int(role == "engineer"), "securityEvidenceCount": int(role == "sheriff"),
                "repairCompletedCount": int(role == "engineer"), "reportsValidatedCount": int(role == "sheriff"),
                "reportsRejectedCount": 0, "incidentsStartedCount": 1, "meanFps": 119,
                "commReceivedCount": 1, "receiptsServerAcceptedCount": 1, "receiptsClientReceivedCount": 1,
                "error": "PRIVATE-ERROR", "errors": [], "warnings": [], "actualCause": "PRIVATE-HIDDEN-ORIGIN",
                "privateDiagnostic": {"evidence_id": "PRIVATE-EVIDENCE-ID"}, "lastMessageId": "PRIVATE-MESSAGE-ID"}
            launch = {"runtime_pid": 1000 + index, "client_pid": 2000 + index, "bridge_port": 50051 + index,
                "bridge_credential_sha256": str(index + 1) * 64, "memory_database": str(memory), "agent_profile": role}
            for name, value in (("runtime.json", runtime), ("client.json", client), ("launch.json", launch)):
                self.write(role, name, value)

    def write(self, role, name, value):
        (self.directory / role / name).write_text(json.dumps(value))

    def modify(self, role, name, fn):
        value = json.loads((self.directory / role / name).read_text()); fn(value); self.write(role, name, value)

    def test_strict_authoritative_two_role_completion_passes(self):
        report = SUMMARY.summarize(self.directory, "mechanical-decoy")
        self.assertTrue(report["passed"])
        self.assertEqual(report["validation_case"], "mechanical-decoy")
        self.assertFalse(report["processes"]["actual_live_pids_verified"])
        self.assertEqual(report["agents"]["engineer"]["runtime"]["command_result_rtt_ms"]["p95"], 100)

    def test_actual_provider_metrics_and_registered_skill_ids_are_retained(self):
        self.modify("sheriff", "runtime.json", lambda value: value["cognitive"]["skills"].extend([
            {"skill_id": "agents-arena.select-role", "status": "succeeded"},
            {"skill_id": "agents-arena.maintenance-override", "status": "failed"}]))
        report = SUMMARY.summarize(self.directory)["agents"]["sheriff"]
        self.assertEqual(report["incident"]["hypotheses_proposed_by_scripted_provider"], 1)
        self.assertEqual(report["skills"]["by_id"]["agents-arena.select-role"], 1)
        self.assertEqual(report["skills"]["by_id"]["agents-arena.maintenance-override"], 1)
        self.assertNotIn("other", report["skills"]["by_id"])

    def test_finish_alone_missing_report_or_unrestored_cooling_never_passes(self):
        path = self.directory / "engineer/client.json"; original = path.read_text()
        for change in ({"incidentStatus": "Active"}, {"coolingStatus": "Failure"},
                       {"investigationReportValidated": False}, {"investigationReportValidated": "true"}):
            path.write_text(original); self.modify("engineer", "client.json", lambda value: value.update(change))
            with self.subTest(change=change): self.assertFalse(SUMMARY.summarize(self.directory)["passed"])

    def test_server_resolution_without_objective_success_never_passes(self):
        self.modify("sheriff", "runtime.json", lambda value: value["objective"]["result"].update(status="failed"))
        self.assertFalse(SUMMARY.summarize(self.directory)["passed"])

    def test_wrong_incident_shared_actor_or_wrong_duty_never_passes(self):
        path = self.directory / "sheriff/client.json"; original = path.read_text()
        for change in ({"incidentId": "other-incident"}, {"actorId": "actor-engineer"}, {"crewRole": "Engineer"}):
            path.write_text(original); self.modify("sheriff", "client.json", lambda value: value.update(change))
            with self.subTest(change=change): self.assertFalse(SUMMARY.summarize(self.directory)["passed"])

    def test_accepted_session_survives_unsuccessful_reconnect(self):
        self.modify("engineer", "client.json", lambda value: value.update(lastAcceptedSessionId=value["sessionId"], sessionId="unaccepted-reconnect"))
        self.assertTrue(SUMMARY.summarize(self.directory)["passed"])

    def test_shared_endpoint_credentials_or_processes_fail_closed(self):
        for name, change in (("runtime.json", lambda value: value["runtime"].update(endpoint="127.0.0.1:50051")),
                ("launch.json", lambda value: value.update(bridge_credential_sha256="1" * 64)),
                ("launch.json", lambda value: value.update(client_pid=2000))):
            path = self.directory / "sheriff" / name; original = path.read_text()
            self.modify("sheriff", name, change)
            self.assertFalse(SUMMARY.summarize(self.directory)["passed"]); path.write_text(original)

    def test_same_database_hardlink_and_foreign_profile_fail(self):
        target = self.directory / "sheriff/memory.sqlite3"; target.unlink()
        target.hardlink_to(self.directory / "engineer/memory.sqlite3")
        report = SUMMARY.summarize(self.directory)
        self.assertFalse(report["acceptance"]["distinct_database_files"])
        self.assertFalse(report["acceptance"]["isolated_own_memory_profiles"])
        self.assertFalse(report["passed"])

    def test_private_receiver_counters_and_missing_authoritative_work_fail(self):
        path = self.directory / "sheriff/client.json"; original = path.read_text()
        for change in ({"diagnosticEvidenceCount": 1}, {"securityEvidenceCount": 0}, {"reportsValidatedCount": 0}):
            path.write_text(original); self.modify("sheriff", "client.json", lambda value: value.update(change))
            self.assertFalse(SUMMARY.summarize(self.directory)["passed"])

    def test_private_content_hidden_origin_session_ids_paths_and_fingerprints_are_not_published(self):
        encoded = json.dumps(SUMMARY.summarize(self.directory))
        for forbidden in ("PRIVATE-", str(self.directory), "1" * 64, "2" * 64):
            self.assertNotIn(forbidden, encoded)

    def test_unredacted_sqlite_evidence_fails_without_exposing_it(self):
        with sqlite3.connect(self.directory / "sheriff/memory.sqlite3") as db:
            db.execute("UPDATE memories SET content=?", (json.dumps({"data_policy": "session_only", "output": {"records": "PRIVATE-LEAK"}}),))
        report = SUMMARY.summarize(self.directory)
        self.assertFalse(report["passed"]); self.assertNotIn("PRIVATE-LEAK", json.dumps(report))

    def test_missing_or_untrusted_reports_remain_safe_incomplete(self):
        self.modify("engineer", "runtime.json", lambda value: value["cognitive"].update(skills=[{"skill_id": [], "status": {}}]))
        (self.directory / "sheriff/launch.json").unlink()
        report = SUMMARY.summarize(self.directory)
        self.assertFalse(report["passed"])
        self.assertEqual(report["agents"]["engineer"]["skills"]["by_id"], {"other": 1})

    def test_cli_strict_failure_and_protected_private_inputs(self):
        self.modify("sheriff", "client.json", lambda value: value.update(incidentStatus="Cancelled"))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(SUMMARY.main([str(self.directory), "--require-success"]), 1)
        self.assertFalse(json.loads(output.getvalue())["passed"])
        raw = self.directory / "engineer/launch.json"; original = raw.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            SUMMARY.main([str(self.directory), "--output", str(raw)])
        self.assertEqual(raw.read_bytes(), original)


class InvestigationLauncherTests(unittest.TestCase):
    def test_plans_isolate_ports_profiles_databases_and_providers(self):
        args = SimpleNamespace(output=Path("/private/tmp/investigation"), engineer_brain="codex", sheriff_brain="scripted",
            room="ABC123", base_port=50100, timeout=600, max_decisions=24, model=None, client=None,
            config=Path("/private/tmp/cognitive.json"))
        plans = LAUNCHER.plans(Path("/repository"), args)
        parsed = [{command[i]: command[i+1] for i in range(2, len(command), 2)} for _, _, command in plans]
        self.assertEqual(parsed[0]["--brain"], "codex")
        self.assertEqual(parsed[1]["--brain"], "investigation-sheriff")
        for field in ("--port", "--output", "--memory-database", "--agent-profile"):
            self.assertNotEqual(parsed[0][field], parsed[1][field])
        self.assertEqual(parsed[0]["--config"], "/private/tmp/cognitive.json")
        self.assertNotIn("--seed", parsed[0]); self.assertNotIn("--scenario", parsed[1])
        self.assertFalse(any("TOKEN" in argument for _, _, command in plans for argument in command))

    def test_final_barrier_requires_authoritative_both_branch_resolution(self):
        states = {role: {"actorId": role, "crewRole": role.title(), "incidentId": "incident", "incidentStatus": "Resolved",
            "coolingStatus": "Restored", "investigationReportValidated": True} for role in LAUNCHER.ROLES}
        self.assertTrue(LAUNCHER.resolved_pair(states))
        for key, value in (("incidentStatus", "Active"), ("coolingStatus", "Repairing"), ("investigationReportValidated", "true")):
            changed = deepcopy(states); changed["engineer"][key] = value
            self.assertFalse(LAUNCHER.resolved_pair(changed))
        self.assertIsNone(LAUNCHER.objective_outcome({"objective": {"passed": "true", "result": {"status": "succeeded"}}}))
        self.assertFalse(LAUNCHER.objective_outcome({"objective": {"passed": True, "result": {"status": "failed"}}}))

    def test_nonfinite_timeout_rejected_before_creating_output(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            LAUNCHER.arguments(["--room", "ABC123", "--timeout", "nan"])


if __name__ == "__main__":
    unittest.main()
