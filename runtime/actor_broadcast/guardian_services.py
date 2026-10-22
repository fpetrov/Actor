"""Guardian-owned native mutations. No work or credential access on import.

The controller can request only this finite operation vocabulary. OBS ownership
stays in the real ObsController in this process, including uncertain Start RPCs.
The authenticated local transport is owned by supervisor.py, not this adapter.
"""
from __future__ import annotations

import asyncio
import ctypes
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
from uuid import UUID

from .admission import AdmissionClient
from .obs import ObsClient, ObsConnection, ObsController, PublicStartPermit, WindowBinding, _profile_list
from .obs_firewall_attestor import FirewallAttestationClient
from .window import OwnedWindowLocator


class GuardianServiceError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


def controller_group_members(group_id):
    """Query only the retained owned process group, using the public Darwin API.

    Never discover a controller by name or enumerate other process groups. A
    truncated/unknown kernel result is not an empty-group proof.
    """
    if type(group_id) is not int or group_id <= 1 or sys.platform != "darwin":
        raise GuardianServiceError("guardian_group_query_unavailable")
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    query = library.proc_listpgrppids
    query.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
    query.restype = ctypes.c_int
    values = (ctypes.c_int32 * 4096)()
    size = ctypes.sizeof(values)
    ctypes.set_errno(0)
    # libproc's convenience wrapper divides proc_listpids' byte count by
    # sizeof(int). Its result is a PID count, while buffersize is still bytes.
    count = query(group_id, values, size)
    if count < 0 or count == 0 and ctypes.get_errno() != 0 or count >= len(values):
        raise GuardianServiceError("guardian_group_query_unverified")
    result = tuple(values[:count])
    if len(result) > 256 or any(value <= 1 for value in result) or len(set(result)) != len(result):
        raise GuardianServiceError("guardian_group_query_unverified")
    return tuple(sorted(result))


OPERATIONS = frozenset({"observe", "release_observer", "connect_output", "prepare_output",
    "audit_output", "complete_output_startup", "show_output", "start_stream", "stop_stream",
    "start_record", "stop_record", "emergency_output", "cleanup_output", "output_status", "require_output_idle"})


