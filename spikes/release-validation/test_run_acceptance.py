"""Offline version/scope/dispatch guards; no API, clients or server are used."""
import copy
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("version_bound_acceptance", Path(__file__).with_name("run_acceptance.py"))
runner = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(runner)
MATCH = "0123456789abcdef0123456789abcdef"
NOW = 1800000000


def target(suite="privacy", extras=()):
    return SimpleNamespace(suite=suite, room="ABC123", match_id=MATCH, game_version="0.9.0",
        protocol_version="4", helper_options=list(extras), output=runner.ROOT / "build/m8-acceptance-test",
        client=runner.ROOT / "build/ordinary-client", api_url=runner.API)


def lobby(mode="SocialDeduction"):
    return dict(lobbyId=MATCH, roomCode="ABC123", gameMode=mode, status="Ready", currentPlayers=0,
        maxPlayers=6, gamePort=7781, gameVersion="0.9.0", protocolVersion="4",
        createdAt=datetime.fromtimestamp(NOW - 10, timezone.utc).isoformat())


class TargetTests(unittest.TestCase):
    def test_exact_public_versions_room_and_mode_allow_only_matching_empty_fresh_target(self):
        version = dict(gameVersion="0.9.0", protocolVersion="4")
        for suite, extras, mode in (("privacy", (), "SocialDeduction"), ("protocol", (), "SocialDeduction"),
                ("fault", ("--case", "runtime-loss"), "SocialDeduction"),
                ("lifecycle", ("--case", "server-loss-observer"), "Sandbox"),
                ("lifecycle", ("--case", "objective-cancel", "--ready-social"), "SocialDeduction"),
                ("sandbox", ("--case", "movement"), "Sandbox")):
            with self.subTest(suite=suite, mode=mode):
                runner.validate_public_target(version, lobby(mode), target(suite, extras), now=NOW)
        for key, bad in (("lobbyId", "f"*32), ("roomCode", "DEF456"), ("gameVersion", "0.8.0"),
                ("protocolVersion", "3"), ("protocolVersion", 4), ("gameMode", "Sandbox"),
                ("status", "Running"), ("currentPlayers", 1), ("currentPlayers", False),
                ("maxPlayers", 3), ("maxPlayers", 7), ("gamePort", 7777),
                ("createdAt", "2027-01-15T08:00:01")):
            value = lobby(); value[key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(runner.Rejected):
                runner.validate_public_target(version, value, target(), now=NOW)
        for age in (-1, 601):
            value = lobby(); value["createdAt"] = datetime.fromtimestamp(NOW-age, timezone.utc).isoformat()
            with self.assertRaises(runner.Rejected): runner.validate_public_target(version, value, target(), now=NOW)
        for bad in (dict(gameVersion="0.8.0", protocolVersion="4"), dict(gameVersion="0.9.0", protocolVersion="3")):
            with self.assertRaises(runner.Rejected): runner.validate_public_target(bad, lobby(), target(), now=NOW)

    def test_pass_through_cannot_override_scope_via_duplicates_aliases_or_abbreviations(self):
        for suite in runner.HELPERS:
            for bad in (("--room", "OTHER1"), ("--roo", "OTHER1"), ("--api-url=http://example.com",),
                    ("--client", "foreign"), ("--summarize-only",), ("--timeout", "60", "--timeout", "90"),
                    ("--timeout",), ("--timeout", "--config", "file"), ("--brain", "codex")):
                with self.subTest(suite=suite, bad=bad), self.assertRaises(runner.Rejected):
                    runner.extra_options(suite, bad)
        self.assertEqual(runner.extra_options("privacy", ("--", "--timeout", "260", "--probe-out-of-sight-kill")),
            ["--timeout", "260", "--probe-out-of-sight-kill"])

    def test_dispatch_keeps_original_helpers_and_binds_lifecycle_match(self):
        for suite in runner.HELPERS:
            args = target(suite, ("--timeout", "180"))
            command = runner.command(args)
            self.assertEqual(command[:2], [runner.sys.executable, str(runner.ROOT / runner.HELPERS[suite])])
            self.assertEqual(command[command.index("--room")+1], "ABC123")
            self.assertEqual(command[-2:], ["--timeout", "180"])
            if suite == "lifecycle": self.assertEqual(command[command.index("--match-id")+1], MATCH)
            if suite == "sandbox": self.assertIn("--execute", command); self.assertNotIn("--api-url", command)
            else: self.assertEqual(command[command.index("--api-url")+1], runner.API)

    def test_plan_never_opens_network_or_executes_and_public_projection_has_no_identity(self):
        args = ["--suite", "privacy", "--room", "ABC123", "--match-id", MATCH,
            "--output", str(runner.ROOT / "build/m8-acceptance-test")]
        with patch.object(runner, "fetch_json") as fetch, patch.object(runner.os, "execv") as execute, \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(runner.main(args), 0)
        fetch.assert_not_called(); execute.assert_not_called()
        value = json.loads(output.getvalue()); self.assertFalse(value["execute"])
        for private in (MATCH, "ABC123", str(runner.ROOT), "ordinary-client"):
            self.assertNotIn(private, output.getvalue())

    def test_explicit_execution_refuses_old_version_before_original_helper_can_start(self):
        args = target(); args.execute = True
        with patch.object(runner, "arguments", return_value=args), patch.object(Path, "is_file", return_value=True), \
                patch.object(runner, "fetch_json", side_effect=[dict(gameVersion="0.8.0", protocolVersion="3"), lobby()]), \
                patch.object(runner.os, "execv") as execute, patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(runner.main([]), 1)
        execute.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["error_code"], "public_version_mismatch")

    def test_cli_scope_and_output_refusals_never_query_public_api(self):
        base = ["--suite", "privacy", "--room", "ABC123", "--match-id", MATCH,
            "--output", str(runner.ROOT / "build/m8-acceptance-test")]
        for flags in (("--api-url", "https://production.example"), ("--api-url", "http://user:secret@127.0.0.1:5081"),
                ("--output", str(runner.ROOT / "docs/report.json")), ("--game-version", "0.9.0\n")):
            with self.subTest(flags=flags), patch.object(runner, "fetch_json") as fetch, patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(runner.main([*base, *flags]), 1)
                fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
