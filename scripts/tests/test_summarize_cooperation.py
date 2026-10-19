"""Offline acceptance/privacy regressions; no game processes or remote services."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "summarize-cooperation.py"
SPEC = importlib.util.spec_from_file_location("summarize_cooperation", SCRIPT)
SUMMARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUMMARY)
LAUNCH_SCRIPT = SCRIPT.with_name("launch-cooperative-agents.py")
LAUNCH_SPEC = importlib.util.spec_from_file_location("launch_cooperative_completion", LAUNCH_SCRIPT)
LAUNCHER = importlib.util.module_from_spec(LAUNCH_SPEC)
LAUNCH_SPEC.loader.exec_module(LAUNCHER)


class CooperationSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        for index, role in enumerate(SUMMARY.ROLES):
            directory = self.directory / role
            directory.mkdir()
            memory = directory / "memory.sqlite3"
            with sqlite3.connect(memory) as db:
                db.execute("CREATE TABLE memories(profile,actor,session,kind,content)")
                db.execute("INSERT INTO memories VALUES(?,?,?,?,?)", (role, f"actor-{index}", f"session-{index}",
                    "skill_result", json.dumps({"data_policy": "sensitive", "output": {"redacted": True},
                    "details": {"redacted": True}, "authorization_code": "[redacted]"})))
            runtime = {"runtime": {"endpoint": f"127.0.0.1:{50051 + index}", "sessions": [{"session_id": f"session-{index}", "actor_id": f"actor-{index}",
                       "command_result_rtt_ms": [1, 2, 3, 4, 100]}]},
                "objective": {"passed": True, "result": {"status": "succeeded", "message": "PRIVATE-OBJECTIVE-PROSE",
                    "duration_seconds": 5, "decision_count": 1}},
                "cognitive_config": {"memory": {"path": str(memory)}},
                "cognitive": {"skills": [{"skill_id": "actor.wait-for-message", "status": "succeeded",
                    "data_policy": "sensitive", "output": {"text": "PRIVATE-MESSAGE", "authorization_code": "PRIVATE-CODE"}}],
                    "objective": {"text": "PRIVATE-OBJECTIVE-PROSE"},
                    "decisions": [{"context_bytes": 123, "decision_reason": "PRIVATE-REASON"}]}}
            client = {"actorId": f"actor-{index}", "sessionId": f"session-{index}",
                      "calibrationTaskId": "task-1", "calibrationStatus": "Completed", "publicCalibrationStatus": "Completed",
                      "calibrationRevision": 7, "ownCalibrationAssignment": True,
                      "meanFps": 118, "commReceivedCount": 1, "diagnosticResultsCount": 1,
                      "error": "PRIVATE-ERROR", "errors": ["PRIVATE-ERROR"], "warnings": []}
            self.write(role, "runtime.json", runtime)
            self.write(role, "client.json", client)

    def write(self, role, name, value):
        (self.directory / role / name).write_text(json.dumps(value))

    def modify(self, role, name, change):
        value = json.loads((self.directory / role / name).read_text())
        change(value)
        self.write(role, name, value)

    def capture_completed(self):
        launches = [(role, self.directory / role, []) for role in SUMMARY.ROLES]
        completed = LAUNCHER.completed_reports(launches)
        self.assertIsNotNone(completed)
        LAUNCHER.save_completed_reports(completed)

    def clear_final_assignments(self):
        for role in SUMMARY.ROLES:
            self.modify(role, "client.json", lambda value: value.update(calibrationTaskId="",
                calibrationStatus="Unspecified", publicCalibrationStatus="Unspecified", calibrationRevision=0,
                ownCalibrationAssignment=False, state="Error", lastAcceptedSessionId=value["sessionId"]))

    def test_two_authoritative_completions_and_objectives_pass(self):
        report = SUMMARY.summarize(self.directory)
        self.assertTrue(report["passed"])
        self.assertEqual(report["agents"]["coordinator"]["runtime"]["command_result_rtt_ms"]["median"], 3)
        self.assertEqual(report["agents"]["coordinator"]["runtime"]["command_result_rtt_ms"]["p95"], 100)
        self.assertEqual(report["agents"]["technician"]["memory"]["retention_audit"]["private_policy_nodes"], 1)
        self.assertFalse(report["processes"]["actual_pids_verified"])

    def test_replan_count_preserves_numeric_measurement_and_rejects_bad_types(self):
        path = self.directory / "coordinator/runtime.json"; original = path.read_text()
        for raw, expected in ((0, 0), (7, 7), ("7", None), (True, None), (-1, None), (1.5, None)):
            path.write_text(original)
            self.modify("coordinator", "runtime.json", lambda value: value["cognitive"].update(replan_count=raw))
            with self.subTest(raw=raw):
                report = SUMMARY.summarize(self.directory)
                self.assertEqual(report["agents"]["coordinator"]["cognition"]["replan_count"], expected)

    def test_finish_objective_without_server_completion_never_passes(self):
        self.modify("technician", "client.json", lambda value: value.update(calibrationStatus="AwaitingAuthorization"))
        report = SUMMARY.summarize(self.directory)
        self.assertTrue(report["acceptance"]["both_objectives_passed"])
        self.assertFalse(report["passed"])

    def test_completed_barrier_snapshot_preserves_proof_after_exact_owner_cleanup(self):
        self.capture_completed()
        self.clear_final_assignments()
        report = SUMMARY.summarize(self.directory)
        self.assertTrue(report["passed"])
        for role in SUMMARY.ROLES:
            agent = report["agents"][role]
            self.assertEqual(agent["completion_evidence"]["source"], "supervisor_completed_snapshot")
            self.assertEqual(agent["completion_evidence"]["validation"], "exact_owner_session_verified")
            self.assertEqual(agent["completion_evidence"]["final_task_status"], "Unspecified")
            self.assertFalse(agent["completion_evidence"]["final_assignment_present"])
            self.assertEqual(agent["client"]["task_status"], "Completed")
            self.assertEqual(agent["client"]["state"], "Error")
            self.assertEqual(agent["client"]["mean_fps"], 118)

    def test_independent_own_success_is_latched_before_partner_finishes(self):
        launches = [(role, self.directory / role, []) for role in SUMMARY.ROLES]
        self.modify("technician", "runtime.json", lambda value: value.update(objective={}))
        LAUNCHER.latch_completed_reports(launches)
        self.assertTrue((self.directory / "coordinator/client-completed.json").is_file())
        self.assertFalse((self.directory / "technician/client-completed.json").exists())
        self.modify("coordinator", "client.json", lambda value: value.update(calibrationTaskId="",
            calibrationStatus="Unspecified", publicCalibrationStatus="Unspecified", calibrationRevision=0,
            ownCalibrationAssignment=False, state="Error", lastAcceptedSessionId=value["sessionId"]))
        self.modify("technician", "runtime.json", lambda value: value.update(
            objective={"passed": True, "result": {"status": "succeeded"}}))
        LAUNCHER.latch_completed_reports(launches)
        self.assertIsNotNone(LAUNCHER.completed_reports(launches))
        self.clear_final_assignments()
        self.assertTrue(SUMMARY.summarize(self.directory)["passed"])

    def test_snapshot_capture_selects_only_public_barrier_fields(self):
        self.modify("coordinator", "client.json", lambda value: value.update(
            authorizationCode="PRIVATE-CODE", privateDiagnostic={"text": "PRIVATE-EVIDENCE"}))
        self.capture_completed()
        snapshot = json.loads((self.directory / "coordinator/client-completed.json").read_text())
        self.assertEqual(set(snapshot), SUMMARY.COMPLETION_FIELDS)
        self.assertNotIn("PRIVATE-", json.dumps(snapshot))

    def test_barrier_rejects_missing_own_assignment_failed_different_task_or_shared_identity(self):
        path = self.directory / "technician/client.json"; original = path.read_text()
        changes = ({"ownCalibrationAssignment": False}, {"calibrationStatus": "Failed"},
            {"publicCalibrationStatus": "AwaitingAuthorization"}, {"calibrationRevision": 0},
            {"calibrationTaskId": "another-task"}, {"actorId": "actor-0"}, {"sessionId": "session-0"})
        for change in changes:
            path.write_text(original)
            self.modify("technician", "client.json", lambda value: value.update(change))
            with self.subTest(change=change):
                launches = [(role, self.directory / role, []) for role in SUMMARY.ROLES]
                self.assertIsNone(LAUNCHER.completed_reports(launches))

    def test_missing_historical_snapshot_does_not_backfill_success_from_objectives(self):
        self.clear_final_assignments()
        report = SUMMARY.summarize(self.directory)
        self.assertFalse(report["passed"])
        self.assertFalse(report["acceptance"]["both_authoritative_clients_completed"])
        self.assertEqual(report["agents"]["coordinator"]["completion_evidence"]["validation"], "missing")

    def test_failed_or_new_current_task_cannot_be_overridden_by_old_completed_snapshot(self):
        self.capture_completed(); self.clear_final_assignments()
        path = self.directory / "technician/client.json"; original = path.read_text()
        for change in ({"calibrationStatus": "Failed"}, {"publicCalibrationStatus": "Failed"},
            {"calibrationTaskId": "next-task", "calibrationStatus": "Completed",
             "publicCalibrationStatus": "Completed", "ownCalibrationAssignment": True, "calibrationRevision": 8}):
            path.write_text(original)
            self.modify("technician", "client.json", lambda value: value.update(change))
            with self.subTest(change=change):
                report = SUMMARY.summarize(self.directory)
                self.assertFalse(report["passed"])
                self.assertEqual(report["agents"]["technician"]["completion_evidence"]["source"], "final_client")

    def test_snapshot_identity_session_and_runtime_binding_are_mandatory(self):
        self.capture_completed(); self.clear_final_assignments()
        path = self.directory / "technician/client.json"; original = path.read_text()
        for change in ({"actorId": "replacement-actor"}, {"lastAcceptedSessionId": "replacement-session"}):
            path.write_text(original)
            self.modify("technician", "client.json", lambda value: value.update(change))
            with self.subTest(change=change):
                report = SUMMARY.summarize(self.directory)
                self.assertFalse(report["passed"])
                self.assertEqual(report["agents"]["technician"]["completion_evidence"]["validation"], "identity_mismatch")
        path.write_text(original)
        self.modify("technician", "runtime.json", lambda value: value["runtime"]["sessions"].append(dict(value["runtime"]["sessions"][0])))
        self.assertFalse(SUMMARY.summarize(self.directory)["passed"])

    def test_malformed_failed_or_private_snapshot_is_rejected(self):
        self.capture_completed(); self.clear_final_assignments()
        path = self.directory / "technician/client-completed.json"; original = path.read_text()
        for change in ({"schema": "unknown"}, {"calibrationStatus": "Failed"}, {"ownCalibrationAssignment": False},
                {"calibrationRevision": 0}, {"authorization_code": "PRIVATE-LEAK"}):
            path.write_text(original)
            self.modify("technician", "client-completed.json", lambda value: value.update(change))
            with self.subTest(change=change):
                report = SUMMARY.summarize(self.directory)
                self.assertFalse(report["passed"])
                self.assertEqual(report["agents"]["technician"]["completion_evidence"]["validation"], "invalid_snapshot")
                self.assertNotIn("PRIVATE-LEAK", json.dumps(report))

    def test_snapshot_never_substitutes_for_failed_objective(self):
        self.capture_completed(); self.clear_final_assignments()
        self.modify("technician", "runtime.json", lambda value: value["objective"]["result"].update(status="failed"))
        self.assertFalse(SUMMARY.summarize(self.directory)["passed"])

    def test_snapshot_is_protected_from_summary_output_overwrite(self):
        self.capture_completed(); self.clear_final_assignments()
        path = self.directory / "coordinator/client-completed.json"; original = path.read_bytes()
        with patch("sys.argv", [str(SCRIPT), str(self.directory), "--output", str(path)]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                SUMMARY.main()
        self.assertEqual(path.read_bytes(), original)

    def test_server_completion_without_objective_success_never_passes(self):
        self.modify("coordinator", "runtime.json", lambda value: value["objective"].update(passed=False))
        self.assertFalse(SUMMARY.summarize(self.directory)["passed"])

    def test_wrong_task_or_shared_actor_never_passes(self):
        self.modify("technician", "client.json", lambda value: value.update(calibrationTaskId="task-2", actorId="actor-0"))
        report = SUMMARY.summarize(self.directory)
        self.assertFalse(report["acceptance"]["same_nonempty_task_id"])
        self.assertFalse(report["acceptance"]["distinct_actor_ids"])
        self.assertFalse(report["passed"])

    def test_shared_runtime_endpoint_never_claims_independent_runtimes(self):
        self.modify("technician", "runtime.json", lambda value: value["runtime"].update(endpoint="127.0.0.1:50051"))
        report = SUMMARY.summarize(self.directory)
        self.assertFalse(report["acceptance"]["distinct_runtime_endpoints"])
        self.assertFalse(report["passed"])

    def test_different_paths_to_same_database_never_pass(self):
        a = self.directory / "coordinator/memory.sqlite3"
        b = self.directory / "technician/memory.sqlite3"
        b.unlink()
        b.hardlink_to(a)
        report = SUMMARY.summarize(self.directory)
        self.assertTrue(report["acceptance"]["distinct_database_paths"])
        self.assertFalse(report["acceptance"]["distinct_database_files"])
        self.assertFalse(report["passed"])

    def test_no_prose_codes_or_message_content_is_published(self):
        encoded = json.dumps(SUMMARY.summarize(self.directory))
        for value in ("PRIVATE-OBJECTIVE-PROSE", "PRIVATE-CODE", "PRIVATE-MESSAGE", "PRIVATE-REASON", "PRIVATE-ERROR"):
            self.assertNotIn(value, encoded)

    def test_unredacted_private_database_payload_fails_without_dumping_it(self):
        with sqlite3.connect(self.directory / "technician/memory.sqlite3") as db:
            db.execute("UPDATE memories SET content=?", (json.dumps({"data_policy": "sensitive",
                       "output": {"text": "PRIVATE-LEAK"}}),))
        report = SUMMARY.summarize(self.directory)
        self.assertFalse(report["passed"])
        self.assertEqual(report["agents"]["technician"]["memory"]["retention_audit"]["unredacted_private_payload_fields"], 1)
        self.assertNotIn("PRIVATE-LEAK", json.dumps(report))

    def test_missing_reports_are_incomplete_and_safe(self):
        (self.directory / "technician/runtime.json").unlink()
        report = SUMMARY.summarize(self.directory)
        self.assertFalse(report["passed"])
        self.assertEqual(report["agents"]["technician"]["input_errors"]["runtime"], "missing")

    def test_require_success_exit_code_and_protected_raw_artifacts(self):
        self.modify("technician", "client.json", lambda value: value.update(calibrationStatus="Cancelled"))
        with patch("sys.argv", [str(SCRIPT), str(self.directory), "--require-success"]), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(SUMMARY.main(), 1)
        self.assertFalse(json.loads(output.getvalue())["passed"])
        raw = self.directory / "technician/client.json"
        original = raw.read_bytes()
        with patch("sys.argv", [str(SCRIPT), str(self.directory), "--output", str(raw)]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                SUMMARY.main()
        self.assertEqual(raw.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
