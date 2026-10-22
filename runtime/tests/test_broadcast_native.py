"""Native production composition with explicit fakes, never GPU/OBS/processes."""
import asyncio
from array import array
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import fcntl
import os
from pathlib import Path
import sys
import time
import threading
from datetime import datetime, timezone
from types import SimpleNamespace as Obj
from uuid import uuid4

import pytest

from actor_broadcast.config import CommentaryConfig
from actor_broadcast.native import (NativeBroadcastFactory, NativeDependencies, NativeError,
    audio_probe_measurement, child_environment, probe_signal, installation_identity,
    verify_backup_receipt, video_similarity, content_top_fraction, png_dimensions)
from actor_broadcast.obs import WindowBinding


class Store:
    def __init__(self):
        self.values = {"broadcast-server-operator-key": "operator-secret-" * 3,
            "obs-websocket-password": "password-secret", "obs-service-binding": json.dumps({
                "channel": "fedorr_codex", "profile": "ACTOR Broadcast", "collection": "ACTOR Broadcast", "fingerprint": "a"*64})}
    def get(self, key): return self.values.get(key)


class Fakes:
    def __init__(self, tmp_path):
        self.calls = []; self.live = False; self.process = None; self.lease = None
        self.pf = True; self.fail_probe = False; self.fail_close = False; self.model_calls = 0
        self.poisoned = False; self.renderer_ready = True; self.budgets = []
        self.admitted = None; self.closed = asyncio.Event(); self.scheduler = None
        self.presented_available = True
        self.config = Obj(state_directory=tmp_path / "private", protected_obs_backup=tmp_path / "backup",
            codex_home=tmp_path / "codex-home",
            game_id="agents-arena", game_version="0.10.0", admission_protocol="4", channel="fedorr_codex",
            client_manifest=tmp_path / "manifest.json", tts_python=Path(sys.executable), tts_model_path=tmp_path,
            firewall_socket=tmp_path / "pf.sock", obs_endpoint="ws://127.0.0.1:4455",
            operator_forward_url="http://127.0.0.1:56251", chat_enabled=False, twitch_client_id="", ffmpeg_executable=Path("/not-executed"))
        self.config.protected_obs_backup.mkdir(mode=0o700)
        for group in ("basic/profiles", "basic/scenes", "plugin_config/obs-websocket"):
            (self.config.protected_obs_backup / group).mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in ("global.ini", "plugin_config/obs-websocket/config.json"):
            (self.config.protected_obs_backup / name).write_text("opaque dummy backup, never parsed")
            (self.config.protected_obs_backup / name).chmod(0o600)
        receipt = self.config.protected_obs_backup / "backup-receipt.json"
        receipt.write_text(json.dumps({"schema": "actor.obs-backup.v1", "completed": True, "files_verified": True,
            "scope": "all_preexisting_obs_configuration", "source_obs_version": "30.2.3",
            "created_at": datetime.now(timezone.utc).isoformat(), "groups": ["basic/profiles", "basic/scenes", "global.ini", "plugin_config/obs-websocket/config.json"],
            "file_count": 2, "total_bytes": 2, "manifest_sha256": "a"*64}))
        receipt.chmod(0o600)

    def dependencies(self):
        own = self
        class Lease:
            def __init__(self, *a): self.held = False; own.lease = self
            def acquire(self): self.held = True; own.calls.append("lease"); return self
            def release(self): self.held = False; own.calls.append("lease-release")
        class Admission:
            def __init__(self, *a, **k): pass
            async def observe(self, room, session):
                own.calls.append("admit")
                own.admitted = Obj(observer_id="observer", lobby_id="match", broadcast_session_id=session,
                    child_environment_value=lambda: "private-admission")
                return own.admitted
            async def release(self, *a): own.calls.append("release")
        class Attestor:
            def __init__(self, *a): pass
            @property
            def verified(self): return own.pf
            async def refresh(self): return own.pf
        class Provider:
            def __init__(self,config):
                assert config is own.config and config.codex_home==own.config.codex_home
            async def prepare(self): own.calls.append("model-prepare")
            async def close(self): own.calls.append("model-close")
            def report(self): return {"poisoned": own.poisoned, "closed": False}
        class Voice:
            def __init__(self, *a): pass
            async def warmup(self): own.calls.append("voice-warm")
            async def aclose(self): own.calls.append("voice-close")
        class Sink:
            def __init__(self, **kwargs): self.on_ack = None; self.route_guard = kwargs["route_guard"]
            renderer_url = "http://127.0.0.1:12345/renderer/#cap=" + "b"*43
            @property
            def public_status(self): return {"renderer_ready": own.renderer_ready}
            async def start(self): own.calls.append("sink-start")
            async def aclose(self): own.calls.append("sink-close")
        class Output:
            def __init__(self, voice, sink, **k): self.voice = voice; self.sink = sink
            async def close(self):
                if own.fail_close: raise RuntimeError("owned-close-failure")
                await self.sink.aclose(); await self.voice.aclose()
        class World:
            sequence = 0
            def latest_entry(self, message_type, entity, **kwargs):
                assert entity == "observer" and kwargs["capability_id"] == "agents-arena.broadcast-presented-view"
                if not own.presented_available: return None
                self.sequence += 1; now = int(time.time()*1000)
                source = message_type()
                source.view.scope.broadcast_session_id = own.admitted.broadcast_session_id
                source.view.scope.observer_id = "observer"; source.view.scope.match_id = "match"
                source.view.scope.connection_generation = 1; source.view.scope.view_epoch = 1
                source.view.sequence = self.sequence; source.view.semantic_revision = self.sequence
                source.view.phase = 3; source.view.observed_at_unix_ms = now; source.view.expires_at_unix_ms = now+3000
                source.view.camera.visibility_radius = 4
                source.presentation.scope.CopyFrom(source.view.scope)
                source.presentation.rendered_sequence = self.sequence; source.presentation.frame_counter = self.sequence
                source.presentation.width = 800; source.presentation.height = 600
                return Obj(entity_id="observer", payload=source, sequence=self.sequence, received_at=time.monotonic())
        class Bridge:
            def __init__(self, registry, token, **kwargs):
                assert len(token) >= 32 and kwargs["observer_id"] == "observer"
                self.port = 12346
            async def start(self): own.calls.append("bridge-start")
            async def next_session(self, **kwargs): return Obj(world=World(), closed=own.closed)
            async def stop(self): own.calls.append("bridge-stop")
        class Process:
            pid = 12345
            def __init__(self): self.exited = False
            def poll(self): return 0 if self.exited else None
            def terminate(self): own.calls.append("terminate"); self.exited = True
            def kill(self): own.calls.append("kill"); self.exited = True
            def wait(self, *a): return 0
        def spawn(args, **kwargs):
            own.calls.append(("spawn", args, dict(kwargs["env"])))
            path=Path(kwargs['env']['ACTOR_BROADCAST_PROBE_PNG'])
            path.write_bytes(b'\x89PNG\r\n\x1a\n' + bytes(4) + b'IHDR' + (800).to_bytes(4,'big') + (600).to_bytes(4,'big'))
            path.chmod(0o600)
            own.process = Process(); return own.process
        class Window:
            def __init__(self, pid, **kwargs): assert pid == 12345
            def locate(self): return WindowBinding(8, 12345, 800, 600)
            def verify(self, value): return value == self.locate()
        class Client:
            def __init__(self, config): assert config.firewall_verified
            async def connect(self): own.calls.append("obs-connect")
            async def close(self): own.calls.append("obs-close")
            async def stream_service_fingerprint(self): return "a"*64
        class Obs:
            _owned_stream = _record_owned = False
            route_verified = True
            def __init__(self, client, **k): self.client = client; self.channel_guard = k["channel_guard"]
            async def prepare(self, *a, **kwargs):
                assert kwargs == {"protected_backup_confirmed": True, "adopt_empty_collection": True, "native_size": (800,600)}
                own.calls.append("obs-prepare")
            async def audit(self): return Obj(passed=True)
            async def check_stream_health(self): pass
            async def complete_startup(self): own.calls.append("obs-startup-complete"); return Obj(passed=True)
            async def emergency_stop(self): own.live = False; own.calls.append("obs-emergency")
            async def cleanup_owned_inputs(self): own.calls.append("obs-owned-inputs-removed")
        class Budget:
            def __init__(self, path, **kwargs):
                assert kwargs["quota_scope_id"].startswith("installation:")
                own.budgets.append((path, kwargs["quota_scope_id"]))
            async def close(self): own.calls.append("budget-close")
            async def report(self): return {"calls": 0}
        class Scheduler:
            def __init__(self, *a, **k): self.view = None; own.scheduler = self
            def observe(self, view): self.view = view; return True
            def _fresh(self): return self.view is not None
            def validate_segment(self, _): return True
            def playback_ack(self, _): pass
            async def run(self): own.model_calls += 1; await asyncio.Event().wait()
            async def stop(self, **kwargs): own.calls.append("scheduler-stop")
            def report(self): return {"last_spoken": None}
            idle_deadline = 0
        async def probe(run):
            own.calls.append("audio-probe")
            assert run._route_guard() and run.sink.on_ack is None
            return {"passed": not own.fail_probe, "scope": "FAKE_test_only_not_actual_capture"}
        def resolve(config):
            def args(**kwargs):
                assert kwargs["headless"] is False
                return ["-screen-fullscreen", "0", "-screen-width", "800", "-screen-height", "600", "--actor-endpoint", kwargs["endpoint"]]
            return Path("/fake/verified-client"), Obj(game_id=config.game_id, game_version=config.game_version, client_arguments=args)
        return NativeDependencies(admission=Admission, lease=Lease, attestor=Attestor, provider=Provider, voice=Voice,
            sink=Sink, output=Output, bridge=Bridge, registry=lambda: None, spawn=spawn, window=Window,
            obs_client=Client, obs_controller=Obs, budget=Budget, scheduler=Scheduler, resolve=resolve, audio_probe=probe,
            canonical_directory=lambda: own.config.state_directory.parent / "canonical")


