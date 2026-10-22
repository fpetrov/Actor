"""Offline process planning and strict match completion; no Unity or models."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "launch-social-agents.py"
SPEC = importlib.util.spec_from_file_location("launch_social_agents", SCRIPT)
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)
AGENT_SPEC = importlib.util.spec_from_file_location("launch_agent_names", SCRIPT.with_name("launch-agent.py"))
AGENT = importlib.util.module_from_spec(AGENT_SPEC)
AGENT_SPEC.loader.exec_module(AGENT)


def argument(command, name):
    return command[command.index(name) + 1]


def proof(index=0, winner="Crew", reason="tasks_completed"):
    actor, session, match = f"actor-{index}", f"session-{index}", "a" * 32
    runtime = {"match": {"passed": True, "result": {"status": "completed", "authoritative_terminal": True,
        "scope_id": match, "outcome": {"match_id": match, "winning_side": "SOCIAL_ALIGNMENT_" + winner.upper(), "win_reason": reason}}},
        "runtime": {"sessions": [{"session_id": session, "actor_id": actor}]}}
    client = {"gameMode": "SocialDeduction", "socialPhase": "GameOver", "socialSummaryPresent": True, "socialAssigned": True,
        "actorId": actor, "lastAcceptedSessionId": session, "socialMatchId": match, "socialWinningSide": winner,
        "socialWinReason": reason, "socialPhaseRevision": 9}
    return runtime, client


class SocialLaunchTests(unittest.TestCase):
    def test_explicit_audit_provider_and_policy_do_not_assign_roles(self):
        with tempfile.TemporaryDirectory() as folder:
            root, args = LAUNCHER.arguments(["--room", "ABC123", "--output", folder,
                "--match-policy", "agents-arena-threat-audit", "--brains", "codex-threat-audit",
                "social-fixture-vote", "social-fixture-vote", "social-fixture-vote"])
            commands = [command for _, _, command in LAUNCHER.plans(root, args)]
            self.assertEqual([argument(c, "--brain") for c in commands],
                ["codex-threat-audit"] + ["social-fixture-vote"] * 3)
            self.assertEqual({argument(c, "--match-policy") for c in commands}, {"agents-arena-threat-audit"})
            self.assertEqual({argument(c, "--objective") for c in commands}, {LAUNCHER.TRUSTED_OBJECTIVE})
            self.assertFalse(any(flag in c for c in commands for flag in ("--seed", "--role", "--alignment", "--winner")))

    def test_policy_registry_name_is_bounded_before_process_creation(self):
        for policy in ("", "../other", "/tmp/other", "name with spaces", "bad\nvalue", "-bad", "x" * 129):
            with self.subTest(policy=repr(policy)), patch("sys.stderr"), patch.object(LAUNCHER.subprocess, "Popen") as spawn:
                with self.assertRaises(SystemExit):
                    LAUNCHER.arguments(["--room", "ABC123", "--match-policy=" + policy])
                spawn.assert_not_called()

    def test_all_independent_participants_receive_same_role_agnostic_objective(self):
        for count in (4, 5, 6):
            with self.subTest(players=count):
                args = SimpleNamespace(players=count, output=Path("/output"), room="ABC123", brains=None,
                    brain="social-scripted", base_port=50200, timeout=1800, max_decisions=24,
                    client=None, config=None, model=None, headless_client=True)
                plans = LAUNCHER.plans(Path("/repository"), args)
                self.assertEqual(len(plans), count)
                commands = [command for _, _, command in plans]
                self.assertEqual({argument(c, "--objective") for c in commands}, {LAUNCHER.TRUSTED_OBJECTIVE})
                for flag in ("--port", "--output", "--memory-database", "--agent-profile", "--player-name"):
                    self.assertEqual(len({argument(c, flag) for c in commands}), count)
                for command in commands:
                    self.assertEqual(argument(command, "--operation"), "match")
                    self.assertEqual(argument(command, "--match-policy"), "agents-arena")
                    self.assertIn("--headless-client", command)
                    for prohibited in ("--seed", "--alignment", "--role", "--scenario", "--token", "--scripted-answer"):
                        self.assertNotIn(prohibited, command)
                    self.assertNotIn("impostor", argument(command, "--player-name").lower())

    def test_provider_choices_and_windowed_setting_do_not_assign_hidden_roles(self):
        args = SimpleNamespace(players=4, output=Path("/output"), room="ABC123", brains=["codex"] * 3 + ["social-scripted"],
            brain="social-scripted", base_port=50200, timeout=1800, max_decisions=24,
            client=Path("/client"), config=Path("/config"), model="model", headless_client=False)
        plans = LAUNCHER.plans(Path("/repository"), args)
        self.assertEqual([argument(c, "--brain") for _, _, c in plans], args.brains)
        for _, _, command in plans:
            self.assertNotIn("--headless-client", command)
            self.assertEqual(argument(command, "--client"), "/client")
            self.assertEqual(argument(command, "--config"), "/config")

    def test_common_objective_override_is_identical_for_all_without_role_assignment(self):
        goal = "First discuss your own observations and vote honestly; then pursue your own side's victory."
        for players in (4, 5, 6):
            with tempfile.TemporaryDirectory() as folder, self.subTest(players=players):
                root, args = LAUNCHER.arguments(["--room", "ABC123", "--players", str(players),
                    "--output", folder, "--brain", "codex", "--objective", goal])
                commands = [command for _, _, command in LAUNCHER.plans(root, args)]
                self.assertEqual(len(commands), players)
                self.assertEqual({argument(command, "--objective") for command in commands}, {goal})
                self.assertEqual([argument(command, "--player-name") for command in commands],
                    args.player_names)
                for command in commands:
                    self.assertEqual(command.count("--objective"), 1)
                    for prohibited in ("--alignment", "--role", "--seed", "--target", "--winner"):
                        self.assertNotIn(prohibited, command)

    def test_russian_name_pool_is_short_unique_and_shared_by_standalone_defaults(self):
        self.assertEqual(LAUNCHER.PLAYER_NAMES, AGENT.PLAYER_NAMES)
        self.assertEqual(len(LAUNCHER.PLAYER_NAMES), 24)
        self.assertEqual(len(set(LAUNCHER.PLAYER_NAMES)), 24)
        for name in LAUNCHER.PLAYER_NAMES:
            self.assertTrue(2 <= len(name) <= 6)
            self.assertTrue(all("А" <= letter <= "я" or letter in "Ёё" for letter in name))

    def test_name_sampling_is_once_without_replacement_and_preserves_internal_identity(self):
        for count in (4, 5, 6):
            with tempfile.TemporaryDirectory() as folder, self.subTest(players=count):
                root, args = LAUNCHER.arguments(["--room", "ABC123", "--players", str(count), "--output", folder])
                chosen = list(reversed(LAUNCHER.PLAYER_NAMES))[:count]
                with patch.object(LAUNCHER.secrets, "SystemRandom") as random:
                    random.return_value.sample.return_value = chosen
                    first = LAUNCHER.plans(root, args)
                    second = LAUNCHER.plans(root, args)
                    random.return_value.sample.assert_called_once_with(LAUNCHER.PLAYER_NAMES, count)
                self.assertEqual(first, second)
                self.assertEqual([argument(command, "--player-name") for _, _, command in first], chosen)
                for index, (identity, output, command) in enumerate(first, 1):
                    self.assertEqual(identity, f"agent-{index:02d}")
                    self.assertEqual(output, args.output / identity)
                    self.assertEqual(argument(command, "--agent-profile"), identity)
                    self.assertEqual(argument(command, "--memory-database"), str(output / "memory.sqlite3"))

    def test_explicit_public_names_are_preserved_without_consuming_randomness(self):
        names = ["Лена", "Олег", "Public.Name", "Player Four"]
        with tempfile.TemporaryDirectory() as folder:
            root, args = LAUNCHER.arguments(["--room", "ABC123", "--output", folder, "--player-names", *names])
            with patch.object(LAUNCHER.secrets, "SystemRandom", side_effect=AssertionError("explicit names need no picker")):
                commands = LAUNCHER.plans(root, args)
            self.assertEqual([argument(command, "--player-name") for _, _, command in commands], names)
        for explicit in ("Лена", "Custom Name", " Agent-kept-verbatim "):
            self.assertEqual(AGENT.participant_name(explicit, sample=lambda *_: self.fail("unexpected picker")), explicit)
        calls = []
        def picker(pool, count):
            calls.append((pool, count)); return ["Кира"]
        self.assertEqual(AGENT.participant_name(sample=picker), "Кира")
        self.assertEqual(calls, [(AGENT.PLAYER_NAMES, 1)])

    def test_invalid_name_overrides_fail_before_any_owned_process(self):
        invalid = [["Аня"] * 4, ["Аня", "аня", "Мира", "Олег"], ["Аня"] * 3,
            [" Лена", "Олег", "Мира", "Ян"], ["Я", "Олег", "Мира", "Ян"],
            ["x" * 25, "Олег", "Мира", "Ян"], ["Bad<name>", "Олег", "Мира", "Ян"],
            ["Лена\0", "Олег", "Мира", "Ян"], ["Лена😀", "Олег", "Мира", "Ян"]]
        for names in invalid:
            with self.subTest(names=names), patch("sys.stderr"), patch.object(LAUNCHER.subprocess, "Popen") as spawn:
                with self.assertRaises(SystemExit):
                    LAUNCHER.arguments(["--room", "ABC123", "--player-names", *names])
                spawn.assert_not_called()

    def test_existing_process_manifest_retains_resolved_names_before_child_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            root, args = LAUNCHER.arguments(["--room", "ABC123", "--output", folder])
            launches = LAUNCHER.plans(root, args)
            metadata = LAUNCHER.process_metadata(launches, [])
            self.assertEqual([row["player_name"] for row in metadata["agents"]], args.player_names)
            self.assertEqual([row["agent"] for row in metadata["agents"]], [f"agent-{i:02d}" for i in range(1, 5)])
            self.assertTrue(all(row["launcher_pid"] is None for row in metadata["agents"]))

    def test_objective_default_is_unchanged_and_maximum_length_is_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            root, args = LAUNCHER.arguments(["--room", "ABC123", "--output", folder])
            self.assertEqual(args.objective, LAUNCHER.TRUSTED_OBJECTIVE)
            self.assertEqual({argument(command, "--objective") for _, _, command in LAUNCHER.plans(root, args)},
                {LAUNCHER.TRUSTED_OBJECTIVE})
            _, args = LAUNCHER.arguments(["--room", "ABC123", "--output", folder, "--objective", "x" * 4096])
            self.assertEqual(len(args.objective), 4096)

    def test_invalid_common_objectives_fail_before_any_process_is_spawned(self):
        for goal in ("", " ", "\t\n", "\u2003", "x" * 4097, "valid\0invalid"):
            with self.subTest(goal=repr(goal)[:32]), patch("sys.stderr"), patch.object(LAUNCHER.subprocess, "Popen") as spawn:
                with self.assertRaises(SystemExit):
                    LAUNCHER.arguments(["--room", "ABC123", "--objective", goal])
                spawn.assert_not_called()

    def test_default_is_headless_and_explicit_windowed_override_is_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            _, args = LAUNCHER.arguments(["--room", "abc123", "--output", folder])
            self.assertTrue(args.headless_client)
            _, args = LAUNCHER.arguments(["--room", "ABC123", "--output", folder, "--windowed"])
            self.assertFalse(args.headless_client)

    def test_invalid_ports_provider_count_timeout_and_room_fail_before_processes(self):
        for extra in (["--base-port", "65533"], ["--timeout", "nan"], ["--max-decisions", "65"],
                      ["--brains", "codex"], ["--room", "bad"]):
            with self.subTest(extra=extra), patch("sys.stderr"):
                with self.assertRaises(SystemExit):
                    LAUNCHER.arguments(["--room", "ABC123", *extra])

    def test_help_never_requires_unity_runtime_or_credentials(self):
        for script, flags in ((SCRIPT, ["--windowed"]), (SCRIPT.with_name("launch-agent.py"), ["--operation", "match"])):
            with self.subTest(script=script.name):
                result = subprocess.run([sys.executable, str(script), *flags, "--help"], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0)
                self.assertIn("--room", result.stdout)

    def test_single_agent_launcher_preserves_agent_default_and_match_passthrough(self):
        source = SCRIPT.with_name("launch-agent.py").read_text()
        self.assertIn('choices=("agent", "match"), default="agent"', source)
        self.assertIn('[str(runtime), args.operation,', source)
        self.assertIn('runtime_args += ["--match-policy", args.match_policy]', source)
        self.assertIn('"-screen-fullscreen", "0", "-screen-width", "800", "-screen-height", "600"', source)

    def test_snapshot_requires_authoritative_runtime_and_actual_client_gameover(self):
        runtime, client = proof()
        self.assertIsNotNone(LAUNCHER.completion_snapshot(runtime, client))
        changes = (("runtime", ("match", "passed"), False), ("runtime", ("match", "result", "authoritative_terminal"), False),
            ("runtime", ("match", "result", "status"), "succeeded"), ("client", ("socialPhase",), "Exploration"),
            ("client", ("socialSummaryPresent",), False), ("client", ("socialAssigned",), False))
        for side, keys, value in changes:
            with self.subTest(side=side, keys=keys):
                r, c = copy.deepcopy(runtime), copy.deepcopy(client)
                target = r if side == "runtime" else c
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                self.assertIsNone(LAUNCHER.completion_snapshot(r, c))

    def test_finishobjective_alone_never_claims_match_completion(self):
        _, client = proof()
        self.assertIsNone(LAUNCHER.completion_snapshot({"objective": {"passed": True, "result": {"status": "succeeded"}}}, client))

    def test_identity_match_phase_and_winner_must_agree(self):
        runtime, client = proof()
        for key, value in (("actorId", "other"), ("lastAcceptedSessionId", "other"), ("socialMatchId", "other"),
                ("socialWinningSide", "Impostor"), ("socialWinReason", "parity"), ("socialPhaseRevision", 0)):
            with self.subTest(field=key):
                changed = dict(client, **{key: value})
                self.assertIsNone(LAUNCHER.completion_snapshot(runtime, changed))
        runtime["runtime"]["sessions"].append({"session_id": client["lastAcceptedSessionId"], "actor_id": "different"})
        self.assertIsNone(LAUNCHER.completion_snapshot(runtime, client))

    def test_terminal_snapshot_is_safe_whitelist_without_alignment_chat_or_context(self):
        runtime, client = proof()
        runtime["private_chat"] = "secret-body"
        runtime["match"]["result"]["outcome"]["own_alignment"] = "SOCIAL_ALIGNMENT_IMPOSTOR"
        client.update(alignment="Impostor", authorization_code="private", text="secret-body", position={"x": 99})
        snapshot = LAUNCHER.completion_snapshot(runtime, client)
        serialized = json.dumps(snapshot)
        self.assertNotIn("secret-body", serialized)
        self.assertNotIn("alignment", serialized)
        self.assertNotIn("authorization", serialized)
        self.assertNotIn("position", serialized)

    def test_distinct_actors_sessions_and_same_match_are_required_for_all_players(self):
        snapshots = [LAUNCHER.completion_snapshot(*proof(i)) for i in range(4)]
        self.assertTrue(LAUNCHER.completed_match(snapshots, 4))
        self.assertFalse(LAUNCHER.completed_match(snapshots[:3], 4))
        for field in ("actorId", "acceptedSessionId"):
            changed = copy.deepcopy(snapshots); changed[1][field] = changed[0][field]
            self.assertFalse(LAUNCHER.completed_match(changed, 4))
        for field, value in (("socialMatchId", "other"), ("socialWinningSide", "Impostor"), ("socialWinReason", "parity")):
            changed = copy.deepcopy(snapshots); changed[1][field] = value
            self.assertFalse(LAUNCHER.completed_match(changed, 4))

    def test_corrupt_reports_are_bounded_and_cannot_complete_a_match(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            path.write_text("[")
            self.assertEqual({}, LAUNCHER.read_report(path))
            path.write_text("[]")
            self.assertEqual({}, LAUNCHER.read_report(path))
        runtime, client = proof(); runtime["runtime"] = "invalid"
        self.assertIsNone(LAUNCHER.completion_snapshot(runtime, client))

    def test_pid_credential_profile_and_database_isolation_are_measured_from_child_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            plans, processes = [], []
            for i in range(4):
                output = Path(folder) / str(i); output.mkdir()
                metadata = {"launcher_pid": 1000 + i, "runtime_pid": 2000 + i, "client_pid": 3000 + i,
                    "bridge_port": 50300 + i, "bridge_credential_sha256": str(i) * 64,
                    "memory_database": str(output / "memory.sqlite3"), "agent_profile": "agent-" + str(i), "operation": "match"}
                (output / "launch.json").write_text(json.dumps(metadata))
                plans.append((str(i), output, []))
                processes.append((str(i), SimpleNamespace(pid=1000 + i, poll=lambda: None)))
            self.assertTrue(all(LAUNCHER.isolation_checks(plans, processes).values()))
            path = plans[1][1] / "launch.json"; changed = json.loads(path.read_text()); changed["runtime_pid"] = 2000
            path.write_text(json.dumps(changed))
            self.assertFalse(LAUNCHER.isolation_checks(plans, processes)["three_distinct_owned_pids_per_agent"])
    def test_api_override_is_validated_and_passed_to_each_ordinary_child(self):
        with tempfile.TemporaryDirectory() as folder:
            root, args = LAUNCHER.arguments(["--room", "ABC123", "--output", folder, "--api-url", "http://127.0.0.1:5081"])
            for _, _, command in LAUNCHER.plans(root, args):
                self.assertEqual(argument(command, "--api-url"), "http://127.0.0.1:5081")
        for url in ("file:///tmp/settings", "http://user:secret@localhost", "http://localhost?token=private",
                    "http://localhost/#secret", "http://localhost:99999", "http://localhost\n/private"):
            with self.subTest(url=url), patch("sys.stderr"):
                with self.assertRaises(SystemExit):
                    LAUNCHER.arguments(["--room", "ABC123", "--api-url", url])

    def test_partial_spawn_failure_still_cleans_only_created_owned_launchers(self):
        with tempfile.TemporaryDirectory() as folder:
            process = SimpleNamespace(pid=12345, poll=lambda: -15)
            with patch.object(LAUNCHER.subprocess, "Popen", side_effect=[process, OSError("fixture")]), \
                 patch.object(LAUNCHER, "stop_owned") as cleanup, patch.object(LAUNCHER.signal, "signal"), patch("builtins.print"):
                code = LAUNCHER.main(["--room", "ABC123", "--output", folder])
                self.assertEqual(code, 1)
                cleanup.assert_called_once_with([("agent-01", process)])
            self.assertEqual(json.loads((Path(folder) / "supervisor.json").read_text())["status"], "supervisor_error")

    def test_output_write_failure_cannot_skip_owned_process_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            process = SimpleNamespace(pid=12345, poll=lambda: -15)
            with patch.object(LAUNCHER.subprocess, "Popen", side_effect=[process, OSError("fixture")]), \
                 patch.object(LAUNCHER, "stop_owned") as cleanup, patch.object(LAUNCHER.signal, "signal"), \
                 patch.object(LAUNCHER, "write_json", side_effect=OSError("fixture")), patch("builtins.print"):
                self.assertEqual(LAUNCHER.main(["--room", "ABC123", "--output", folder]), 1)
                cleanup.assert_called_once_with([("agent-01", process)])
    def test_deterministic_fixture_providers_are_explicitly_available_without_role_hints(self):
        with tempfile.TemporaryDirectory() as folder:
            for provider in LAUNCHER.PROVIDERS:
                with self.subTest(provider=provider):
                    root, args = LAUNCHER.arguments(["--room", "ABC123", "--output", folder, "--brain", provider])
                    commands = [command for _, _, command in LAUNCHER.plans(root, args)]
                    self.assertEqual({argument(command, "--brain") for command in commands}, {provider})
                    self.assertEqual({argument(command, "--objective") for command in commands}, {LAUNCHER.TRUSTED_OBJECTIVE})


if __name__ == "__main__":
    unittest.main()
