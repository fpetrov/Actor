"""Controller side of the inherited private guardian channel.

No environment flag alone enables process-group inheritance. Only the verified
socket/isolated-session handshake installs the in-memory context below.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import os
import socket
import stat
import time

from .admission import ObserverAdmission
from .obs import GraphAudit, ObsError, ObsController
from .obs_firewall_attestor import peer_uid


class GuardianError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


_context = None


def inherited_group_options():
    client = _context
    if client is None: return {"start_new_session": True}
    client.require_alive()
    if os.getpid() != client.controller_pid or os.getpgrp() != client.controller_pid:
        raise GuardianError("guardian_controller_group_changed")
    return {"start_new_session": False}


class GuardianClient:
    def __init__(self, transport, *, controller_pid=None, clock=time.monotonic):
        self.transport, self.controller_pid = transport, controller_pid or os.getpid()
        self.clock = clock; self.run_id = None; self._next = 0; self._sequence = 0
        self._pending = {}; self._reader = self._heartbeat = None; self._operation_lock = asyncio.Lock()
        self._begin_task = None
        self._closed = False; self._terminal = None; self._callbacks = set()
        self.output_status = {}; self._status_at = -float("inf")

    @classmethod
    async def from_inherited_fd(cls, fd):
        global _context
        from .supervisor import FramedSocket
        if type(fd) is not int or fd < 3 or not stat.S_ISSOCK(os.fstat(fd).st_mode):
            raise GuardianError("guardian_private_socket_required")
        connection = socket.socket(fileno=fd)
        client = None
        try:
            if connection.family != socket.AF_UNIX or connection.type != socket.SOCK_STREAM:
                raise GuardianError("guardian_private_socket_required")
            if connection.getsockname() not in ("", b"") or connection.getpeername() not in ("", b""):
                raise GuardianError("guardian_private_socket_required")
            # CPython 3.12 on Darwin exposes neither socket.getpeereid nor
            # SO_PEERCRED. Reuse the audited public libc getpeereid helper.
            try: uid = peer_uid(connection)
            except Exception: raise GuardianError("guardian_peer_unverified") from None
            if type(uid) is not int or uid != os.geteuid(): raise GuardianError("guardian_peer_invalid")
            if hasattr(socket, "SO_PEERCRED"):
                import struct
                peer, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.geteuid() or peer != os.getppid(): raise GuardianError("guardian_peer_invalid")
            if os.getsid(0) != os.getpid() or os.getpgrp() != os.getpid():
                raise GuardianError("guardian_isolated_controller_required")
            if _context is not None: raise GuardianError("guardian_context_exists")
            connection.set_inheritable(False); connection.setblocking(False)
            client = cls(FramedSocket(connection))
            await client.start()
            if _context is not None: raise GuardianError("guardian_context_exists")
            _context = client
            return client
        except BaseException:
            connection.close()
            if client is not None:
                # A rejected/cancelled hello cannot leave a receiver/heartbeat
                # alive or send a shutdown RPC through an unaccepted context.
                client._fail("guardian_connection_lost")
                await client.aclose()
            raise

    async def start(self):
        self._reader = asyncio.create_task(self._receive(), name="broadcast-guardian-receiver")
        hello = await self._request("hello", {"pid": self.controller_pid}, run_id=None)
        if hello != {"controller_pid": self.controller_pid, "supervised": True}:
            raise GuardianError("guardian_handshake_invalid")
        self._heartbeat = asyncio.create_task(self._heartbeats(), name="broadcast-guardian-heartbeat")

    def require_alive(self):
        if self._terminal is not None or self._closed: raise GuardianError(self._terminal or "guardian_closed")

    def on_unsafe(self, callback): self._callbacks.add(callback)
    def remove_unsafe(self, callback): self._callbacks.discard(callback)

    def _fail(self, code):
        if self._terminal is not None: return
        self._terminal = code; self.output_status = {}; self._status_at = -float("inf")
        for future in self._pending.values():
            if not future.done(): future.set_exception(GuardianError(code))
        for callback in tuple(self._callbacks): callback(code)

    async def _receive(self):
        try:
            while True:
                message = await self.transport.receive()
                if message.get("event") == "unsafe":
                    if message.get("run_id") != self.run_id: continue
                    self.output_status = {}; self._status_at = -float("inf")
                    for callback in tuple(self._callbacks): callback("guardian_output_unsafe")
                    continue
                if (message.get("v") != 1 or type(message.get("id")) is not int
                        or type(message.get("ok")) is not bool): raise GuardianError("guardian_response_invalid")
                future = self._pending.get(message["id"])
                if future is None: raise GuardianError("guardian_response_unknown")
                status = message.get("output_status")
                if isinstance(status, dict): self.output_status = status; self._status_at = self.clock()
                if not future.done():
                    if message["ok"]: future.set_result(message.get("value"))
                    else: future.set_exception(GuardianError(message.get("error", "guardian_operation_failed")))
        except asyncio.CancelledError: raise
        except BaseException: self._fail("guardian_connection_lost")

    async def _request(self, op, args, *, run_id=None):
        self.require_alive(); self._next += 1; identity = self._next
        future = asyncio.get_running_loop().create_future(); self._pending[identity] = future
        try:
            await self.transport.send({"v": 1, "id": identity, "op": op, "run_id": run_id, "args": args})
            # Keep a response slot even if the caller is cancelled. The guardian
            # retains every mutation; a late reply must not poison the channel.
            return await asyncio.shield(future)
        finally:
            if future.done(): self._pending.pop(identity, None)
            else:
                def discard(done):
                    self._pending.pop(identity, None)
                    if not done.cancelled(): done.exception()
                future.add_done_callback(discard)

    async def _heartbeats(self):
        try:
            while True:
                self._sequence += 1
                await asyncio.wait_for(self._request("heartbeat", {"sequence": self._sequence}, run_id=None), 1.5)
                await asyncio.sleep(.25)
        except asyncio.CancelledError: raise
        except BaseException: self._fail("guardian_heartbeat_lost")

    async def begin_run(self, room, session):
        async with self._operation_lock:
            self.run_id = session
            task = self._begin_task = asyncio.create_task(self._request("begin_run", {"room_code": room}, run_id=session))
            task.add_done_callback(lambda value: None if value.cancelled() else value.exception())
            try: await asyncio.shield(task)
            except asyncio.CancelledError: raise  # Possibly accepted lease/run remains ours.
            except Exception:
                if self.output_status.get("session_id") != session and self._terminal is None: self.run_id = None
                raise

    async def end_run(self):
        async with self._operation_lock:
            if self.run_id is None: return
            if self._begin_task is not None:
                task = self._begin_task
                done, _ = await asyncio.wait((task,), timeout=30)
                if task not in done: raise GuardianError("guardian_begin_pending")
                if not task.cancelled(): task.exception()
                self._begin_task = None
                if self.output_status.get("session_id") != self.run_id:
                    # Only the exact settled rejection can retire an intent
                    # that never acquired a guardian run/lease.
                    if task.cancelled() or task.exception() is None: raise GuardianError("guardian_begin_unverified")
                    self.run_id = None; return
            await self._request("end_run", {}, run_id=self.run_id)
            self.run_id = None; self.output_status = {}; self._status_at = -float("inf")

    async def call(self, op, args=None):
        async with self._operation_lock:
            if self.run_id is None: raise GuardianError("guardian_run_required")
            return await self._request(op, args or {}, run_id=self.run_id)

    @property
    def route_verified(self):
        return (not self._closed and self._terminal is None and self.output_status.get("route_verified") is True
                and 0 <= self.clock() - self._status_at <= 1)

    async def aclose(self):
        if self._closed: return
        # Normal shutdown is allowed only after Native end_run. An unresolved
        # resource instead closes the socket and invokes independent containment.
        try:
            if self.run_id is None and self._terminal is None:
                await self._request("shutdown", {}, run_id=None)
        finally:
            self._closed = True
            for task in (self._heartbeat, self._reader):
                if task is not None: task.cancel()
            await asyncio.gather(*(x for x in (self._heartbeat, self._reader) if x is not None), return_exceptions=True)
            await self.transport.aclose()
            # Preserve the in-memory terminal fence for the rest of this
            # controller's lifetime. A late task must not fall back to creating
            # a new independent session merely because cleanup closed IPC.


class GuardianLease:
    def __init__(self, client): self.client = client; self.held = False
    def acquire(self):
        self.client.require_alive()
        if self.client.run_id is None: raise GuardianError("guardian_run_required")
        self.held = True; return self
    def release(self):
        if self.client.run_id is not None: raise GuardianError("guardian_run_cleanup_pending")
        self.held = False


class GuardianAdmission:
    def __init__(self, client): self.client = client
    async def observe(self, room, session):
        if session != self.client.run_id: raise GuardianError("guardian_scope_changed")
        value = await self.client.call("observe")
        return ObserverAdmission.parse(value, room_code=room, session_id=session)
    async def release(self, room, session):
        if session != self.client.run_id: raise GuardianError("guardian_scope_changed")
        await self.client.call("release_observer")


class GuardianObsController:
    SCENES, VIDEO, VOICE = ObsController.SCENES, ObsController.VIDEO, ObsController.VOICE
    def __init__(self, guardian, client, *, window_guard, channel_guard, on_unsafe=None):
        self.guardian, self.client, self.window_guard, self.on_unsafe = guardian, client, window_guard, on_unsafe
        self._binding = None; self._invalid = False; self._startup_pending = True
        self._expected_scene = "ACTOR Preparing"
        self._old_callback = client.on_event; client.on_event = self._event
        guardian.on_unsafe(self._unsafe)
    def _unsafe(self, code):
        self._invalid = True
        if self.on_unsafe is not None: self.on_unsafe(code)
    def _event(self, name, data):
        # The parent's mutation connection is the sole startup graph authority:
        # this second read-only connection can deliver an already audited setup
        # event later. Identity/transport loss and parent unsafe stay permanent.
        hard = name in {"ConnectionLost", "CurrentSceneCollectionChanging", "CurrentSceneCollectionChanged",
                    "CurrentProfileChanging", "CurrentProfileChanged"}
        graph = (name == "CurrentProgramSceneChanged" and data.get("sceneName") != self._expected_scene
                or name in {"InputCreated", "InputRemoved", "InputNameChanged",
                    "SceneCreated", "SceneRemoved", "SceneNameChanged", "SceneListChanged", "InputSettingsChanged",
                    "InputMuteStateChanged", "InputVolumeChanged", "InputAudioTracksChanged", "InputAudioMonitorTypeChanged",
                    "InputAudioSyncOffsetChanged", "SourceFilterCreated", "SourceFilterRemoved", "SourceFilterEnableStateChanged",
                    "SourceFilterSettingsChanged", "SourceFilterNameChanged", "SourceFilterListReindexed", "SceneItemCreated",
                    "SceneItemRemoved", "SceneItemEnableStateChanged", "SceneItemTransformChanged", "SceneItemListReindexed"})
        if hard or graph and not self._startup_pending:
            self._unsafe("obs_graph_changed")
        if self._old_callback is not None: self._old_callback(name, data)
    @property
    def route_verified(self): return not self._invalid and self.guardian.route_verified and self.window_guard(self._binding)
    @property
    def _owned_stream(self): return self.guardian.output_status.get("owned_stream", False)
    @property
    def _record_owned(self): return self.guardian.output_status.get("record_owned", False)
    @property
    def video_transform(self): return self.guardian.output_status.get("video_transform")
    async def prepare(self, binding, renderer_url, *, protected_backup_confirmed=False, adopt_empty_collection=False, native_size=None):
        await self.guardian.call("connect_output")
        result = await self.guardian.call("prepare_output", {"binding": asdict(binding), "renderer_url": renderer_url,
            "native_size": list(native_size), "protected_backup_confirmed": protected_backup_confirmed,
            "adopt_empty_collection": adopt_empty_collection})
        self._binding = binding
        return GraphAudit(**result)
    async def audit(self):
        result = GraphAudit(**await self.guardian.call("audit_output"))
        # A foreign event is terminal after startup. Never clear its local fence
        # merely because a later graph happens to match again.
        return result
    async def complete_startup(self):
        result = GraphAudit(**await self.guardian.call("complete_output_startup"))
        if result.passed:
            if (self._invalid or self.guardian.output_status.get("startup_pending") is not False
                    or not self.guardian.route_verified):
                raise GuardianError("guardian_startup_seal_unverified")
            self._startup_pending = False
        return result
    async def show(self, scene):
        self._expected_scene = scene
        await self.guardian.call("show_output", {"scene": scene})
    async def require_idle(self):
        await self.guardian.call("require_output_idle")
    async def start_stream(self, permit):
        await self.guardian.call("start_stream", {"channel": permit.channel_login,
            "expires_at_monotonic": permit.expires_at_monotonic, "explicit_public_start": permit.explicit_public_start})
    async def stop_stream(self): await self.guardian.call("stop_stream")
    async def check_stream_health(self): await self.guardian.call("output_status")
    async def start_record(self, directory): return await self.guardian.call("start_record", {"directory": str(directory)})
    async def stop_record(self): return await self.guardian.call("stop_record")
    async def emergency_stop(self):
        self._expected_scene = "ACTOR Safe"
        await self.guardian.call("emergency_output")
    async def cleanup_owned_inputs(self):
        await self.guardian.call("cleanup_output")
        self.guardian.remove_unsafe(self._unsafe)
        self.client.on_event = self._old_callback


class ReadOnlyObsClient:
    """The controller's image/status connection cannot mutate managed output."""
    def __init__(self, client): self._client = client
    @property
    def on_event(self): return self._client.on_event
    @on_event.setter
    def on_event(self, callback): self._client.on_event = callback
    async def request(self, request_type, data=None, *, timeout=5):
        if (not isinstance(request_type, str) or not request_type.startswith("Get")
                or request_type in ("GetStreamServiceSettings", "GetOutputSettings")):
            raise ObsError("obs_guardian_mutation_required")
        return await self._client.request(request_type, data, timeout=timeout)
    async def stream_service_fingerprint(self): return await self._client.stream_service_fingerprint()
    async def close(self): await self._client.close()