async def prepared(fakes):
    factory = NativeBroadcastFactory(fakes.config, Store(), dependencies=fakes.dependencies())
    flags = []; unsafe = []; terminal = []
    run = await factory.prepare("ABC123", uuid4().hex, CommentaryConfig(), on_ready=flags.append,
        on_unsafe=unsafe.append, on_terminal=lambda: terminal.append(True))
    return factory, run, flags, unsafe, terminal


async def test_preview_composes_only_observer_and_is_nonblocking_until_activate(tmp_path, monkeypatch):
    monkeypatch.setenv("TWITCH_ACCESS_TOKEN", "must-not-enter-unity")
    monkeypatch.setenv("ACTOR_BRIDGE_TOKEN", "old-session-must-not-enter")
    monkeypatch.setenv("ACTOR_BROADCAST_MOTION_REPORT", "/untrusted-parent-path.json")
    fakes = Fakes(tmp_path); factory, run, flags, unsafe, terminal = await prepared(fakes)
    try:
        assert fakes.model_calls == 0 and run.readiness() == {**dict.fromkeys(flags[-1], True), "chat": False}
        assert fakes.calls.index("voice-warm") < fakes.calls.index("admit")
        spawn = next(x for x in fakes.calls if isinstance(x, tuple) and x[0] == "spawn")
        assert "--broadcast-observer" in spawn[1] and "private-admission" not in repr(spawn[1])
        assert spawn[2]["ACTOR_OBSERVER_ADMISSION"] == "private-admission" and "TWITCH_ACCESS_TOKEN" not in spawn[2]
        assert spawn[2]["ACTOR_BRIDGE_TOKEN"] != "old-session-must-not-enter"
        assert spawn[2]["ACTOR_BROADCAST_PROBE_PNG"] == str(run.directory / "native-presented.png")
        assert spawn[2]["ACTOR_BROADCAST_MOTION_REPORT"] == str(run.directory / "motion.json")
        await asyncio.wait_for(run.activate(), .1); await asyncio.sleep(0)
        assert fakes.model_calls == 1 and not unsafe
    finally: await factory.aclose()
    assert fakes.process.poll() == 0 and not fakes.lease.held and not factory._owned


