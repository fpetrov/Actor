"""Safe projection tests only; never capture traffic or launch a process."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("social_performance", Path(__file__).with_name("performance.py"))
performance = importlib.util.module_from_spec(spec); spec.loader.exec_module(performance)


def server():
    return {"schema": "actor.social-performance.v1", "match_id": "private-match", **performance.SCOPES,
        "elapsed_seconds": 10.0, "physics_ticks": 500, "update_frames": 600, "physics_ticks_per_second": 50.0,
        "mean_update_rate": 60.0, "visibility_samples": 1200, "refresh_samples": 100,
        "visibility_p95_ms": .2, "visibility_max_ms": 2.0, "refresh_p95_ms": 1.0, "refresh_max_ms": 3.0,
        "mean_observer_pairs": 8.0, "observer_pair_samples": 1200,
        "social_payloads": [{"actor_id": f"secret-actor-{i}", "bytes": 1000 + i,
            "messages": 30, "world_samples": 20, "visible_actor_total": 30, "visible_actor_max": 3} for i in range(4)]}


def headers():
    rows = []
    for peer in range(4):
        for timestamp in (100.0, 102.0):
            rows.append(f"{timestamp + peer / 1000:.6f} ens3 In IP 192.0.2.{peer + 1}.{50000 + peer} > 198.51.100.1.7781: UDP, length 100\n")
            rows.append(f"{timestamp + peer / 1000:.6f} ens3 Out IP 198.51.100.1.7781 > 192.0.2.{peer + 1}.{50000 + peer}: UDP, length 200\n")
    return rows


def log(count=16, drops=0):
    return ["tcpdump: data link type LINUX_SLL2\n",
        "tcpdump: verbose output suppressed, use -v[v]... for full protocol decode\n",
        "listening on any, link-type LINUX_SLL2 (Linux cooked v2), snapshot length 262144 bytes\n",
        f"{count} packets captured\n", f"{count + 1} packets received by filter\n", f"{drops} packets dropped by kernel\n"]


def optional():
    clients, runtimes = [], []
    for index in range(4):
        clients.append({"actorId": f"secret-actor-{index}", "socialMatchId": "private-match", "lastAcceptedSessionId": f"private-session-{index}",
            "meanFps": 59.0, "elapsedSeconds": 10.0, "managedHeapStart": 1000, "managedHeapCurrent": 2000,
            "bytesSent": 5000, "bytesReceived": 1000, "text": "PRIVATE CHAT", "authorization_code": "PRIVATE CODE"})
        runtimes.append({"match": {"result": {"scope_id": "private-match"}},
            "runtime": {"elapsed_seconds": 10.0, "cpu_seconds": 1.0, "cpu_percent_one_core": 10.0,
                "python_traced_memory_bytes": {"current": 100, "peak": 200}, "endpoint": "SECRET-ENDPOINT",
                "sessions": [{"actor_id": f"secret-actor-{index}", "session_id": f"private-session-{index}",
                    "command_result_rtt_ms": [1.0, 2.0, 3.0, 4.0, 10.0]}]},
            "cognitive": {"objective": "PRIVATE OBJECTIVE", "text": "PRIVATE CHAT"}})
    return clients, runtimes


class UdpTests(unittest.TestCase):
    def test_exact_four_anonymous_bidirectional_peers_and_payload_rates(self):
        result = performance.project_udp(headers(), log())
        self.assertTrue(result["four_bidirectional_peers_measured"])
        self.assertEqual([row["peer"] for row in result["peers"]], [f"peer{i:02}" for i in range(1, 5)])
        for row in result["peers"]:
            self.assertEqual(row["client_to_server"]["packets"], 2)
            self.assertEqual(row["client_to_server"]["udp_payload_bytes"], 200)
            self.assertAlmostEqual(row["client_to_server"]["mean_active_udp_payload_bytes_per_second"], 100)
            self.assertEqual(row["server_to_client"]["udp_payload_bytes"], 400)
        self.assertEqual(result["aggregate"]["client_to_server"]["udp_payload_bytes"], 800)
        self.assertEqual(result["aggregate"]["server_to_client"]["udp_payload_bytes"], 1600)
        self.assertAlmostEqual(result["packet_span_seconds"], 2.003)
        self.assertEqual(result["capture"]["received_not_written_at_capture_stop"], 1)
        self.assertNotIn("192.0.2", json.dumps(result)); self.assertNotIn("50000", json.dumps(result))

    def test_missing_peer_missing_direction_and_one_packet_are_not_measurements(self):
        for remove in (lambda line: "192.0.2.4." in line,
                lambda line: "192.0.2.1." in line and " Out " in line,
                lambda line: "100.000000" in line and "192.0.2.1." in line):
            data = [line for line in headers() if not remove(line)]
            with self.assertRaises(performance.InvalidEvidence): performance.project_udp(data, log(len(data)))

    def test_extra_peer_is_rejected_instead_of_silently_omitted(self):
        data = headers() + ["103.000000 ens3 In IP 192.0.2.5.50005 > 198.51.100.1.7781: UDP, length 10\n"]
        with self.assertRaisesRegex(performance.InvalidEvidence, "extra_udp_peer"):
            performance.project_udp(data, log(len(data)))

    def test_unknown_packet_payload_dump_blank_or_wrong_socket_never_gets_skipped(self):
        for bad in ("0x0000: SECRET PAYLOAD\n", "\n", "SECRET ERROR\n", headers()[0].replace("7781", "7782"),
                headers()[0].replace(" In ", " Out "), headers()[0].replace("192.0.2.1", "999.0.2.1"),
                headers()[0].replace("length 100", "length 65508")):
            data = [bad, *headers()[1:]]
            with self.assertRaises(performance.InvalidEvidence): performance.project_udp(data, log())

    def test_multiple_server_addresses_fail_closed(self):
        data = headers(); data[0] = data[0].replace("198.51.100.1", "198.51.100.2")
        with self.assertRaisesRegex(performance.InvalidEvidence, "multiple_gameplay_server"):
            performance.project_udp(data, log())

    def test_copy_terminal_blank_does_not_hide_a_packet_but_internal_blank_is_rejected(self):
        self.assertEqual(performance.project_udp([*headers(), "\n"], log())["capture"]["trailing_blank_lines"], 1)
        with self.assertRaisesRegex(performance.InvalidEvidence, "interleaved_blank"):
            performance.project_udp([*headers()[:1], "\n", *headers()[1:]], log())

    def test_capture_requires_complete_matching_statistics_and_no_kernel_drops(self):
        for value in (log(15), log(drops=1), log()[:-1], [*log(), log()[-1]], [*log(), "SECRET STDERR\n"]):
            with self.assertRaises(performance.InvalidEvidence): performance.project_udp(headers(), value)


class ServerTests(unittest.TestCase):
    def test_old_candidate_cpu_fields_are_null_and_recipients_are_anonymous(self):
        result = performance.project_server(server())
        self.assertIsNone(result["server_process_cpu_seconds"])
        self.assertIsNone(result["server_cpu_percent_one_core"])
        self.assertIsNone(result["server_peak_working_set_bytes"])
        self.assertEqual(result["social_payload_total_bytes"], 4006)
        self.assertEqual(result["social_payload_recipients"][0]["mean_visible_actors"], 1.5)
        self.assertNotIn("secret-actor", json.dumps(result)); self.assertNotIn("private-match", json.dumps(result))

    def test_new_cpu_percent_matches_elapsed_and_peak_is_numeric(self):
        value = server(); value.update(server_process_cpu_seconds=2.0, server_cpu_percent_one_core=20.0, server_peak_working_set_bytes=1000000)
        self.assertEqual(performance.project_server(value)["server_cpu_percent_one_core"], 20)
        value["server_cpu_percent_one_core"] = 21
        with self.assertRaisesRegex(performance.InvalidEvidence, "inconsistent_server_cpu"):
            performance.project_server(value)

    def test_counts_not_boolean_nonfinite_negative_or_strings(self):
        for invalid in (True, float("nan"), float("inf"), -1, "1", 10**1000):
            value = server(); value["physics_ticks"] = invalid
            with self.assertRaises(performance.InvalidEvidence): performance.project_server(value)

    def test_scope_labels_cannot_be_arbitrary_prose(self):
        value = server(); value["payload_scope"] = "PRIVATE TEXT"
        with self.assertRaisesRegex(performance.InvalidEvidence, "unknown_server_metric_scope"):
            performance.project_server(value)

    def test_all_four_unique_recipients_and_consistent_visibility_are_required(self):
        for mutation in (lambda x: x["social_payloads"].pop(),
                lambda x: x["social_payloads"][1].update(actor_id="secret-actor-0"),
                lambda x: x["social_payloads"][1].update(visible_actor_total=1000),
                lambda x: x["social_payloads"][1].update(visible_actor_max=6),
                lambda x: x.update(physics_ticks_per_second=51),
                lambda x: x.update(visibility_p95_ms=3, visibility_max_ms=2)):
            value = server(); mutation(value)
            with self.assertRaises(performance.InvalidEvidence): performance.project_server(value)


class OptionalTests(unittest.TestCase):
    def test_whitelist_rtt_loop_cpu_heap_and_identity_binding(self):
        clients, runtimes = optional()
        result = performance.project(server(), headers(), log(), clients=clients, runtimes=runtimes)
        self.assertTrue(result["passed"])
        row = result["participants"][0]
        self.assertEqual(row["runtime"]["command_result_rtt_ms"], {"samples": 5, "median": 3, "p95": 10, "max": 10})
        self.assertEqual(row["unity"]["headless_loop_rate"], 59)
        data = json.dumps(result)
        for private in ("PRIVATE", "SECRET", "secret-actor", "private-match", "private-session", "192.0.2.", "198.51.100."):
            self.assertNotIn(private, data)

    def test_foreign_match_session_or_duplicate_accepted_id_is_rejected(self):
        for mutation in (lambda c, r: c[0].update(socialMatchId="foreign"),
                lambda c, r: r[0]["match"]["result"].update(scope_id="foreign"),
                lambda c, r: r[0]["runtime"]["sessions"][0].update(actor_id="foreign"),
                lambda c, r: r[0]["runtime"]["sessions"].append({"session_id": "private-session-0", "actor_id": "foreign"})):
            clients, runtimes = optional(); mutation(clients, runtimes)
            with self.assertRaises(performance.InvalidEvidence): performance.project(server(), headers(), log(), clients=clients, runtimes=runtimes)

    def test_nonfinite_rtt_is_not_silently_removed_and_optional_reports_are_exactly_four(self):
        clients, runtimes = optional(); runtimes[0]["runtime"]["sessions"][0]["command_result_rtt_ms"] = [float("nan")]
        with self.assertRaises(performance.InvalidEvidence): performance.project(server(), headers(), log(), clients=clients, runtimes=runtimes)
        with self.assertRaises(performance.InvalidEvidence): performance.project(server(), headers(), log(), clients=clients[:3])

    def test_cleared_client_match_requires_exact_accepted_runtime_session_scope(self):
        clients, runtimes = optional(); clients[1]["socialMatchId"] = ""
        projected = performance.project(server(), headers(), log(), clients=clients, runtimes=runtimes)
        self.assertIn("cleared on cleanup", projected["participants"][1]["client_scope_binding"])
        with self.assertRaises(performance.InvalidEvidence): performance.project(server(), headers(), log(), clients=clients)
        runtimes[1]["runtime"]["sessions"][0]["session_id"] = "wrong-session"
        with self.assertRaises(performance.InvalidEvidence): performance.project(server(), headers(), log(), clients=clients, runtimes=runtimes)

    def test_empty_rtt_remains_unmeasured_instead_of_zero_latency(self):
        clients, runtimes = optional(); runtimes[0]["runtime"]["sessions"][0]["command_result_rtt_ms"] = []
        row = performance.project(server(), headers(), log(), clients=clients, runtimes=runtimes)["participants"][0]["runtime"]
        self.assertEqual(row["command_result_rtt_ms"], {"samples": 0, "median": None, "p95": None, "max": None})


class CliTests(unittest.TestCase):
    def test_failure_writes_only_bounded_error_code_no_input_or_addresses(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "server.json").write_text(json.dumps(server()))
            (folder / "headers.txt").write_text("PRIVATE-CHAT 192.0.2.1 SECRET\n")
            (folder / "capture.log").write_text("".join(log()))
            args = ["--server", str(folder / "server.json"), "--udp-headers", str(folder / "headers.txt"),
                "--capture-log", str(folder / "capture.log"), "--output", str(folder / "public.json")]
            self.assertEqual(performance.main(args), 1)
            public = (folder / "public.json").read_text()
            self.assertNotIn("PRIVATE", public); self.assertNotIn("192.0.2", public)
            self.assertEqual(json.loads(public)["error_code"], "unexpected_udp_header_format")


if __name__ == "__main__":
    unittest.main(verbosity=2)
