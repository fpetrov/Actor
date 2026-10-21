"""Offline staging plan/projection checks. No Unity, HTTP, Runtime or models."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("sandbox_regression", Path(__file__).with_name("run_case.py"))
regression = importlib.util.module_from_spec(spec); spec.loader.exec_module(regression)


class WrapperTests(unittest.TestCase):
    def test_wrapper_uses_exec_preserves_auth_environment_and_forces_staging_headless_window_flags(self):
        source = regression.wrapper_source(Path("/tmp/client with 'quote' $(literal)"))
        arguments = ["wrapper", "--auto-room", "ABC123", "--api-url", "http://production.invalid", "-screen-fullscreen", "1",
            "-screen-width", "1920", "-screen-height", "1080", "-batchmode", "-nographics"]
        with patch.object(sys, "argv", arguments), patch.object(regression.os, "execv") as invoke:
            exec(compile(source, "client-wrapper", "exec"), {})
        actual, command = invoke.call_args.args
        self.assertEqual(actual, "/tmp/client with 'quote' $(literal)")
        self.assertEqual(command[command.index("--api-url") + 1], regression.STAGING)
        self.assertEqual(command[command.index("-screen-fullscreen") + 1], "0")
        self.assertEqual(command[command.index("-screen-width") + 1], "800")
        self.assertNotIn("http://production.invalid", command)
        self.assertEqual(command.count("-batchmode"), 1); self.assertEqual(command.count("-nographics"), 1)
        self.assertNotIn("environ", source); self.assertNotIn("shell", source)

    def test_default_preparation_never_launches_or_reserves_a_room(self):
        for case in regression.CASES:
            with tempfile.TemporaryDirectory() as folder, self.subTest(case=case), \
                    patch.object(regression.subprocess, "Popen", side_effect=AssertionError("no process")):
                self.assertEqual(regression.main(["--case", case, "--room", "PLAN00", "--output", folder]), 0)
                plan = json.loads((Path(folder) / "plan.json").read_text())
                self.assertFalse(plan["execution_requested"])
                self.assertTrue(plan["headless"]); self.assertFalse(plan["fullscreen"])
                self.assertEqual(plan["api"], regression.STAGING)

    def test_legacy_plans_stay_scripted_independent_and_use_the_only_client_wrapper(self):
        for case, names in (("cooperation", ("coordinator", "technician")), ("investigation", ("engineer", "sheriff"))):
            args = SimpleNamespace(case=case, output=Path("/output"), room="ABC123", base_port=18800, timeout=240)
            _, plans = regression.make_plans(args, Path("/wrapper"))
            self.assertEqual(tuple(role for role, _, _ in plans), names)
            for role, _, command in plans:
                self.assertEqual(command[command.index("--client") + 1], "/wrapper")
                self.assertIn("--headless-client", command)
                self.assertNotIn("codex", command)
                self.assertIn(role, command[command.index("--brain") + 1])
            for option in ("--port", "--memory-database", "--output", "--agent-profile"):
                self.assertEqual(len({command[command.index(option) + 1] for _, _, command in plans}), 2)


class AcceptanceTests(unittest.TestCase):
    def prediction(self):
        return {"role": "P", "connected": True, "completed": True,
            "checks": [{"name": name, "passed": True} for name in regression.PREDICTION_CHECKS], "errors": [], "warnings": [],
            "visualResponseMs": 33, "authoritativeResponseMs": 120, "releaseResponseMs": 80, "idleError": .01, "maxSpeed": 2.6}

    def test_prediction_requires_all_distinct_real_check_names_and_nonfinite_cannot_pass(self):
        client = {"mode": "Human", "keyboardEnabled": True}
        self.assertTrue(regression.prediction_projection(self.prediction(), client)["passed"])
        for mutation in (lambda p: p.update(visualResponseMs=float("nan")), lambda p: p.update(visualResponseMs=100),
                lambda p: p.update(checks=[p["checks"][0]] * 13), lambda p: p.update(warnings=["PRIVATE"]),
                lambda p: p["checks"][0].update(passed=False)):
            report = self.prediction(); mutation(report)
            self.assertFalse(regression.prediction_projection(report, client)["passed"])
        self.assertFalse(regression.prediction_projection(self.prediction(), {"mode": "Runtime", "keyboardEnabled": False})["passed"])

    def test_objective_success_alone_is_not_an_authoritative_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); plans = []
            for index, role in enumerate(("coordinator", "technician")):
                path = root / role; path.mkdir()
                regression.write(path / "runtime.json", {"objective": {"passed": True, "result": {"status": "succeeded"}}})
                regression.write(path / "launch.json", {"launcher_pid": 100 + index, "runtime_pid": 200 + index, "client_pid": 300 + index,
                    "bridge_port": 18800 + index, "bridge_credential_sha256": str(index) * 64, "memory_database": str(path / "memory.sqlite3"), "agent_profile": role})
                plans.append((role, path, []))
            module = SimpleNamespace(completed_reports=lambda _: None)
            processes = [(role, SimpleNamespace(pid=100 + index)) for index, (role, _, _) in enumerate(plans)]
            result = regression.pair_result(SimpleNamespace(case="cooperation"), module, plans, processes)
            self.assertFalse(result["passed"])
            self.assertTrue(result["both_objectives_passed"])
            self.assertTrue(result["independent_processes_credentials_and_memory"])

    def test_public_prediction_projection_omits_private_ids_positions_and_error_text(self):
        value = self.prediction(); value.update(actorId="PRIVATE", text="PRIVATE", trajectory=[{"root": "PRIVATE"}], peers=[{"actorId": "PRIVATE"}])
        public = regression.prediction_projection(value, {"mode": "Human", "keyboardEnabled": True})
        self.assertNotIn("PRIVATE", json.dumps(public)); self.assertIn("synthetic", public["input"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