async def test_failed_real_probe_does_not_mark_ready_and_attempts_all_owned_cleanup(tmp_path):
    fakes = Fakes(tmp_path); fakes.fail_probe = True
    factory = NativeBroadcastFactory(fakes.config, Store(), dependencies=fakes.dependencies())
    with pytest.raises(NativeError, match="probe_unproven"):
        await factory.prepare("ABC123", uuid4().hex, CommentaryConfig(), on_ready=lambda _: None,
            on_unsafe=lambda _: None, on_terminal=lambda: None)
    assert fakes.model_calls == 0 and fakes.process.poll() == 0 and not fakes.lease.held
    for required in ("obs-emergency", "sink-close", "voice-close", "model-close", "terminate", "bridge-stop", "budget-close", "release"):
        assert required in fakes.calls


async def test_startup_splash_is_never_committed_before_fresh_presented_and_stable_final_window(tmp_path):
    fakes = Fakes(tmp_path); fakes.presented_available = False
    deps = fakes.dependencies(); state = Obj(binding=WindowBinding(7,12345,400,332), verifies=[])
    class Window:
        def __init__(self, pid, **kwargs): assert pid == 12345
        def locate(self):
            if state.binding is None: raise RuntimeError("owned_window_temporarily_missing")
            return state.binding
        def verify(self, value): state.verifies.append(value); return value == state.binding
    deps.window = Window
    factory = NativeBroadcastFactory(fakes.config, Store(), dependencies=deps)
    flags=[]; unsafe=[]
    preparing=asyncio.create_task(factory.prepare("ABC123",uuid4().hex,CommentaryConfig(),
        on_ready=flags.append,on_unsafe=unsafe.append,on_terminal=lambda:None))
    try:
        for _ in range(100):
            if factory._owned and factory._owned[0].scheduler is not None: break
            await asyncio.sleep(.005)
        run=factory._owned[0]
        await asyncio.sleep(.06)
        assert run._binding is None and not state.verifies and not run.readiness()["observer"]
        # A splash disappearing/resizing is normal startup, not loss of a
        # committed capture route. Native bounds include the OS title bar.
        state.binding=None; await asyncio.sleep(.06)
        state.binding=WindowBinding(8,12345,800,664)
        await asyncio.sleep(.18)
        assert run._binding is None and not state.verifies and not unsafe
        fakes.presented_available=True
        await asyncio.sleep(.06)
        assert run._binding is None and not run.readiness()["observer"]
        result=await asyncio.wait_for(preparing,1)
        assert result is run and run._binding == state.binding
        assert run.readiness()["observer"] and not unsafe
        for _ in range(80):
            if state.verifies: break
            await asyncio.sleep(.01)
        assert state.verifies and all(value == state.binding for value in state.verifies)
    finally:
        if not preparing.done(): preparing.cancel()
        await asyncio.gather(preparing,return_exceptions=True)
        await factory.aclose()
    assert fakes.process.poll() == 0 and not fakes.lease.held


@pytest.mark.parametrize("replacement", [None, WindowBinding(9,12345,800,664)])
async def test_committed_window_loss_or_replacement_still_stops_output(tmp_path,replacement):
    fakes=Fakes(tmp_path); deps=fakes.dependencies(); state=Obj(binding=WindowBinding(8,12345,800,664))
    class Window:
        def __init__(self,pid,**kwargs): assert pid == 12345
        def locate(self): return state.binding
        def verify(self,value): return value == state.binding
    deps.window=Window
    factory=NativeBroadcastFactory(fakes.config,Store(),dependencies=deps); unsafe=[]
    run=await factory.prepare("ABC123",uuid4().hex,CommentaryConfig(),on_ready=lambda _:None,
        on_unsafe=unsafe.append,on_terminal=lambda:None)
    try:
        committed=run._binding;state.binding=replacement
        for _ in range(80):
            if unsafe and "obs-emergency" in fakes.calls: break
            await asyncio.sleep(.01)
        assert unsafe == ["broadcast_window_lost"] and run._binding == committed
        assert "obs-emergency" in fakes.calls and not run._route_guard()
    finally: await factory.aclose()


async def test_pf_revocation_is_fresh_dependency_and_stops_owned_output(tmp_path):
    fakes = Fakes(tmp_path); factory, run, _, unsafe, _ = await prepared(fakes)
    try:
        fakes.pf = False
        for _ in range(80):
            if unsafe and "obs-emergency" in fakes.calls: break
            await asyncio.sleep(.01)
        assert unsafe == ["obs_firewall_attestation_lost"] and not run._route_guard()
        assert "obs-emergency" in fakes.calls
    finally: await factory.aclose()


@pytest.mark.parametrize("dependency,reason", [("renderer_ready", "audio_renderer_unavailable"), ("poisoned", "broadcast_model_unavailable")])
async def test_idle_dependency_loss_revokes_readiness_and_stops_owned_output(tmp_path, dependency, reason):
    fakes = Fakes(tmp_path); factory, run, _, unsafe, _ = await prepared(fakes)
    try:
        setattr(fakes, dependency, dependency == "poisoned")
        for _ in range(80):
            if unsafe and "obs-emergency" in fakes.calls: break
            await asyncio.sleep(.01)
        assert unsafe == [reason] and not run._route_guard() and "obs-emergency" in fakes.calls
        assert not run.readiness()["model" if dependency == "poisoned" else "audio"]
    finally: await factory.aclose()


