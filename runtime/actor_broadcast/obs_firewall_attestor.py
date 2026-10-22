"""Narrow root-run PF CHECK service, never a privileged command endpoint.

Install protection explicitly with obs_firewall first. This service only
reads the fixed root-private state and current PF rules. Its socket returns
safe challenged proofs to the unprivileged controller. Stopping it NEVER
removes firewall protection; explicit removal is a separate operation after
the OBS listener is disabled. Deploy this module and obs_firewall.py as an
audited root-owned immutable snapshot, not a user-writable root daemon.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import struct
import time

try:
    from .obs_firewall import FirewallError, PfInstaller, _read_state, _state_path
except ImportError:
    # -I -S deliberately excludes the script directory from sys.path. Load
    # only the fixed audited sibling; never add user package/search paths.
    import importlib.util
    import sys
    _spec = importlib.util.spec_from_file_location("_actor_obs_firewall_snapshot", Path(__file__).with_name("obs_firewall.py"))
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _module
    _spec.loader.exec_module(_module)
    FirewallError, PfInstaller, _read_state, _state_path = (_module.FirewallError,
        _module.PfInstaller, _module._read_state, _module._state_path)


class AttestationError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


DEFAULT_SOCKET_PATH = Path("/private/var/db/actor-broadcast-firewall-run/control.sock")
CLOCK_DOMAIN = "clock_gettime:CLOCK_MONOTONIC"


def attestation_clock_ns():
    # On macOS Python <3.10, monotonic_ns() subtracts a process-local
    # origin; newer Python uses a different, shared Mach uptime clock.
    # Explicit POSIX CLOCK_MONOTONIC has the same kernel origin across
    # both interpreters/processes (and on Linux). Never mix or fall back.
    try:
        return time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    except (AttributeError, OSError) as error:
        raise AttestationError("pf_attestor_clock_unavailable") from error


def peer_uid(connection):
    if hasattr(socket, "SO_PEERCRED"):
        _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        return uid
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    libc = ctypes.CDLL(None, use_errno=True)
    call = libc.getpeereid
    call.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    call.restype = ctypes.c_int
    if call(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise AttestationError("pf_attestor_peer_unverified")
    return uid.value


def verify_path(path, *, uid=0, require_socket=True):
    path = Path(path).absolute()
    # Refuse aliases and any user-writable ancestor, preventing a same-UID
    # impostor socket from replacing a privileged service path.
    if path.parent.resolve() != path.parent: raise AttestationError("pf_attestor_path_unsafe")
    for parent in (path.parent, *path.parent.parents):
        value = parent.lstat()
        if not stat.S_ISDIR(value.st_mode) or value.st_uid != uid or value.st_mode & 0o022:
            raise AttestationError("pf_attestor_directory_unsafe")
    if require_socket:
        value = path.lstat()
        if not stat.S_ISSOCK(value.st_mode) or value.st_uid != uid:
            raise AttestationError("pf_attestor_socket_unsafe")
    return path


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value: raise AttestationError("pf_attestor_duplicate_key")
        value[key] = item
    return value


class PfAttestor:
    def __init__(self, state_path, socket_path, *, port=4455, inspector=None,
                 verify_filesystem=verify_path, administrator_uid=0):
        if type(port) is not int or not 1024 <= port <= 65535: raise AttestationError("pf_attestor_port_invalid")
        self.state_path, self.socket_path = Path(state_path), Path(socket_path)
        self.port = port; self.inspector = inspector; self.verify_filesystem = verify_filesystem
        self.administrator_uid = administrator_uid
        self._server = None; self._connections = set(); self._check_task = None; self._inode = None

    def _inspect(self):
        if self.inspector is not None: return self.inspector()
        state = _read_state(_state_path(self.state_path))
        if state["port"] != self.port: raise AttestationError("pf_attestor_port_mismatch")
        return PfInstaller().inspect(state)

    async def start(self):
        if os.geteuid() != self.administrator_uid: raise AttestationError("pf_attestor_administrator_required")
        self.verify_filesystem(self.socket_path, require_socket=False)
        if len(os.fsencode(self.socket_path)) > 100: raise AttestationError("pf_attestor_socket_path_bound")
        if self.socket_path.exists() or self.socket_path.is_symlink(): raise AttestationError("pf_attestor_socket_exists")
        # start_unix_server(path=...) removes pre-existing sockets. Bind our
        # own descriptor instead, so concurrent startup never unlinks a peer.
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.bind(str(self.socket_path)); connection.setblocking(False)
            value = self.socket_path.lstat(); self._inode = (value.st_dev, value.st_ino)
            os.chmod(self.socket_path, 0o666)  # safe read-only proofs, no mutations
            self._server = await asyncio.start_unix_server(self._handle, sock=connection, limit=1024)
        except BaseException:
            connection.close()
            if self._inode is not None:
                current = self.socket_path.lstat()
                if (current.st_dev, current.st_ino) == self._inode: self.socket_path.unlink()
            raise

    async def _handle(self, reader, writer):
        task = asyncio.current_task(); self._connections.add(task)
        try:
            if len(self._connections) > 8: raise AttestationError("pf_attestor_busy")
            raw = await asyncio.wait_for(reader.readuntil(b"\n"), 1)
            if len(raw) > 512: raise AttestationError("pf_attestor_bound")
            request = json.loads(raw, object_pairs_hook=_unique)
            if (not isinstance(request, dict) or set(request) != {"schema", "challenge", "port", "clock_domain"}
                    or request["schema"] != "actor.obs-pf-query.v2" or request["clock_domain"] != CLOCK_DOMAIN
                    or type(request["port"]) is not int
                    or request["port"] != self.port or not isinstance(request["challenge"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", request["challenge"])):
                raise AttestationError("pf_attestor_query_rejected")
            if self._check_task is not None and not self._check_task.done(): raise AttestationError("pf_attestor_busy")
            async def inspect_once():
                result = await asyncio.to_thread(self._inspect)
                return result, attestation_clock_ns()
            self._check_task = asyncio.create_task(inspect_once())
            self._check_task.add_done_callback(lambda value: None if value.cancelled() else value.exception())
            result, checked = await asyncio.wait_for(asyncio.shield(self._check_task), 1.2)
            if (result.get("verified") is not True or result.get("port") != self.port
                    or result.get("loopback_allowed") is not True
                    or result.get("non_loopback_ipv4_ipv6_denied") is not True):
                raise AttestationError("pf_attestor_rules_unverified")
            response = {"schema": "actor.obs-pf-attestation.v2", "challenge": request["challenge"],
                "clock_domain": CLOCK_DOMAIN,
                "port": self.port, "verified": True, "checked_at_monotonic_ns": checked,
                "loopback_allowed": True, "non_loopback_ipv4_ipv6_denied": True}
        except asyncio.CancelledError:
            writer.close(); self._connections.discard(task); raise
        except Exception:
            response = {"verified": False, "reason": "pf_attestation_unavailable"}
        try:
            writer.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
            await asyncio.wait_for(writer.drain(), .3)
        except (OSError, asyncio.TimeoutError): pass
        finally:
            writer.close(); self._connections.discard(task)

    async def close(self):
        if self._server is not None:
            self._server.close(); await self._server.wait_closed()
        for task in list(self._connections): task.cancel()
        # The running check has fixed command timeouts and performs no writes.
        # Do not cancel its thread and spawn replacements on repeated clients.
        if self._inode is not None and self.socket_path.exists():
            value = self.socket_path.lstat()
            if stat.S_ISSOCK(value.st_mode) and (value.st_dev, value.st_ino) == self._inode:
                self.socket_path.unlink()


class FirewallAttestationClient:
    def __init__(self, socket_path, *, port=4455, timeout=1.5, max_age_seconds=1.5,
                 verify_filesystem=verify_path, get_peer_uid=peer_uid, administrator_uid=0,
                 query_lease_factory=None):
        if not .1 <= timeout <= 3 or not .1 <= max_age_seconds <= 3: raise AttestationError("pf_attestor_time_bound")
        self.socket_path = Path(socket_path); self.port = port; self.timeout = timeout
        self.max_age_ns = int(max_age_seconds * 1e9)
        self.verify_filesystem, self.get_peer_uid = verify_filesystem, get_peer_uid
        self.administrator_uid = administrator_uid
        self._query_lease_factory = query_lease_factory
        self._verified_at = None; self.last_error = "pf_attestation_missing"
        self._lock = asyncio.Lock()
        self._query_lease = None

    @property
    def verified(self):
        try:
            return self._verified_at is not None and 0 <= attestation_clock_ns() - self._verified_at <= self.max_age_ns
        except AttestationError:
            return False

    async def refresh(self):
        async with self._lock: return await self._refresh()

    async def _refresh(self):
        # A still-fresh previous proof remains valid during the bounded query;
        # starting a poll must not itself create a false audio unsafe event.
        writer = lease = None
        deadline = asyncio.get_running_loop().time() + self.timeout
        try:
            async def exchange():
                nonlocal writer, lease
                # Import client-only ownership lazily. The immutable root-run
                # service snapshot has no new package/file dependency.
                from .ownership import LeaseError
                self.verify_filesystem(self.socket_path)
                lease = (self._query_lease_factory or _client_query_lease)(self.socket_path)
                self._query_lease = lease  # retained before the first await
                while True:
                    if asyncio.get_running_loop().time() >= deadline: raise asyncio.TimeoutError
                    try:
                        lease.acquire()
                        break
                    except LeaseError as error:
                        if error.code != "channel_already_owned": raise
                        await asyncio.sleep(.01)
                if asyncio.get_running_loop().time() >= deadline: raise asyncio.TimeoutError
                challenge = secrets.token_hex(32); started = attestation_clock_ns()
                reader, writer = await asyncio.open_unix_connection(self.socket_path, limit=2048)
                if self.get_peer_uid(writer.get_extra_info("socket")) != self.administrator_uid:
                    raise AttestationError("pf_attestor_peer_unverified")
                writer.write(json.dumps({"schema": "actor.obs-pf-query.v2", "challenge": challenge,
                    "port": self.port, "clock_domain": CLOCK_DOMAIN}).encode() + b"\n")
                await writer.drain(); raw = await reader.readuntil(b"\n")
                if len(raw) > 1024: raise AttestationError("pf_attestor_bound")
                value = json.loads(raw, object_pairs_hook=_unique)
                now = attestation_clock_ns()
                if (set(value) != {"schema", "challenge", "port", "clock_domain", "verified", "checked_at_monotonic_ns", "loopback_allowed", "non_loopback_ipv4_ipv6_denied"}
                        or value["schema"] != "actor.obs-pf-attestation.v2" or value["clock_domain"] != CLOCK_DOMAIN
                        or value["challenge"] != challenge
                        or type(value["port"]) is not int or value["port"] != self.port or value["verified"] is not True
                        or value["loopback_allowed"] is not True or value["non_loopback_ipv4_ipv6_denied"] is not True
                        or type(value["checked_at_monotonic_ns"]) is not int
                        or not started <= value["checked_at_monotonic_ns"] <= now
                        or now - value["checked_at_monotonic_ns"] > self.max_age_ns):
                    raise AttestationError("pf_attestor_proof_invalid")
                if asyncio.get_running_loop().time() >= deadline: raise asyncio.TimeoutError
                self._verified_at = value["checked_at_monotonic_ns"]; self.last_error = None
                return True
            return await asyncio.wait_for(exchange(), self.timeout)
        except asyncio.CancelledError:
            self._verified_at = None; raise
        except Exception as error:
            self._verified_at = None
            self.last_error = getattr(error, "code", "pf_attestation_unavailable")
            return False
        finally:
            try:
                if writer is not None: writer.close()
            finally:
                try:
                    if lease is not None: lease.release()
                finally: self._query_lease = None


def _client_query_lease(socket_path):
    """One non-configurable per-account mutex for this root service path.

    HOME/output/config cannot split the namespace. No inode is ever unlinked.
    Only a client query loads these dependencies, never root service startup.
    """
    import hashlib
    import pwd
    from .ownership import ChannelLease
    try:
        uid = os.geteuid()
        home = Path(pwd.getpwuid(uid).pw_dir)
        service = Path(socket_path).absolute()
        if (not home.is_absolute() or home.resolve() != home
                or service.parent.resolve() != service.parent):
            raise AttestationError("pf_attestor_query_lock_unsafe")
        key = hashlib.sha256(str(service).encode("utf-8")).hexdigest()
        directory = home / ".actor" / "broadcast" / "pf-attestor-query-locks" / str(uid) / key
        # Check every existing ancestor before ChannelLease may mkdir. A
        # writable ancestor must not let another UID replace the lock namespace.
        for parent in reversed((directory, *directory.parents)):
            try: info = parent.lstat()
            except FileNotFoundError: break
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, uid}
                    or info.st_mode & 0o022):
                raise AttestationError("pf_attestor_query_lock_unsafe")
        return ChannelLease("pf_query", directory=directory)
    except AttestationError: raise
    except (OSError, ValueError, KeyError):
        raise AttestationError("pf_attestor_query_lock_unsafe") from None


async def _serve(args):
    service = PfAttestor(args.state, args.socket, port=args.port)
    await service.start(); stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(signum, stop.set)
    try: await stop.wait()
    finally: await service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--state", required=True)
    parser.add_argument("--socket", default=str(DEFAULT_SOCKET_PATH), help="Owned root-only-ancestor attestation socket")
    parser.add_argument("--port", type=int, default=4455)
    args = parser.parse_args()
    try: asyncio.run(_serve(args))
    except (AttestationError, FirewallError, OSError, ValueError): raise SystemExit("pf_attestor_start_failed")


if __name__ == "__main__": main()
