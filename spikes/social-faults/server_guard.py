#!/usr/bin/env python3
"""Exact-instance staging-only server inspection / opt-in signal guard.

Execute on the staging Linux host, normally via root-owned SSH stdin. This
module never opens SSH, creates a lobby, reads environ, or signals by name.
The explicit ``terminate`` operation requires a recent inspect identity and a
fresh ordinary lifecycle observer readiness file. Stdout is a safe projection;
the separate 0600 identity file is private local evidence, not a public report.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import subprocess
import sys
import time
import urllib.request
import uuid


API = "http://127.0.0.1:5081"
PORT = 7781
SERVER_EXE = "/opt/actor/staging/m7-visibility/server/AgentsArenaServer"
CONTROL_EXE = "/opt/actor/staging/m7-visibility/control-plane/Actor.ControlPlane"
STATE = Path("/var/lib/actor/m7-visibility")
SCHEMA = "actor.staging-server-guard.v1"
IDENTITY_SCHEMA = "actor.staging-server-identity.v1"
CHECKS = ("fresh_exact_lobby", "exact_staging_control_plane", "exact_allocation_record",
    "actor_owned_exact_server", "current_control_plane_parent", "exclusive_udp_owner",
    "same_network_namespace", "actual_ready_identity", "same_inspected_instance", "fresh_observer_ready")
CODES = frozenset(CHECKS) | {"invalid_arguments", "inspection_expired", "identity_file_invalid",
    "bounded_io_failed", "host_not_supported", "pidfd_unavailable", "signal_failed", "internal_guard_failure"}


class GuardRejected(Exception):
    def __init__(self, code):
        self.code = code if code in CODES else "internal_guard_failure"
        super().__init__(self.code)


def require(condition, code):
    if not condition:
        raise GuardRejected(code)


def integer(value, minimum=1, maximum=2**63-1):
    return type(value) is int and minimum <= value <= maximum


def canonical_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F-]{32,36}", value):
        raise GuardRejected("invalid_arguments")
    try:
        return uuid.UUID(value).hex
    except ValueError:
        raise GuardRejected("invalid_arguments") from None


def target_profile(staging_name="m7-visibility", game_version="0.8.0", protocol_version="3"):
    # Derive paths only beneath the dedicated staging roots. Arbitrary executable
    # and state paths (in particular production current symlinks) are not options.
    require(isinstance(staging_name, str) and len(staging_name) <= 48
        and re.fullmatch(r"m[1-9][0-9]{0,2}-[a-z0-9]+(?:-[a-z0-9]+)*", staging_name) is not None
        and isinstance(game_version, str) and re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", game_version) is not None
        and isinstance(protocol_version, str) and re.fullmatch(r"[1-9][0-9]{0,2}", protocol_version) is not None,
        "invalid_arguments")
    return dict(staging_name=staging_name, game_version=game_version, protocol_version=protocol_version)


def profile_paths(expected):
    profile = target_profile(expected.get("staging_name", "m7-visibility"),
        expected.get("game_version", "0.8.0"), expected.get("protocol_version", "3"))
    name = profile["staging_name"]
    return (f"/opt/actor/staging/{name}/server/AgentsArenaServer",
        f"/opt/actor/staging/{name}/control-plane/Actor.ControlPlane", Path("/var/lib/actor") / name)


def expected_identity(room, lobby, control_pid, control_start, *, staging_name="m7-visibility",
        game_version="0.8.0", protocol_version="3"):
    require(isinstance(room, str) and re.fullmatch(r"[A-Z0-9]{6}", room) is not None
        and integer(control_pid, 2) and integer(control_start), "invalid_arguments")
    return dict(room_code=room, lobby_id=canonical_id(lobby), control_plane_pid=control_pid,
        control_plane_start_ticks=control_start, **target_profile(staging_name, game_version, protocol_version))


def proc_stat(value):
    """Parse Linux stat without assuming comm contains no spaces/parentheses."""
    try:
        close = value.rindex(")")
        pid = int(value[:value.index(" (")])
        fields = value[close+2:].split()
        parent, started = int(fields[1]), int(fields[19])
        require(integer(pid, 2) and integer(parent, 1) and integer(started), "bounded_io_failed")
        return dict(pid=pid, parent_pid=parent, start_ticks=started, state=fields[0])
    except (ValueError, IndexError, AttributeError):
        raise GuardRejected("bounded_io_failed") from None


def parse_socket_owners(value):
    lines = value.splitlines()
    require(0 < len(lines) <= 16, "exclusive_udp_owner")
    owners = set()
    for line in lines:
        found = re.findall(r"\bpid=(\d+),", line)
        require(bool(found) and len(line) <= 16384, "exclusive_udp_owner")
        owners.update(map(int, found))
    return owners


def allocation_pid(value, expected):
    found = []
    for line in value.splitlines():
        if len(line) > 65536:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        state = item.get("State") if isinstance(item, dict) else None
        if not isinstance(state, dict) or state.get("{OriginalFormat}") != "ServerProcessStarted {LobbyId} {RoomCode} {Pid} {Port}":
            continue
        try:
            match = canonical_id(state.get("LobbyId"))
        except GuardRejected:
            continue
        if match != expected["lobby_id"]:
            continue
        require(state.get("RoomCode") == expected["room_code"] and type(state.get("Port")) is int
            and state["Port"] == PORT and integer(state.get("Pid"), 2), "exact_allocation_record")
        found.append(state["Pid"])
    require(len(found) == 1, "exact_allocation_record")
    return found[0]


def ready_log_matches(value, expected):
    pattern = re.compile(r"ACTOR_READY match=([a-f0-9]{32}) room=([A-Z0-9]{6}) port=([0-9]{1,5})")
    found = [pattern.fullmatch(line.strip()) for line in value.splitlines()]
    found = [entry for entry in found if entry is not None]
    return (len(found) == 1 and found[0].groups() ==
        (expected["lobby_id"], expected["room_code"], str(PORT)))


def command_identity(command):
    require(isinstance(command, list) and 1 <= len(command) <= 64, "actor_owned_exact_server")
    result = {}
    for key in ("--match-id", "--room-code", "--port", "--game-mode"):
        require(command.count(key) == 1, "actor_owned_exact_server")
        index = command.index(key)
        require(index + 1 < len(command), "actor_owned_exact_server")
        result[key] = command[index+1]
    return result


def validate(evidence, expected):
    """Pure validation. Raw evidence never becomes a public projection."""
    now = evidence["now_unix_ms"]
    server_exe, control_exe, _ = profile_paths(expected)
    lobby, cp, server = evidence["lobby"], evidence["control"], evidence["server"]
    try:
        created = int(datetime.fromisoformat(lobby["createdAt"].replace("Z", "+00:00")).timestamp()*1000)
        lobby_id = canonical_id(lobby.get("lobbyId"))
    except (KeyError, TypeError, ValueError, AttributeError, GuardRejected):
        raise GuardRejected("fresh_exact_lobby") from None
    require(integer(now) and integer(evidence.get("acquired_at_unix_ms"))
        and 0 <= now - evidence["acquired_at_unix_ms"] <= 5000
        and 0 <= now-created <= 1800_000 and lobby_id == expected["lobby_id"]
        and lobby.get("roomCode") == expected["room_code"] and type(lobby.get("gamePort")) is int
        and lobby["gamePort"] == PORT and lobby.get("status") in ("Ready", "Running")
        and lobby.get("gameMode") in ("Sandbox", "SocialDeduction")
        and lobby.get("gameVersion") == expected.get("game_version", "0.8.0")
        and lobby.get("protocolVersion") == expected.get("protocol_version", "3"), "fresh_exact_lobby")
    require(cp["pid"] == expected["control_plane_pid"] and cp["start_ticks"] == expected["control_plane_start_ticks"]
        and cp["pid"] == evidence["control_pid_file"] and cp["executable"] == control_exe
        and cp["uids"] == (evidence["actor_uid"],)*4 and cp["file_uid"] == evidence["actor_uid"]
        and cp["state"] not in ("Z", "X") and evidence["tcp_owners"] == {cp["pid"]}
        and cp["started_unix_ms"] <= created+2000, "exact_staging_control_plane")
    require(evidence["allocation_pid"] == server["pid"], "exact_allocation_record")
    command = command_identity(server["command"])
    require(server["pid"] != cp["pid"] and server["executable"] == server_exe
        and server["uids"] == (evidence["actor_uid"],)*4 and server["file_uid"] == evidence["actor_uid"]
        and server["state"] not in ("Z", "X") and created-2000 <= server["started_unix_ms"] <= created+60000
        and command == {"--match-id": expected["lobby_id"], "--room-code": expected["room_code"],
            "--port": str(PORT), "--game-mode": lobby["gameMode"]}, "actor_owned_exact_server")
    require(server["parent_pid"] == cp["pid"], "current_control_plane_parent")
    require(evidence["udp_owners"] == {server["pid"]}, "exclusive_udp_owner")
    require(server["network_namespace"] == cp["network_namespace"] == evidence["network_namespace"], "same_network_namespace")
    require(evidence["ready_log_matches"] is True, "actual_ready_identity")
    return dict(schema=IDENTITY_SCHEMA, inspected_at_unix_ms=now, **expected,
        server_pid=server["pid"], server_start_ticks=server["start_ticks"], game_mode=lobby["gameMode"])


def validate_previous(previous, current, now):
    require(isinstance(previous, dict) and previous.get("schema") == IDENTITY_SCHEMA
        and integer(previous.get("inspected_at_unix_ms"))
        and 0 <= now-previous["inspected_at_unix_ms"] <= 30000, "inspection_expired")
    keys = ("room_code", "lobby_id", "control_plane_pid", "control_plane_start_ticks", "server_pid", "server_start_ticks", "game_mode",
        "staging_name", "game_version", "protocol_version")
    require(all(previous.get(key) == current[key] and type(previous.get(key)) is type(current[key]) for key in keys), "same_inspected_instance")


def validate_observer(value, expected, now):
    require(isinstance(value, dict) and value.get("schema") == "actor.social-lifecycle-acceptance.v1"
        and value.get("case") == "server-loss-observer" and value.get("ready") is True
        and value.get("room_code") == expected["room_code"] and value.get("match_id") == expected["lobby_id"]
        and integer(value.get("created_at_unix_ms")) and 0 <= now-value["created_at_unix_ms"] <= 30000
        and all(isinstance(value.get(key), str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value[key]) for key in ("actor_id", "session_id"))
        and integer(value.get("client_pid"), 2) and integer(value.get("commands"), 0), "fresh_observer_ready")


def read_file(path, limit, *, owner=None, private=False, tail=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        allowed_owners = owner if isinstance(owner, tuple) else (owner,)
        require(stat.S_ISREG(info.st_mode) and (owner is None or info.st_uid in allowed_owners)
            and (info.st_mode & (0o077 if private else 0o022)) == 0, "bounded_io_failed")
        offset = max(0, info.st_size-limit) if tail else 0
        require(tail or info.st_size <= limit, "bounded_io_failed")
        if offset:
            os.lseek(fd, offset, os.SEEK_SET)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit+1)
        require(len(data) <= limit, "bounded_io_failed")
        if offset:
            data = data.partition(b"\n")[2]
        return data.decode("utf-8", errors="strict")
    finally:
        os.close(fd)


def read_json(path, *, private=False):
    return json.loads(read_file(path, 65536, owner=os.geteuid(), private=private))


def process(pid, actor_uid, boot_ms, ticks_per_second, *, server_exe=SERVER_EXE):
    root = Path("/proc") / str(pid)
    value = proc_stat((root / "stat").read_text())
    require(value["pid"] == pid, "bounded_io_failed")
    uid = re.search(r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$", (root / "status").read_text(), re.MULTILINE)
    require(uid is not None, "bounded_io_failed")
    value.update(executable=os.readlink(root / "exe"), file_uid=root.stat().st_uid,
        uids=tuple(map(int, uid.groups())), network_namespace=os.readlink(root / "ns/net"),
        started_unix_ms=boot_ms+int(value["start_ticks"]*1000/ticks_per_second))
    if value["executable"] == server_exe:
        data = (root / "cmdline").read_bytes()
        require(len(data) <= 16384, "bounded_io_failed")
        value["command"] = data.rstrip(b"\0").decode().split("\0")
    return value


def sockets(kind, port):
    result = subprocess.run(["ss", "-H", "-l" if kind == "tcp" else "-a", "-t" if kind == "tcp" else "-u",
        "-n", "-p", "sport = :"+str(port)], capture_output=True, timeout=3, check=True)
    require(len(result.stdout) <= 262144, "bounded_io_failed")
    return parse_socket_owners(result.stdout.decode())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        raise GuardRejected("fresh_exact_lobby")


def fresh_lobby(room):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(API+"/api/v1/lobbies/"+room, headers={"Cache-Control": "no-cache"})
    with opener.open(request, timeout=3) as response:
        require(response.status == 200 and response.geturl() == request.full_url, "fresh_exact_lobby")
        data = response.read(65537)
    require(len(data) <= 65536, "fresh_exact_lobby")
    value = json.loads(data)
    require(isinstance(value, dict), "fresh_exact_lobby")
    return value


def collect(expected):
    server_exe, control_exe, state = profile_paths(expected)
    uid = pwd.getpwnam("actor").pw_uid
    require(uid > 0, "exact_staging_control_plane")
    boot = re.search(r"^btime (\d+)$", Path("/proc/stat").read_text(), re.MULTILINE)
    require(boot is not None, "bounded_io_failed")
    boot_ms, ticks = int(boot[1])*1000, os.sysconf("SC_CLK_TCK")
    cp = process(expected["control_plane_pid"], uid, boot_ms, ticks, server_exe=server_exe)
    cp_pid = int(read_file(state / "control-plane.pid", 32, owner=uid).strip())
    tcp = sockets("tcp", 5081)
    require(cp["executable"] == control_exe and cp["start_ticks"] == expected["control_plane_start_ticks"]
        and cp_pid == cp["pid"] and tcp == {cp["pid"]}, "exact_staging_control_plane")
    acquired = int(time.time()*1000)
    lobby = fresh_lobby(expected["room_code"])
    # A root-owned nohup shell can open this fixed log before runuser switches
    # the actual CP process to actor. Both trusted owners are valid here only.
    allocation = allocation_pid(read_file(state / "control-plane.log", 4*1024*1024, owner=(0, uid), tail=True), expected)
    server = process(allocation, uid, boot_ms, ticks, server_exe=server_exe)
    ready = ready_log_matches(read_file(state / "matches" / (expected["lobby_id"]+".log"), 4*1024*1024, owner=uid, tail=True), expected)
    return dict(now_unix_ms=int(time.time()*1000), acquired_at_unix_ms=acquired, lobby=lobby,
        control=cp, control_pid_file=cp_pid, server=server, actor_uid=uid, allocation_pid=allocation,
        tcp_owners=sockets("tcp", 5081), udp_owners=sockets("udp", PORT), ready_log_matches=ready,
        network_namespace=os.readlink("/proc/self/ns/net"))


def private_write(path, value):
    # Exclusive creation: never clobber another run's identity or follow links.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, allow_nan=False); stream.write("\n")


def execute(operation, expected, *, identity_path, observer_path=None, signal_name="SIGTERM", collector=collect):
    require(operation in ("inspect", "terminate"), "invalid_arguments")
    require(signal_name in ("SIGTERM", "SIGKILL"), "invalid_arguments")
    if operation == "inspect":
        current = validate(collector(expected), expected)
        private_write(identity_path, current)
        return current
    previous = read_json(identity_path, private=True)
    require(integer(previous.get("server_pid"), 2), "identity_file_invalid")
    require(hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"), "pidfd_unavailable")
    # Hold the exact process before *both* fresh validations. No numeric-PID
    # signal fallback is permitted, even if the process exits during checking.
    descriptor = os.pidfd_open(previous["server_pid"])
    try:
        for _ in range(2):
            current = validate(collector(expected), expected)
            now = int(time.time()*1000)
            validate_previous(previous, current, now)
            validate_observer(read_json(observer_path), expected, now)
        signal.pidfd_send_signal(descriptor, getattr(signal, signal_name))
        return current
    finally:
        os.close(descriptor)


def projection(operation, *, passed, code=None, signal_name="SIGTERM"):
    terminated = passed and operation == "terminate"
    return dict(schema=SCHEMA, operation=operation if operation in ("inspect", "terminate") else None,
        passed=passed is True, scope="Exact staging instance only; no gameplay/Runtime-loss acceptance implied.",
        checks={name: passed is True if name not in ("same_inspected_instance", "fresh_observer_ready")
            else passed is True and operation == "terminate" for name in CHECKS},
        signal=signal_name if terminated and signal_name in ("SIGTERM", "SIGKILL") else None, signal_issued=bool(terminated),
        rejection_code=code if code in CODES else None,
        limits=["No lobby, PID, executable path, private state, log text or credential is published.",
            "Successful signal dispatch is not proof of process exit or client cleanup; the independent observer supplies that evidence."])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "terminate"))
    parser.add_argument("--room", required=True)
    parser.add_argument("--lobby-id", required=True)
    parser.add_argument("--control-plane-pid", required=True, type=int)
    parser.add_argument("--control-plane-start-ticks", required=True, type=int)
    parser.add_argument("--staging-name", default="m7-visibility", help="Dedicated staging basename only; derives fixed executable/state roots")
    parser.add_argument("--game-version", default="0.8.0", help="Exact public game version; historical default unchanged")
    parser.add_argument("--protocol-version", default="3", help="Exact public admission version; historical default unchanged")
    parser.add_argument("--identity", required=True, type=Path, help="Inspect creates a fresh 0600 private file; terminate reuses that exact file")
    parser.add_argument("--observer-ready", type=Path, help="Terminate only: fresh lifecycle server-loss-observer ready.json")
    parser.add_argument("--signal", choices=("SIGTERM", "SIGKILL"), default="SIGTERM",
        help="Explicit owned-instance termination signal; SIGKILL tests abrupt loss, SIGTERM does not prove a crash")
    args = parser.parse_args(argv)
    try:
        expected = expected_identity(args.room, args.lobby_id, args.control_plane_pid, args.control_plane_start_ticks,
            staging_name=args.staging_name, game_version=args.game_version, protocol_version=args.protocol_version)
        require(sys.platform == "linux" and os.geteuid() == 0, "host_not_supported")
        require(args.operation != "terminate" or args.observer_ready is not None, "fresh_observer_ready")
        execute(args.operation, expected, identity_path=args.identity, observer_path=args.observer_ready, signal_name=args.signal)
        result = projection(args.operation, passed=True, signal_name=args.signal)
    except GuardRejected as error:
        result = projection(args.operation, passed=False, code=error.code, signal_name=args.signal)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        result = projection(args.operation, passed=False, code="bounded_io_failed")
    except Exception:
        result = projection(args.operation, passed=False, code="internal_guard_failure")
    print(json.dumps(result, allow_nan=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