async def test_cancelled_admission_post_is_drained_before_release_without_late_reservation(tmp_path):
    fakes = Fakes(tmp_path); deps = fakes.dependencies()
    entered, allow_result = asyncio.Event(), asyncio.Event()
    class Admission:
        def __init__(self, *a, **k): pass
        async def observe(self, *a):
            entered.set(); await allow_result.wait(); fakes.calls.append("post-committed")
            return Obj(observer_id="observer", lobby_id="match")
        async def release(self, *a): fakes.calls.append("release")
    deps.admission = Admission
    factory = NativeBroadcastFactory(fakes.config, Store(), dependencies=deps)
    task = asyncio.create_task(factory.prepare("ABC123", uuid4().hex, CommentaryConfig(), on_ready=lambda _: None,
        on_unsafe=lambda _: None, on_terminal=lambda: None))
    await asyncio.wait_for(entered.wait(), 1); task.cancel(); await asyncio.sleep(.02)
    assert not task.done() and "release" not in fakes.calls
    allow_result.set()
    with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 1)
    assert fakes.calls.index("post-committed") < fakes.calls.index("release")
    assert not fakes.lease.held and not factory._owned and fakes.process is None


async def test_repeat_prepare_keeps_budget_identity_across_diagnostic_directory_changes(tmp_path):
    fakes = Fakes(tmp_path); factory, run, _, _, _ = await prepared(fakes)
    await run.close()
    fakes.config.state_directory = tmp_path / "other-private"
    second = await factory.prepare("ABC123", uuid4().hex, CommentaryConfig(), on_ready=lambda _: None,
        on_unsafe=lambda _: None, on_terminal=lambda: None)
    await second.close()
    assert len(fakes.budgets) == 2 and fakes.budgets[0] == fakes.budgets[1]
    assert fakes.calls.count("obs-owned-inputs-removed") == 2


async def test_room_refresh_is_one_bounded_background_query_not_status_backlog(tmp_path, monkeypatch):
    import urllib.request
    fakes = Fakes(tmp_path); calls = []; release = threading.Event()
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, count):
            assert count == 32769; release.wait(2)
            return json.dumps([{"roomCode":"ABC123", "gameMode":"SocialDeduction", "status":"Ready", "currentPlayers":1,"maxPlayers":4}]).encode()
    class Opener:
        def open(self, request, timeout): calls.append(timeout); return Response()
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    factory = NativeBroadcastFactory(fakes.config, Store(), dependencies=fakes.dependencies())
    started = time.monotonic()
    results = await asyncio.gather(*(factory.rooms() for _ in range(5)))
    assert all(value == [] for value in results) and time.monotonic()-started < 1.2 and calls == [3]
    release.set(); await asyncio.wait_for(factory._rooms_task, 1)
    assert (await factory.rooms())[0]["room_code"] == "ABC123" and calls == [3]
    await factory.aclose(); assert (await factory.rooms())[0]["room_code"] == "ABC123"


def test_concurrent_installation_identity_waits_for_same_persisted_inode(tmp_path):
    directory = tmp_path / "canonical"; directory.mkdir(mode=0o700)
    fd = os.open(directory / "opaque-install-id", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    started = threading.Barrier(3)
    def read(): started.wait(timeout=1); return installation_identity(directory)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(read) for _ in range(2)]
            started.wait(timeout=1)
            try:
                time.sleep(.03)
                assert not any(value.done() for value in futures)
            finally: fcntl.flock(fd, fcntl.LOCK_UN)
            identities = [value.result(timeout=1) for value in futures]
        assert identities[0] == identities[1] == (directory / "opaque-install-id").read_text()
    finally: os.close(fd)


@pytest.mark.parametrize("empty_file", [False, True])
def test_missing_identity_never_resets_existing_quota(tmp_path, empty_file):
    directory = tmp_path / "canonical"; directory.mkdir(mode=0o700)
    if empty_file: (directory / "opaque-install-id").touch(mode=0o600)
    (directory / "quota.sqlite").write_bytes(b"existing quota evidence")
    with pytest.raises(NativeError, match="identity_missing_with_quota"): installation_identity(directory)
    assert (directory / "quota.sqlite").read_bytes() == b"existing quota evidence"


def test_backup_requires_trusted_receipt_and_groups_not_empty_directory(tmp_path):
    fakes = Fakes(tmp_path); root = fakes.config.protected_obs_backup
    assert verify_backup_receipt(root)
    (root / "backup-receipt.json").unlink()
    with pytest.raises(FileNotFoundError): verify_backup_receipt(root)


def test_video_probe_rejects_black_unrelated_and_unknown_window_geometry():
    expected = bytes((x*7+y*11) % 256 for y in range(48) for x in range(64))
    assert video_similarity(expected, expected)["passed"]
    assert not video_similarity(bytes(len(expected)), expected)["passed"]
    assert not video_similarity(bytes(255-x for x in expected), expected)["passed"]
    binding = WindowBinding(1, 10, 800, 625)
    assert content_top_fraction(800,600,binding,(800,600)) == 0
    assert content_top_fraction(1600,1250,binding,(800,600)) == pytest.approx(.04)
    with pytest.raises(NativeError, match="aspect_unverified"): content_top_fraction(1920,1080,binding,(800,600))
    header = b"\x89PNG\r\n\x1a\n" + bytes(4) + b"IHDR" + (800).to_bytes(4,"big") + (600).to_bytes(4,"big")
    assert png_dimensions(header) == (800,600)
    with pytest.raises(NativeError): png_dimensions(header[:16] + (80000).to_bytes(4,"big") + header[20:])


async def test_cleanup_failure_keeps_channel_lease_and_run_handle_until_retry(tmp_path):
    fakes = Fakes(tmp_path); factory, run, _, _, _ = await prepared(fakes)
    fakes.fail_close = True
    with pytest.raises(NativeError, match="cleanup_incomplete"): await run.close()
    assert fakes.lease.held and factory._owned == [run] and run.scheduler is not None
    assert fakes.process.poll() == 0 and "release" in fakes.calls
    with pytest.raises(NativeError, match="cleanup_pending"):
        await factory.prepare("OTHER1", uuid4().hex, CommentaryConfig(), on_ready=lambda _: None, on_unsafe=lambda _: None, on_terminal=lambda: None)
    fakes.fail_close = False; await run.close()
    assert not fakes.lease.held and not factory._owned


