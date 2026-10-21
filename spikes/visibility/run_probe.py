#!/usr/bin/env python3
"""Run three ordinary headless clients against an explicitly enabled NGO visibility fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest


def validate_report(value: object, role: str) -> bool:
    if not isinstance(value, dict) or value.get("fixture") != "ngo-2.13.3-visibility" or value.get("role") != role:
        return False
    checks = value.get("checks")
    required = ({f"stage_{index}_{suffix}" for index in range(30) for suffix in
                 ("server_observers", "three_client_spawned_sets")} | {"all_visibility_stages"}
                if role == "server" else {f"stage_{index}_spawned_set_and_owner" for index in range(30)} |
                {"server_confirmed_all_client_stages"})
    return (
        value.get("completed") is True and value.get("passed") is True
        and value.get("preNetworkHookInstalled") is True
        and type(value.get("stages")) is int and value["stages"] == 30
        and all(type(value.get(key)) is int and value[key] == 0 for key in ("duplicateSpawnWarnings", "initialForeignSpawns"))
        and type(value.get("policyFailures")) is int and value["policyFailures"] == 0
        and isinstance(checks, list) and len(checks) >= 31
        and all(isinstance(check, dict) and check.get("passed") is True and isinstance(check.get("name"), str) for check in checks)
        and required.issubset({check["name"] for check in checks})
    )


def read_report(path: Path) -> dict:
    try:
        if path.stat().st_size > 128 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def client_command(client: Path, room: str, role: str, output: Path, api_url: str | None = None) -> list[str]:
    command = [str(client), "--auto-room", room.upper(), "--player-name", f"Visibility-{role}",
        "--control-mode", "human", "--visibility-spike-role", role,
        "--visibility-spike-output", str(output / f"{role}.json"),
        "-batchmode", "-nographics", "-screen-fullscreen", "0", "-screen-width", "800", "-screen-height", "600",
        "-logFile", str(output / f"{role}-client.log")]
    if api_url is not None:
        command += ["--api-url", api_url]
    return command


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room")
    parser.add_argument("--client", type=Path, default=root / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena")
    parser.add_argument("--output", type=Path, default=root / "build/visibility-compatibility")
    parser.add_argument("--server-report", type=Path, help="Optional report from this exact fixture server, copied after completion")
    parser.add_argument("--api-url", help="Optional control-plane endpoint forwarded to the ordinary game client; no credential is accepted")
    parser.add_argument("--timeout", type=float, default=150)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return not unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProjectionTests)).wasSuccessful()
    if not args.room or len(args.room) != 6 or not args.room.isascii() or not args.room.isalnum():
        parser.error("--room must identify a dedicated SocialDeduction fixture server enabled with --visibility-spike-role server")
    if not 20 <= args.timeout <= 360:
        parser.error("--timeout must be 20–360 seconds")
    client = args.client.resolve(); output = args.output.resolve()
    if not client.is_file():
        parser.error("Build the client containing SocialVisibilityProbe first")
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh empty output directory; previous fixture reports are not accepted")
    output.mkdir(parents=True, exist_ok=True)
    processes: list[subprocess.Popen] = []
    logs = []
    interrupted = False

    def stop(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        for role in ("A", "B", "C"):
            log = (output / f"{role}-stdout.log").open("w"); logs.append(log)
            command = client_command(client, args.room, role, output, args.api_url)
            processes.append(subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT))
            time.sleep(.3)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            values = {role: read_report(output / f"{role}.json") for role in ("A", "B", "C")}
            if all(value.get("completed") is True for value in values.values()):
                break
            if any(process.poll() is not None for process in processes):
                break
            time.sleep(.2)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        for log in logs:
            log.close()
    values = {role: read_report(output / f"{role}.json") for role in ("A", "B", "C")}
    passed = {role: validate_report(value, role) for role, value in values.items()}
    if args.server_report:
        passed["server"] = validate_report(read_report(args.server_report.resolve()), "server")
    summary = {"fixture": "ngo-2.13.3-visibility", "passed": all(passed.values()) and not interrupted,
        "server_report_checked": args.server_report is not None, "clients": passed,
        "owned_clients_remaining": sum(process.poll() is None for process in processes),
        "scope": "Actual NGO observer/spawn/transform/owner compatibility; no Runtime, model, game outcome or raw packet payload claim."}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


class ProjectionTests(unittest.TestCase):
    def test_isolated_api_forwarding_preserves_headless_human_client(self):
        args = client_command(Path("client"), "ROOM01", "B", Path("output"), "http://127.0.0.1:5081")
        self.assertEqual(args[args.index("--api-url") + 1], "http://127.0.0.1:5081")
        self.assertEqual(args.count("--api-url"), 1)
        self.assertEqual(args[args.index("--control-mode") + 1], "human")
        self.assertEqual(args[args.index("-screen-fullscreen") + 1], "0")
        self.assertIn("-batchmode", args); self.assertIn("-nographics", args)
        self.assertNotIn("--api-url", client_command(Path("client"), "ROOM01", "A", Path("output")))

    def fixture(self):
        return {"fixture": "ngo-2.13.3-visibility", "role": "A", "completed": True, "passed": True,
            "preNetworkHookInstalled": True, "stages": 30, "duplicateSpawnWarnings": 0,
            "initialForeignSpawns": 0, "policyFailures": 0,
            "checks": [{"name": f"stage_{index}_spawned_set_and_owner", "passed": True} for index in range(30)] +
                [{"name": "server_confirmed_all_client_stages", "passed": True}]}

    def test_complete_fixture_is_accepted(self):
        self.assertTrue(validate_report(self.fixture(), "A"))

    def test_partial_or_wrong_role_cannot_pass(self):
        for key, value in (("completed", False), ("role", "B"), ("stages", 29), ("stages", True), ("preNetworkHookInstalled", False)):
            report = self.fixture(); report[key] = value
            self.assertFalse(validate_report(report, "A"), key)

    def test_leaks_duplicate_spawns_and_policy_errors_cannot_pass(self):
        for key in ("duplicateSpawnWarnings", "initialForeignSpawns", "policyFailures"):
            report = self.fixture(); report[key] = 1
            self.assertFalse(validate_report(report, "A"), key)
            report[key] = False
            self.assertFalse(validate_report(report, "A"), key)

    def test_failed_or_missing_check_cannot_pass(self):
        report = self.fixture(); report["checks"][4]["passed"] = False
        self.assertFalse(validate_report(report, "A"))
        report = self.fixture(); report["checks"] = []
        self.assertFalse(validate_report(report, "A"))
        report = self.fixture(); report["checks"] = [report["checks"][0]] * 31
        self.assertFalse(validate_report(report, "A"))


if __name__ == "__main__":
    sys.exit(main())
