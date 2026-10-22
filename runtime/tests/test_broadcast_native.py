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
                assert kwargs == {"protected_backup_confirmed": True, "adopt_empty_collection": True}
                own.calls.append("obs-prepare")
            async def audit(self): return Obj(passed=True)
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
    fakes = Fakes(tmp_path); factory, run, flags, unsafe, terminal = await prepared(fakes)
    try:
        assert fakes.model_calls == 0 and run.readiness() == {**dict.fromkeys(flags[-1], True), "chat": False}
        assert fakes.calls.index("voice-warm") < fakes.calls.index("admit")
        spawn = next(x for x in fakes.calls if isinstance(x, tuple) and x[0] == "spawn")
        assert "--broadcast-observer" in spawn[1] and "private-admission" not in repr(spawn[1])
        assert spawn[2]["ACTOR_OBSERVER_ADMISSION"] == "private-admission" and "TWITCH_ACCESS_TOKEN" not in spawn[2]
        assert spawn[2]["ACTOR_BRIDGE_TOKEN"] != "old-session-must-not-enter"
        assert spawn[2]["ACTOR_BROADCAST_PROBE_PNG"] == str(run.directory / "native-presented.png")
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
