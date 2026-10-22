"""Production composition for one authorized native observer broadcast.

No work happens on import. prepare is local preview: it never starts a public
stream or a commentary turn. All privileged/native/account operations are
explicit integration boundaries, with injectable implementations for tests.
"""
from __future__ import annotations

import asyncio
from array import array
import base64
import fcntl
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import struct
import subprocess
import time
from uuid import uuid4

from .admission import AdmissionClient
from .arena import ArenaBroadcastProjector
from .audio import BrowserAudioSink, SpeechAudioOutput
from .bridge import BroadcastBridge
from .budget import BudgetLedger
from .chat import TokenPair, TwitchChatSource, TwitchDeviceAuth
from .contracts import EditorialFact, PcmChunk, Scope, SegmentKey, SpeechSegment, SynthesisDone
from .director import CameraDirector, ViewIntent
from .obs import ObsClient, ObsConnection, ObsController, ObsError, capture_content_crop
from .obs_firewall_attestor import FirewallAttestationClient
from .ownership import ChannelLease
from .scheduler import CommentaryScheduler
from .speech import MlxSpeechConfig, MlxSpeechSynthesizer, _cleanup
from .window import OwnedWindowLocator


class NativeError(RuntimeError):
    def __init__(self, code, *, prepare_code=None, cleanup_stages=()):
        self.code = code; self.prepare_code = prepare_code; self.cleanup_stages = tuple(cleanup_stages)
        super().__init__(code)


@dataclass
class _DirectorSelection:
    intent: ViewIntent
    correlation_id: str
    sent_at: float
    after_sequence: int
    result_received: bool = False


def _prepare_code(error):
    if isinstance(error, asyncio.CancelledError): return "broadcast_prepare_cancelled"
    code = getattr(error, "code", None)
    if (isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code)
            and code.startswith(("broadcast_", "obs_", "pf_", "speech_", "commentary_", "admission_", "observer_", "twitch_", "audio_"))):
        return code
    return "broadcast_prepare_failed"


def private_directory(path, *, create=True):
    path = Path(path).absolute()
    if path.resolve() != path: raise NativeError("broadcast_private_path_alias")
    if create: path.mkdir(mode=0o700, parents=True, exist_ok=True)
    value = path.lstat()
    if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.geteuid() or value.st_mode & 0o077:
        raise NativeError("broadcast_private_directory_required")
    return path


def installation_identity(directory):
    directory = private_directory(directory)
    path = directory / "opaque-install-id"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as stream:
        # Serialise even different channel controllers. The inode is never
        # unlinked: concurrent first use must return the one persisted scope.
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        value = os.fstat(stream.fileno())
        if not stat.S_ISREG(value.st_mode) or value.st_uid != os.geteuid() or value.st_mode & 0o077 or value.st_nlink != 1:
            raise NativeError("broadcast_installation_identity_unsafe")
        identity = stream.read(65)
        if not identity:
            # Losing the identity must not silently reset an existing quota.
            if (directory / "quota.sqlite").exists() or (directory / "quota.sqlite").is_symlink():
                raise NativeError("broadcast_installation_identity_missing_with_quota")
            identity = uuid4().hex; stream.write(identity); stream.flush(); os.fsync(stream.fileno())
            parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(parent)
            finally: os.close(parent)
        if not re.fullmatch(r"[a-f0-9]{32}", identity): raise NativeError("broadcast_installation_identity_invalid")
        return identity


def verify_backup_receipt(directory):
    directory = private_directory(directory, create=False)
    path = directory / "backup-receipt.json"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r") as stream:
        value = os.fstat(stream.fileno())
        if not stat.S_ISREG(value.st_mode) or value.st_uid != os.geteuid() or value.st_mode & 0o077 or value.st_size > 8192:
            raise NativeError("obs_backup_receipt_unsafe")
        receipt = json.load(stream)
    required = {"schema", "completed", "files_verified", "scope", "source_obs_version", "created_at", "groups", "file_count", "total_bytes", "manifest_sha256"}
    groups = {"basic/profiles", "basic/scenes", "global.ini", "plugin_config/obs-websocket/config.json"}
    try:
        stamp = datetime.fromisoformat(receipt["created_at"].replace("Z", "+00:00"))
        valid = (set(receipt) == required and receipt["schema"] == "actor.obs-backup.v1"
            and receipt["completed"] is True and receipt["files_verified"] is True
            and receipt["scope"] == "all_preexisting_obs_configuration"
            and re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", receipt["source_obs_version"])
            and stamp.tzinfo is not None and 0 < stamp.timestamp() <= time.time()+60
            and isinstance(receipt["groups"], list) and len(receipt["groups"]) <= 16
            and all(isinstance(x, str) for x in receipt["groups"]) and groups <= set(receipt["groups"])
            and type(receipt["file_count"]) is int and receipt["file_count"] > 0
            and type(receipt["total_bytes"]) is int and receipt["total_bytes"] > 0
            and re.fullmatch(r"[a-f0-9]{64}", receipt["manifest_sha256"]))
    except (ValueError, TypeError, KeyError, AttributeError): valid = False
    if not valid: raise NativeError("obs_backup_receipt_invalid")
    # Metadata only. Never read native OBS authorization/configuration copies.
    for group in groups:
        target = directory / group
        if target.resolve() != target or not target.exists(): raise NativeError("obs_backup_group_missing")
        info = target.lstat()
        is_directory = group in ("basic/profiles", "basic/scenes")
        if (info.st_uid != os.geteuid() or info.st_mode & 0o077
                or not (stat.S_ISDIR(info.st_mode) if is_directory else stat.S_ISREG(info.st_mode))):
            raise NativeError("obs_backup_group_unsafe")
    return True