class GuardianServices:
    def __init__(self, config, store, *, forward=None, obs_client=ObsClient,
                 obs_controller=ObsController, admission=AdmissionClient,
                 attestor=FirewallAttestationClient, window=OwnedWindowLocator,
                 group_check=os.getpgid):
        self.config, self.store, self.forward = config, store, forward
        self._client_factory, self._obs_factory, self._admission_factory = obs_client, obs_controller, admission
        self._attestor_factory, self._window_factory, self._group_check = attestor, window, group_check
        self.controller = None; self.room = self.session = None
        self.obs_client = self.obs = self.admission = self.attestor = self.window = None
        self._post = self._delete = None; self._release = False
        self._unsafe = None; self._closing = False; self._forward_started = False
        self._output_identity_lost = self._route_identity_lost = False
        self._stream_identity_lost = self._record_identity_lost = False
        self._scene_ids = {}

    def bind_controller(self, controller):
        if self.controller is not None: raise GuardianServiceError("guardian_controller_already_bound")
        self.controller = controller

    def bind_unsafe(self, callback): self._unsafe = callback

    async def start_forward(self):
        if self.forward is not None and not self._forward_started:
            # The real object registers its Popen before its first await.
            await self.forward.start(); self._forward_started = True

    async def begin_run(self, room_code, session_id):
        if self.session is not None: raise GuardianServiceError("guardian_run_cleanup_pending")
        if not re.fullmatch(r"[A-Z0-9]{6}", room_code) or not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise GuardianServiceError("guardian_run_invalid")
        self.room, self.session = room_code, session_id; self._closing = False
        self._output_identity_lost = self._route_identity_lost = False
        self._stream_identity_lost = self._record_identity_lost = False
        self._scene_ids = {}
        await self.start_forward()

    def validate_call(self, op, args):
        if op not in OPERATIONS or not isinstance(args, dict): raise GuardianServiceError("guardian_operation_invalid")
        fields = {"prepare_output": {"binding", "renderer_url", "native_size", "protected_backup_confirmed", "adopt_empty_collection"},
                  "show_output": {"scene"}, "start_stream": {"channel", "expires_at_monotonic", "explicit_public_start"},
                  "start_record": {"directory"}}.get(op, set())
        if set(args) != fields: raise GuardianServiceError("guardian_operation_shape")
        if op == "prepare_output":
            if not isinstance(args["binding"], dict) or set(args["binding"]) != {"window_id", "owner_pid", "width", "height"}:
                raise GuardianServiceError("guardian_window_invalid")
            WindowBinding(**args["binding"])
            size = args["native_size"]
            if (not isinstance(size, list) or len(size) != 2 or any(type(x) is not int or not 1 <= x <= 3840 for x in size)
                    or not isinstance(args["renderer_url"], str) or not 1 <= len(args["renderer_url"]) <= 4096
                    or any(type(args[k]) is not bool for k in ("protected_backup_confirmed", "adopt_empty_collection"))):
                raise GuardianServiceError("guardian_output_invalid")
        if op == "show_output" and args["scene"] not in ObsController.SCENES:
            raise GuardianServiceError("guardian_scene_invalid")
        if op == "start_stream" and (args["channel"] != self.config.channel
                or args["explicit_public_start"] is not True
                or type(args["expires_at_monotonic"]) not in (int, float)
                or not math.isfinite(args["expires_at_monotonic"])):
            raise GuardianServiceError("guardian_start_invalid")
        if op == "start_record" and (not isinstance(args["directory"], str) or not 1 <= len(args["directory"]) <= 4096):
            raise GuardianServiceError("guardian_record_directory_invalid")

    def _output_unsafe(self, code):
        if self._closing or self.obs is None or getattr(self.obs, "_startup_pending", True): return
        if self._unsafe is not None: self._unsafe(code)

    def _watch_output_event(self, name, data):
        obs = self.obs
        if obs is None or obs._startup_pending and not obs._owned_stream and not obs._record_owned: return
        if name in {"CurrentProfileChanging", "CurrentProfileChanged", "CurrentSceneCollectionChanging", "CurrentSceneCollectionChanged"}:
            self._output_identity_lost = self._route_identity_lost = True
        if name in {"InputRemoved", "InputNameChanged", "InputSettingsChanged"}:
            if data.get("inputUuid") in obs._owned_inputs.values() or data.get("inputName") in (obs.VOICE, obs.VIDEO):
                self._route_identity_lost = True
        if name in {"SceneCreated","SceneRemoved","SceneNameChanged","SceneListChanged",
                    "SceneItemCreated","SceneItemRemoved","SceneItemEnableStateChanged","SceneItemTransformChanged","SceneItemListReindexed",
                    "SourceFilterCreated","SourceFilterRemoved","SourceFilterEnableStateChanged","SourceFilterSettingsChanged","SourceFilterNameChanged","SourceFilterListReindexed"}:
            self._route_identity_lost = True
        if name=="CurrentProgramSceneChanged" and data.get("sceneName")!=obs._expected_scene:
            self._route_identity_lost = True
        if (name == "StreamStateChanged" and obs._owned_stream and obs._stream_confirmed
                and not obs._stream_stopping and data.get("outputActive") is False):
            self._stream_identity_lost = True
        if (name == "RecordStateChanged" and obs._record_owned and getattr(obs,"_record_confirmed",False)
                and not getattr(obs,"_record_stopping",False) and data.get("outputActive") is False):
            self._record_identity_lost = True

    async def _verify_output_identity(self):
        if self._output_identity_lost: raise GuardianServiceError("guardian_output_ownership_unknown")
        collections = await self.obs_client.request("GetSceneCollectionList")
        profile, _ = _profile_list(await self.obs_client.request("GetProfileList"))
        if collections.get("currentSceneCollectionName") != self.obs.OWNED_NAME or profile != self.obs.OWNED_NAME:
            self._output_identity_lost = True
            raise GuardianServiceError("guardian_output_ownership_unknown")
        if self.obs._owned_stream and not await self.obs.verify_channel(self.config.channel):
            self._output_identity_lost = True
            raise GuardianServiceError("guardian_output_ownership_unknown")

    def _mutation_allowed(self, request, data):
        if self._output_identity_lost: return False
        if request == "StopStream" and self._stream_identity_lost: return False
        if request == "StopRecord" and self._record_identity_lost: return False
        if request in ("SetInputMute", "SetCurrentProgramScene") and self._route_identity_lost: return False
        if request in ("StartStream", "StartRecord") and (self._route_identity_lost or self._stream_identity_lost or self._record_identity_lost): return False
        return True

    async def _stop_managed(self, record=False):
        await self._verify_output_identity()
        lost = self._record_identity_lost if record else self._stream_identity_lost
        owned = self.obs._record_owned if record else self.obs._owned_stream
        if lost and owned:
            state = await self.obs_client.request("GetRecordStatus" if record else "GetStreamStatus")
            if state.get("outputActive") is not False: raise GuardianServiceError("guardian_output_ownership_unknown")
            # Our old output is gone. Do not issue Stop against a later start
            # that might race this read: retire intent without a mutation.
            if record: self.obs._record_owned = self.obs._record_confirmed = False
            else: self.obs._owned_stream = self.obs._stream_confirmed = False
            return {}
        return await (self.obs.stop_record() if record else self.obs.stop_stream())

    async def _emergency_output(self):
        self._closing = True
        await self._verify_output_identity()
        route_error = False
        try:
            if self.obs._prepared and not self._route_identity_lost:
                voice = self.obs._owned_inputs.get(self.obs.VOICE)
                if voice is not None:
                    settings = (await self.obs_client.request("GetInputSettings", {"inputUuid":voice})).get("inputSettings",{})
                    if settings.get("actor_broadcast_owner") != self.obs._owner_nonce:
                        self._route_identity_lost = True
                        raise GuardianServiceError("guardian_route_ownership_unknown")
                    await self.obs_client.request("SetInputMute", {"inputUuid":voice,"inputMuted":True},timeout=2)
                scenes = (await self.obs_client.request("GetSceneList")).get("scenes",[])
                safe = [row for row in scenes if row.get("sceneName")=="ACTOR Safe"]
                if len(safe)!=1 or safe[0].get("sceneUuid")!=self._scene_ids.get("ACTOR Safe"):
                    raise GuardianServiceError("guardian_route_ownership_unknown")
                self.obs._expected_scene = "ACTOR Safe"; self.obs._graph_ok=False
                await self.obs_client.request("SetCurrentProgramScene", {"sceneUuid":safe[0]["sceneUuid"]},timeout=2)
        except BaseException: route_error = True
        finally:
            # Both stops retain their exact owned transaction state. The wire
            # guard rechecks continuity immediately before each mutation.
            try: await self._stop_managed()
            finally: await self._stop_managed(record=True)
        if route_error or self._route_identity_lost: raise GuardianServiceError("guardian_route_ownership_unknown")

    def _owned_window(self, binding):
        if self.controller is None or self.window is None: return False
        try:
            return (not self.controller.closed and self._group_check(binding.owner_pid) == self.controller.pid
                    and self.window.verify(binding))
        except Exception: return False

    async def _channel(self, login):
        value = await asyncio.to_thread(self.store.get, "obs-service-binding")
        try:
            binding = json.loads(value)
            if (set(binding) != {"channel", "profile", "collection", "fingerprint"}
                    or binding["channel"] != login or binding["profile"] != "ACTOR Broadcast"
                    or binding["collection"] != "ACTOR Broadcast"
                    or not re.fullmatch(r"[a-f0-9]{64}", binding["fingerprint"])): return False
            return secrets.compare_digest(binding["fingerprint"], await self.obs_client.stream_service_fingerprint())
        except (ValueError, TypeError, KeyError): return False

    async def _connect_output(self):
        if self.obs_client is not None: return
        self.attestor = self._attestor_factory(self.config.firewall_socket)
        if not await self.attestor.refresh() or not self.attestor.verified:
            raise GuardianServiceError("obs_firewall_attestation_required")
        password = await asyncio.to_thread(self.store.get, "obs-websocket-password")
        self.obs_client = self._client_factory(ObsConnection(self.config.obs_endpoint, password, firewall_verified=True))
        self.obs_client.mutation_guard = self._mutation_allowed
        await self.obs_client.connect()
        self.obs = self._obs_factory(self.obs_client, window_guard=self._owned_window,
            channel_guard=self._channel, on_unsafe=self._output_unsafe)
        old_callback = self.obs_client.on_event
        def event(name, data):
            self._watch_output_event(name,data)
            if old_callback is not None: old_callback(name,data)
        self.obs_client.on_event = event

    async def _observe(self):
        if self.admission is None:
            key = await asyncio.to_thread(self.store.get, "broadcast-server-operator-key")
            self.admission = self._admission_factory(self.config.operator_forward_url, key,
                game_version=self.config.game_version, protocol_version=self.config.admission_protocol)
        self._release = True
        if self._post is None:
            self._post = asyncio.create_task(self.admission.observe(self.room, self.session))
        value = await asyncio.shield(self._post)
        # Admission bearer travels only in the inherited private socket, never
        # status, argv or a recovery journal.
        return json.loads(value.child_environment_value())

    async def _release_observer(self):
        if self._post is not None:
            done, _ = await asyncio.wait((self._post,), timeout=10)
            if self._post not in done: raise GuardianServiceError("guardian_admission_pending")
            if not self._post.cancelled(): self._post.exception()
            self._post = None
        if self._release:
            if self._delete is None:
                self._delete = asyncio.create_task(self.admission.release(self.room, self.session))
            await asyncio.shield(self._delete)
            self._delete = None; self._release = False

    async def call(self, op, args):
        self.validate_call(op, args)
        if self.session is None: raise GuardianServiceError("guardian_run_required")
        if op == "observe": return await self._observe()
        if op == "release_observer": await self._release_observer(); return True
        if op == "connect_output": await self._connect_output(); return True
        if op == "output_status":
            if self.obs is not None: await self.obs.check_stream_health()
            return self.status()
        if op == "cleanup_output": await self._close_output(); return True
        if self.obs is None: raise GuardianServiceError("guardian_output_not_connected")
        if op == "require_output_idle": await self.obs.require_idle(); return True
        if op == "prepare_output":
            binding = WindowBinding(**args["binding"])
            if self.controller is None or self._group_check(binding.owner_pid) != self.controller.pid:
                raise GuardianServiceError("guardian_window_not_owned")
            self.window = self._window_factory(binding.owner_pid,
                alive=lambda: self.controller is not None and not self.controller.closed
                    and self._group_check(binding.owner_pid) == self.controller.pid)
            result = await self.obs.prepare(binding, args["renderer_url"],
                native_size=tuple(args["native_size"]), protected_backup_confirmed=args["protected_backup_confirmed"],
                adopt_empty_collection=args["adopt_empty_collection"])
            rows = (await self.obs_client.request("GetSceneList")).get("scenes")
            if (not isinstance(rows,list) or len(rows)!=4 or any(not isinstance(row,dict) for row in rows)
                    or {row.get("sceneName") for row in rows}!=set(self.obs.SCENES)):
                raise GuardianServiceError("guardian_scene_identity_unknown")
            identities = {row["sceneName"]:row.get("sceneUuid") for row in rows}
            try:
                for value in identities.values(): UUID(value)
            except (ValueError,TypeError,AttributeError): raise GuardianServiceError("guardian_scene_identity_unknown") from None
            if len(set(identities.values()))!=4: raise GuardianServiceError("guardian_scene_identity_unknown")
            self._scene_ids=identities
            return asdict(result)
        if op == "audit_output": return asdict(await self.obs.audit())
        if op == "complete_output_startup": return asdict(await self.obs.complete_startup())
        if op == "show_output": await self.obs.show(args["scene"]); return True
        if op == "start_stream": await self.obs.start_stream(PublicStartPermit(args["channel"], args["expires_at_monotonic"], True)); return True
        if op == "stop_stream": await self._stop_managed(); return True
        if op == "start_record":
            from .native import private_directory
            directory = Path(args["directory"])
            allowed = self.config.state_directory / "runs" / self.session
            if not directory.is_absolute() or directory.resolve() != directory or not directory.is_relative_to(allowed):
                raise GuardianServiceError("guardian_record_directory_invalid")
            private_directory(directory, create=False)
            return await self.obs.start_record(directory)
        if op == "stop_record": return await self._stop_managed(record=True)
        if op == "emergency_output":
            await self._emergency_output(); return True
        raise GuardianServiceError("guardian_operation_invalid")

    def status(self):
        obs = self.obs
        return {"session_id": self.session, "route_verified": obs is not None and obs.route_verified,
            "owned_stream": obs is not None and obs._owned_stream,
            "stream_confirmed": obs is not None and obs._stream_confirmed,
            "record_owned": obs is not None and obs._record_owned,
            "startup_pending": obs is None or obs._startup_pending,
            "output_connected": self.obs_client is not None,
            "video_transform": obs.video_transform if obs is not None and obs._capture_shape is not None else None}

    async def _close_output(self):
        self._closing = True
        stop_error = None
        if self.obs is not None and (self.obs._prepared or self.obs._owned_stream or self.obs._record_owned
                or self.obs._owned_inputs or self.obs._creating_inputs or self.obs._record_directory_original is not None):
            # Keep every ownership marker/client on any failure, for retry.
            try: await self._emergency_output()
            except BaseException as error: stop_error = error
            if self._output_identity_lost: raise GuardianServiceError("guardian_output_ownership_unknown")
            if self.obs._owned_stream or self.obs._record_owned:
                raise GuardianServiceError("guardian_owned_output_cleanup_pending") from None
            await self.obs.restore_record_directory()
            await self.obs.cleanup_owned_inputs()
        if self.obs_client is not None:
            await self.obs_client.close(); self.obs_client = None; self.obs = None
        # As in Native, missing old VOICE after a previous successful removal
        # can make mute/Safe fail. Actual idle+removed inputs+closed client is
        # the recovery proof, never merely a stop boolean or unavailable ACK.

    async def cleanup_run(self):
        failures = []
        try: await self._close_output()
        except BaseException: failures.append("output")
        try: await self._release_observer()
        except BaseException: failures.append("admission")
        if self.controller is not None and not self.controller.closed:
            try:
                if set(self.controller.members()) != {self.controller.pid}: failures.append("children")
            except BaseException: failures.append("children")
        if failures: raise GuardianServiceError("guardian_cleanup_pending")
        self.room = self.session = None; self.admission = self.window = self.attestor = None
        return True

    async def close(self):
        await self.cleanup_run()
        if self.forward is not None:
            await self.forward.aclose(); self._forward_started = False
        return True
