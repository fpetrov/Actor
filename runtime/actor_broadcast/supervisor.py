"""Private guardian ownership and bounded IPC; no application starts on import.

Services own OBS/admission transactions. This module owns their lease and the
isolated, unreaped controller group. It never discovers processes by name and
never treats a dispatched signal as proof that a group has drained.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import signal
import socket
import struct
import sys
import time

from .ownership import ChannelLease


VERSION = 1
MAX_FRAME_BYTES = 32768
MAX_PENDING_REPLIES = 4
SERVICE_OPERATIONS = frozenset((
    "observe", "release_observer", "connect_output", "prepare_output", "audit_output",
    "complete_output_startup", "show_output", "start_stream", "stop_stream",
    "start_record", "stop_record", "emergency_output", "cleanup_output", "output_status", "require_output_idle",
))
OPERATIONS = SERVICE_OPERATIONS | {"hello", "heartbeat", "status", "begin_run", "end_run", "shutdown"}
_CLEANUP_OPERATIONS = {"release_observer", "stop_stream", "stop_record", "emergency_output", "cleanup_output", "output_status", "require_output_idle", "audit_output"}
_ID = re.compile(r"[a-f0-9]{32}\Z")
_ROOM = re.compile(r"[A-Z0-9]{6}\Z")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class SupervisorError(RuntimeError):
    def __init__(self, code):
        self.code = code if isinstance(code, str) and _CODE.fullmatch(code) else "supervisor_failed"
        super().__init__(self.code)


def _bounded(value, depth=0, count=None):
    count = [0] if count is None else count
    count[0] += 1
    if depth > 8 or count[0] > 256:
        raise SupervisorError("supervisor_frame_bound")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > 2**63 - 1: raise SupervisorError("supervisor_frame_bound")
        return
    if type(value) is float:
        if not math.isfinite(value): raise SupervisorError("supervisor_frame_bound")
        return
    if type(value) is str:
        try: size = len(value.encode("utf-8"))
        except UnicodeError: raise SupervisorError("supervisor_frame_shape") from None
        if size > 4096 or "\x00" in value:
            raise SupervisorError("supervisor_frame_bound")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or len(key) > 128: raise SupervisorError("supervisor_frame_shape")
            _bounded(key, depth + 1, count); _bounded(item, depth + 1, count)
        return
    if type(value) is list:
        for item in value: _bounded(item, depth + 1, count)
        return
    raise SupervisorError("supervisor_frame_shape")


def encode_packet(value):
    if type(value) is not dict: raise SupervisorError("supervisor_frame_shape")
    _bounded(value)
    try: raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError): raise SupervisorError("supervisor_frame_shape") from None
    if not 1 <= len(raw) <= MAX_FRAME_BYTES: raise SupervisorError("supervisor_frame_bound")
    return struct.pack("!I", len(raw)) + raw


def decode_packet(frame):
    if type(frame) is not bytes or len(frame) < 5: raise SupervisorError("supervisor_frame_shape")
    length = struct.unpack("!I", frame[:4])[0]
    if not 1 <= length <= MAX_FRAME_BYTES or len(frame) != length + 4:
        raise SupervisorError("supervisor_frame_bound")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise SupervisorError("supervisor_frame_shape")
            result[key] = value
        return result
    try: result = json.loads(frame[4:].decode("utf-8"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError): raise SupervisorError("supervisor_frame_shape") from None
    if type(result) is not dict: raise SupervisorError("supervisor_frame_shape")
    _bounded(result)
    return result


def parse_request(value):
    _bounded(value)
    if (type(value) is not dict or set(value) != {"v", "id", "op", "run_id", "args"}
            or type(value["v"]) is not int or value["v"] != VERSION
            or type(value["id"]) is not int or not 1 <= value["id"] <= 2**53 - 1
            or type(value["op"]) is not str or value["op"] not in OPERATIONS
            or type(value["args"]) is not dict
            or value["run_id"] is not None and (type(value["run_id"]) is not str or not _ID.fullmatch(value["run_id"]))):
        raise SupervisorError("supervisor_request_shape")
    return value


class FramedSocket:
    """An unnamed inherited AF_UNIX socketpair, never a public listener.

    The receiving process adopts this one descriptor. The caller passes it only
    to the controller and closes its other copies so EOF retains its meaning.
    """
    def __init__(self, sock, *, timeout=3.0):
        if (sock.family != socket.AF_UNIX or sock.type != socket.SOCK_STREAM
                or sock.getsockname() not in ("", b"") or sock.getpeername() not in ("", b"")):
            raise SupervisorError("supervisor_private_socket_required")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 5:
            raise SupervisorError("supervisor_timeout_invalid")
        sock.setblocking(False); sock.set_inheritable(False)
        self.socket, self.timeout = sock, timeout
        self._send_lock = asyncio.Lock(); self._closed = False

    @classmethod
    def from_inherited_fd(cls, fd, **options):
        if type(fd) is not int or fd < 3: raise SupervisorError("supervisor_private_socket_required")
        sock = socket.socket(fileno=fd)
        try: return cls(sock, **options)
        except BaseException:
            sock.close(); raise

    async def send(self, value):
        data = encode_packet(value)
        async def locked_send():
            async with self._send_lock:
                if self._closed: raise SupervisorError("supervisor_channel_closed")
                await asyncio.get_running_loop().sock_sendall(self.socket, data)
        # The deadline includes waiting behind another blocked frame.
        await asyncio.wait_for(locked_send(), self.timeout)

    async def receive(self):
        async def exact(size):
            parts = bytearray()
            while len(parts) < size:
                part = await asyncio.get_running_loop().sock_recv(self.socket, size - len(parts))
                if not part: raise EOFError
                parts.extend(part)
            return bytes(parts)
        # Idle is bounded by GuardianHost's separate heartbeat timer. Only an
        # in-progress frame has this deadline; silence is not a framing error.
        first = await asyncio.get_running_loop().sock_recv(self.socket, 1)
        if not first: raise EOFError
        async def remainder():
            prefix = first + await exact(3); size = struct.unpack("!I", prefix)[0]
            if not 1 <= size <= MAX_FRAME_BYTES: raise SupervisorError("supervisor_frame_bound")
            return decode_packet(prefix + await exact(size))
        return await asyncio.wait_for(remainder(), self.timeout)

    async def aclose(self):
        if not self._closed:
            self._closed = True; self.socket.close()


def _darwin_unreaped_exit(pid):
    # Darwin SDK sys/signal.h:178-189, sys/wait.h: P_PID/WEXITED/WNOWAIT.
    # CPython 3.12 on macOS does not expose os.waitid despite libc supporting it.
    # This is the public libc waitid ABI, not a private process/SDK handle.
    import ctypes
    class Sigval(ctypes.Union):
        _fields_ = [("integer", ctypes.c_int), ("pointer", ctypes.c_void_p)]
    class Siginfo(ctypes.Structure):
        _fields_ = [("signo", ctypes.c_int), ("error", ctypes.c_int), ("code", ctypes.c_int),
            ("pid", ctypes.c_int32), ("uid", ctypes.c_uint32), ("status", ctypes.c_int),
            ("address", ctypes.c_void_p), ("value", Sigval), ("band", ctypes.c_long),
            ("reserved", ctypes.c_ulong * 7)]
    if ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(Siginfo) != 104 or Siginfo.pid.offset != 12:
        raise SupervisorError("supervisor_exit_probe_unavailable")
    try:
        waitid = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True).waitid
        waitid.argtypes = (ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(Siginfo), ctypes.c_int)
        waitid.restype = ctypes.c_int
        info = Siginfo()
        result = waitid(1, pid, ctypes.byref(info), 0x04 | 0x01 | 0x20)
    except (OSError, AttributeError): raise SupervisorError("supervisor_exit_probe_unavailable") from None
    if result != 0: raise SupervisorError("supervisor_exit_probe_failed")
    if info.pid == 0: return False
    if info.pid != pid or info.signo != signal.SIGCHLD or info.code not in (1, 2, 3):
        raise SupervisorError("supervisor_exit_probe_failed")
    return True


def unreaped_controller_exited(pid):
    """Observe our direct child's exit without releasing its reserved PID."""
    if type(pid) is not int or not 1 < pid <= 2**31 - 1:
        raise SupervisorError("supervisor_exit_probe_failed")
    if not hasattr(os, "waitid") and sys.platform == "darwin": return _darwin_unreaped_exit(pid)
    names = ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "CLD_EXITED", "CLD_KILLED", "CLD_DUMPED")
    if not hasattr(os, "waitid") or any(not hasattr(os, name) for name in names):
        raise SupervisorError("supervisor_exit_probe_unavailable")
    try: result = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except (OSError, ValueError): raise SupervisorError("supervisor_exit_probe_failed") from None
    if result is None: return False
    if result.si_pid != pid or result.si_code not in (os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED):
        raise SupervisorError("supervisor_exit_probe_failed")
    return True


