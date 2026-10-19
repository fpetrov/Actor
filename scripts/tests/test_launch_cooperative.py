"""Calibration launch planning is offline and keeps agent processes independent."""
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "launch-cooperative-agents.py"
SPEC = importlib.util.spec_from_file_location("launch_cooperative_agents", SCRIPT)
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)


class CooperationLaunchPlanTests(unittest.TestCase):
    def test_headless_switch_reaches_both_isolated_launchers_only_when_requested(self):
        for headless in (False, True):
            with self.subTest(headless=headless):
                args = SimpleNamespace(output=Path("/fixture/acceptance"), room="ABC123", base_port=50100,
                    coordinator_brain="scripted", technician_brain="codex", timeout=600,
                    model=None, client=None, headless_client=headless)
                plans = LAUNCHER.plans(Path("/repository"), args)
                commands = [command for _, _, command in plans]
                def argument(command, flag):
                    return command[command.index(flag) + 1]
                self.assertEqual([role for role, _, _ in plans], ["coordinator", "technician"])
                self.assertEqual(argument(commands[0], "--brain"), "calibration-coordinator")
                self.assertEqual(argument(commands[1], "--brain"), "codex")
                for flag in ("--port", "--output", "--agent-profile", "--memory-database"):
                    self.assertNotEqual(argument(commands[0], flag), argument(commands[1], flag))
                for command in commands:
                    self.assertEqual(command.count("--headless-client"), int(headless))
                    self.assertEqual(argument(command, "--room"), "ABC123")
                    self.assertNotIn("-nographics", command)
                    self.assertNotIn("--seed", command)
                    self.assertNotIn("--scenario", command)
                    self.assertNotIn("--token", command)
                self.assertNotEqual(argument(commands[0], "--objective"), argument(commands[1], "--objective"))

    def test_help_accepts_flag_without_starting_runtime_or_unity(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--headless-client", "--help"],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--headless-client", result.stdout)


if __name__ == "__main__":
    unittest.main()
