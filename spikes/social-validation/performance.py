#!/usr/bin/env python3
"""Offline safe projection of postgame server metrics and header-only UDP capture.

No network access or process launch. UDP peers and protobuf recipients have
independent anonymous numbering: header summaries do not map sockets to actors.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
from pathlib import Path
import re
import statistics

SCHEMA = "actor.social-performance-acceptance.v1"
SCOPES = {
    "scope": "server session initialization through GameOver",
    "percentile_window": "latest 1024 samples; maximum covers all samples",
    "payload_scope": "social protobuf payloads only; excludes RPC/NGO/UDP headers, transforms, spawn and Standard communication",
}
TOKEN = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")
IP = r"(?:\d{1,3}\.){3}\d{1,3}"
HEADER = re.compile(r"(?P<timestamp>\d{1,13}\.\d{1,9})\s+[A-Za-z0-9_.:-]{1,64}\s+"
    r"(?P<direction>In|Out)\s+IP\s+(?P<src>" + IP + r")\.(?P<sport>\d{1,5})\s+>\s+"
    r"(?P<dst>" + IP + r")\.(?P<dport>\d{1,5}):\s+UDP, length (?P<length>\d{1,5})\Z")


class InvalidEvidence(ValueError):
    """A fixed diagnostic code; never includes an untrusted input line."""


def numeric(value, *, minimum=0, maximum=10**15, integer=False):
    if ((type(value) is not int if integer else type(value) not in (int, float))
            or not minimum <= value <= maximum or not math.isfinite(value)):
        raise InvalidEvidence("invalid_numeric_metric")
    return value


def required(value, key, **bounds):
    if not isinstance(value, dict) or key not in value:
        raise InvalidEvidence("missing_numeric_metric")
    return numeric(value[key], **bounds)


def optional(value, key, **bounds):
    return required(value, key, **bounds) if key in value else None


def read_json(path):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise InvalidEvidence("json_input_too_large")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise InvalidEvidence("json_object_required")
    return value


def project_server(value):
    if (value.get("schema") != "actor.social-performance.v1" or not isinstance(value.get("match_id"), str)
            or not TOKEN.fullmatch(value["match_id"])):
        raise InvalidEvidence("invalid_server_report_identity")
    if any(value.get(key) != expected for key, expected in SCOPES.items()):
        raise InvalidEvidence("unknown_server_metric_scope")
    elapsed = required(value, "elapsed_seconds", minimum=.001, maximum=14400)
    result = {"measurement_scope": SCOPES["scope"], "percentile_window": SCOPES["percentile_window"],
        "payload_scope": SCOPES["payload_scope"], "elapsed_seconds": elapsed}
    for key in ("physics_ticks", "update_frames", "visibility_samples", "refresh_samples", "observer_pair_samples"):
        result[key] = required(value, key, minimum=1, maximum=10**9, integer=True)
    for key in ("physics_ticks_per_second", "mean_update_rate"):
        result[key] = required(value, key, maximum=100000)
    for counter, rate in (("physics_ticks", "physics_ticks_per_second"), ("update_frames", "mean_update_rate")):
        if not math.isclose(result[counter] / elapsed, result[rate], rel_tol=1e-6, abs_tol=1e-6):
            raise InvalidEvidence("inconsistent_server_tick_rate")
    for kind in ("visibility", "refresh"):
        for suffix in ("p95_ms", "max_ms"):
            key = kind + "_" + suffix
            result[key] = required(value, key, maximum=60000)
        if result[kind + "_p95_ms"] > result[kind + "_max_ms"]:
            raise InvalidEvidence("invalid_percentile_order")
    result["mean_observer_pairs"] = required(value, "mean_observer_pairs", maximum=128)
    result["server_process_cpu_seconds"] = optional(value, "server_process_cpu_seconds", maximum=14400 * 256)
    result["server_cpu_percent_one_core"] = optional(value, "server_cpu_percent_one_core", maximum=25600)
    result["server_peak_working_set_bytes"] = optional(value, "server_peak_working_set_bytes", minimum=1, maximum=2**50, integer=True)
    if (result["server_process_cpu_seconds"] is None) != (result["server_cpu_percent_one_core"] is None):
        raise InvalidEvidence("incomplete_server_cpu_measurement")
    if result["server_process_cpu_seconds"] is not None and not math.isclose(
            result["server_process_cpu_seconds"] / elapsed * 100, result["server_cpu_percent_one_core"], rel_tol=1e-6, abs_tol=1e-6):
        raise InvalidEvidence("inconsistent_server_cpu_percent")
    rows = value.get("social_payloads")
    if not isinstance(rows, list) or len(rows) != 4 or any(not isinstance(row, dict) for row in rows):
        raise InvalidEvidence("four_server_recipients_required")
    ids = [row.get("actor_id") for row in rows]
    if any(not isinstance(actor, str) or TOKEN.fullmatch(actor) is None for actor in ids) or len(set(ids)) != 4:
        raise InvalidEvidence("invalid_server_recipient_identities")
    recipients = []
    for index, row in enumerate(sorted(rows, key=lambda row: row["actor_id"]), 1):
        projected = {"recipient": f"recipient{index:02}"}
        for key in ("bytes", "messages", "world_samples", "visible_actor_total", "visible_actor_max"):
            projected[key] = required(row, key, maximum=10**12, integer=True)
        samples, total, maximum = (projected[key] for key in ("world_samples", "visible_actor_total", "visible_actor_max"))
        if samples < 1 or maximum > 5 or total > samples * maximum or projected["messages"] < samples:
            raise InvalidEvidence("inconsistent_recipient_visibility_counts")
        projected["mean_visible_actors"] = total / samples
        projected["mean_social_payload_bytes_per_second"] = projected["bytes"] / elapsed
        recipients.append(projected)
    result["social_payload_recipients"] = recipients
    result["social_payload_total_bytes"] = sum(row["bytes"] for row in recipients)
    result["social_payload_total_messages"] = sum(row["messages"] for row in recipients)
    return result


def capture_stats(lines):
    counts = {}
    allowed = (
        re.compile(r"tcpdump: data link type LINUX_SLL2\Z"),
        re.compile(r"tcpdump: verbose output suppressed, use -v\[v\]\.\.\. for full protocol decode\Z"),
        re.compile(r"listening on [A-Za-z0-9_.:-]{1,64}, link-type LINUX_SLL2 \(Linux cooked v2\), snapshot length \d{1,8} bytes\Z"),
    )
    for line in lines:
        text = line.strip()
        if not text:
            continue
        found = re.fullmatch(r"(\d{1,12}) packets (captured|received by filter|dropped by kernel)", text)
        if found:
            key = {"captured": "captured_packets", "received by filter": "received_by_filter", "dropped by kernel": "kernel_drops"}[found[2]]
            if key in counts:
                raise InvalidEvidence("duplicate_capture_counter")
            counts[key] = int(found[1])
        elif not any(pattern.fullmatch(text) for pattern in allowed):
            raise InvalidEvidence("unexpected_capture_log_format")
    if len(counts) != 3 or counts["captured_packets"] < 1 or counts["received_by_filter"] < counts["captured_packets"]:
        raise InvalidEvidence("incomplete_capture_statistics")
    if counts["kernel_drops"] != 0:
        raise InvalidEvidence("capture_kernel_drops")
    counts["received_not_written_at_capture_stop"] = counts["received_by_filter"] - counts["captured_packets"]
    return counts


def direction_metrics(packets):
    if len(packets) < 2:
        raise InvalidEvidence("insufficient_bidirectional_peer_samples")
    first = min(timestamp for timestamp, _ in packets)
    last = max(timestamp for timestamp, _ in packets)
    duration = last - first
    if duration <= 0:
        raise InvalidEvidence("zero_active_packet_interval")
    size = sum(length for _, length in packets)
    return {"packets": len(packets), "udp_payload_bytes": size, "active_packet_span_seconds": duration,
        "mean_active_udp_payload_bytes_per_second": size / duration}


def project_udp(header_lines, log_lines, *, game_port=7781):
    numeric(game_port, minimum=1, maximum=65535, integer=True)
    stats = capture_stats(log_lines)
    peers = {}; server = None; parsed = 0; earliest = math.inf; latest = -math.inf; trailing_blank_lines = 0
    for line in header_lines:
        text = line.strip()
        # A terminal empty line is normal when copying a text capture. Empty
        # lines followed by more packets, continuations and payload dumps are
        # not silently skipped. Written packet count must still match exactly.
        if not text:
            trailing_blank_lines += 1
            continue
        if trailing_blank_lines:
            raise InvalidEvidence("unexpected_interleaved_blank_header")
        found = HEADER.fullmatch(text)
        if found is None:
            raise InvalidEvidence("unexpected_udp_header_format")
        try:
            source, destination = str(ipaddress.IPv4Address(found["src"])), str(ipaddress.IPv4Address(found["dst"]))
        except ipaddress.AddressValueError:
            raise InvalidEvidence("invalid_header_address") from None
        sport, dport, length = (int(found[key]) for key in ("sport", "dport", "length"))
        if not 1 <= sport <= 65535 or not 1 <= dport <= 65535 or not 0 <= length <= 65507:
            raise InvalidEvidence("invalid_udp_header_number")
        incoming = dport == game_port and sport != game_port
        outgoing = sport == game_port and dport != game_port
        if not incoming and not outgoing or found["direction"] != ("In" if incoming else "Out"):
            raise InvalidEvidence("unexpected_gameplay_socket_or_direction")
        endpoint = (source, sport) if incoming else (destination, dport)
        server_ip = destination if incoming else source
        if server is not None and server_ip != server:
            raise InvalidEvidence("multiple_gameplay_server_endpoints")
        server = server_ip
        timestamp = float(found["timestamp"])
        numeric(timestamp, maximum=10**13)
        bucket = peers.setdefault(endpoint, {"first": timestamp, "client_to_server": [], "server_to_client": []})
        bucket["client_to_server" if incoming else "server_to_client"].append((timestamp, length))
        bucket["first"] = min(bucket["first"], timestamp)
        if len(peers) > 4:
            raise InvalidEvidence("unexpected_extra_udp_peer")
        earliest = min(earliest, timestamp); latest = max(latest, timestamp); parsed += 1
    if parsed != stats["captured_packets"]:
        raise InvalidEvidence("capture_text_count_mismatch")
    if len(peers) != 4:
        raise InvalidEvidence("four_udp_peers_required")
    projected = []
    for index, row in enumerate(sorted(peers.values(), key=lambda row: row["first"]), 1):
        projected.append({"peer": f"peer{index:02}", **{kind: direction_metrics(row[kind]) for kind in ("client_to_server", "server_to_client")}})
    span = latest - earliest
    if span <= 0:
        raise InvalidEvidence("zero_capture_packet_span")
    aggregate = {}
    for direction in ("client_to_server", "server_to_client"):
        payload = sum(row[direction]["udp_payload_bytes"] for row in projected)
        aggregate[direction] = {"packets": sum(row[direction]["packets"] for row in projected), "udp_payload_bytes": payload,
            "mean_udp_payload_bytes_per_capture_second": payload / span}
    return {"four_bidirectional_peers_measured": True, "packet_span_seconds": span,
        "capture": {**stats, "parsed_packets": parsed, "trailing_blank_lines": trailing_blank_lines}, "peers": projected, "aggregate": aggregate,
        "scope": "Header-only IPv4 UDP payload lengths for one selected gameplay socket. Excludes IP/UDP/link headers; includes transport/NGO overhead and all captured gameplay traffic."}


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def project_optional(clients=None, runtimes=None, *, server_actor_ids, match_id):
    if clients is not None and len(clients) != 4 or runtimes is not None and len(runtimes) != 4:
        raise InvalidEvidence("four_optional_participant_reports_required")
    rows = []
    if clients is not None:
        actors = [client.get("actorId") for client in clients]
        if (len(set(actors)) != 4 or set(actors) != server_actor_ids
                or any(client.get("socialMatchId") not in (None, "", match_id) for client in clients)
                or runtimes is None and any(client.get("socialMatchId") != match_id for client in clients)):
            raise InvalidEvidence("optional_client_match_mismatch")
    for index in range(4):
        row = {"participant": f"participant{index + 1:02}"}
        if clients is not None:
            client = clients[index]
            row["unity"] = {"headless_loop_rate": required(client, "meanFps", maximum=100000),
                "elapsed_seconds": required(client, "elapsedSeconds", minimum=.001, maximum=14400),
                "managed_heap_start_bytes": required(client, "managedHeapStart", maximum=2**50, integer=True),
                "managed_heap_current_bytes": required(client, "managedHeapCurrent", maximum=2**50, integer=True),
                "grpc_bytes_sent": required(client, "bytesSent", maximum=2**50, integer=True),
                "grpc_bytes_received": required(client, "bytesReceived", maximum=2**50, integer=True)}
        if runtimes is not None:
            report = runtimes[index]; runtime = report.get("runtime", {})
            result = report.get("match", {}).get("result", {})
            if result.get("scope_id") != match_id:
                raise InvalidEvidence("optional_runtime_match_mismatch")
            sessions = runtime.get("sessions", [])
            if not isinstance(sessions, list) or not 1 <= len(sessions) <= 256:
                raise InvalidEvidence("invalid_runtime_sessions")
            if clients is not None:
                accepted = clients[index].get("lastAcceptedSessionId") or clients[index].get("sessionId")
                chosen = [s for s in sessions if isinstance(s, dict) and s.get("session_id") == accepted]
                if len(chosen) != 1 or chosen[0].get("actor_id") != clients[index]["actorId"]:
                    raise InvalidEvidence("optional_runtime_client_identity_mismatch")
                row["client_scope_binding"] = ("client match field and accepted Runtime session" if clients[index].get("socialMatchId") == match_id
                    else "client match field cleared on cleanup; exact actor and accepted Runtime session bound to Runtime match scope")
            else:
                chosen = [s for s in sessions if isinstance(s, dict) and s.get("actor_id") in server_actor_ids]
                if len(chosen) != 1:
                    raise InvalidEvidence("ambiguous_runtime_recipient")
            samples = chosen[0].get("command_result_rtt_ms")
            if not isinstance(samples, list) or len(samples) > 10000:
                raise InvalidEvidence("invalid_runtime_rtt_samples")
            for sample in samples:
                numeric(sample, maximum=600000)
            traced = runtime.get("python_traced_memory_bytes", {})
            row["runtime"] = {"elapsed_seconds": required(runtime, "elapsed_seconds", minimum=.001, maximum=14400),
                "cpu_seconds": required(runtime, "cpu_seconds", maximum=14400 * 256),
                "cpu_percent_one_core": required(runtime, "cpu_percent_one_core", maximum=25600),
                "python_traced_current_bytes": optional(traced, "current", maximum=2**50, integer=True),
                "python_traced_peak_bytes": optional(traced, "peak", maximum=2**50, integer=True),
                "command_result_rtt_ms": {"samples": len(samples), "median": statistics.median(samples) if samples else None,
                    "p95": percentile(samples, .95), "max": max(samples) if samples else None}}
        if clients is not None or runtimes is not None:
            rows.append(row)
    return rows


def project(server, header_lines, log_lines, *, clients=None, runtimes=None, game_port=7781):
    return {"schema": SCHEMA, "passed": True, "server": project_server(server),
        "udp": project_udp(header_lines, log_lines, game_port=game_port),
        "participants": project_optional(clients, runtimes, server_actor_ids={row["actor_id"] for row in server["social_payloads"]}, match_id=server["match_id"]),
        "limits": [
            "Performance measurements are not privacy proof and do not repair a failed kill/far-interval temporal correlation.",
            "UDP peer, protobuf recipient and local participant labels are independent; this input does not prove socket-to-actor mapping.",
            "UDP payload lengths exclude IP/UDP/link headers and differ from Social protobuf-only payload counts and local gRPC counters.",
            "Per-direction active rates use that direction's first/last packet span; aggregate rates use the whole capture packet span, not session or match duration.",
            "Packet headers show volume/directions only, not absence of secret payloads; kernel drops0 does not prove end-to-end delivery.",
            "Server timing starts at metrics initialization and ends at GameOver; capture/client/Runtime windows differ and are not normalized.",
            "An empty client match field after cleanup is accepted only with its exact actor/accepted bridge session in a Runtime report bound to the server match; this is performance binding, not terminal gameplay acceptance.",
            "p95 visibility/refresh uses latest1024 samples; maxima cover all samples. Refresh cost is authority/session work, not a whole engine CPU tick.",
            "Optional absent server CPU/peak fields remain null. Peak working set is process-lifetime high-water RSS, not incremental match allocation.",
            "Unity meanFps is a headless loop rate here, not display FPS. Legacy comparison is only valid for similarly measured headless loops; no causal speedup is claimed.",
            "Command-result RTT is local Unity/Runtime bridge round trip, not remote multiplayer RTT. Managed/traced heap counters are not process RSS."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, type=Path)
    parser.add_argument("--udp-headers", required=True, type=Path)
    parser.add_argument("--capture-log", required=True, type=Path)
    parser.add_argument("--game-port", type=int, default=7781)
    parser.add_argument("--clients", type=Path, nargs=4)
    parser.add_argument("--runtimes", type=Path, nargs=4)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.udp_headers.stat().st_size > 128 * 1024 * 1024 or args.capture_log.stat().st_size > 16384:
            raise InvalidEvidence("capture_input_too_large")
        server = read_json(args.server)
        with args.udp_headers.open() as headers, args.capture_log.open() as log:
            value = project(server, headers, log,
                clients=[read_json(path) for path in args.clients] if args.clients else None,
                runtimes=[read_json(path) for path in args.runtimes] if args.runtimes else None, game_port=args.game_port)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        code = str(error) if isinstance(error, InvalidEvidence) else type(error).__name__
        value = {"schema": SCHEMA, "passed": False, "error_code": code}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n"); temporary.replace(args.output)
    print(json.dumps({"passed": value["passed"], **({"error_code": value["error_code"]} if not value["passed"] else {})}))
    return 0 if value["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