class RetainedControllerGroup:
    """Only this direct Popen, created with start_new_session=True.

    group_members must query only the retained PGID and fail on unknown results.
    Never poll/reap the leader before the final group kill/drain: its unreaped
    PID reserves the process-group identity across the entire cleanup attempt.
    """
    def __init__(self, process, *, group_members, verify_group=os.getpgid,
                 send_signal=os.killpg, wait_child=None, leader_exited=unreaped_controller_exited,
                 clock=time.monotonic, sleep=asyncio.sleep,
                 term_grace=.5, drain_timeout=3.0):
        if (type(process.pid) is not int or process.pid <= 1 or process.returncode is not None
                or verify_group(process.pid) != process.pid):
            raise SupervisorError("supervisor_group_identity_invalid")
        for value in (term_grace, drain_timeout):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 5:
                raise SupervisorError("supervisor_timeout_invalid")
        self.process, self.pid = process, process.pid
        self.group_members, self.send_signal, self.clock, self.sleep = group_members, send_signal, clock, sleep
        self.wait_child = wait_child or (lambda timeout: asyncio.to_thread(process.wait, timeout=timeout))
        self.leader_exited = leader_exited
        self.term_grace, self.drain_timeout = term_grace, drain_timeout
        self.closed = False; self._killed = False; self._lock = asyncio.Lock()
        self._last_clock = None

    def _now(self):
        value = self.clock()
        if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
                or self._last_clock is not None and value < self._last_clock):
            raise SupervisorError("supervisor_clock_invalid")
        self._last_clock = value
        return value

    def members(self):
        result = self.group_members(self.pid)
        if (type(result) not in (tuple, list) or len(result) > 256
                or any(type(pid) is not int or pid <= 1 for pid in result) or len(set(result)) != len(result)):
            raise SupervisorError("supervisor_group_members_unverified")
        return tuple(result)

    async def terminate(self):
        async with self._lock:
            if self.closed: return True
            if not self._killed:
                if self.process.returncode is not None: raise SupervisorError("supervisor_group_reaped_early")
                exited = self.leader_exited(self.pid)
                if type(exited) is not bool: raise SupervisorError("supervisor_exit_probe_failed")
                # Query AFTER exit proof: the leader could have forked between
                # an earlier membership snapshot and its actual exit.
                members = self.members()  # Refuse unknown enumeration before signalling.
                # Darwin may reject killpg for an already-zombie-only group.
                # No signal is needed only after actual non-reaping exit AND
                # no other member: nobody remains capable of forking. EPERM
                # on any live/unknown/nonempty group remains an error.
                if not (exited and all(pid == self.pid for pid in members)):
                    try: self.send_signal(self.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                    await self.sleep(self.term_grace)
                    if self.process.returncode is not None: raise SupervisorError("supervisor_group_reaped_early")
                    # TERM may have left only the retained zombie leader.
                    # Re-prove exit before membership: its last fork must not
                    # escape KILL through an earlier sole-leader snapshot.
                    exited = self.leader_exited(self.pid)
                    if type(exited) is not bool: raise SupervisorError("supervisor_exit_probe_failed")
                    members = self.members()
                    if not (exited and all(pid == self.pid for pid in members)):
                        try: self.send_signal(self.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                self._killed = True
            deadline = self._now() + self.drain_timeout
            # Signal dispatch alone is not an exit proof: a still-running
            # leader could fork after an otherwise empty membership snapshot.
            while True:
                exited = self.leader_exited(self.pid)
                if type(exited) is not bool: raise SupervisorError("supervisor_exit_probe_failed")
                if exited: break
                if self._now() >= deadline: raise SupervisorError("supervisor_group_not_drained")
                await self.sleep(.02)
            while any(pid != self.pid for pid in self.members()):
                if self._now() >= deadline: raise SupervisorError("supervisor_group_not_drained")
                await self.sleep(.02)
            # Reap only after no other group member can remain/fork. A failed
            # wait retains this object; a retry never signals a reused PGID.
            result = await self.wait_child(self.drain_timeout)
            if type(result) is not int: raise SupervisorError("supervisor_group_wait_unverified")
            self.closed = True
            return True


class GuardianHost:
    """One guardian per controller; leases are held per active broadcast run.

    A failed shutdown returns False and retains ownership for explicit retry.
    The outer CLI must retain this host/process until closed, not discard it in
    a finally block. No automatic restart or ownership transfer is implemented.
    """
    def __init__(self, channel, controller, services, *, lease_factory=ChannelLease,
                 clock=time.monotonic, heartbeat_timeout=2.0, startup_timeout=10.0,
                 operation_timeout=30.0, cleanup_timeout=30.0):
        for value in (heartbeat_timeout, startup_timeout, operation_timeout, cleanup_timeout):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 60:
                raise SupervisorError("supervisor_timeout_invalid")
        self.channel, self.controller, self.services, self.lease_factory = channel, controller, services, lease_factory
        self.clock = clock
        self.heartbeat_timeout, self.startup_timeout = heartbeat_timeout, startup_timeout
        self.operation_timeout, self.cleanup_timeout = operation_timeout, cleanup_timeout
        self.created_at = self.last_heartbeat = self._now()
        self._clock_last = self.created_at
        self._hello = False; self._heartbeat_sequence = self._request_sequence = 0
        self.run_id = self.room_code = self.lease = None
        self.run_unsafe = False; self.terminal = False; self.closed = False; self.reason = None
        self.cleanup_failures = (); self._operation = None; self._cleanup_task = None
        self._cleanup_jobs = {}; self._replies = set(); self._transport = None
        self._unsafe_notified = False
        services.bind_controller(controller)
        services.bind_unsafe(self._unsafe)

    def _now(self):
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise SupervisorError("supervisor_clock_invalid")
        if hasattr(self, "_clock_last") and now < self._clock_last: raise SupervisorError("supervisor_clock_invalid")
        self._clock_last = now
        return now

    def _status(self):
        value = self.services.status()
        if type(value) is not dict: raise SupervisorError("supervisor_status_invalid")
        _bounded(value)
        return value

    def _unsafe(self, code):
        self.run_unsafe = True
        if self._transport is not None and not self.terminal and not self._unsafe_notified:
            self._unsafe_notified = True
            try:
                message = {"v": VERSION, "event": "unsafe", "run_id": self.run_id,
                    "error": SupervisorError(code).code, "output_status": self._status()}
                self._queue_reply(lambda: self._send(message))
            except BaseException: self.fail("supervisor_notification_failed")

    def _queue_reply(self, factory):
        if len(self._replies) >= MAX_PENDING_REPLIES:
            self.fail("supervisor_reply_backpressure")
            raise SupervisorError("supervisor_reply_backpressure")
        task = asyncio.create_task(factory())
        self._replies.add(task)
        def finished(value):
            self._replies.discard(value)
            if not value.cancelled() and value.exception() is not None:
                self.fail("supervisor_notification_failed")
        task.add_done_callback(finished)

    def heartbeat(self, sequence, run_id):
        if (not self.check_deadline() or not self._hello or run_id is not None
                or type(sequence) is not int or not self._heartbeat_sequence < sequence <= 2**53 - 1):
            raise SupervisorError("supervisor_heartbeat_rejected")
        self._heartbeat_sequence = sequence; self.last_heartbeat = self._now()

    def check_deadline(self):
        if self.terminal: return False
        now = self._now()
        if now - (self.last_heartbeat if self._hello else self.created_at) >= (self.heartbeat_timeout if self._hello else self.startup_timeout):
            self.fail("supervisor_heartbeat_lost" if self._hello else "supervisor_handshake_timeout")
            return False
        return True

    def fail(self, code):
        if not self.terminal:
            self.terminal = True; self.reason = SupervisorError(code).code
        return self.reason

    async def begin_run(self, room_code, session_id):
        if self.terminal or self.run_id is not None: raise SupervisorError("supervisor_run_unavailable")
        if type(room_code) is not str or not _ROOM.fullmatch(room_code) or type(session_id) is not str or not _ID.fullmatch(session_id):
            raise SupervisorError("supervisor_run_invalid")
        self.lease = self.lease_factory(self.channel).acquire()
        self.run_id, self.room_code, self.run_unsafe = session_id, room_code, False
        self._unsafe_notified = False
        await self.services.begin_run(room_code, session_id)

    async def end_run(self):
        if self.run_id is None: return True
        if self.terminal: raise SupervisorError("supervisor_terminal")
        if self._operation is not None and not self._operation.done(): raise SupervisorError("supervisor_operation_pending")
        if await self.services.cleanup_run() is not True: raise SupervisorError("supervisor_cleanup_unverified")
        self.lease.release(); self.lease = None
        self.run_id = self.room_code = None; self.run_unsafe = False
        return True

    async def _send(self, message):
        try: await self._transport.send(message)
        except BaseException:
            self.fail("supervisor_transport_lost")

    async def _reply(self, request_id, *, value=None, error=None):
        message = {"v": VERSION, "id": request_id, "ok": error is None, "output_status": self._status()}
        if error is None: message["value"] = value
        else: message["error"] = SupervisorError(error).code
        await self._send(message)

    async def _operate(self, request):
        try:
            op, args = request["op"], request["args"]
            if op == "begin_run":
                if set(args) != {"room_code"}: raise SupervisorError("supervisor_request_shape")
                await self.begin_run(args["room_code"], request["run_id"]); return None
            if op == "end_run":
                if args: raise SupervisorError("supervisor_request_shape")
                # This operation is itself retained; only a previous pending
                # mutation must block a normal end_run.
                if await self.services.cleanup_run() is not True: raise SupervisorError("supervisor_cleanup_unverified")
                if self.lease is not None: self.lease.release(); self.lease = None
                self.run_id = self.room_code = None; self.run_unsafe = False
                return True
            if self.run_id is None or request["run_id"] != self.run_id: raise SupervisorError("supervisor_run_mismatch")
            if self.run_unsafe and op not in _CLEANUP_OPERATIONS: raise SupervisorError("supervisor_run_unsafe")
            self.services.validate_call(op, args)
            return await self.services.call(op, args)
        except BaseException:
            self.run_unsafe = True
            raise

    async def _operation_reply(self, request, transaction):
        done, _ = await asyncio.wait((transaction,), timeout=self.operation_timeout)
        if transaction not in done:
            self.fail("supervisor_operation_timeout")
            await self._reply(request["id"], error="supervisor_operation_pending")
            return
        try: value = transaction.result()
        except BaseException as error: await self._reply(request["id"], error=getattr(error, "code", "supervisor_service_failed"))
        else: await self._reply(request["id"], value=value)

    async def _receive_request(self, request):
        request = parse_request(request)
        if not self.check_deadline(): raise SupervisorError("supervisor_terminal")
        if request["id"] <= self._request_sequence: raise SupervisorError("supervisor_request_replayed")
        self._request_sequence = request["id"]
        op, args = request["op"], request["args"]
        if op == "hello":
            if not self.check_deadline() or self._hello or request["run_id"] is not None or set(args) != {"pid"} or type(args["pid"]) is not int or args["pid"] != self.controller.pid:
                raise SupervisorError("supervisor_handshake_rejected")
            self._hello = True; self.last_heartbeat = self._now()
            await self._reply(request["id"], value={"controller_pid": self.controller.pid, "supervised": True}); return
        if not self._hello: raise SupervisorError("supervisor_handshake_required")
        if op == "heartbeat":
            if set(args) != {"sequence"}: raise SupervisorError("supervisor_request_shape")
            self.heartbeat(args["sequence"], request["run_id"])
            await self._reply(request["id"], value=True); return
        if op == "status":
            if args or request["run_id"] is not None: raise SupervisorError("supervisor_request_shape")
            await self._reply(request["id"], value={"terminal": self.terminal, "closed": self.closed, "run_active": self.run_id is not None}); return
        if self.terminal: raise SupervisorError("supervisor_terminal")
        if op == "shutdown":
            if args or request["run_id"] != self.run_id: raise SupervisorError("supervisor_request_shape")
            await self._reply(request["id"], value=True); self.fail("supervisor_shutdown"); return
        if op != "begin_run" and request["run_id"] != self.run_id: raise SupervisorError("supervisor_run_mismatch")
        if self._operation is not None and not self._operation.done():
            await self._reply(request["id"], error="supervisor_busy"); return
        if len(self._replies) >= MAX_PENDING_REPLIES:
            self.fail("supervisor_reply_backpressure")
            raise SupervisorError("supervisor_reply_backpressure")
        self._operation = asyncio.create_task(self._operate(request))
        self._operation.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        transaction = self._operation
        self._queue_reply(lambda: self._operation_reply(request, transaction))

    async def serve(self, transport):
        self._transport = transport
        async def receive():
            while not self.terminal:
                await self._receive_request(await transport.receive())
        receiver = asyncio.create_task(receive())
        try:
            while not self.terminal:
                if receiver.done():
                    try: receiver.result()
                    except EOFError: self.fail("supervisor_controller_eof")
                    except BaseException: self.fail("supervisor_protocol_failed")
                    else: self.fail("supervisor_controller_eof")
                    break
                try: self.check_deadline()
                except SupervisorError: self.fail("supervisor_clock_invalid")
                await asyncio.sleep(.05)
        finally:
            self.fail(self.reason or "supervisor_serve_interrupted")
            # Establish the retained cleanup owner before any cancellable
            # receiver/socket drain. The CLI can subsequently await shutdown.
            self._start_cleanup()
            receiver.cancel()
            try: await asyncio.gather(receiver, return_exceptions=True)
            finally: await transport.aclose()
        return await self.shutdown(self.reason or "supervisor_shutdown")

    async def _cleanup_step(self, name, factory, failures):
        task = self._cleanup_jobs.get(name)
        if task is None or task.done():
            if task is not None and not task.cancelled(): task.exception()
            task = self._cleanup_jobs[name] = asyncio.create_task(factory())
            task.add_done_callback(lambda value: None if value.cancelled() else value.exception())
        done, _ = await asyncio.wait((task,), timeout=self.cleanup_timeout)
        if task not in done:
            failures.append(name); return False
        try:
            value = task.result()
            if name in ("run", "group") and value is not True: raise SupervisorError("supervisor_cleanup_unverified")
            if name == "group" and self.controller.closed is not True: raise SupervisorError("supervisor_cleanup_unverified")
        except BaseException:
            failures.append(name); return False
        return True

    async def _cleanup(self):
        failures = []
        pending = self._operation is not None and not self._operation.done()
        if pending:
            done, _ = await asyncio.wait((self._operation,), timeout=self.operation_timeout)
            pending = self._operation not in done
        if pending: failures.append("operation")
        # A mutating guardian transaction must settle before cleanup. A timed
        # out await is not cancellation of a Start/POST already sent to OBS/CP.
        run_clean = not pending and (self.run_id is None or await self._cleanup_step("run", self.services.cleanup_run, failures))
        # Always try the retained group even when OBS/admission cleanup failed.
        group_clean = await self._cleanup_step("group", self.controller.terminate, failures)
        replies_clean = await self._close_transport_replies()
        if not replies_clean: failures.append("transport")
        closed_services = False
        if run_clean and group_clean and replies_clean and not pending:
            closed_services = await self._cleanup_step("services", self.services.close, failures)
        if closed_services:
            if self.lease is not None: self.lease.release(); self.lease = None
            self.run_id = self.room_code = None
            self.closed = True
        self.cleanup_failures = tuple(failures)
        return self.closed

    async def _close_transport_replies(self):
        if self._transport is not None:
            try: await asyncio.wait_for(self._transport.aclose(), 3)
            except BaseException: return False
        tasks = tuple(self._replies)
        for task in tasks: task.cancel()
        if not tasks: return True
        _, pending = await asyncio.wait(tasks, timeout=3)
        return not pending

    def _start_cleanup(self):
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup())
            self._cleanup_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())

    async def shutdown(self, reason="supervisor_shutdown"):
        self.fail(reason)
        if self.closed: return True
        self._start_cleanup()
        # Shield the sole owner: cancelling a CLI wait cannot cancel cleanup.
        return await asyncio.shield(self._cleanup_task)

    async def retry_cleanup(self):
        return await self.shutdown(self.reason or "supervisor_shutdown")
