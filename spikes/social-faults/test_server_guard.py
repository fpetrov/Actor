"""Offline exact-instance guard tests; no SSH, API, process or signal is used."""
import copy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("staging_server_guard", Path(__file__).with_name("server_guard.py"))
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)
NOW = 1_800_000_000_000
LOBBY = "0123456789abcdef0123456789abcdef"
EXPECTED = guard.expected_identity("ABC123", LOBBY, 200, 100)


def evidence():
    cp = dict(pid=200, parent_pid=1, start_ticks=100, state="S", executable=guard.CONTROL_EXE,
        file_uid=1001, uids=(1001,)*4, started_unix_ms=NOW-100000, network_namespace="net:[123]")
    server = dict(pid=300, parent_pid=200, start_ticks=200, state="S", executable=guard.SERVER_EXE,
        file_uid=1001, uids=(1001,)*4, started_unix_ms=NOW-19000, network_namespace="net:[123]",
        command=[guard.SERVER_EXE, "-batchmode", "--match-id", LOBBY, "--room-code", "ABC123", "--port", "7781", "--game-mode", "Sandbox"])
    return dict(now_unix_ms=NOW, acquired_at_unix_ms=NOW-20, actor_uid=1001,
        control_pid_file=200, control=cp, server=server, allocation_pid=300,
        udp_owners={300}, tcp_owners={200}, network_namespace="net:[123]", ready_log_matches=True,
        lobby=dict(lobbyId=LOBBY, roomCode="ABC123", gamePort=7781, status="Running", gameMode="Sandbox",
            gameVersion="0.8.0", protocolVersion="3", createdAt=datetime.fromtimestamp((NOW-20000)/1000, timezone.utc).isoformat()))


def ready():
    return dict(schema="actor.social-lifecycle-acceptance.v1", ready=True, case="server-loss-observer",
        room_code="ABC123", match_id=LOBBY, created_at_unix_ms=NOW-1000,
        actor_id="own-actor", session_id="own-session", client_pid=500, commands=0)


def allocation(**changes):
    state = {"{OriginalFormat}": "ServerProcessStarted {LobbyId} {RoomCode} {Pid} {Port}",
        "LobbyId": LOBBY, "RoomCode": "ABC123", "Pid": 300, "Port": 7781}
    state.update(changes)
    return json.dumps({"Message": "ignored private text", "State": state})


