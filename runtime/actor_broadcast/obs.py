"""Optional authenticated obs-websocket v5 control and fail-closed source audit.

Passwords/service keys are never logged, placed in argv, or read from OBS files.
The caller owns Keychain, protected backups, OS window identity and UI consent.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import hashlib
import inspect
import json
import math
import secrets
import time
from uuid import UUID
from urllib.parse import urlsplit

from .speech import _cleanup


class ObsError(RuntimeError):
    def __init__(self, code, *, request_status_code=None):
        self.code = code
        # The server's comment can contain private details; retain only its
        # bounded numeric protocol status for narrow compatibility checks.
        self.request_status_code = (request_status_code if type(request_status_code) is int
                                    and 0 <= request_status_code <= 9999 else None)
        super().__init__(code)


def authentication(password, salt, challenge):
    if not all(isinstance(v, str) and 0 < len(v) <= 1024 for v in (password, salt, challenge)):
        raise ObsError("obs_auth_shape")
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


def _profile_list(value):
    # obs-websocket 5.7.4 GetProfileList: profiles is Array<String>, not
    # profile objects. Bounds are local fail-closed limits, not OBS guarantees.
    def name(item):
        return isinstance(item, str) and 1 <= len(item) <= 256 and all(32 <= ord(ch) != 127 for ch in item)
    if not isinstance(value, dict) or set(value) != {"currentProfileName", "profiles"}:
        raise ObsError("obs_profile_list_invalid")
    current, names = value["currentProfileName"], value["profiles"]
    if (not name(current) or not isinstance(names, list) or not 1 <= len(names) <= 128
            or not all(name(item) for item in names) or len(set(names)) != len(names) or current not in names):
        raise ObsError("obs_profile_list_invalid")
    return current, tuple(names)


@dataclass(frozen=True)
class ObsConnection:
    endpoint: str = "ws://127.0.0.1:4455"
    password: str = field(default="", repr=False)
    local_listener_verified: bool = False
    firewall_verified: bool = False

    def __post_init__(self):
        p = urlsplit(self.endpoint)
        if (p.scheme != "ws" or p.hostname not in ("127.0.0.1", "::1") or p.username or p.password
                or p.query or p.fragment or p.path not in ("", "/") or not p.port):
            raise ObsError("obs_endpoint_not_loopback")
        if not isinstance(self.password, str) or not 8 <= len(self.password) <= 1024:
            raise ObsError("obs_password_required")
        if self.local_listener_verified is not True and self.firewall_verified is not True:
            raise ObsError("obs_listener_isolation_unverified")


class ObsClient:
    def __init__(self, config: ObsConnection, *, connect=None, on_event=None):
        self.config = config; self._connect = connect; self.on_event = on_event
        self._ws = None; self._reader = None; self._pending = {}; self._send_lock = asyncio.Lock()
        self.available_requests = frozenset(); self.version = None

    async def connect(self):
        if self._ws is not None: return self.version
        connector = self._connect
        if connector is None:
            try: from websockets.asyncio.client import connect as connector
            except ImportError: raise ObsError("broadcast_websockets_dependency_missing") from None
        try:
            ws = await connector(self.config.endpoint, proxy=None, compression=None,
                max_size=6*1024*1024, max_queue=4, open_timeout=5, close_timeout=2,
                ping_interval=10, ping_timeout=5)
            self._ws = ws
            hello = await self._read_message(timeout=5)
            if hello.get("op") != 0 or not isinstance(hello.get("d"), dict): raise ObsError("obs_hello_invalid")
            d = hello["d"]; auth = d.get("authentication")
            if not isinstance(auth, dict) or set(auth) != {"salt", "challenge"}:
                raise ObsError("obs_authentication_must_be_enabled")
            identify = {"rpcVersion": 1, "eventSubscriptions": 1 | 2 | 4 | 8 | 32 | 64 | 128 | (1 << 19),
                "authentication": authentication(self.config.password, auth["salt"], auth["challenge"])}
            await self._ws.send(json.dumps({"op": 1, "d": identify}))
            identified = await self._read_message(timeout=5)
            if identified.get("op") != 2 or identified.get("d", {}).get("negotiatedRpcVersion") != 1:
                raise ObsError("obs_identification_failed")
            self._reader = asyncio.create_task(self._receive(), name="actor-obs-reader")
            version = await self.request("GetVersion")
            if not isinstance(version.get("availableRequests"), list): raise ObsError("obs_version_invalid")
            self.available_requests = frozenset(version["availableRequests"])
            self.version = {"obsVersion": version.get("obsVersion"), "obsWebSocketVersion": version.get("obsWebSocketVersion"),
                            "rpcVersion": version.get("rpcVersion")}
            return self.version
        except BaseException as error:
            await self.close()
            if isinstance(error, asyncio.CancelledError): raise
            if isinstance(error, ObsError): raise
            raise ObsError("obs_connection_failed") from None

    async def _read_message(self, timeout):
        raw = await self._ws.recv() if timeout is None else await asyncio.wait_for(self._ws.recv(), timeout)
        if not isinstance(raw, str) or len(raw.encode()) > 6*1024*1024: raise ObsError("obs_message_bound")
        x = json.loads(raw)
        if not isinstance(x, dict) or set(x) != {"op", "d"} or type(x["op"]) is not int:
            raise ObsError("obs_message_shape")
        return x

    async def _receive(self):
        try:
            while True:
                # Idle OBS is healthy: websocket ping/pong detects a dead peer.
                x = await self._read_message(timeout=None)
                if x["op"] == 7:
                    d = x["d"]; fut = self._pending.get(d.get("requestId"))
                    if fut is not None and not fut.done(): fut.set_result(d)
                elif x["op"] == 5:
                    d = x["d"]
                    # Callback receives trusted OBS events in RAM only. Never
                    # serialize eventData or input settings into public telemetry.
                    if self.on_event is not None: self.on_event(d.get("eventType"), d.get("eventData", {}))
                else: raise ObsError("obs_unexpected_opcode")
        except asyncio.CancelledError: pass
        except BaseException:
            for fut in self._pending.values():
                if not fut.done(): fut.set_exception(ObsError("obs_connection_lost"))
            if self.on_event is not None: self.on_event("ConnectionLost", {})

    async def request(self, request_type, data=None, *, timeout=5):
        if request_type in ("GetStreamServiceSettings", "GetOutputSettings"):
            raise ObsError("obs_secret_settings_not_exposed")
        return await self._request(request_type, data, timeout=timeout)

    async def stream_service_fingerprint(self):
        """Trusted Keychain binding only; raw service/key never leaves client.

        This detects changes relative to a GUI-verified binding. It does not
        independently identify the Twitch account behind a stream key.
        """
        raw = await self._request("GetStreamServiceSettings")
        if set(raw) != {"streamServiceType", "streamServiceSettings"} or not isinstance(raw["streamServiceType"], str) or not isinstance(raw["streamServiceSettings"], dict):
            raise ObsError("obs_service_binding_invalid")
        try: canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError): raise ObsError("obs_service_binding_invalid") from None
        if len(canonical) > 65536: raise ObsError("obs_service_binding_invalid")
        return hashlib.sha256(canonical).hexdigest()

    async def _request(self, request_type, data=None, *, timeout=5):
        if self._ws is None: raise ObsError("obs_not_connected")
        if self.available_requests and request_type not in self.available_requests:
            raise ObsError("obs_request_unavailable")
        if len(self._pending) >= 32: raise ObsError("obs_request_capacity")
        identity = secrets.token_hex(16); fut = asyncio.get_running_loop().create_future(); self._pending[identity] = fut
        try:
            async with self._send_lock:
                await asyncio.wait_for(self._ws.send(json.dumps({"op": 6, "d": {
                    "requestType": request_type, "requestId": identity, "requestData": data or {}}}, allow_nan=False)), timeout)
            response = await asyncio.wait_for(asyncio.shield(fut), timeout)
            if response.get("requestType") != request_type:
                raise ObsError("obs_request_rejected")
            status = response.get("requestStatus", {})
            if not isinstance(status, dict) or status.get("result") is not True:
                raise ObsError("obs_request_rejected", request_status_code=(status.get("code")
                    if isinstance(status, dict) and status.get("result") is False else None))
            value = response.get("responseData", {})
            if not isinstance(value, dict): raise ObsError("obs_response_shape")
            return value
        except asyncio.CancelledError: raise
        except ObsError: raise
        except BaseException: raise ObsError("obs_request_failed") from None
        finally:
            self._pending.pop(identity, None)
            if fut.done() and not fut.cancelled(): fut.exception()
            else: fut.cancel()

    async def close(self):
        task = self._reader; self._reader = None
        if task is not None and task is not asyncio.current_task():
            task.cancel(); await asyncio.gather(task, return_exceptions=True)
        ws = self._ws; self._ws = None
        if ws is not None:
            try: await asyncio.wait_for(ws.close(), 3)
            except BaseException: pass


@dataclass(frozen=True)
class WindowBinding:
    window_id: int
    owner_pid: int = field(repr=False)
    width: int = 1280
    height: int = 720

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in (self.window_id, self.owner_pid, self.width, self.height)):
            raise ObsError("obs_window_binding_invalid")
        if self.width > 3840 or self.height > 2160: raise ObsError("obs_window_size_bound")


def capture_content_crop(width, height, binding, native_size):
    """Remove only a bounded top decoration band of this exact owned window."""
    if (any(type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 7680
            or int(value) != value for value in (width, height))
            or not isinstance(native_size, tuple) or len(native_size) != 2
            or any(type(value) is not int or not 1 <= value <= 3840 for value in native_size)):
        raise ObsError("obs_capture_dimensions_unavailable")
    ratio = width / height; native_ratio = native_size[0] / native_size[1]
    if abs(ratio / native_ratio - 1) <= .02: return 0  # SCK already excludes decorations.
    if abs(ratio / (binding.width / binding.height) - 1) > .02:
        raise ObsError("obs_capture_window_aspect_unverified")
    top = round(height - width * native_size[1] / native_size[0])
    if not 0 <= top <= height * .12: raise ObsError("obs_capture_window_chrome_unverified")
    return top


@dataclass(frozen=True)
class PublicStartPermit:
    channel_login: str
    expires_at_monotonic: float
    explicit_public_start: bool = False


@dataclass(frozen=True)
class GraphAudit:
    passed: bool
    checks: int
    reason: str


class ObsController:
    OWNED_NAME = "ACTOR Broadcast"
    SCENES = ("ACTOR Preparing", "ACTOR Match", "ACTOR Ending", "ACTOR Safe")
    VOICE = "ACTOR Voice"
    VIDEO = "ACTOR Observer Window"
    STABILIZE_TIMEOUT_SECONDS = 3.0
    STABILIZE_QUIET_SECONDS = .15

    def __init__(self, client: ObsClient, *, window_guard, channel_guard, on_unsafe=None):
        self.client = client; self.window_guard = window_guard; self.channel_guard = channel_guard
        self.on_unsafe = on_unsafe; self._original = None; self._prepared = False; self._binding = None
        self._renderer_url = None; self._owned_stream = False; self._graph_ok = False
        self._graph_generation = 0
        self._last_audit_at = -float("inf")
        self._owner_nonce = secrets.token_hex(16); self._owned_inputs = {}; self._creating_inputs = set()
        self._expected_scene = "ACTOR Preparing"
        self._startup_pending = True
        self._native_size = None; self._capture_shape = None; self._capture_crop_top = 0
        self._setup_lock = asyncio.Lock(); self._stream_lock = asyncio.Lock(); self._record_owned = False
        self._audit_lock = asyncio.Lock()
        self._old_callback = client.on_event; client.on_event = self._event

    @property
    def route_verified(self): return (self._graph_ok and 0 <= time.monotonic()-self._last_audit_at <= 2
                                      and self._prepared and self._expected_scene in ("ACTOR Match", "ACTOR Ending")
                                      and self.window_guard(self._binding))

    def _event(self, name, data):
        if name == "CurrentProgramSceneChanged" and data.get("sceneName") != self._expected_scene:
            self._graph_ok = False; self._graph_generation += 1
            if self.on_unsafe is not None: self.on_unsafe("obs_program_scene_changed")
        if name in {"ConnectionLost", "CurrentSceneCollectionChanging", "CurrentSceneCollectionChanged",
                    "CurrentProfileChanging", "CurrentProfileChanged", "InputCreated", "InputRemoved",
                    "InputNameChanged", "SceneCreated", "SceneRemoved", "SceneNameChanged", "SceneListChanged",
                    "InputSettingsChanged", "InputMuteStateChanged", "InputVolumeChanged",
                    "InputAudioTracksChanged", "InputAudioMonitorTypeChanged", "InputAudioSyncOffsetChanged",
                    "SourceFilterCreated", "SourceFilterRemoved", "SourceFilterEnableStateChanged",
                    "SourceFilterSettingsChanged", "SourceFilterNameChanged", "SourceFilterListReindexed",
                    "SceneItemCreated", "SceneItemRemoved", "SceneItemEnableStateChanged", "SceneItemTransformChanged",
                    "SceneItemListReindexed"}:
            self._graph_ok = False
            self._graph_generation += 1
            if self.on_unsafe is not None: self.on_unsafe("obs_graph_changed")
        if self._old_callback is not None: self._old_callback(name, data)

    async def require_idle(self):
        for request in ("GetStreamStatus", "GetRecordStatus", "GetVirtualCamStatus"):
            status = await self.client.request(request)
            if status.get("outputActive") is not False: raise ObsError("obs_foreign_output_active")
        try:
            status = await self.client.request("GetReplayBufferStatus")
        except ObsError as error:
            if error.code != "obs_request_rejected" or error.request_status_code != 604:
                raise
            await self._require_disabled_replay()
        else:
            if status.get("outputActive") is not False: raise ObsError("obs_foreign_output_active")

    async def _require_disabled_replay(self):
        # obs-websocket 5.7.4 returns InvalidResourceState (604) when the
        # frontend replay output is absent. That code alone is insufficient:
        # verify the selected OBS output mode explicitly disables RecRB, then
        # independently enumerate all actual outputs, without reading settings.
        mode = (await self.client.request("GetProfileParameter", {
            "parameterCategory": "Output", "parameterName": "Mode"})).get("parameterValue")
        category = {"Simple": "SimpleOutput", "Advanced": "AdvOut"}.get(mode) if isinstance(mode, str) else None
        if category is None: raise ObsError("obs_replay_disabled_unverified")
        enabled = (await self.client.request("GetProfileParameter", {
            "parameterCategory": category, "parameterName": "RecRB"})).get("parameterValue")
        if not isinstance(enabled, str) or enabled.lower() not in ("false", "0"):
            raise ObsError("obs_replay_disabled_unverified")
        outputs = (await self.client.request("GetOutputList")).get("outputs")
        if not isinstance(outputs, list) or not 1 <= len(outputs) <= 64:
            raise ObsError("obs_replay_disabled_unverified")
        for output in outputs:
            if (not isinstance(output, dict) or output.get("outputActive") is not False
                    or not isinstance(output.get("outputName"), str) or not 1 <= len(output["outputName"]) <= 256
                    or not isinstance(output.get("outputKind"), str) or not 1 <= len(output["outputKind"]) <= 128
                    or output["outputKind"] == "replay_buffer"):
                raise ObsError("obs_replay_disabled_unverified")

    async def _require_safe_canvas(self):
        video = await self.client.request("GetVideoSettings")
        if (type(video.get("baseWidth")) is not int or video["baseWidth"] != 1280
                or type(video.get("baseHeight")) is not int or video["baseHeight"] != 720):
            raise ObsError("obs_safe_canvas_unverified")

    async def _require_voice_coverage(self, scene, items):
        voice = next(item for item in items if item["sourceName"] == self.VOICE)
        if (voice.get("sceneItemEnabled") is not True
                or type(voice.get("sceneItemId")) is not int or voice["sceneItemId"] < 0):
            raise ObsError("obs_safe_overlay_geometry_unverified")
        # OBS normal blending is required: an additive black watchdog layer
        # would leave the underlying game visible despite full-canvas geometry.
        if voice.get("sceneItemBlendMode") != "OBS_BLEND_NORMAL":
            raise ObsError("obs_safe_overlay_blend_unverified")
        transform = (await self.client.request("GetSceneItemTransform", {
            "sceneName": scene, "sceneItemId": voice["sceneItemId"]})).get("sceneItemTransform")
        numeric = {"sourceWidth": 1280, "sourceHeight": 720, "width": 1280, "height": 720,
            "positionX": 0, "positionY": 0, "scaleX": 1, "scaleY": 1, "rotation": 0,
            "alignment": 5, "boundsAlignment": 0, "boundsWidth": 0, "boundsHeight": 0,
            "cropTop": 0, "cropBottom": 0, "cropLeft": 0, "cropRight": 0}
        if (not isinstance(transform, dict) or transform.get("boundsType") != "OBS_BOUNDS_NONE"
                or transform.get("cropToBounds") is not False
                or any(type(transform.get(key)) not in (int, float) or transform[key] != expected
                    or not math.isfinite(transform[key]) for key, expected in numeric.items())):
            raise ObsError("obs_safe_overlay_geometry_unverified")
        if scene == "ACTOR Match":
            video = next(item for item in items if item["sourceName"] == self.VIDEO)
            # GetSceneItemList reports 0 at the bottom; the JSON array may be
            # reordered by a caller, so compare explicit indices only.
            if (any(type(item.get("sceneItemIndex")) is not int or not 0 <= item["sceneItemIndex"] < 2
                    for item in (video, voice)) or voice["sceneItemIndex"] <= video["sceneItemIndex"]):
                raise ObsError("obs_safe_overlay_order_unverified")

    async def prepare(self, binding: WindowBinding, renderer_url: str, *, protected_backup_confirmed=False,
                      adopt_empty_collection=False, native_size=None):
        """Create an owned graph, only after caller backed up profile+collection.

        Does not read/change service credentials, encoder, VOD or start output.
        Same-instance setup is idempotent; an existing unknown collection is
        refused, never silently taken over by matching its display name.
        """
        async with self._setup_lock:
            if self._prepared:
                if binding != self._binding or renderer_url != self._renderer_url or native_size != self._native_size:
                    raise ObsError("obs_setup_identity_changed")
                return await self.stabilize() if self._startup_pending else await self.audit()
            if protected_backup_confirmed is not True: raise ObsError("obs_protected_backup_required")
            p = urlsplit(renderer_url)
            capability = p.fragment.removeprefix("cap=")
            if (p.scheme != "http" or p.hostname != "127.0.0.1" or not p.port or p.path != "/renderer/"
                    or p.username or p.password or p.query or not p.fragment.startswith("cap=")
                    or not 32 <= len(capability) <= 128 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in capability)):
                raise ObsError("obs_renderer_url_invalid")
            if not self.window_guard(binding): raise ObsError("obs_window_identity_unavailable")
            await self._require_safe_canvas()
            await self.require_idle()
            collections = await self.client.request("GetSceneCollectionList")
            profiles = await self.client.request("GetProfileList")
            current_profile, profile_names = _profile_list(profiles)
            collection_exists = self.OWNED_NAME in collections.get("sceneCollections", [])
            profile_exists = self.OWNED_NAME in profile_names
            if collection_exists or profile_exists:
                if (adopt_empty_collection is not True or not collection_exists or not profile_exists
                        or collections.get("currentSceneCollectionName") != self.OWNED_NAME
                        or current_profile != self.OWNED_NAME):
                    raise ObsError("obs_existing_collection_requires_explicit_recovery")
                if (await self.client.request("GetInputList")).get("inputs") != []:
                    raise ObsError("obs_adopt_collection_not_empty")
                special = await self.client.request("GetSpecialInputs")
                if any(value not in (None, "") for value in special.values()): raise ObsError("obs_global_audio_forbidden")
                for scene in (await self.client.request("GetSceneList"))["scenes"]:
                    if (await self.client.request("GetSceneItemList", {"sceneName": scene["sceneName"]})).get("sceneItems") != []:
                        raise ObsError("obs_adopt_collection_not_empty")
            self._original = (collections["currentSceneCollectionName"], current_profile)
            try:
                if not profile_exists: await self.client.request("CreateProfile", {"profileName": self.OWNED_NAME})
                if not collection_exists: await self.client.request("CreateSceneCollection", {"sceneCollectionName": self.OWNED_NAME})
                existing_scenes = {value["sceneName"] for value in (await self.client.request("GetSceneList"))["scenes"]}
                for scene in self.SCENES:
                    if scene not in existing_scenes: await self.client.request("CreateScene", {"sceneName": scene})
                # Fresh collections sometimes include one empty default scene.
                for scene in (await self.client.request("GetSceneList")).get("scenes", []):
                    name = scene["sceneName"]
                    if name not in self.SCENES:
                        if (await self.client.request("GetSceneItemList", {"sceneName": name})).get("sceneItems"):
                            raise ObsError("obs_fresh_collection_not_empty")
                        await self.client.request("RemoveScene", {"sceneName": name})
                await self._create_input({"sceneName": "ACTOR Match", "inputName": self.VIDEO,
                    "inputKind": "screen_capture", "inputSettings": {"type": 1, "window": binding.window_id,
                        "show_cursor": False, "show_hidden_windows": False}, "sceneItemEnabled": True})
                await self.client.request("SetInputMute", {"inputName": self.VIDEO, "inputMuted": True})
                await self.client.request("SetInputAudioTracks", {"inputName": self.VIDEO, "inputAudioTracks": {str(i): False for i in range(1, 7)}})
                await self.client.request("SetInputAudioMonitorType", {"inputName": self.VIDEO, "monitorType": "OBS_MONITORING_TYPE_NONE"})
                await self._create_input({"sceneName": "ACTOR Match", "inputName": self.VOICE,
                    "inputKind": "browser_source", "inputSettings": {"url": renderer_url, "width": 1280, "height": 720,
                        "is_local_file": False, "reroute_audio": True, "webpage_control_level": 0,
                        "shutdown": False, "restart_when_active": False, "css": ""}, "sceneItemEnabled": True})
                await self.client.request("SetInputMute", {"inputName": self.VOICE, "inputMuted": False})
                await self.client.request("SetInputVolume", {"inputName": self.VOICE, "inputVolumeMul": 1.0})
                await self.client.request("SetInputAudioTracks", {"inputName": self.VOICE, "inputAudioTracks": {str(i): i == 1 for i in range(1, 7)}})
                await self.client.request("SetInputAudioMonitorType", {"inputName": self.VOICE, "monitorType": "OBS_MONITORING_TYPE_NONE"})
                await self.client.request("CreateSceneItem", {"sceneName": "ACTOR Ending", "sourceName": self.VOICE, "sceneItemEnabled": True})
                video_items = (await self.client.request("GetSceneItemList", {"sceneName": "ACTOR Match"}))["sceneItems"]
                video = next(x for x in video_items if x["sourceName"] == self.VIDEO)
                await self.client.request("SetSceneItemTransform", {"sceneName": "ACTOR Match", "sceneItemId": video["sceneItemId"],
                    "sceneItemTransform": {"positionX": 0.0, "positionY": 0.0,
                        "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": 1280.0, "boundsHeight": 720.0,
                        "boundsAlignment": 0, "alignment": 5}})
                self._binding = binding; self._renderer_url = renderer_url; self._native_size = native_size; self._prepared = True
                await self.client.request("SetCurrentProgramScene", {"sceneName": "ACTOR Preparing"})
                return await self._pin_capture()
            except BaseException:
                # Retain ownership markers for explicit cleanup/retry even
                # when CreateInput's response was lost after OBS committed it.
                raise

    async def _pin_capture(self):
        async with self._audit_lock:
            # Dimensions must come from the same full, generation-stable graph
            # audit as the owned window/UUID/marker proof, never a guessed size.
            await self._stabilize_audit()
            width, height = (self.video_transform[key] for key in ("sourceWidth", "sourceHeight"))
            top = capture_content_crop(width, height, self._binding, self._native_size) if self._native_size is not None else 0
            self._capture_shape = (width, height); self._capture_crop_top = top
            if top:
                rows = (await self.client.request("GetSceneItemList", {"sceneName": "ACTOR Match"}))["sceneItems"]
                matches = [row for row in rows if row.get("sourceUuid") == self._owned_inputs.get(self.VIDEO)
                           and row.get("sourceName") == self.VIDEO]
                if len(matches) != 1: raise ObsError("obs_capture_ownership_changed")
                self._graph_ok = False
                await self.client.request("SetSceneItemTransform", {"sceneName": "ACTOR Match",
                    "sceneItemId": matches[0]["sceneItemId"], "sceneItemTransform": {
                        "cropTop": top, "cropBottom": 0, "cropLeft": 0, "cropRight": 0}})
            return await self._stabilize_audit()

    async def _create_input(self, data):
        name = data["inputName"]; self._creating_inputs.add(name)
        settings = dict(data["inputSettings"]); settings["actor_broadcast_owner"] = self._owner_nonce
        response = await self.client.request("CreateInput", {**data, "inputSettings": settings})
        identity = response.get("inputUuid")
        try: UUID(identity)
        except (ValueError, TypeError, AttributeError): raise ObsError("obs_input_uuid_required") from None
        self._owned_inputs[name] = identity; self._creating_inputs.discard(name)

    async def cleanup_owned_inputs(self):
        """After output stop, remove only UUIDs actually created by this run.

        Names alone never grant deletion rights. A lost CreateInput response
        may be reconciled only by its independent random settings marker.
        Unknown/foreign resources are preserved and fail the empty-adoption gate.
        """
        await self.require_idle()
        collections = await self.client.request("GetSceneCollectionList")
        profiles = await self.client.request("GetProfileList")
        current_profile, _ = _profile_list(profiles)
        if (collections.get("currentSceneCollectionName") != self.OWNED_NAME
                or current_profile != self.OWNED_NAME):
            raise ObsError("obs_owned_cleanup_collection_changed")
        inputs = (await self.client.request("GetInputList"))["inputs"]
        for name in list(self._creating_inputs):
            candidates = [value for value in inputs if value.get("inputName") == name]
            if len(candidates) > 1: raise ObsError("obs_owned_cleanup_ambiguous")
            if candidates:
                row = candidates[0]; identity = row.get("inputUuid")
                settings = (await self.client.request("GetInputSettings", {"inputUuid": identity})).get("inputSettings", {})
                if settings.get("actor_broadcast_owner") != self._owner_nonce:
                    raise ObsError("obs_owned_cleanup_foreign_input")
                try: UUID(identity)
                except (ValueError, TypeError, AttributeError): raise ObsError("obs_input_uuid_required") from None
                self._owned_inputs[name] = identity
        verified = set()
        for name, identity in list(self._owned_inputs.items()):
            candidates = [value for value in inputs if value.get("inputUuid") == identity]
            if len(candidates) > 1: raise ObsError("obs_owned_cleanup_ambiguous")
            if candidates:
                settings = (await self.client.request("GetInputSettings", {"inputUuid": identity})).get("inputSettings", {})
                if settings.get("actor_broadcast_owner") != self._owner_nonce:
                    raise ObsError("obs_owned_cleanup_foreign_input")
                verified.add(identity)
            elif any(row.get("inputName") == name for row in inputs):
                raise ObsError("obs_owned_cleanup_foreign_input")
        scenes = (await self.client.request("GetSceneList")).get("scenes")
        if (not isinstance(scenes, list) or len(scenes) > len(self.SCENES)
                or any(not isinstance(row, dict) or row.get("sceneName") not in self.SCENES for row in scenes)
                or len({row["sceneName"] for row in scenes}) != len(scenes)):
            raise ObsError("obs_owned_cleanup_scene_changed")
        scene_ids = []
        for row in scenes:
            identity = row.get("sceneUuid")
            try: UUID(identity)
            except (ValueError, TypeError, AttributeError): raise ObsError("obs_owned_cleanup_scene_changed") from None
            if identity in scene_ids: raise ObsError("obs_owned_cleanup_scene_changed")
            scene_ids.append(identity)

        async def scene_items(identity):
            rows = (await self.client.request("GetSceneItemList", {"sceneUuid": identity})).get("sceneItems")
            if (not isinstance(rows, list) or len(rows) > 64
                    or any(not isinstance(row, dict) or type(row.get("sceneItemId")) is not int
                           or row["sceneItemId"] < 0 for row in rows)
                    or len({row["sceneItemId"] for row in rows}) != len(rows)):
                raise ObsError("obs_owned_cleanup_scene_changed")
            for row in rows:
                try: UUID(row.get("sourceUuid"))
                except (ValueError, TypeError, AttributeError): raise ObsError("obs_owned_cleanup_scene_changed") from None
            return rows

        # libobs 32 prunes removed sources only while their scenes render. Safe
        # is deliberately current here, so detach the exact owned references
        # explicitly instead of waiting for inactive Match/Ending to render.
        for scene_id in scene_ids:
            for row in await scene_items(scene_id):
                if row.get("sourceUuid") in verified:
                    await self.client.request("RemoveSceneItem", {"sceneUuid": scene_id, "sceneItemId": row["sceneItemId"]})
        owned_ids = set(self._owned_inputs.values())
        for scene_id in scene_ids:
            if any(row.get("sourceUuid") in owned_ids for row in await scene_items(scene_id)):
                raise ObsError("obs_owned_cleanup_references_retained")
        # The last detached scene item may already have released the source.
        # Never issue a name-based removal or assume the UUID still exists.
        collections = await self.client.request("GetSceneCollectionList")
        profile, _ = _profile_list(await self.client.request("GetProfileList"))
        if collections.get("currentSceneCollectionName") != self.OWNED_NAME or profile != self.OWNED_NAME:
            raise ObsError("obs_owned_cleanup_collection_changed")
        current = (await self.client.request("GetInputList"))["inputs"]
        for identity in owned_ids:
            candidates = [row for row in current if row.get("inputUuid") == identity]
            if len(candidates) > 1: raise ObsError("obs_owned_cleanup_ambiguous")
            if candidates:
                settings = (await self.client.request("GetInputSettings", {"inputUuid": identity})).get("inputSettings", {})
                if settings.get("actor_broadcast_owner") != self._owner_nonce:
                    raise ObsError("obs_owned_cleanup_foreign_input")
                await self.client.request("RemoveInput", {"inputUuid": identity})
        remaining = (await self.client.request("GetInputList"))["inputs"]
        if remaining: raise ObsError("obs_owned_cleanup_foreign_input")
        # Keep both UUID and lost-response markers until absence is confirmed.
        self._owned_inputs.clear(); self._creating_inputs.clear()
        self._graph_ok = False; self._prepared = False

    async def audit(self):
        async with self._audit_lock: return await self._audit()

    async def stabilize(self):
        """During startup only, wait for full strict audits in a quiet graph.

        No event or generation check is weakened. This bounded startup wait is
        never available while a stream/recording is owned or actually active.
        """
        async with self._audit_lock:
            if not self._startup_pending: raise ObsError("obs_startup_already_completed")
            return await self._stabilize_audit()

    async def complete_startup(self):
        """Seal startup settling after the actual local audio/video probe."""
        async with self._audit_lock:
            if not self._startup_pending: return await self._audit()
            result = await self._stabilize_audit()
            self._startup_pending = False
            return result

    async def _stabilize_audit(self):
        self._graph_ok = False
        generation = None; valid_since = None
        try:
            async with asyncio.timeout(self.STABILIZE_TIMEOUT_SECONDS):
                if self._owned_stream or self._record_owned: raise ObsError("obs_stabilization_requires_idle")
                await self.require_idle()
                while True:
                    if self._owned_stream or self._record_owned: raise ObsError("obs_stabilization_requires_idle")
                    result = await self._audit()
                    now = time.monotonic()
                    if result.passed:
                        if generation != self._graph_generation or valid_since is None:
                            generation = self._graph_generation; valid_since = now
                        if (generation == self._graph_generation and valid_since is not None
                                and now - valid_since >= self.STABILIZE_QUIET_SECONDS):
                            self._graph_ok = False
                            await self.require_idle()
                            if (generation == self._graph_generation and not self._owned_stream
                                    and not self._record_owned):
                                self._graph_ok = True
                                return result
                    else:
                        generation = None; valid_since = None
                    # A first passing audit is not settled readiness. Pending
                    # activation/initialization must never expose a ready route.
                    self._graph_ok = False
                    await asyncio.sleep(.05)
        except TimeoutError:
            self._graph_ok = False
            raise ObsError("obs_graph_stabilization_timeout") from None
        except BaseException:
            self._graph_ok = False
            raise

    async def _audit(self):
        # Keep a previously verified, still-fresh proof during a normal poll.
        # Events revoke it immediately; a hung audit expires within two seconds.
        if not self._prepared or not self.window_guard(self._binding):
            self._graph_ok = False
            return GraphAudit(False, 0, "obs_window_identity_unavailable")
        checks = 0; generation = self._graph_generation
        try:
            c = await self.client.request("GetSceneCollectionList"); p = await self.client.request("GetProfileList")
            current_profile, _ = _profile_list(p)
            if c.get("currentSceneCollectionName") != self.OWNED_NAME or current_profile != self.OWNED_NAME:
                raise ObsError("obs_collection_changed")
            checks += 1
            await self._require_safe_canvas()
            checks += 1
            if (await self.client.request("GetCurrentProgramScene")).get("currentProgramSceneName") != self._expected_scene:
                raise ObsError("obs_program_scene_changed")
            checks += 1
            special = await self.client.request("GetSpecialInputs")
            if any(value not in (None, "") for value in special.values()): raise ObsError("obs_global_audio_forbidden")
            checks += 1
            inputs = (await self.client.request("GetInputList"))["inputs"]
            if len(inputs) != 2 or {x["inputName"] for x in inputs} != {self.VIDEO, self.VOICE}: raise ObsError("obs_unknown_input")
            for item in inputs:
                name = item["inputName"]; value = await self.client.request("GetInputSettings", {"inputName": name}); s = value["inputSettings"]
                if self._owned_inputs and (item.get("inputUuid") != self._owned_inputs.get(name) or s.get("actor_broadcast_owner") != self._owner_nonce):
                    raise ObsError("obs_input_ownership_changed")
                kind = value.get("inputKind", item.get("unversionedInputKind", item.get("inputKind")))
                if name == self.VIDEO:
                    if kind != "screen_capture" or type(s.get("type")) is not int or s["type"] != 1 or type(s.get("window")) is not int or s["window"] != self._binding.window_id:
                        raise ObsError("obs_capture_not_exact_window")
                    if (await self.client.request("GetInputMute", {"inputName": name})).get("inputMuted") is not True: raise ObsError("obs_game_audio_not_muted")
                    wanted_tracks = {str(i): False for i in range(1, 7)}
                else:
                    wanted = {"url": self._renderer_url, "is_local_file": False, "reroute_audio": True,
                        "webpage_control_level": 0, "width": 1280, "height": 720, "css": "",
                        "shutdown": False, "restart_when_active": False}
                    if (kind != "browser_source" or any(s.get(k) != v for k, v in wanted.items())
                            or type(s.get("webpage_control_level")) is not int
                            or any(type(s.get(k)) is not int for k in ("width", "height"))
                            or s.get("reroute_audio") is not True or s.get("is_local_file") is not False
                            or s.get("shutdown") is not False or s.get("restart_when_active") is not False):
                        raise ObsError("obs_browser_settings_changed")
                    if (await self.client.request("GetInputMute", {"inputName": name})).get("inputMuted") is not False: raise ObsError("obs_voice_muted")
                    volume = (await self.client.request("GetInputVolume", {"inputName": name})).get("inputVolumeMul")
                    if type(volume) not in (int, float) or not .001 <= volume <= 1.0: raise ObsError("obs_voice_volume_invalid")
                    wanted_tracks = {str(i): i == 1 for i in range(1, 7)}
                if (await self.client.request("GetInputAudioTracks", {"inputName": name})).get("inputAudioTracks") != wanted_tracks:
                    raise ObsError("obs_audio_tracks_changed")
                if (await self.client.request("GetInputAudioMonitorType", {"inputName": name})).get("monitorType") != "OBS_MONITORING_TYPE_NONE":
                    raise ObsError("obs_unexpected_monitoring")
                if (await self.client.request("GetSourceFilterList", {"sourceName": name})).get("filters") != []:
                    raise ObsError("obs_unknown_source_filter")
                checks += 1
            scenes = (await self.client.request("GetSceneList"))["scenes"]
            if len(scenes) != 4 or {x["sceneName"] for x in scenes} != set(self.SCENES): raise ObsError("obs_unknown_scene")
            expected = {"ACTOR Preparing": set(), "ACTOR Match": {self.VIDEO, self.VOICE}, "ACTOR Ending": {self.VOICE}, "ACTOR Safe": set()}
            for scene in self.SCENES:
                if (await self.client.request("GetSourceFilterList", {"sourceName": scene})).get("filters") != []:
                    raise ObsError("obs_unknown_scene_filter")
                items = (await self.client.request("GetSceneItemList", {"sceneName": scene}))["sceneItems"]
                if len(items) != len(expected[scene]) or {x["sourceName"] for x in items} != expected[scene] or any(x.get("isGroup") for x in items):
                    raise ObsError("obs_scene_graph_not_allowlisted")
                if scene in ("ACTOR Match", "ACTOR Ending"):
                    await self._require_voice_coverage(scene, items)
                if any(x.get("sceneItemEnabled") is not True for x in items):
                    raise ObsError("obs_scene_graph_not_allowlisted")
                if scene == "ACTOR Match":
                    item = next(value for value in items if value["sourceName"] == self.VIDEO)
                    transform = (await self.client.request("GetSceneItemTransform", {"sceneName": scene,
                        "sceneItemId": item["sceneItemId"]})).get("sceneItemTransform", {})
                    expected_geometry = {"positionX": 0, "positionY": 0, "rotation": 0, "alignment": 5,
                        "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0, "boundsWidth": 1280,
                        "boundsHeight": 720, "cropTop": self._capture_crop_top, "cropBottom": 0, "cropLeft": 0, "cropRight": 0}
                    if any(transform.get(k) != v for k,v in expected_geometry.items()): raise ObsError("obs_capture_geometry_changed")
                    if any(type(transform.get(k)) not in (int,float) or not 0 < transform[k] <= 7680 for k in ("sourceWidth","sourceHeight")):
                        raise ObsError("obs_capture_dimensions_unavailable")
                    if self._capture_shape is not None and tuple(transform[key] for key in ("sourceWidth", "sourceHeight")) != self._capture_shape:
                        raise ObsError("obs_capture_dimensions_changed")
                    self.video_transform = dict(transform)
                checks += 1
            if not self.window_guard(self._binding): raise ObsError("obs_window_identity_unavailable")
            if generation != self._graph_generation: raise ObsError("obs_graph_changed_during_audit")
            self._last_audit_at = time.monotonic(); self._graph_ok = True
            return GraphAudit(True, checks, "verified_owned_graph")
        except ObsError as error:
            self._graph_ok = False
            reason = error.code if error.code in {"obs_safe_canvas_unverified", "obs_safe_overlay_geometry_unverified",
                "obs_safe_overlay_order_unverified", "obs_safe_overlay_blend_unverified",
                "obs_profile_list_invalid"} else "obs_graph_audit_failed"
            return GraphAudit(False, checks, reason)
        except (KeyError, TypeError):
            self._graph_ok = False
            return GraphAudit(False, checks, "obs_graph_audit_failed")
        except BaseException:
            self._graph_ok = False
            raise

    async def show(self, scene):
        if scene not in self.SCENES: raise ObsError("obs_scene_not_owned")
        # The monitor must see the complete authorized transition, not an
        # intermediate old-program/new-expected state from our own request.
        async with self._audit_lock:
            if scene != "ACTOR Safe":
                audit = await self._stabilize_audit() if self._startup_pending else await self._audit()
                if not audit.passed: raise ObsError(audit.reason)
                if scene == self._expected_scene: return
            self._expected_scene = scene; self._graph_ok = False; self._graph_generation += 1
            await self.client.request("SetCurrentProgramScene", {"sceneName": scene})
            if scene != "ACTOR Safe":
                audit = await self._stabilize_audit() if self._startup_pending else await self._audit()
                if not audit.passed: raise ObsError(audit.reason)

    async def start_stream(self, permit: PublicStartPermit):
        async with self._stream_lock:
            if self._owned_stream: return
            if self._startup_pending: raise ObsError("obs_startup_incomplete")
            if (not isinstance(permit, PublicStartPermit) or permit.explicit_public_start is not True
                or permit.channel_login != "fedorr_codex" or type(permit.expires_at_monotonic) not in (int, float)
                or not time.monotonic() < permit.expires_at_monotonic <= time.monotonic() + 60):
                raise ObsError("obs_public_start_not_authorized")
            if not await self.verify_channel(permit.channel_login): raise ObsError("obs_channel_binding_unverified")
            await self.require_idle()
            if not (await self.audit()).passed: raise ObsError("obs_graph_audit_failed")
            await self.show("ACTOR Match")
            if not await self.verify_channel(permit.channel_login): raise ObsError("obs_channel_binding_unverified")
            if (not time.monotonic() < permit.expires_at_monotonic or not self.route_verified):
                raise ObsError("obs_public_start_not_authorized")
            # Idle graph + explicit permit + the coordinator's exclusive OBS
            # lease establish ownership before the request can affect OBS.
            # A lost response must not turn an owned output into an orphan.
            self._owned_stream = True
            try:
                await self.client.request("StartStream")
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    state = await self.client.request("GetStreamStatus", timeout=max(.1, min(2, deadline - time.monotonic())))
                    if state.get("outputActive") is True and state.get("outputReconnecting") is False: return
                    await asyncio.sleep(.25)
                raise ObsError("obs_live_unconfirmed")
            except BaseException:
                await _cleanup(self.stop_stream(), 12)
                raise

    async def verify_channel(self, login="fedorr_codex"):
        value = self.channel_guard(login)
        if inspect.isawaitable(value): value = await asyncio.wait_for(value, 6)
        return value is True

    async def stop_stream(self):
        if not self._owned_stream: return
        if (await self.client.request("GetStreamStatus", timeout=2)).get("outputActive") is False:
            self._owned_stream = False; return
        await self.client.request("StopStream")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (await self.client.request("GetStreamStatus", timeout=max(.1, min(2, deadline - time.monotonic())))).get("outputActive") is False:
                self._owned_stream = False; return
            await asyncio.sleep(.25)
        raise ObsError("obs_stop_unconfirmed")

    async def stop_record(self):
        if not self._record_owned: return {}
        # A previous StopRecord may have committed with a lost response.
        # Confirm inactivity before retrying, rather than treating OBS's
        # "not recording" rejection as an uncloseable owned resource.
        if (await self.client.request("GetRecordStatus", timeout=1)).get("outputActive") is False:
            self._record_owned = False; return {}
        result = await self.client.request("StopRecord", timeout=5)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if (await self.client.request("GetRecordStatus", timeout=1)).get("outputActive") is False:
                self._record_owned = False; return result
            await asyncio.sleep(.05)
        raise ObsError("obs_record_stop_unconfirmed")

    async def emergency_stop(self):
        self._graph_ok = False
        route_error = False
        try:
            if self._prepared:
                self._expected_scene = "ACTOR Safe"
                for request, data in (("SetInputMute", {"inputName": self.VOICE, "inputMuted": True}),
                                      ("SetCurrentProgramScene", {"sceneName": "ACTOR Safe"})):
                    try: await self.client.request(request, data, timeout=2)
                    except Exception: route_error = True
        finally:
            # Source removal or caller cancellation must never prevent the
            # independently owned output stop.
            try: await _cleanup(self.stop_stream(), 12)
            finally: await _cleanup(self.stop_record(), 8)
        if route_error: raise ObsError("obs_emergency_route_cleanup_failed")

    async def restore(self):
        if self._owned_stream: await self.stop_stream()
        if self._owned_inputs or self._creating_inputs: await self.cleanup_owned_inputs()
        if self._original is not None:
            await self.require_idle()
            collection, profile = self._original
            await self.client.request("SetCurrentSceneCollection", {"sceneCollectionName": collection})
            await self.client.request("SetCurrentProfile", {"profileName": profile})
        self._prepared = False; self._graph_ok = False

    async def close(self):
        await self.emergency_stop()
        if self._owned_inputs or self._creating_inputs: await self.cleanup_owned_inputs()
        await self.client.close()