@pytest.mark.parametrize("blocked_output_stop", [False, True])
async def test_real_obs_partial_remove_retry_preserves_prepare_reason_and_releases_only_after_idle_proof(tmp_path, blocked_output_stop):
    from actor_broadcast.obs import ObsController, ObsError
    from test_broadcast_obs import Graph

    fakes=Fakes(tmp_path)
    class PartialRemove(Graph):
        def __init__(self):
            super().__init__();self.inputs=[];self.settings={};self.items={key:[] for key in ObsController.SCENES}
            self.current_scene="ACTOR Preparing";self.created=False;self.failed_empty=False;self.block_stop=False
            # Actual controller audit rejects this graph before readiness.
            self.voice_transforms["ACTOR Match"]["positionX"]=1
        async def connect(self): fakes.calls.append("obs-connect")
        async def close(self): fakes.calls.append("obs-close")
        async def request(self,kind,data=None,**kwargs):
            if kind=="SetInputMute" and not any(row["inputName"]==(data or {}).get("inputName") for row in self.inputs):
                self.calls.append((kind,data));raise ObsError("obs_request_rejected")
            if kind=="StopStream" and self.block_stop:
                self.calls.append((kind,data));raise ObsError("obs_request_failed")
            value=await super().request(kind,data,**kwargs)
            if kind=="CreateInput": self.created=True
            if kind=="GetInputList" and self.created and not self.inputs and not self.failed_empty:
                self.failed_empty=True;raise ObsError("obs_request_failed")
            return value

    class FastObs(ObsController): STABILIZE_TIMEOUT_SECONDS=.08
    graph=PartialRemove();deps=fakes.dependencies();deps.obs_client=lambda _:graph;deps.obs_controller=FastObs
    factory=NativeBroadcastFactory(fakes.config,Store(),dependencies=deps)
    with pytest.raises(NativeError,match="^broadcast_prepare_cleanup_incomplete$") as failure:
        await factory.prepare("ABC123",uuid4().hex,CommentaryConfig(),on_ready=lambda _:None,
            on_unsafe=lambda _:None,on_terminal=lambda:None)
    run=factory._owned[0]
    assert failure.value.prepare_code == run.prepare_failure_code == "obs_graph_stabilization_timeout"
    assert failure.value.cleanup_stages == run.cleanup_failures == ("obs_owned_inputs",)
    assert not graph.inputs and run.obs._prepared and fakes.lease.held
    assert fakes.process.poll()==0 and "obs-close" not in fakes.calls
    if blocked_output_stop:
        graph.active=True;graph.block_stop=True;run.obs._owned_stream=True
        with pytest.raises(NativeError,match="^broadcast_factory_cleanup_incomplete$"):
            await factory.aclose()
        assert graph.active and run.obs._owned_stream and fakes.lease.held and factory._owned==[run]
        assert run.cleanup_failures == ("obs_stop",) and not run.recovered_cleanup_stages
        assert "obs-close" not in fakes.calls
        graph.block_stop=False
    await factory.aclose()
    assert not graph.active and not factory._owned and not fakes.lease.held and run._closed
    assert run.recovered_cleanup_stages == ("obs_stop",) and not run.cleanup_failures
    assert run.prepare_failure_code == "obs_graph_stabilization_timeout" and fakes.calls.count("obs-close")==1


async def test_cancelled_cleanup_wait_keeps_owned_job_and_all_handles_until_actual_drain(tmp_path):
    fakes = Fakes(tmp_path); factory, run, _, _, _ = await prepared(fakes)
    entered, drained = asyncio.Event(), asyncio.Event()
    async def slow_close(): entered.set(); await drained.wait(); fakes.calls.append("model-close")
    run.provider.close = slow_close
    waiter = asyncio.create_task(run.close())
    await asyncio.wait_for(entered.wait(), 1); waiter.cancel()
    with pytest.raises(asyncio.CancelledError): await waiter
    assert not run._close_task.done() and run in factory._owned and fakes.lease.held
    drained.set(); await asyncio.wait_for(run.close(), 1)
    assert fakes.process.poll() == 0 and not fakes.lease.held and not factory._owned