class IdentityTests(unittest.TestCase):
    def test_sandbox_and_social_are_exactly_bound_without_reading_gameplay(self):
        for mode in ("Sandbox", "SocialDeduction"):
            value = evidence(); value["lobby"]["gameMode"] = value["server"]["command"][-1] = mode
            identity = guard.validate(value, EXPECTED)
            self.assertEqual(identity["game_mode"], mode)
            self.assertEqual(identity["server_pid"], 300)
            self.assertNotIn("command", identity)

    def test_lobby_scope_mode_port_status_version_and_freshness_fail_closed(self):
        for key, bad in (("lobbyId", "f"*32), ("roomCode", "DEF456"), ("gamePort", 7777),
                ("gamePort", True), ("status", "Finished"), ("gameMode", "Unexpected"),
                ("gameVersion", "0.7.0"), ("protocolVersion", "2"), ("createdAt", "invalid")):
            with self.subTest(key=key, bad=bad):
                value = evidence(); value["lobby"][key] = bad
                with self.assertRaises(guard.GuardRejected): guard.validate(value, EXPECTED)
        for acquired in (NOW-5001, NOW+1):
            value = evidence(); value["acquired_at_unix_ms"] = acquired
            with self.assertRaises(guard.GuardRejected): guard.validate(value, EXPECTED)
        value = evidence(); value["now_unix_ms"] += 1800001
        with self.assertRaises(guard.GuardRejected): guard.validate(value, EXPECTED)

    def test_control_plane_requires_exact_exe_pidfile_start_uid_and_listener(self):
        mutations = [lambda x: x["control"].update(executable="/opt/actor/control-plane/current/Actor.ControlPlane"),
            lambda x: x["control"].update(start_ticks=101), lambda x: x.update(control_pid_file=201),
            lambda x: x["control"].update(uids=(0,)*4), lambda x: x["control"].update(file_uid=0),
            lambda x: x["control"].update(state="Z"), lambda x: x.update(tcp_owners={200, 900}),
            lambda x: x["control"].update(started_unix_ms=NOW)]
        for mutate in mutations:
            value = evidence(); mutate(value)
            with self.assertRaises(guard.GuardRejected): guard.validate(value, EXPECTED)

    def test_server_requires_exact_exe_actor_uid_parent_and_command_flags(self):
        mutations = [lambda x: x["server"].update(executable="/opt/actor/agents-arena/current/AgentsArenaServer"),
            lambda x: x["server"].update(executable=guard.SERVER_EXE+" (deleted)"),
            lambda x: x["server"].update(parent_pid=999), lambda x: x["server"].update(uids=(1001, 0, 1001, 1001)),
            lambda x: x["server"].update(file_uid=0), lambda x: x["server"].update(state="Z"),
            lambda x: x.update(allocation_pid=301), lambda x: x["server"].update(started_unix_ms=NOW-100000),
            lambda x: x["server"]["command"].extend(["--port", "7781"]),
            lambda x: x["server"]["command"].__setitem__(3, "f"*32),
            lambda x: x["server"]["command"].__setitem__(7, "7777")]
        for mutate in mutations:
            value = evidence(); mutate(value)
            with self.assertRaises(guard.GuardRejected): guard.validate(value, EXPECTED)

    def test_udp_ready_and_network_namespace_must_match_same_instance(self):
        for mutate in (lambda x: x.update(udp_owners=set()), lambda x: x.update(udp_owners={300, 301}),
                lambda x: x.update(ready_log_matches=False), lambda x: x["server"].update(network_namespace="net:[456]")):
            value = evidence(); mutate(value)
            with self.assertRaises(guard.GuardRejected): guard.validate(value, EXPECTED)

    def test_previous_identity_rejects_pid_reuse_parent_restart_and_aged_inspect(self):
        current = guard.validate(evidence(), EXPECTED)
        guard.validate_previous(current, current, NOW)
        for key, bad in (("server_pid", 301), ("server_start_ticks", 201), ("control_plane_start_ticks", 101),
                ("lobby_id", "f"*32), ("game_mode", "SocialDeduction"), ("server_pid", True)):
            previous = dict(current, **{key: bad})
            with self.assertRaises(guard.GuardRejected): guard.validate_previous(previous, current, NOW)
        for stamp in (NOW-30001, NOW+1):
            with self.assertRaises(guard.GuardRejected): guard.validate_previous(dict(current, inspected_at_unix_ms=stamp), current, NOW)

    def test_observer_readiness_must_be_live_exact_scope_and_recent(self):
        guard.validate_observer(ready(), EXPECTED, NOW)
        for key, bad in (("ready", False), ("ready", 1), ("case", "objective-cancel"), ("room_code", "DEF456"),
                ("match_id", "f"*32), ("created_at_unix_ms", NOW-30001), ("created_at_unix_ms", NOW+1),
                ("client_pid", True), ("commands", False), ("session_id", "")):
            with self.subTest(key=key):
                with self.assertRaises(guard.GuardRejected): guard.validate_observer(dict(ready(), **{key: bad}), EXPECTED, NOW)