def child_environment(admission, bridge_token):
    allowed = {"HOME", "USER", "LOGNAME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(ACTOR_OBSERVER_ADMISSION=admission.child_environment_value(), ACTOR_BRIDGE_TOKEN=bridge_token)
    return env


def _provider(config):
    # The official provider owns account/catalog verification. No fallback.
    from actor_codex.commentary import CodexCommentaryProvider
    return CodexCommentaryProvider(codex_home=config.codex_home)


def _registry():
    from actor_runtime.extensions import ExtensionRegistry
    return ExtensionRegistry.discover()


def _canonical_directory(): return Path.home() / ".actor" / "broadcast"


def _resolve(config):
    from actor_agents_arena.launcher import game_profile
    from actor_runtime.launching import resolve_client
    profile = game_profile()
    if (profile.game_id, profile.game_version) != (config.game_id, config.game_version):
        raise NativeError("broadcast_installed_game_version_mismatch")
    client, proof = resolve_client(profile, manifest=config.client_manifest)
    if proof.get("integrity") != "sha256_verified": raise NativeError("broadcast_verified_client_required")
    return client, profile


@dataclass
class NativeDependencies:
    """Explicit fakes in tests; CLI constructs the real defaults unchanged."""
    admission: object = AdmissionClient
    lease: object = ChannelLease
    bridge: object = BroadcastBridge
    registry: object = _registry
    provider: object = _provider
    voice: object = MlxSpeechSynthesizer
    sink: object = BrowserAudioSink
    output: object = SpeechAudioOutput
    obs_client: object = ObsClient
    obs_controller: object = ObsController
    window: object = OwnedWindowLocator
    attestor: object = FirewallAttestationClient
    budget: object = BudgetLedger
    scheduler: object = CommentaryScheduler
    projector: object = ArenaBroadcastProjector
    director: object = CameraDirector
    auth: object = TwitchDeviceAuth
    chat: object = TwitchChatSource
    spawn: object = subprocess.Popen
    resolve: object = _resolve
    audio_probe: object = None
    canonical_directory: object = _canonical_directory


class NativeBroadcastFactory:
    def __init__(self, config, store, *, dependencies=None, guardian=None):
        self.config, self.store = config, store
        self.guardian = guardian
        self.dependencies = dependencies or NativeDependencies()
        self._owned = []; self._preparing = False
        self._closed = False
        self._rooms_cache = []; self._rooms_at = -float("inf"); self._rooms_task = None

    async def rooms(self):
        if self._closed: return list(self._rooms_cache)
        # Public listing, no operator key on the public endpoint.
        from urllib.request import build_opener, ProxyHandler, Request
        from .admission import _NoRedirect
        def read():
            opener = build_opener(ProxyHandler({}), _NoRedirect())
            try:
                with opener.open(Request(self.config.operator_forward_url.rstrip("/") + "/api/v1/lobbies",
                                         headers={"Accept": "application/json", "Cache-Control": "no-store"}), timeout=3) as response:
                    raw = response.read(32769)
            except OSError: raise NativeError("broadcast_rooms_unavailable") from None
            if len(raw) > 32768: raise NativeError("broadcast_room_list_bound")
            value = json.loads(raw)
            if isinstance(value, dict): value = value.get("lobbies")
            if not isinstance(value, list) or len(value) > 32: raise NativeError("broadcast_room_list_invalid")
            result = []
            for row in value:
                if (not isinstance(row, dict) or not re.fullmatch(r"[A-Z0-9]{6}", row.get("roomCode", ""))
                        or row.get("gameMode") != "SocialDeduction"): continue
                result.append({"room_code": row["roomCode"], "mode": "SocialDeduction",
                    "state": row.get("status") if row.get("status") in ("Allocating", "Ready", "InProgress", "Completed") else "Unknown",
                    "players": row.get("currentPlayers") if type(row.get("currentPlayers")) is int else None,
                    "capacity": row.get("maxPlayers") if type(row.get("maxPlayers")) is int else None})
            return result
        if time.monotonic() - self._rooms_at >= 5 and (self._rooms_task is None or self._rooms_task.done()):
            async def refresh():
                try:
                    self._rooms_cache = await asyncio.to_thread(read)
                    self._rooms_at = time.monotonic()
                except Exception: self._rooms_at = time.monotonic() - 4
            self._rooms_task = asyncio.create_task(refresh())
        if self._rooms_task is not None and not self._rooms_task.done():
            await asyncio.wait((self._rooms_task,), timeout=.8)
        return list(self._rooms_cache)

    async def prepare(self, room_code, session_id, commentary, *, on_ready, on_unsafe, on_terminal):
        if self._closed: raise NativeError("broadcast_factory_closed")
        if self._preparing or self._owned: raise NativeError("broadcast_cleanup_pending")
        if not re.fullmatch(r"[A-Z0-9]{6}", room_code) or not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise NativeError("broadcast_scope_invalid")
        self._preparing = True
        run = PreparedNativeRun(self, room_code, session_id, commentary, on_ready, on_unsafe, on_terminal)
        self._owned.append(run)
        try:
            if self.guardian is not None: await self.guardian.begin_run(room_code, session_id)
            await run.prepare()
            return run
        except BaseException as error:
            run.prepare_failure_code = _prepare_code(error)
            try: await _cleanup(run.close(emergency=True), 35)
            except Exception:
                raise NativeError("broadcast_prepare_cleanup_incomplete", prepare_code=run.prepare_failure_code,
                    cleanup_stages=run.cleanup_failures) from None
            raise
        finally: self._preparing = False

    async def aclose(self):
        self._closed = True
        failures = []
        for run in list(self._owned):
            try: await _cleanup(run.close(emergency=True), 35)
            except Exception: failures.append(1)
        if self._rooms_task is not None and not self._rooms_task.done():
            done, _ = await asyncio.wait((self._rooms_task,), timeout=3.5)
            if self._rooms_task not in done: failures.append(1)
        if failures: raise NativeError("broadcast_factory_cleanup_incomplete")


class PreparedNativeRun:
    def __init__(self, factory, room, session_id, commentary, on_ready, on_unsafe, on_terminal):
        self.factory = factory; self.config = factory.config; self.deps = factory.dependencies; self.store = factory.store
        self.room, self.session_id, self.commentary = room, session_id, commentary
        self.on_ready, self.on_unsafe, self.on_terminal = on_ready, on_unsafe, on_terminal
        self.lease = self.admission = self.bridge = self.process = self.log = self.provider = self.voice = None
        self.sink = self.output = self.obs = self.obs_client = self.window = self.attestor = self.budget = None
        self.scheduler = self.session = self.projector = self.auth = self.chat = None
        self._flags = dict.fromkeys(("observer", "model", "tts", "obs", "audio", "chat"), False)
        self._tasks = set(); self._close_lock = asyncio.Lock(); self._closing = False; self._closed = False
        self._failure = None; self._failed = asyncio.Event(); self._terminal = False; self._last_sequence = None
        self._last_presented_key = self._last_presented_at = None
        self._feed_started_at = time.monotonic()
        self._feed_entry_at = None; self._feed_latest_matches = False
        self._feed_eligible = None; self._feed_transitions = []; self._feed_transition_count = 0
        self._feed_fatal = None; self._feed_guard_at = None
        self._feed_guard_gap = self._feed_pf_ms = self._feed_obs_ms = None
        self._release_admission = False; self._binding = None; self._channel_binding = None
        self._activated = False; self._record_directory_original = None; self._decoder = None
        self._admission_pending = None
        self.director = None; self._director_selection = None
        self._director_commands = self._director_results_accepted = self._director_results_rejected = 0
        self._close_task = None
        self.prepare_failure_code = None
        self.cleanup_failures = (); self.recovered_cleanup_stages = ()

    def readiness(self):
        flags = dict(self._flags)
        flags["observer"] = bool(flags["observer"] and not self._closing and self._failure is None
            and self.scheduler is not None and self.scheduler._fresh() and self._feed_live())
        return flags

    def _feed_live(self):
        # Only a newly accepted coherent native presentation can advance this
        # clock. Repeated bridge envelopes and rejected/expired views cannot.
        return (self._last_presented_at is not None and
            0 <= time.monotonic() - self._last_presented_at <= self.commentary.feed_stale_seconds)

    def _feed_snapshot(self, code):
        now, wall = time.monotonic(), time.time() * 1000
        view = self.scheduler.view if self.scheduler is not None else None
        def bounded(value):
            return round(max(-3600000, min(3600000, value)), 3) if type(value) in (int, float) and math.isfinite(value) else None
        return {"code": code, "elapsed_ms": bounded((now - self._feed_started_at) * 1000),
            "entry_age_ms": bounded((now - self._feed_entry_at) * 1000) if self._feed_entry_at is not None else None,
            "unique_ack_age_ms": bounded((now - self._last_presented_at) * 1000) if self._last_presented_at is not None else None,
            "expiry_remaining_ms": bounded(view.expires_at_unix_ms - wall) if view is not None else None,
            "eligible": self.scheduler is not None and self.scheduler._fresh(), "feed_live": self._feed_live(),
            "latest_matches_accepted": self._feed_latest_matches,
            "presentation_matches_view": view is not None and view.presentation.rendered_sequence == view.sequence,
            "guard_gap_ms": bounded(self._feed_guard_gap), "pf_ms": bounded(self._feed_pf_ms),
            "obs_ms": bounded(self._feed_obs_ms)}

    def feed_health_report(self):
        return {"transition_count": self._feed_transition_count,
            "transitions": [dict(row) for row in self._feed_transitions],
            "fatal": dict(self._feed_fatal) if self._feed_fatal is not None else None}

    def _check_feed(self):
        if self.scheduler is None or not self._flags["observer"]: return
        eligible = self.scheduler._fresh()
        if eligible != self._feed_eligible:
            if self._feed_eligible is not None or not eligible:
                self._feed_transition_count = min(1000000, self._feed_transition_count + 1)
                self._feed_transitions.append(self._feed_snapshot("fresh" if eligible else "presentation_expired"))
                del self._feed_transitions[:-8]
            if not eligible and (getattr(self.scheduler, "_active", None) or getattr(self.scheduler, "_speech_pending", None)):
                # Fence synchronously; existing scheduler cancellation and every
                # PCM/playback validator still enforce the original expiry.
                self.scheduler._invalidate("feed_stale")
            self._feed_eligible = eligible
        if not self._feed_live(): raise NativeError("broadcast_feed_stale")

    def _ready(self, name):
        self._flags[name] = True; self.on_ready(self.readiness())

    def _task(self, coroutine):
        task = asyncio.create_task(coroutine); self._tasks.add(task)
        def done(value):
            self._tasks.discard(value)
            if not value.cancelled() and value.exception() is not None and not self._closing:
                self._trip("broadcast_background_failed")
        task.add_done_callback(done); return task

    def _trip(self, code):
        if self._closing or self._failure is not None: return
        if code in {"broadcast_feed_stale", "broadcast_feed_disconnected", "broadcast_feed_invalid"}:
            try: self._feed_fatal = self._feed_snapshot(code)
            except Exception: self._feed_fatal = None  # Diagnostics never prevent the unsafe fence.
        self._failure = code; self._failed.set(); self.on_unsafe(code)
        if self.obs is not None: self._task(self.obs.emergency_stop())

    def _check(self):
        if self._failure is not None: raise NativeError(self._failure)

    async def _stage(self, operation, timeout):
        task = asyncio.create_task(operation); failed = asyncio.create_task(self._failed.wait())
        try:
            done, _ = await asyncio.wait((task, failed), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if failed in done: self._check()
            if task not in done: raise NativeError("broadcast_prepare_deadline")
            return task.result()
        finally:
            failed.cancel()
            if not task.done():
                task.cancel()
                done, _ = await asyncio.wait((task,), timeout=5)
                if task not in done:
                    # Retain the actual owned task. close cannot release its
                    # channel lease while a late integration still runs.
                    self._tasks.add(task)
                    task.add_done_callback(lambda value: None if value.cancelled() else value.exception())
            elif not task.cancelled(): task.exception()

    async def prepare(self):
        c, d = self.config, self.deps
        root = private_directory(c.state_directory)
        # Ownership is per channel, independent of configurable diagnostics or
        # installation state directories. Two configs must converge on one inode.
        if self.factory.guardian is not None:
            from .guardian_client import GuardianLease
            self.lease = GuardianLease(self.factory.guardian).acquire()
        else: self.lease = d.lease(c.channel).acquire()
        quota_root = private_directory(d.canonical_directory())
        scope_id = installation_identity(quota_root)
        runs = private_directory(root / "runs"); self.directory = runs / self.session_id
        self.directory.mkdir(mode=0o700)  # fresh diagnostics, never old scope reuse
        verify_backup_receipt(c.protected_obs_backup)
        client, profile = await asyncio.to_thread(d.resolve, c)
        if (profile.game_id, profile.game_version) != (c.game_id, c.game_version): raise NativeError("broadcast_client_version_mismatch")
        self.attestor = d.attestor(c.firewall_socket)
        if not await self.attestor.refresh() or not self.attestor.verified: raise NativeError("obs_firewall_attestation_required")
        self.budget = d.budget(quota_root / "quota.sqlite", quota_scope_id="installation:" + scope_id, config=self.commentary)
        self.provider = d.provider(c)
        await self._stage(self.provider.prepare(), 30); self._ready("model")
        floor=getattr(self.provider,"minimum_input_reservation",None)
        if floor is not None: await self.budget.raise_unknown_input_floor(floor)
        self.voice = d.voice(MlxSpeechConfig(c.tts_python, c.tts_model_path))
        await self._stage(self.voice.warmup(), 95); self._ready("tts")
        # Cold model/MPS preparation has no OBS client, audio route, observer
        # or admission yet. Re-attest AFTER that work, then continuously guard
        # every operational resource without a grace period or stale proof.
        if not await self.attestor.refresh() or not self.attestor.verified: raise NativeError("obs_firewall_attestation_required")
        self._require_healthy_provider()
        self._task(self._guard_loop())
        if self.factory.guardian is not None:
            from .guardian_client import GuardianAdmission
            self.admission = GuardianAdmission(self.factory.guardian)
        else:
            key = await asyncio.to_thread(self.store.get, "broadcast-server-operator-key")
            self.admission = d.admission(c.operator_forward_url, key, game_version=c.game_version, protocol_version=c.admission_protocol)
        self.sink = d.sink(route_guard=self._route_guard)
        await self.sink.start()
        # Admission is short-lived; do not allocate it behind cold GPU/model setup.
        admitted = await self._obtain_admission()
        self.observer_id = admitted.observer_id
        token = secrets.token_urlsafe(32)
        self.bridge = d.bridge(d.registry(), token, observer_id=self.observer_id, game_version=c.game_version, port=0)
        await self.bridge.start()
        args = profile.client_arguments(room=self.room, player_name="ACTOR Broadcast",
            endpoint=f"http://127.0.0.1:{self.bridge.port}", report=self.directory / "client.json",
            log=self.directory / "client.log", headless=False, api_url=c.operator_forward_url)
        if any(value in args for value in ("-batchmode", "-nographics")): raise NativeError("broadcast_windowed_observer_required")
        env = child_environment(admitted, token)
        self.native_probe_path = self.directory / "native-presented.png"
        env["ACTOR_BROADCAST_PROBE_PNG"] = str(self.native_probe_path)
        env["ACTOR_BROADCAST_MOTION_REPORT"] = str(self.directory / "motion.json")
        self.log = (self.directory / "process.log").open("xb")
        # Register the returned handle synchronously before any await/IO.
        from .guardian_client import inherited_group_options
        self.process = d.spawn([str(client), *args, "--broadcast-observer"], env=env,
            stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log, **inherited_group_options())
        env.clear()
        self.window = d.window(self.process.pid, alive=lambda: self.process is not None and self.process.poll() is None)
        self.session = await self._stage(self.bridge.next_session(timeout=30), 31)
        self.projector = d.projector(observer_id=self.observer_id, broadcast_session_id=self.session_id, match_id=admitted.lobby_id)
        self.output = d.output(self.voice, self.sink, validate=lambda s: self.scheduler is not None and self.scheduler.validate_segment(s), on_error=self._trip)
        self.scheduler = d.scheduler(self.provider, self.output, self.budget, config=self.commentary)
        self.sink.on_ack = self.scheduler.playback_ack
        await self._stage(self._wait_presented_and_window(), 20); self._ready("observer")
        password = await asyncio.to_thread(self.store.get, "obs-websocket-password")
        if not self.attestor.verified: raise NativeError("obs_firewall_attestation_required")
        self.obs_client = d.obs_client(ObsConnection(c.obs_endpoint, password, firewall_verified=True))
        await self._stage(self.obs_client.connect(), 10)
        if self.factory.guardian is not None:
            from .guardian_client import GuardianObsController, ReadOnlyObsClient
            self.obs_client = ReadOnlyObsClient(self.obs_client)
            self.obs = GuardianObsController(self.factory.guardian, self.obs_client,
                window_guard=self.window.verify, channel_guard=self._verify_channel, on_unsafe=self._obs_event)
        else:
            self.obs = d.obs_controller(self.obs_client, window_guard=self.window.verify,
                channel_guard=self._verify_channel, on_unsafe=self._obs_event)
        self.native_size = read_native_size(self.native_probe_path)
        await self._stage(self.obs.prepare(self._binding, self.sink.renderer_url,
            protected_backup_confirmed=True, adopt_empty_collection=True, native_size=self.native_size), 20)
        if not await self._verify_channel(c.channel): raise NativeError("obs_channel_binding_unverified")
        # Synthetic setup scope must never enter the spoken-coverage ledger.
        callback, self.sink.on_ack = self.sink.on_ack, None
        try:
            probe = d.audio_probe or record_audio_route
            result = await self._stage(probe(self), 20)
            if not isinstance(result, dict) or result.get("passed") is not True:
                raise NativeError("audio_route_probe_unproven")
        finally: self.sink.on_ack = callback
        # First activation may legitimately materialize browser geometry. Do
        # not latch OBS readiness until the actual probe and final strict
        # idle/quiet audit have finished; route_guard still revokes immediately.
        if not (await self._stage(self.obs.complete_startup(), 4)).passed: raise NativeError("obs_graph_audit_failed")
        self._require_ready_route()
        self._ready("obs")
        self._ready("audio")
        if c.chat_enabled:
            if not c.twitch_client_id: raise NativeError("twitch_client_id_required")
            self.auth = d.auth(c.twitch_client_id, expected_login=c.channel, save_token=self._save_token)
            pair = await asyncio.to_thread(self.store.load_twitch_token_pair)
            if pair is None: raise NativeError("twitch_chat_authorization_required")
            await self._stage(self.auth.adopt_token(TokenPair(pair["access_token"], pair["refresh_token"], pair["expires_at_monotonic"])), 15)
            self.chat = d.chat(self.auth, on_status=self._chat_status)
            # messages() reports connected only after the actual EventSub
            # welcome and accepted subscription. Merely validating OAuth is insufficient.
            self._task(self._chat_loop())
            await self._stage(self._wait_chat(), 15)
        self._require_ready_route()

    def _require_ready_route(self):
        self._check()
        if not self._route_guard(): raise NativeError("obs_route_unverified")
        if self.sink.public_status.get("renderer_ready") is not True: raise NativeError("audio_renderer_unavailable")
        if not self.scheduler._fresh(): raise NativeError("broadcast_presented_view_stale")

    def _obs_event(self, code):
        # Setup events are re-audited before readiness. Once ready they are
        # unsafe until a later explicitly prepared run; no silent rebinding.
        if self._flags["obs"]:
            self._trip(code if code in {"obs_owned_stream_inactive", "obs_owned_stream_reconnecting",
                "obs_owned_stream_status_unverified"} else "obs_graph_changed")

    async def _obtain_admission(self):
        # An HTTP POST may reach CP even when its local await is cancelled.
        # Drain that bounded request before DELETE, so late POST cannot
        # recreate an observer reservation after our cleanup already returned.
        self._release_admission = True
        task = self._admission_pending = asyncio.create_task(self.admission.observe(self.room, self.session_id))
        try: return await asyncio.shield(task)
        except BaseException:
            async def drain():
                try: return await asyncio.shield(task)
                except Exception: return None
            await _cleanup(drain(), 10)
            raise
        finally:
            if task.done(): self._admission_pending = None

    def _route_guard(self):
        return (self._failure is None and not self._closing and self.attestor is not None and self.attestor.verified
                and self.obs is not None and self.obs.route_verified)

    async def _verify_channel(self, login):
        value = await asyncio.to_thread(self.store.get, "obs-service-binding")
        try:
            binding = json.loads(value)
            if (set(binding) != {"channel", "profile", "collection", "fingerprint"}
                    or binding["channel"] != login or binding["profile"] != "ACTOR Broadcast" or binding["collection"] != "ACTOR Broadcast"
                    or not re.fullmatch(r"[a-f0-9]{64}", binding["fingerprint"])): return False
            return secrets.compare_digest(binding["fingerprint"], await self.obs_client.stream_service_fingerprint())
        except (ValueError, TypeError, KeyError): return False

    async def _save_token(self, pair):
        await asyncio.to_thread(self.store.save_twitch_token_pair, access_token=pair.access_token,
            refresh_token=pair.refresh_token, expires_at_unix=time.time() + max(0, pair.expires_at_monotonic - time.monotonic()))

    def _chat_status(self, code):
        if code == "connected": self._ready("chat")
        elif self._flags["chat"]: self._trip("twitch_chat_unavailable")

    async def _wait_chat(self):
        while not self._flags["chat"]: self._check(); await asyncio.sleep(.05)

    async def _chat_loop(self):
        try:
            async for message in self.chat.messages():
                if self.scheduler.view is None: continue
                now = int(time.time() * 1000)
                fact = EditorialFact("viewer-chat:" + message.id, "1", "player_claim", "viewer-chat:" + message.id,
                    (), "viewer-question", now, now + 60000, {"sender": message.sender, "text": message.text},
                    3, "viewer-chat:" + message.id, origin="viewer_chat")
                if self.scheduler.admit_viewer_claim(fact) and "?" in message.text:
                    self.scheduler.offer("chat_response", (fact.ref,), priority=3)
        except asyncio.CancelledError: raise
        except Exception: self._trip("twitch_chat_unavailable")

    def _capture(self):
        from extensions.agents_arena.v1.broadcast_pb2 import BroadcastPresentedViewObservation
        if self.session is None: return
        if self.session.closed.is_set(): raise NativeError("broadcast_feed_disconnected")
        entry = self.session.world.latest_entry(BroadcastPresentedViewObservation, self.observer_id,
            capability_id="agents-arena.broadcast-presented-view")
        if entry is None: return
        now = time.monotonic()
        if type(entry.received_at) not in (int, float) or not math.isfinite(entry.received_at) or not 0 <= entry.received_at <= now:
            raise NativeError("broadcast_feed_invalid")
        self._feed_entry_at = entry.received_at
        if now - entry.received_at > self.commentary.feed_stale_seconds:
            raise NativeError("broadcast_feed_stale")
        if entry.sequence == self._last_sequence: return
        try: view = self.projector.project(entry)
        except ValueError: raise NativeError("broadcast_feed_invalid") from None
        previous = self.scheduler.view
        if previous is not None and (view.scope.connection_generation, view.scope.view_epoch) < (
                previous.scope.connection_generation, previous.scope.view_epoch):
            raise NativeError("broadcast_feed_invalid")
        key = (view.scope, view.sequence)
        self._feed_latest_matches = key == self._last_presented_key
        self._last_sequence = entry.sequence
        if self._feed_latest_matches: return
        if self.scheduler.observe(view):
            self._last_presented_key, self._last_presented_at = key, entry.received_at
            self._feed_latest_matches = True
            if self.director is not None: self.director.observe(view)
            if view.terminal and not self._terminal:
                self._terminal = True
                if self._activated: asyncio.get_running_loop().call_soon(self.on_terminal)

    def _direct_camera(self):
        """One fixed typed RPC, never a model tool or a presentation ACK."""
        if (not self._activated or self._closing or self._failure is not None or self._terminal
                or self.director is None or self.scheduler is None or self.session is None): return
        from extensions.agents_arena.v1.broadcast_pb2 import BroadcastScope, BroadcastSelectViewCommand, BroadcastViewSelectionResultEvent
        view = self.scheduler.view
        pending = self._director_selection
        if pending is not None:
            intent = pending.intent
            inbox = getattr(self.session.world, "inboxes", {}).get("agents-arena.broadcast-view-selection-result")
            def matching(result):
                scope = result.scope
                return (result.correlation_id == pending.correlation_id and
                    (scope.broadcast_session_id, scope.match_id, scope.observer_id, scope.connection_generation) ==
                    (intent.scope.broadcast_session_id, intent.scope.match_id, self.observer_id, intent.scope.connection_generation)
                    and scope.view_epoch == intent.scope.view_epoch + (1 if result.accepted else 0))
            event = (inbox.find(self.observer_id, BroadcastViewSelectionResultEvent, matching,
                after_sequence=pending.after_sequence) if inbox is not None and not pending.result_received else None)
            if event is not None and pending.sent_at <= event.received_at <= pending.sent_at + 5:
                pending.result_received = True
                accepted = event.payload.accepted
                self.director.selection_result(intent, accepted)
                if accepted: self._director_results_accepted += 1
                else:
                    self._director_results_rejected += 1; self._director_selection = None
            # The real frame may precede its result event in one capture pass.
            # Still match the event above; neither ordering invents a frame.
            if view is None or view.scope != intent.scope or time.monotonic() - pending.sent_at >= 5:
                self._director_selection = None
        if self._director_selection is not None: return
        if not getattr(self.scheduler, "camera_change_allowed", False): return
        intent = self.director.next_intent()
        if intent is None: return
        # All checks and enqueue remain synchronous in this event-loop turn:
        # no speech, newer view or scope change may slip across an await.
        view = self.scheduler.view
        remaining = intent.expires_at_unix_ms - int(time.time() * 1000)
        if (not self.scheduler.camera_change_allowed or view is None or view.scope != intent.scope
                or view.sequence != intent.presentation_sequence or remaining <= 0):
            self.director.selection_result(intent, False); return
        correlation = uuid4().hex
        command = BroadcastSelectViewCommand(scope=BroadcastScope(
            broadcast_session_id=intent.scope.broadcast_session_id, match_id=intent.scope.match_id,
            observer_id=self.observer_id, connection_generation=intent.scope.connection_generation,
            view_epoch=intent.scope.view_epoch), correlation_id=correlation,
            anchor_id=intent.anchor_id, focus_actor_id=intent.focus_actor_id,
            expires_at_unix_ms=intent.expires_at_unix_ms)
        self._director_selection = _DirectorSelection(intent, correlation, time.monotonic(), self.session.last_received_sequence)
        try:
            self.session.command("agents-arena.broadcast-select-view", command,
                target_actor_id=self.observer_id, ttl_ms=min(remaining, 500))
        except Exception: raise NativeError("broadcast_camera_command_failed") from None
        self._director_commands += 1

    async def _wait_presented_and_window(self):
        self._activated = False
        candidate = None; stable_since = None
        while True:
            self._check(); self._capture()
            try: located = self.window.locate()
            except Exception: located = None
            # Startup splash/resize windows are candidates, not capture owners.
            # Start stability only after an actual fresh presented view exists;
            # the guard remains strict once the final binding is committed.
            if located is None or self.scheduler.view is None or not self.scheduler._fresh():
                candidate = None; stable_since = None
            elif located != candidate:
                candidate = located; stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= .15:
                self._binding = candidate
                return
            await asyncio.sleep(.05)

    async def _guard_loop(self):
        next_pf = next_obs = 0.0
        try:
            while not self._closing:
                now = time.monotonic()
                self._feed_guard_gap = (now - self._feed_guard_at) * 1000 if self._feed_guard_at is not None else None
                self._feed_guard_at = now
                if now >= next_pf:
                    next_pf = now + .5
                    began = time.monotonic()
                    if not await self.attestor.refresh(): raise NativeError("obs_firewall_attestation_lost")
                    self._feed_pf_ms = (time.monotonic() - began) * 1000
                if self.factory.guardian is not None: self.factory.guardian.require_alive()
                if self.process is not None and self.process.poll() is not None: raise NativeError("broadcast_observer_exited")
                if self._flags["model"]:
                    self._require_healthy_provider()
                if self.scheduler is not None:
                    self._capture()
                    self._check_feed()
                if self._binding is not None and not self.window.verify(self._binding): raise NativeError("broadcast_window_lost")
                audited = False
                if self._flags["obs"] and now >= next_obs:
                    next_obs = now + 1
                    began = time.monotonic()
                    await self.obs.check_stream_health()
                    if not (await self.obs.audit()).passed or not await self._verify_channel(self.config.channel):
                        raise NativeError("obs_route_or_destination_changed")
                    audited = True
                    self._feed_obs_ms = (time.monotonic() - began) * 1000
                if self._flags["audio"] and self.sink.public_status.get("renderer_ready") is not True:
                    self._flags["audio"] = False
                    raise NativeError("audio_renderer_unavailable")
                # OBS audits above may await. Capture the newest actual view
                # again before the synchronous speech/sequence admission gate.
                if self._activated:
                    if audited: self._capture(); self._check_feed()
                    self._direct_camera()
                await asyncio.sleep(.05)
        except asyncio.CancelledError: raise
        except Exception as error: self._trip(getattr(error, "code", "broadcast_guard_failed"))

    def _require_healthy_provider(self):
        report = self.provider.report()
        if not isinstance(report, dict) or report.get("poisoned") is not False or report.get("closed") is not False:
            self._flags["model"] = False
            raise NativeError("broadcast_model_unavailable")

    async def activate(self):
        self._check()
        if self._closing or getattr(self, "_activated", False): return
        self._activated = True
        # Preparation/voice warmup time earns no camera dwell.
        self.director = self.deps.director()
        if self.scheduler.view is not None: self.director.observe(self.scheduler.view)
        self._task(self.scheduler.run())
        if self._terminal: asyncio.get_running_loop().call_soon(self.on_terminal)

    async def status(self):
        report = self.scheduler.report() if self.scheduler is not None else {}
        return {"last_spoken": report.get("last_spoken"), "usage": await self.budget.report() if self.budget is not None else {},
            "idle_deadline_seconds": max(0, self.scheduler.idle_deadline - time.monotonic()) if self.scheduler is not None else None,
            "chat_enabled": self.config.chat_enabled,
            "feed_health": self.feed_health_report(),
            "director": {**(self.director.report() if self.director is not None else {}),
                "commands_dispatched": self._director_commands, "selection_results_accepted": self._director_results_accepted,
                "selection_results_rejected": self._director_results_rejected}}

    async def close(self, *, emergency=True):
        if self._closed: return
        if self._close_task is None or self._close_task.done():
            self._close_task = asyncio.create_task(self._close_owned(emergency=emergency))
            self._close_task.add_done_callback(lambda value: None if value.cancelled() else value.exception())
        # A coordinator's cancellation/deadline may end its wait, but may not
        # cancel the only owner that still has to stop child/audio/OBS handles.
        # Retain the run + channel lease until this exact job confirms cleanup.
        done, _ = await asyncio.wait((self._close_task,), timeout=30)
        if self._close_task not in done: raise NativeError("broadcast_owned_cleanup_pending")
        return self._close_task.result()

    async def _close_owned(self, *, emergency):
        async with self._close_lock:
            if self._closed: return
            self._closing = True; failures = []; recovered = []
            async def finish(name, operation, timeout=8):
                value = getattr(self, name)
                if value is None: return
                try:
                    await _cleanup(operation(value), timeout)
                    # Keep the stopped scheduler report available while a
                    # different resource still needs an explicit cleanup retry.
                    if name != "scheduler": setattr(self, name, None)
                except BaseException: failures.append(name)
            # Output stop is attempted before any potentially slow provider/GPU drain.
            if self.obs is not None:
                try: await _cleanup(self.obs.emergency_stop(), 17)
                except BaseException: failures.append("obs_stop")
            if self._record_directory_original is not None:
                if self.obs_client is None or getattr(self.obs, "_record_owned", False):
                    failures.append("record_directory")
                else:
                    try:
                        await _cleanup(self.obs_client.request("SetRecordDirectory", {"recordDirectory": self._record_directory_original}), 5)
                        self._record_directory_original = None
                    except BaseException: failures.append("record_directory")
            for task in list(self._tasks):
                if task is not asyncio.current_task(): task.cancel()
            pending = [task for task in self._tasks if task is not asyncio.current_task()]
            if pending:
                done, still = await asyncio.wait(pending, timeout=8)
                if still: failures.append("background")
            await finish("scheduler", lambda value: value.stop(emergency=emergency), 9)
            if self.output is not None:
                await finish("output", lambda value: value.close(), 12)
                if self.output is None: self.voice = self.sink = None
            await finish("sink", lambda value: value.aclose(), 6)
            await finish("voice", lambda value: value.aclose(), 5)
            await finish("provider", lambda value: value.close(), 8)
            await finish("chat", lambda value: value.close(), 4)
            if self.auth is not None: self.auth.close(); self.auth = None
            if self.process is not None:
                try:
                    process = self.process
                    if process.poll() is None:
                        process.terminate()
                        try: await asyncio.to_thread(process.wait, 5)
                        except subprocess.TimeoutExpired:
                            process.kill(); await asyncio.to_thread(process.wait, 2)
                    self.process = None
                except BaseException: failures.append("observer_process")
            if self.log is not None and self.process is None: self.log.close(); self.log = None
            if self._decoder is not None:
                try:
                    process = self._decoder
                    if process.poll() is None:
                        process.terminate()
                        try: await asyncio.to_thread(process.wait, 1)
                        except subprocess.TimeoutExpired:
                            process.kill(); await asyncio.to_thread(process.wait, 1)
                    self._decoder = None
                except BaseException: failures.append("audio_decoder")
            await finish("bridge", lambda value: value.stop(), 5)
            await finish("budget", lambda value: value.close(), 3)
            if self._admission_pending is not None:
                task = self._admission_pending
                done, _ = await asyncio.wait((task,), timeout=10)
                if task in done:
                    if not task.cancelled(): task.exception()
                    self._admission_pending = None
                else: failures.append("admission_pending")
            if self._release_admission:
                if self._admission_pending is not None: failures.append("admission_release")
                else:
                    try:
                        await _cleanup(self.admission.release(self.room, self.session_id), 10)
                        self._release_admission = False
                    except BaseException: failures.append("admission_release")
            if self.obs is None or (not getattr(self.obs, "_owned_stream", True) and not getattr(self.obs, "_record_owned", True)):
                clean_inputs = True
                if self.obs is not None:
                    try: await _cleanup(self.obs.cleanup_owned_inputs(), 8)
                    except BaseException: failures.append("obs_owned_inputs"); clean_inputs = False
                if clean_inputs and self._record_directory_original is None:
                    await finish("obs_client", lambda value: value.close(), 4)
                    if self.obs_client is None:
                        self.obs = None
                        # A prior mute/scene request can fail after a previous
                        # attempt already removed VOICE. Fresh idle+empty-owned
                        # input proof and actual client close supersede that
                        # route-stop failure; an active output cannot reach here.
                        if "obs_stop" in failures:
                            failures.remove("obs_stop"); recovered.append("obs_stop")
            if (failures==["scheduler"] and self.output is None and self.sink is None and self.voice is None
                    and self.provider is None and self.obs is None and self.obs_client is None
                    and self.process is None and self._decoder is None and self.log is None
                    and self._record_directory_original is None
                    and self.bridge is None and self.budget is None and self.chat is None and self.auth is None
                    and self._admission_pending is None and not self._release_admission
                    and all(task.done() for task in self._tasks)):
                # Closing deliberately revokes the route before slow drains.
                # A missing render flush remains a diagnostic, but cannot keep
                # an otherwise fully closed run/lease alive forever. No audio
                # ACK is invented and pending spoken text stays interrupted.
                recover=getattr(self.scheduler,"recover_terminal_cleanup",None)
                try:confirmed=callable(recover) and recover() is True
                except Exception:confirmed=False
                if confirmed:
                    failures.remove("scheduler");recovered.append("scheduler_terminal_audio_flush")
            self.cleanup_failures = tuple(failures); self.recovered_cleanup_stages = tuple(recovered)
            if failures: raise NativeError("broadcast_owned_cleanup_incomplete", cleanup_stages=self.cleanup_failures)
            if self.factory.guardian is not None:
                try: await self.factory.guardian.end_run()
                except BaseException:
                    self.cleanup_failures = ("guardian",)
                    raise NativeError("broadcast_owned_cleanup_incomplete", cleanup_stages=self.cleanup_failures) from None
            if self.lease is not None: self.lease.release(); self.lease = None
            self._closed = True
            if self in self.factory._owned: self.factory._owned.remove(self)


def probe_signal():
    # Deterministic amplitude-code, not model/reference speech. Correlation is
    # measured on the RMS envelope so a codec delay/phase isn't mistaken for silence.
    pattern = (1, 4, 2, 6, 1, 5, 3, 1, 6, 2, 5, 1, 3, 6, 1, 4, 2, 5, 1, 6, 3, 2, 6, 1, 5, 2, 4, 1, 6, 3, 1, 5)
    values = array("f", (pattern[min(31, i // 1200)] * .015 * math.sin(2 * math.pi * 700 * i / 24000) for i in range(38400)))
    return values.tobytes()


def audio_probe_measurement(raw, expected):
    if not raw or len(raw) % 4 or len(raw) > 24000 * 10 * 4: raise NativeError("audio_probe_decode_bound")
    samples = array("f"); samples.frombytes(raw)
    reference = array("f"); reference.frombytes(expected)
    if any(not math.isfinite(x) or abs(x) > 1.05 for x in samples): raise NativeError("audio_probe_nonfinite_or_clipped")
    def envelope(values):
        return [math.sqrt(sum(x*x for x in values[i:i+240]) / 240) for i in range(0, len(values)-239, 240)]
    actual, wanted = envelope(samples), envelope(reference)
    mean = sum(wanted) / len(wanted); centered = [x-mean for x in wanted]
    norm = sum(x*x for x in centered); best = -1.0
    for start in range(max(0, len(actual)-len(wanted)+1)):
        part = actual[start:start+len(wanted)]; average = sum(part)/len(part)
        variance = sum((x-average)**2 for x in part)
        if variance > 1e-12:
            best = max(best, sum(a*(b-average) for a,b in zip(centered, part))/math.sqrt(norm*variance))
    rms = math.sqrt(sum(x*x for x in samples)/len(samples))
    return {"passed": rms > .003 and best >= .8, "rms": rms, "envelope_correlation": best,
            "scope": "owned_obs_recording_browser_voice_only_not_viewer_audibility"}


async def record_audio_route(run):
    """Actual local record+decode; never StartStream or a generic audio device."""
    client, sink, obs = run.obs_client, run.sink, run.obs
    guardian = getattr(getattr(run, "factory", None), "guardian", None)
    await obs.require_idle(); await obs.show("ACTOR Match")
    if not run._route_guard(): raise NativeError("audio_probe_route_unverified")
    directory = private_directory(run.directory / "audio-probe")
    original = (await client.request("GetRecordDirectory")).get("recordDirectory")
    if not isinstance(original, str): raise NativeError("audio_probe_record_directory_unavailable")
    recording = False; output = None; run._record_directory_original = original
    try:
        if guardian is not None:
            run._record_directory_original = None
            recording = True; await obs.start_record(directory)
        else:
            await client.request("SetRecordDirectory", {"recordDirectory": str(directory)})
            # Establish owned intent before RPC: a lost StartRecord ACK must also stop.
            recording = True; obs._record_owned = True; await client.request("StartRecord")
        deadline = time.monotonic() + 4
        while (await client.request("GetRecordStatus")).get("outputActive") is not True:
            if time.monotonic() >= deadline: raise NativeError("audio_probe_recording_unconfirmed")
            await asyncio.sleep(.05)
        s = SpeechSegment(Scope("audio-route-probe", "setup", 1, 1), SegmentKey(uuid4().hex, 1, 0), 3,
            "Проверка локального аудиомаршрута.", (), 0, time.monotonic_ns()+8_000_000_000, 1, 38400)
        expected = probe_signal()
        async def chunks():
            offset = 0
            for sequence, start in enumerate(range(0, len(expected), 131072)):
                part = expected[start:start+131072]
                yield PcmChunk(s.scope, s.key, sequence, offset, part); offset += len(part)//4
            yield SynthesisDone(s.scope, s.key, 38400, 0, False, "generator_completed")
        ack = await sink.play(s, chunks(), validate=lambda _: run._route_guard())
        if ack.status != "completed" or ack.consumed_source_frames != 38400:
            raise NativeError("audio_probe_playback_unconfirmed")
        await asyncio.sleep(.3)
        response = await obs.stop_record(); recording = False
        output = Path(response.get("outputPath", "")).resolve()
        if not output.is_relative_to(directory) or not output.is_file() or output.stat().st_size > 32*1024**2:
            raise NativeError("audio_probe_recording_path_unverified")
        from .guardian_client import inherited_group_options
        process = subprocess.Popen([str(run.config.ffmpeg_executable), "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-i", str(output),
                "-t", "10", "-vn", "-ac", "1", "-ar", "24000", "-f", "f32le", "pipe:1"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **inherited_group_options(),
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
        run._decoder = process  # registered before a cancellable wait
        raw, _ = await asyncio.to_thread(process.communicate, timeout=8)
        if process.returncode: raise NativeError("audio_probe_decode_failed")
        run._decoder = None
        audio = audio_probe_measurement(raw, expected)
        video = await video_probe_measurement(run, output)
        return {"passed": audio["passed"] and video["passed"], "audio": audio, "video": video,
                "scope": "owned_local_probe_no_public_output_or_viewer_audibility"}
    finally:
        try:
            if recording: await _cleanup(obs.stop_record(), 7)
        finally:
            if not obs._record_owned and guardian is None:
                await _cleanup(client.request("SetRecordDirectory", {"recordDirectory": original}), 5)
                run._record_directory_original = None


def png_dimensions(raw):
    if not isinstance(raw, bytes) or not 24 <= len(raw) <= 4*1024**2 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        raise NativeError("video_probe_png_invalid")
    width, height = struct.unpack("!II", raw[16:24])
    if not 1 <= width <= 3840 or not 1 <= height <= 2160: raise NativeError("video_probe_pixel_bound")
    return width, height


def video_similarity(actual, expected):
    if len(actual) != 64*48 or len(expected) != len(actual): raise NativeError("video_probe_decode_bound")
    mean_a, mean_b = sum(actual)/len(actual), sum(expected)/len(expected)
    variance_a = sum((x-mean_a)**2 for x in actual); variance_b = sum((x-mean_b)**2 for x in expected)
    if min(variance_a, variance_b) < len(actual)*4:
        return {"passed": False, "correlation": None, "mean_absolute_error": None}
    correlation = sum((a-mean_a)*(b-mean_b) for a,b in zip(actual,expected))/math.sqrt(variance_a*variance_b)
    error = sum(abs(a-b) for a,b in zip(actual,expected))/len(actual)/255
    return {"passed": correlation >= .65 and error <= .25, "correlation": correlation, "mean_absolute_error": error}


def content_top_fraction(width, height, binding, native_size):
    try: return capture_content_crop(width, height, binding, native_size) / height
    except ObsError as error: raise NativeError(error.code.replace("obs_capture_", "video_probe_", 1)) from None


def read_native_size(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o077 or metadata.st_nlink != 1 or metadata.st_size > 4*1024**2):
            raise NativeError("video_probe_native_artifact_unsafe")
        return png_dimensions(stream.read(4*1024**2+1))


def recorded_content_roi(transform, video):
    """OBS scale-inner fits the cropped source, not its raw SCK dimensions."""
    dims = [video.get(key) for key in ("baseWidth", "baseHeight", "outputWidth", "outputHeight")]
    if any(type(value) is not int or not 1 <= value <= 7680 for value in dims):
        raise NativeError("video_probe_output_geometry_unverified")
    base_w, base_h, out_w, out_h = dims
    values = [transform.get(key) for key in ("sourceWidth", "sourceHeight", "cropLeft", "cropRight", "cropTop", "cropBottom")]
    if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 7680 for value in values):
        raise NativeError("video_probe_output_geometry_unverified")
    raw_w, raw_h, left, right, top, bottom = values
    width, height = raw_w-left-right, raw_h-top-bottom
    if min(width, height) <= 0: raise NativeError("video_probe_output_geometry_unverified")
    scale = min(1280/width, 720/height)
    visible_w, visible_h = width*scale, height*scale
    crop = (round((1280-visible_w)/2*out_w/base_w), round((720-visible_h)/2*out_h/base_h),
        round(visible_w*out_w/base_w), round(visible_h*out_h/base_h))
    if crop[0]+crop[2] > out_w or crop[1]+crop[3] > out_h:
        raise NativeError("video_probe_output_crop_unverified")
    return crop


async def _decode_gray(run, path, *, crop=None, movie=False):
    args = [str(run.config.ffmpeg_executable), "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe"]
    if movie: args += ["-sseof", "-0.1"]
    args += ["-i", str(path), "-frames:v", "1", "-an"]
    filters = []
    if crop is not None:
        x,y,width,height = crop
        if any(type(v) is not int or v < 0 or v > 7680 for v in crop) or min(width,height) < 1:
            raise NativeError("video_probe_crop_invalid")
        filters.append(f"crop={width}:{height}:{x}:{y}")
    filters += ["scale=64:48", "format=gray"]
    args += ["-vf", ",".join(filters), "-f", "rawvideo", "pipe:1"]
    from .guardian_client import inherited_group_options
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        **inherited_group_options(), env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
    run._decoder = process
    raw, _ = await asyncio.to_thread(process.communicate, timeout=5)
    if process.returncode or len(raw) != 64*48: raise NativeError("video_probe_decode_failed")
    run._decoder = None
    return raw


async def video_probe_measurement(run, recording):
    # Read only this child's one-shot coherent rendered artifact. It is never
    # an observation/control/audio IPC channel or a public screenshot dump.
    path = run.native_probe_path
    native_size = read_native_size(path)
    if native_size != run.native_size: raise NativeError("video_probe_native_dimensions_changed")
    # 5.7.4 TakeSourceScreenshot scales axes independently when both sizes are
    # supplied. Width alone preserves the raw window/decorations aspect.
    response = await run.obs_client.request("GetSourceScreenshot", {"sourceName": run.obs.VIDEO,
        "imageFormat": "png", "imageWidth": 800})
    encoded = response.get("imageData")
    if not isinstance(encoded, str) or len(encoded) > 6*1024**2 or not encoded.startswith("data:image/png;base64,"):
        raise NativeError("video_probe_obs_screenshot_invalid")
    try: image = base64.b64decode(encoded.split(",",1)[1], validate=True)
    except ValueError: raise NativeError("video_probe_obs_screenshot_invalid") from None
    width, height = png_dimensions(image)
    screenshot = private_directory(run.directory / "video-probe") / "obs-source.png"
    fd = os.open(screenshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream: stream.write(image)
    top = round(height*content_top_fraction(width, height, run._binding, native_size))
    source = await _decode_gray(run, screenshot, crop=(0,top,width,height-top))
    expected = await _decode_gray(run, path)
    transform = run.obs.video_transform
    video = await run.obs_client.request("GetVideoSettings")
    crop = recorded_content_roi(transform, video)
    final = await _decode_gray(run, recording, crop=crop, movie=True)
    a,b = video_similarity(source,expected),video_similarity(final,expected)
    return {"passed": a["passed"] and b["passed"], "source": a, "recorded": b,
        "scope": "one_shot_owned_observer_window_and_owned_record_not_full_match_visual_acceptance"}