def test_recorded_pcm_probe_requires_real_energy_and_known_envelope_correlation():
    reference = probe_signal(); silence = bytes(24000)
    measured = audio_probe_measurement(silence + reference + silence, reference)
    assert measured["passed"] and measured["envelope_correlation"] > .99
    assert not audio_probe_measurement(bytes(len(reference)), reference)["passed"]
    unrelated = array("f", [.1] * (len(reference)//4)).tobytes()
    assert not audio_probe_measurement(unrelated, reference)["passed"]
    with pytest.raises(NativeError): audio_probe_measurement(b"broken", reference)


async def test_obs_readiness_latches_only_after_actual_probe_and_final_startup_audit(tmp_path):
    fakes = Fakes(tmp_path); deps = fakes.dependencies(); phases = []
    async def probe(run):
        assert not run.readiness()['obs'] and not run.readiness()['audio']
        assert run._route_guard()
        run._obs_event('obs_graph_changed')
        assert run._failure is None  # Startup still requires actual final audit.
        phases.append('probe')
        return {'passed': True}
    deps.audio_probe = probe
    factory = NativeBroadcastFactory(fakes.config, Store(), dependencies=deps)
    flags = []; unsafe = []
    try:
        run = await factory.prepare('ABC123', uuid4().hex, CommentaryConfig(),
            on_ready=flags.append, on_unsafe=unsafe.append, on_terminal=lambda: None)
        assert phases == ['probe']
        assert any(row['obs'] and not row['audio'] for row in flags)
        assert flags[-1]['obs'] and flags[-1]['audio']
        assert 'obs-startup-complete' in fakes.calls and not unsafe
        run._obs_event('obs_graph_changed')
        assert run._failure == 'obs_graph_changed' and unsafe == ['obs_graph_changed']
        assert not run._route_guard()
    finally:
        await factory.aclose()


@pytest.mark.parametrize('invalidate', ['route', 'renderer', 'feed'])
async def test_startup_audit_return_cannot_latch_stale_ready_proof(tmp_path, invalidate):
    fakes = Fakes(tmp_path); deps = fakes.dependencies(); base = deps.obs_controller
    class InvalidatedAfterAudit(base):
        async def complete_startup(self):
            # Reproduce invalidation between a successful audit and the
            # Native _stage waiter consuming that result.
            if invalidate == 'route': self.route_verified = False
            elif invalidate == 'renderer': fakes.renderer_ready = False
            else: fakes.scheduler._fresh = lambda: False
            return Obj(passed=True)
    deps.obs_controller = InvalidatedAfterAudit
    factory = NativeBroadcastFactory(fakes.config, Store(), dependencies=deps)
    flags = []
    with pytest.raises(NativeError):
        await factory.prepare('ABC123', uuid4().hex, CommentaryConfig(),
            on_ready=flags.append, on_unsafe=lambda _: None, on_terminal=lambda: None)
    assert all(not row['obs'] and not row['audio'] for row in flags)
    assert not factory._owned and not fakes.lease.held and fakes.process.poll() == 0


@pytest.mark.parametrize('raw_size,top,output,expected', [
    ((800,664),64,(1280,720),(160,0,960,720)),
    ((1600,1328),128,(1280,720),(160,0,960,720)),
    ((800,600),0,(1280,720),(160,0,960,720)),
    ((800,664),64,(1920,1080),(240,0,1440,1080)),
    ((1280,720),0,(1280,720),(0,0,1280,720)),
])
def test_recorded_content_roi_fits_already_cropped_scene_without_double_titlebar_trim(raw_size,top,output,expected):
    from actor_broadcast.native import recorded_content_roi
    transform={'sourceWidth':raw_size[0],'sourceHeight':raw_size[1],
        'cropTop':top,'cropBottom':0,'cropLeft':0,'cropRight':0}
    video={'baseWidth':1280,'baseHeight':720,'outputWidth':output[0],'outputHeight':output[1]}
    assert recorded_content_roi(transform,video) == expected


async def test_video_probe_preserves_source_aspect_and_uses_actual_cropped_record_roi(tmp_path,monkeypatch):
    import base64
    import actor_broadcast.native as native
    path=tmp_path/'native.png'
    def png(width,height):
        return b'\x89PNG\r\n\x1a\n'+bytes(4)+b'IHDR'+width.to_bytes(4,'big')+height.to_bytes(4,'big')
    path.write_bytes(png(800,600));path.chmod(0o600)
    requests=[];decoded=[]
    class Client:
        async def request(self,kind,data=None):
            requests.append((kind,data))
            if kind=='GetSourceScreenshot':
                assert data=={'sourceName':'owned-video','imageFormat':'png','imageWidth':800}
                return {'imageData':'data:image/png;base64,'+base64.b64encode(png(800,664)).decode()}
            assert kind=='GetVideoSettings'
            return {'baseWidth':1280,'baseHeight':720,'outputWidth':1280,'outputHeight':720}
    async def decode(run,path,**kwargs):
        decoded.append((path,kwargs))
        return bytes((x*7+y*11)%256 for y in range(48) for x in range(64))
    monkeypatch.setattr(native,'_decode_gray',decode)
    run=Obj(native_probe_path=path,native_size=(800,600),directory=tmp_path,obs_client=Client(),
        _binding=WindowBinding(1,10,800,664),obs=Obj(VIDEO='owned-video',video_transform={
            'sourceWidth':800,'sourceHeight':664,'cropTop':64,'cropBottom':0,'cropLeft':0,'cropRight':0}))
    result=await native.video_probe_measurement(run,tmp_path/'recording.mkv')
    assert result['passed']
    assert decoded[0][1]=={'crop':(0,64,800,600)}
    assert decoded[1][1]=={}
    assert decoded[2][1]=={'crop':(160,0,960,720),'movie':True}


async def test_production_prepare_requires_own_native_png_before_obs_graph_mutation(tmp_path):
    fakes=Fakes(tmp_path);deps=fakes.dependencies();spawn=deps.spawn
    def missing_probe(*args,**kwargs):
        process=spawn(*args,**kwargs)
        Path(kwargs['env']['ACTOR_BROADCAST_PROBE_PNG']).unlink()
        return process
    deps.spawn=missing_probe
    factory=NativeBroadcastFactory(fakes.config,Store(),dependencies=deps)
    with pytest.raises(FileNotFoundError):
        await factory.prepare('ABC123',uuid4().hex,CommentaryConfig(),on_ready=lambda _:None,
            on_unsafe=lambda _:None,on_terminal=lambda:None)
    assert 'obs-prepare' not in fakes.calls and not factory._owned
    assert fakes.process.poll()==0 and not fakes.lease.held


@pytest.mark.parametrize('event_present',[False,True])
async def test_native_real_owned_stream_health_revokes_run_for_event_or_missing_event_poll(tmp_path,event_present):
    from test_broadcast_obs import controller
    fakes=Fakes(tmp_path);factory,run,_,unsafe,_=await prepared(fakes)
    original=run.obs; obs,graph=controller();obs.on_unsafe=run._obs_event
    obs._owned_stream=obs._stream_confirmed=True;graph.active=True
    assert (await obs.audit()).passed
    run.obs=obs
    try:
        graph.active=False
        if event_present:
            graph.on_event('StreamStateChanged',{'outputActive':False,'outputState':'OBS_WEBSOCKET_OUTPUT_STOPPED'})
            assert unsafe==['obs_owned_stream_inactive'] and not run._route_guard()
        # No event is needed: the production native guard invokes the actual
        # controller status query on its normal one-second OBS audit cadence.
        for _ in range(140):
            if unsafe and not obs._owned_stream: break
            await asyncio.sleep(.01)
        assert unsafe==['obs_owned_stream_inactive'] and not run._route_guard()
        assert not obs._owned_stream and not graph.active
        assert not any(kind=='StopStream' for kind,_ in graph.calls)  # Already inactive.
        assert any(kind=='GetStreamStatus' for kind,_ in graph.calls)
    finally:
        run.obs=original;await factory.aclose()
    assert not factory._owned and not fakes.lease.held


async def test_initial_pf_refusal_prevents_all_cold_prepare_and_operational_resources(tmp_path):
    fakes=Fakes(tmp_path);fakes.pf=False
    factory=NativeBroadcastFactory(fakes.config,Store(),dependencies=fakes.dependencies())
    with pytest.raises(NativeError,match='obs_firewall_attestation_required'):
        await factory.prepare('ABC123',uuid4().hex,CommentaryConfig(),on_ready=lambda _:None,
            on_unsafe=lambda _:None,on_terminal=lambda:None)
    assert not fakes.budgets and fakes.process is None
    assert not any(name in fakes.calls for name in ('model-prepare','voice-warm','sink-start','admit','bridge-start','obs-connect'))
    assert not factory._owned and not fakes.lease.held


@pytest.mark.parametrize('provider_invalid',[False,True])
async def test_post_warmup_fresh_pf_and_provider_health_gate_all_admission_audio_obs(tmp_path,provider_invalid):
    fakes=Fakes(tmp_path);deps=fakes.dependencies();base_voice=deps.voice
    class Voice(base_voice):
        async def warmup(self):
            await super().warmup()
            if provider_invalid:fakes.poisoned=True
            else:fakes.pf=False
    deps.voice=Voice
    reads=[]
    class WatchedStore(Store):
        def get(self,name):reads.append(name);return super().get(name)
    factory=NativeBroadcastFactory(fakes.config,WatchedStore(),dependencies=deps);flags=[]
    with pytest.raises(NativeError,match='broadcast_model_unavailable' if provider_invalid else 'obs_firewall_attestation_required'):
        await factory.prepare('ABC123',uuid4().hex,CommentaryConfig(),on_ready=flags.append,
            on_unsafe=lambda _:None,on_terminal=lambda:None)
    assert 'model-prepare' in fakes.calls and 'voice-warm' in fakes.calls
    assert not reads and fakes.admitted is None and fakes.process is None
    assert not any(name in fakes.calls for name in ('sink-start','admit','bridge-start','obs-connect'))
    assert 'voice-close' in fakes.calls and 'model-close' in fakes.calls
    assert not factory._owned and not fakes.lease.held


async def test_cold_warmup_has_no_continuous_pf_poll_and_fresh_proof_precedes_operational_setup(tmp_path):
    fakes=Fakes(tmp_path);deps=fakes.dependencies();base_voice=deps.voice;base_provider=deps.provider
    entered=asyncio.Event();finish=asyncio.Event();refreshes=[]
    class Attestor:
        def __init__(self,*args):self.generation=0
        @property
        def verified(self):return self.generation>0 and fakes.pf
        async def refresh(self):
            self.generation+=1;refreshes.append(('pf',self.generation));fakes.calls.append('pf-refresh')
            return fakes.pf
    class Voice(base_voice):
        async def warmup(self):
            entered.set();await finish.wait();await super().warmup()
    class Provider(base_provider):
        def report(self):fakes.calls.append('model-health');return super().report()
    deps.attestor=Attestor;deps.voice=Voice;deps.provider=Provider
    factory=NativeBroadcastFactory(fakes.config,Store(),dependencies=deps)
    preparing=asyncio.create_task(factory.prepare('ABC123',uuid4().hex,CommentaryConfig(),
        on_ready=lambda _:None,on_unsafe=lambda _:None,on_terminal=lambda:None))
    try:
        await entered.wait();run=factory._owned[0]
        # A cold compile may outlive the original proof, but cannot touch OBS,
        # server admission or audio. Operational proof will be reacquired.
        fakes.pf=False;await asyncio.sleep(.12)
        assert refreshes==[('pf',1)] and not run._tasks and run._failure is None
        assert run.obs_client is run.obs is run.sink is run.admission is run.process is None
        fakes.pf=True;finish.set();run=await asyncio.wait_for(preparing,1)
        assert len(refreshes)>=2
        second=[index for index,name in enumerate(fakes.calls) if name=='pf-refresh'][1]
        assert fakes.calls.index('voice-warm') < second
        assert second < fakes.calls.index('model-health') < fakes.calls.index('admit')
        assert second < fakes.calls.index('sink-start') and second < fakes.calls.index('admit')
        assert run._tasks and run.readiness()['observer'] and run.readiness()['obs']
        # There is no post-start grace. The existing runtime guard still trips
        # and requests Safe immediately on its next failed actual refresh.
        fakes.pf=False
        for _ in range(80):
            if run._failure is not None:break
            await asyncio.sleep(.01)
        assert run._failure=='obs_firewall_attestation_lost' and not run._route_guard()
    finally:
        finish.set()
        if not preparing.done():preparing.cancel()
        await asyncio.gather(preparing,return_exceptions=True)
        await factory.aclose()
    assert not factory._owned and not fakes.lease.held


async def test_successful_post_warmup_refresh_without_current_verified_proof_cannot_admit(tmp_path):
    fakes=Fakes(tmp_path);deps=fakes.dependencies()
    class Attestor:
        def __init__(self,*args):self.calls=0
        @property
        def verified(self):return self.calls==1
        async def refresh(self):self.calls+=1;return True
    deps.attestor=Attestor
    factory=NativeBroadcastFactory(fakes.config,Store(),dependencies=deps)
    with pytest.raises(NativeError,match='obs_firewall_attestation_required'):
        await factory.prepare('ABC123',uuid4().hex,CommentaryConfig(),on_ready=lambda _:None,
            on_unsafe=lambda _:None,on_terminal=lambda:None)
    assert 'voice-warm' in fakes.calls and fakes.admitted is None and fakes.process is None
    assert not any(name in fakes.calls for name in ('sink-start','admit','bridge-start','obs-connect'))
    assert not factory._owned and not fakes.lease.held


@pytest.mark.parametrize('blocked_stage',['none','output','record_directory'])
async def test_failed_render_flush_recovers_only_after_actual_terminal_dependency_cleanup(tmp_path,blocked_stage):
    from actor_broadcast.audio import AudioError
    from actor_broadcast.budget import BudgetLedger
    from actor_broadcast.scheduler import CommentaryScheduler
    from test_broadcast_commentary import Clock,Provider,Speech,fact,pump,view
    fakes=Fakes(tmp_path);factory,run,_,_,_=await prepared(fakes)
    # Isolate the teardown under test; this is all fake native composition.
    for task in tuple(run._tasks):task.cancel()
    await asyncio.gather(*tuple(run._tasks),return_exceptions=True)
    class ClosedProvider(Provider):
        def report(self):return {'closed':self.closed,'decision_active':self.active!=0}
    class LostFlush(Speech):
        async def cancel(self,*args,**kwargs):raise AudioError('audio_renderer_disconnected')
    clock=Clock();provider=ClosedProvider();speech=LostFlush();budget=BudgetLedger()
    scheduler=CommentaryScheduler(provider,speech,budget,clock=clock.monotonic,wall_clock=clock.wall)
    run.provider=provider;run.scheduler=scheduler;run.budget=budget
    scheduler.observe(view(clock,facts=[fact(clock,priority=0)]));scheduler.ready()
    await pump(scheduler)
    assert scheduler._speech_pending and scheduler.spoken.report()['completed']==0
    restores=[]
    if blocked_stage=='record_directory':
        run._record_directory_original='/original-private-record-directory'
        async def restore(kind,values):
            assert kind=='SetRecordDirectory' and values=={'recordDirectory':'/original-private-record-directory'}
            restores.append(kind)
            if len(restores)==1:raise RuntimeError('owned restore failed')
        run.obs_client.request=restore
    fakes.fail_close=blocked_stage=='output'
    if blocked_stage!='none':
        with pytest.raises(NativeError,match='cleanup_incomplete'):await run.close()
        assert run in factory._owned and fakes.lease.held and 'scheduler' in run.cleanup_failures
        if blocked_stage=='output':assert run.output is not None and 'output' in run.cleanup_failures
        else:
            assert 'record_directory' in run.cleanup_failures and run._record_directory_original is not None
            assert run.obs_client is not None and 'obs-close' not in fakes.calls and restores==['SetRecordDirectory']
        assert not scheduler.report()['stopped'] and not run.recovered_cleanup_stages
        fakes.fail_close=False
    await run.close()
    assert run._closed and not fakes.lease.held and not factory._owned
    assert run.recovered_cleanup_stages==('scheduler_terminal_audio_flush',)
    report=scheduler.report()
    assert report['stopped'] and report['counts']['audio_flush_failed']>=1
    assert report['counts']['terminal_audio_flush_recovered']==1
    assert report['spoken']['interrupted']==1 and report['spoken']['completed']==0
    assert report['spoken']['covered_versions']==0 and report['last_spoken'] is None
    assert not scheduler._speech_pending and budget.closed and provider.closed
    assert run._record_directory_original is None
    if blocked_stage=='record_directory':assert restores==['SetRecordDirectory','SetRecordDirectory']
    assert all(item in fakes.calls for item in ('sink-close','voice-close','obs-owned-inputs-removed','obs-close'))


@pytest.mark.parametrize('foreign_graph_event',[False,True])
async def test_terminal_fsm_preserves_verified_match_route_and_still_rejects_external_graph_change(tmp_path,foreign_graph_event):
    from actor_broadcast.coordinator import BroadcastCoordinator
    from test_broadcast_obs import controller as actual_obs,ObsController
    fakes=Fakes(tmp_path);factory,run,_,_,_=await prepared(fakes)
    for task in tuple(run._tasks):task.cancel()
    await asyncio.gather(*tuple(run._tasks),return_exceptions=True)
    obs,graph=actual_obs()
    obs._binding=run._binding;obs.window_guard=run.window.verify
    graph.settings[ObsController.VIDEO]['window']=run._binding.window_id
    obs._renderer_url=run.sink.renderer_url
    graph.settings[ObsController.VOICE]['url']=obs._renderer_url
    for item in graph.inputs:
        identity=str(uuid4());item['inputUuid']=identity
        obs._owned_inputs[item['inputName']]=identity
        graph.settings[item['inputName']]['actor_broadcast_owner']=obs._owner_nonce
    for items in graph.items.values():
        for item in items:item['sourceUuid']=obs._owned_inputs[item['sourceName']]
    run.obs=obs;run.obs_client=graph;obs.on_unsafe=run._obs_event
    obs._owned_stream=obs._stream_confirmed=True;graph.active=True
    assert (await obs.audit()).passed and run._route_guard()
    coordinator=BroadcastCoordinator(factory,chat_enabled=False,mux_drain_seconds=0)
    coordinator._run=run;coordinator.state='Live';run._terminal=True
    run.on_unsafe=lambda code:coordinator.unsafe(code,generation=coordinator._generation)
    ending_routes=[];ending_mutations=[];original=graph.request;fired=False
    async def inspect(kind,data=None,**kwargs):
        nonlocal fired
        if coordinator.state=='Ending':
            ending_routes.append(run._route_guard())
            if kind=='SetCurrentProgramScene':ending_mutations.append(data)
            if foreign_graph_event and kind=='GetCurrentProgramScene' and not fired:
                fired=True;obs._event('SceneItemTransformChanged',{'sceneName':'ACTOR Match','sceneItemId':2})
        return await original(kind,data,**kwargs)
    graph.request=inspect
    run.scheduler.report=lambda:{'terminal_outro_finished':True}
    try:
        coordinator.terminal()
        async with asyncio.timeout(3):
            while coordinator.state not in ('Stopped','Failed'):await asyncio.sleep(.001)
        assert ending_routes and not ending_mutations
        assert not any(kind=='SetCurrentProgramScene' and data['sceneName']=='ACTOR Ending' for kind,data in graph.calls)
        if foreign_graph_event:
            assert fired and run._failure=='obs_graph_changed'
            assert coordinator.state=='Failed' and coordinator.metrics['unsafe_stops']==1
            assert coordinator.metrics['terminal_drains']==0
        else:
            assert all(ending_routes) and run._failure is None
            assert coordinator.state=='Stopped' and coordinator.metrics['terminal_drains']==1
            assert coordinator.metrics['unsafe_stops']==coordinator.metrics['outro_timeouts']==0
        assert not graph.active and not graph.inputs and run._closed and not factory._owned and not fakes.lease.held
    finally:await coordinator.aclose();await factory.aclose()