class ParserTests(unittest.TestCase):
    def test_structured_allocator_record_not_arbitrary_prose_and_no_ambiguity(self):
        self.assertEqual(guard.allocation_pid("unstructured server log\n"+allocation(), EXPECTED), 300)
        for value in ("ServerProcessStarted "+LOBBY+" ABC123 300 7781", allocation(Pid=True),
                allocation(Port=7777), allocation(RoomCode="DEF456"), allocation()+"\n"+allocation(Pid=301),
                allocation()+"\n"+allocation()):
            with self.assertRaises(guard.GuardRejected): guard.allocation_pid(value, EXPECTED)

    def test_ready_log_requires_actual_exact_full_line(self):
        line = "ACTOR_READY match="+LOBBY+" room=ABC123 port=7781"
        self.assertTrue(guard.ready_log_matches("startup\n"+line+"\nstack", EXPECTED))
        for value in ("prefix "+line, line+" extra", line.replace("7781", "7777"), line+"\n"+line):
            self.assertFalse(guard.ready_log_matches(value, EXPECTED))

    def test_socket_requires_pid_evidence_for_every_bound_socket(self):
        line = 'UNCONN 0 0 0.0.0.0:7781 0.0.0.0:* users:(("Unity Main Thre",pid=300,fd=99))'
        self.assertEqual(guard.parse_socket_owners(line), {300})
        self.assertEqual(guard.parse_socket_owners(line+"\n"+line.replace("pid=300", "pid=301")), {300,301})
        for value in ("", "UNCONN 0 0 0.0.0.0:7781 0.0.0.0:*", line+"\nnot-a-socket"):
            with self.assertRaises(guard.GuardRejected): guard.parse_socket_owners(value)

    def test_proc_stat_handles_spaces_and_parentheses(self):
        fields = ["S", "200"] + ["0"]*17 + ["12345"] + ["0"]*3
        value = guard.proc_stat("300 (Unity Main (worker)) "+" ".join(fields))
        self.assertEqual(value, dict(pid=300, parent_pid=200, start_ticks=12345, state="S"))
        for value in ("broken", "0 (bad) S 1", "300 (bad) S 200"):
            with self.assertRaises(guard.GuardRejected): guard.proc_stat(value)

    def test_identity_file_exclusive_private_and_symlink_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/"identity.json"
            guard.private_write(target, {"private": "value"})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(guard.read_json(target, private=True), {"private": "value"})
            with self.assertRaises(FileExistsError): guard.private_write(target, {})
            link = Path(folder)/"link"; link.symlink_to(target)
            with self.assertRaises(OSError): guard.read_json(link, private=True)
            target.chmod(0o644)
            with self.assertRaises(guard.GuardRejected): guard.read_json(target, private=True)

    def test_large_file_is_rejected_or_bounded_to_complete_tail_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/"log"; target.write_text("large-prefix\nlast\n")
            with self.assertRaises(guard.GuardRejected): guard.read_file(target, 5)
            self.assertEqual(guard.read_file(target, 6, tail=True), "last\n")

    def test_root_owned_redirect_log_exception_does_not_expand_actor_only_files(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/"log"; target.write_text("allocation\n")
            values = list(target.stat()); values[4] = 0
            with patch.object(guard.os, "fstat", return_value=os.stat_result(values)):
                self.assertEqual(guard.read_file(target, 100, owner=(0, 1001)), "allocation\n")
                with self.assertRaises(guard.GuardRejected): guard.read_file(target, 100, owner=1001)
            values[4] = 900
            with patch.object(guard.os, "fstat", return_value=os.stat_result(values)):
                with self.assertRaises(guard.GuardRejected): guard.read_file(target, 100, owner=(0, 1001))

    def test_redirect_log_trusted_owner_still_rejects_group_or_other_write(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/"log"; target.write_text("allocation\n")
            for mode in (0o664, 0o646):
                values = list(target.stat()); values[4] = 0; values[0] = (values[0] & ~0o777) | mode
                with patch.object(guard.os, "fstat", return_value=os.stat_result(values)):
                    with self.assertRaises(guard.GuardRejected): guard.read_file(target, 100, owner=(0, 1001))

    def test_projection_never_copies_private_identity_prose_or_exception(self):
        value = guard.projection("terminate", passed=True)
        encoded = json.dumps(value)
        for private in (LOBBY, "ABC123", guard.SERVER_EXE, guard.CONTROL_EXE, '"pid"', '"room"'):
            self.assertNotIn(private, encoded)
        self.assertTrue(value["signal_issued"]); self.assertEqual(value["signal"], "SIGTERM")
        value = guard.projection("inspect", passed=False, code="SECRET_PRIVATE_ERROR")
        self.assertIsNone(value["rejection_code"]); self.assertFalse(value["signal_issued"])
        self.assertNotIn("SECRET", json.dumps(value))


class SignalTests(unittest.TestCase):
    def setup_files(self, folder):
        identity, observer = Path(folder)/"identity.json", Path(folder)/"ready.json"
        guard.private_write(identity, guard.validate(evidence(), EXPECTED))
        guard.private_write(observer, ready())
        return identity, observer

    def test_inspect_performs_no_pidfd_open_or_signal(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(guard.os, "pidfd_open", create=True) as opened, \
                patch.object(guard.signal, "pidfd_send_signal", create=True) as sent:
            result = guard.execute("inspect", EXPECTED, identity_path=Path(folder)/"identity", collector=lambda _: evidence())
            self.assertEqual(result["server_pid"], 300); opened.assert_not_called(); sent.assert_not_called()

    def test_terminate_double_revalidates_then_signals_only_held_pidfd(self):
        with tempfile.TemporaryDirectory() as folder:
            identity, observer = self.setup_files(folder); collect = Mock(side_effect=[evidence(), evidence()])
            with patch.object(guard.os, "pidfd_open", return_value=456, create=True) as opened, \
                    patch.object(guard.signal, "pidfd_send_signal", create=True) as sent, patch.object(guard.os, "close") as closed, \
                    patch.object(guard.time, "time", return_value=NOW/1000):
                guard.execute("terminate", EXPECTED, identity_path=identity, observer_path=observer, collector=collect)
            opened.assert_called_once_with(300); self.assertEqual(collect.call_count, 2)
            sent.assert_called_once_with(456, signal.SIGTERM)
            self.assertEqual(sum(call.args == (456,) for call in closed.call_args_list), 1)

    def test_failed_second_validation_never_signals(self):
        with tempfile.TemporaryDirectory() as folder:
            identity, observer = self.setup_files(folder); changed = evidence(); changed["udp_owners"] = {900}
            with patch.object(guard.os, "pidfd_open", return_value=456, create=True), \
                    patch.object(guard.signal, "pidfd_send_signal", create=True) as sent, patch.object(guard.os, "close") as closed, \
                    patch.object(guard.time, "time", return_value=NOW/1000):
                with self.assertRaises(guard.GuardRejected):
                    guard.execute("terminate", EXPECTED, identity_path=identity, observer_path=observer,
                        collector=Mock(side_effect=[evidence(), changed]))
            sent.assert_not_called()
            self.assertEqual(sum(call.args == (456,) for call in closed.call_args_list), 1)

    def test_explicit_abrupt_signal_uses_same_double_validation_and_pidfd(self):
        with tempfile.TemporaryDirectory() as folder:
            identity, observer = self.setup_files(folder); collect = Mock(side_effect=[evidence(), evidence()])
            with patch.object(guard.os, "pidfd_open", return_value=456, create=True), \
                    patch.object(guard.signal, "pidfd_send_signal", create=True) as sent, patch.object(guard.os, "close"), \
                    patch.object(guard.time, "time", return_value=NOW/1000):
                guard.execute("terminate", EXPECTED, identity_path=identity, observer_path=observer, signal_name="SIGKILL", collector=collect)
            self.assertEqual(collect.call_count, 2); sent.assert_called_once_with(456, signal.SIGKILL)
        self.assertEqual(guard.projection("terminate", passed=True, signal_name="SIGKILL")["signal"], "SIGKILL")
        self.assertEqual(guard.projection("terminate", passed=True)["signal"], "SIGTERM")

    def test_unapproved_signal_never_opens_process_or_sends(self):
        with patch.object(guard.os, "pidfd_open", create=True) as opened, \
                patch.object(guard.signal, "pidfd_send_signal", create=True) as sent:
            for value in ("SIGINT", "SIGSTOP", "9", True):
                with self.assertRaises(guard.GuardRejected):
                    guard.execute("terminate", EXPECTED, identity_path=Path("unused"), signal_name=value)
            opened.assert_not_called(); sent.assert_not_called()

    def test_expired_or_revoked_observer_never_signals(self):
        for change in ({"ready": False}, {"created_at_unix_ms": NOW-30001}):
            with tempfile.TemporaryDirectory() as folder:
                identity, observer = self.setup_files(folder); observer.write_text(json.dumps(dict(ready(), **change)))
                with patch.object(guard.os, "pidfd_open", return_value=456, create=True), \
                        patch.object(guard.signal, "pidfd_send_signal", create=True) as sent, patch.object(guard.os, "close"), \
                        patch.object(guard.time, "time", return_value=NOW/1000):
                    with self.assertRaises(guard.GuardRejected):
                        guard.execute("terminate", EXPECTED, identity_path=identity, observer_path=observer, collector=lambda _: evidence())
                sent.assert_not_called()

    def test_cli_validation_never_reads_host_or_signals_on_bad_scope(self):
        with patch.object(guard, "collect") as collect, patch("builtins.print"), patch("sys.stderr"):
            for room, lobby in (("bad", LOBBY), ("ABC123", "not-guid")):
                result = guard.main(["inspect", "--room", room, "--lobby-id", lobby, "--control-plane-pid", "200",
                    "--control-plane-start-ticks", "100", "--identity", "/tmp/unused-private"])
                self.assertEqual(result, 1)
            collect.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
