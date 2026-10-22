"""In-memory guardian composition only: no sockets, processes or accounts."""
import asyncio
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace as Obj
import time

import pytest

from actor_broadcast.guardian_client import (GuardianClient, GuardianAdmission, GuardianLease,
    GuardianObsController, GuardianError, ReadOnlyObsClient)
from actor_broadcast.guardian_services import GuardianServices, GuardianServiceError
from actor_broadcast.obs import ObsController, ObsClient, ObsConnection, ObsError, GraphAudit, PublicStartPermit, WindowBinding
from actor_broadcast.supervisor import GuardianHost


class MemoryTransport:
    def __init__(self): self.queue = asyncio.Queue(); self.peer = None
    async def send(self, value): await self.peer.queue.put(value)
    async def receive(self):
        value = await self.queue.get()
        if value is None: raise EOFError
        return value
    async def aclose(self): await self.peer.queue.put(None)


def transport_pair():
    a, b = MemoryTransport(), MemoryTransport(); a.peer = b; b.peer = a
    return a, b


class Group:
    pid = 321
    closed = False
    def members(self): return (self.pid,)
    async def terminate(self): self.closed = True; return True


class Lease:
    def __init__(self, channel): self.held = False
    def acquire(self): self.held = True; return self
    def release(self): self.held = False


def config(tmp_path):
    return Obj(channel="fedorr_codex", state_directory=tmp_path, firewall_socket=tmp_path/"pf",
        obs_endpoint="ws://127.0.0.1:4455", operator_forward_url="http://127.0.0.1:56251",
        game_version="0.10.0", admission_protocol="4")


class FakeOutput:
    OWNED_NAME=ObsController.OWNED_NAME
    SCENES=ObsController.SCENES
    VOICE,VIDEO=ObsController.VOICE,ObsController.VIDEO
    def __init__(self, client, **kwargs):
        self.client = client; self.guards = kwargs; self._owned_stream = self._record_owned = False
        self._stream_confirmed = False; self._startup_pending = True; self.route_verified = False
        self._stream_stopping=False
        self._prepared=False;self._owned_inputs={};self._creating_inputs=set();self._record_directory_original=None
        self._capture_shape = None; self.prepares = []
    async def prepare(self, binding, renderer_url, **options):
        assert self.guards["window_guard"](binding)
        self._prepared=True
        self.prepares.append((binding, renderer_url)); return GraphAudit(True, 10, "ok")
    async def complete_startup(self):
        self._startup_pending = False; self.route_verified = True; return GraphAudit(True, 10, "ok")
    async def audit(self): return GraphAudit(self.route_verified, 10, "ok")
    async def require_idle(self):
        if self._owned_stream or self._record_owned: raise ObsError("obs_outputs_not_idle")
    async def show(self, scene): pass
    async def check_stream_health(self): pass
    async def verify_channel(self,login): return True
    async def start_stream(self, permit):
        assert permit.explicit_public_start
        self._owned_stream = self._stream_confirmed = True
    async def stop_stream(self): self._owned_stream = self._stream_confirmed = False
    async def start_record(self, directory): self._record_owned = True; return {}
    async def stop_record(self): self._record_owned = False; return {"outputPath": "private"}
    async def emergency_stop(self): await self.stop_stream(); await self.stop_record()
    async def restore_record_directory(self): pass
    async def cleanup_owned_inputs(self): pass


class FakeClient:
    on_event = None
    def __init__(self, *args): self.calls = []; self.closed = False
    async def connect(self): self.calls.append("connect")
    async def close(self): self.closed = True
    async def request(self, kind, data=None, **kwargs):
        if not kind.startswith("Get") and getattr(self,"mutation_guard",None) is not None and not self.mutation_guard(kind,data or {}):
            raise ObsError("obs_guardian_ownership_lost")
        self.calls.append(kind)
        if kind=="GetSceneCollectionList":return {"currentSceneCollectionName":"ACTOR Broadcast"}
        if kind=="GetProfileList":return {"currentProfileName":"ACTOR Broadcast","profiles":["ACTOR Broadcast"]}
        if kind=="GetSceneList":return {"scenes":[{"sceneName":scene,"sceneUuid":str(index+1)*32} for index,scene in enumerate(ObsController.SCENES)]}
        return {}
    async def stream_service_fingerprint(self): return "a"*64


def services(tmp_path, **options):
    return GuardianServices(config(tmp_path), Obj(get=lambda _: "owned-secret"),
        obs_client=FakeClient, obs_controller=FakeOutput,
        attestor=lambda _: Obj(refresh=async_true, verified=True),
        window=lambda *a, **k: Obj(verify=lambda _: True), group_check=lambda _: 321, **options)


async def async_true(*args): return True


async def test_real_host_client_services_repeated_preview_end_uses_parent_mutations_and_one_lease(tmp_path):
    backend = services(tmp_path); group = Group()
    host = GuardianHost("fedorr_codex", group, backend, lease_factory=Lease)
    a, b = transport_pair(); owner = asyncio.create_task(host.serve(a))
    client = GuardianClient(b, controller_pid=321)
    await client.start()
    for scope in ("a"*32, "b"*32):
        await client.begin_run("ABC123", scope)
        lease = GuardianLease(client).acquire()
        with pytest.raises(GuardianError, match="cleanup_pending"): lease.release()
        assert host.lease.held
        read = FakeClient(); unsafe = []
        obs = GuardianObsController(client, read, window_guard=lambda _: True,
            channel_guard=async_true, on_unsafe=unsafe.append)
        assert (await obs.prepare(WindowBinding(8, 123, 800, 664), "private-renderer-cap",
            native_size=(800,600))).passed
        assert backend.obs.prepares[0][0].owner_pid == 123
        await obs.complete_startup()
        assert obs.route_verified
        await obs.start_stream(PublicStartPermit("fedorr_codex", time.monotonic()+30, True))
        assert backend.obs._owned_stream and obs._owned_stream
        await obs.emergency_stop(); await obs.cleanup_owned_inputs()
        assert not read.calls  # Read connection did not mutate OBS.
        await client.end_run(); lease.release()
        assert host.lease is None and not lease.held and not client._callbacks
    await client.aclose()
    assert await asyncio.wait_for(owner, 1)
    assert host.closed and group.closed


async def test_pending_http_post_survives_caller_cancel_and_delete_waits_for_actual_settlement(tmp_path):
    gate = asyncio.Event(); started = asyncio.Event(); calls = []
    class Admission:
        def __init__(self, *args, **kwargs): pass
        async def observe(self, *args):
            calls.append("post"); started.set(); await gate.wait()
            return Obj(child_environment_value=lambda: "{}")
        async def release(self, *args): calls.append("delete")
    backend = services(tmp_path, admission=Admission)
    await backend.begin_run("ABC123", "a"*32)
    task = asyncio.create_task(backend.call("observe", {}))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    cleanup = asyncio.create_task(backend.cleanup_run())
    await asyncio.sleep(0)
    assert backend._post is not None and not backend._post.cancelled() and "delete" not in calls
    gate.set(); assert await cleanup is True
    assert calls == ["post", "delete"] and backend.session is None


async def test_normal_end_cannot_release_lease_while_any_owned_group_child_remains(tmp_path):
    backend = services(tmp_path); group = Group(); members = [321, 654]
    group.members = lambda: tuple(members)
    host = GuardianHost("fedorr_codex", group, backend, lease_factory=Lease)
    await host.begin_run("ABC123", "a"*32)
    with pytest.raises(GuardianServiceError, match="cleanup_pending"): await host.end_run()
    assert host.lease.held and backend.session == "a"*32
    members.remove(654)
    assert await host.end_run() is True
    assert host.lease is None and backend.session is None


async def test_unknown_group_query_never_becomes_clean_or_releases_run(tmp_path):
    backend = services(tmp_path); group = Group()
    def unknown(): raise OSError("not a proof")
    group.members = unknown
    host = GuardianHost("fedorr_codex", group, backend, lease_factory=Lease)
    await host.begin_run("ABC123", "a"*32)
    with pytest.raises(GuardianServiceError): await host.end_run()
    assert host.lease.held and backend.session == "a"*32


async def test_cancelled_begin_retains_accepted_lease_until_late_reply_drain_and_second_preview(tmp_path):
    backend=services(tmp_path);group=Group();started=asyncio.Event();gate=asyncio.Event()
    original=backend.begin_run
    async def begin(room,scope):
        await original(room,scope);started.set();await gate.wait()
    backend.begin_run=begin
    host=GuardianHost("fedorr_codex",group,backend,lease_factory=Lease)
    a,b=transport_pair();owner=asyncio.create_task(host.serve(a));client=GuardianClient(b,controller_pid=321)
    await client.start()
    task=asyncio.create_task(client.begin_run("ABC123","a"*32))
    await started.wait();assert host.lease.held
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert client.run_id=="a"*32
    cleanup=asyncio.create_task(client.end_run());await asyncio.sleep(0)
    assert not cleanup.done() and host.lease.held
    gate.set();await cleanup
    assert client.run_id is None and host.lease is None
    await client.begin_run("ABC123","b"*32);await client.end_run()
    await client.aclose();assert await owner


async def test_startup_activation_event_requires_parent_quiet_proof_but_sealed_fence_never_heals(tmp_path):
    backend=services(tmp_path);host=GuardianHost("fedorr_codex",Group(),backend,lease_factory=Lease)
    a,b=transport_pair();owner=asyncio.create_task(host.serve(a));client=GuardianClient(b,controller_pid=321)
    await client.start();await client.begin_run("ABC123","a"*32)
    read=FakeClient();proxy=GuardianObsController(client,read,window_guard=lambda _:True,channel_guard=async_true)
    await proxy.prepare(WindowBinding(8,123,800,664),"private-renderer",native_size=(800,600))
    async def materialize(scene):
        read.on_event("SceneItemTransformChanged",{})
        backend.obs.route_verified=True  # Parent returns its full quiet startup proof.
    backend.obs.show=materialize
    await proxy.show("ACTOR Match")
    assert proxy.route_verified  # Allows the real route probe before final readiness.
    await proxy.complete_startup()
    await proxy.show("ACTOR Match")
    assert not proxy.route_verified  # Same event after sealing is terminal.
    await proxy.emergency_stop();await proxy.cleanup_owned_inputs();await client.end_run()
    await client.aclose();assert await owner


async def test_profile_takeover_preserves_unknown_output_and_never_mutates_foreign_graph(tmp_path):
    backend=services(tmp_path);await backend.begin_run("ABC123","a"*32)
    await backend.call("connect_output",{})
    backend.obs._prepared=True;backend.obs._startup_pending=False
    backend.obs._owned_stream=backend.obs._stream_confirmed=True
    backend._watch_output_event("CurrentProfileChanged",{})
    backend.obs_client.calls.clear()
    with pytest.raises(GuardianServiceError,match="ownership_unknown"):
        await backend.call("emergency_output",{})
    assert backend.obs_client.calls==[] and backend.obs._owned_stream
    with pytest.raises(GuardianServiceError,match="cleanup_pending"):await backend.cleanup_run()
    assert backend.obs is not None and backend.session=="a"*32


async def test_unexpected_stop_then_foreign_start_is_not_stopped_by_old_intent(tmp_path):
    backend=services(tmp_path);await backend.begin_run("ABC123","a"*32)
    await backend.call("connect_output",{})
    backend.obs._prepared=True;backend.obs._startup_pending=False
    backend.obs._owned_stream=backend.obs._stream_confirmed=True
    backend._watch_output_event("StreamStateChanged",{"outputActive":False})
    original=backend.obs_client.request
    async def request(name,*a,**k):
        if name=="GetStreamStatus":return {"outputActive":True}
        return await original(name,*a,**k)
    backend.obs_client.request=request
    with pytest.raises(GuardianServiceError,match="ownership_unknown"):await backend.call("stop_stream",{})
    assert backend.obs._owned_stream
    backend._stream_identity_lost=False;backend.obs._stream_stopping=True
    backend._watch_output_event("StreamStateChanged",{"outputActive":False})
    assert not backend._stream_identity_lost  # Intentional Stop event is not takeover.


async def test_output_cleanup_error_still_releases_exact_admission_but_preserves_owner_and_forward(tmp_path):
    calls = []
    forward = Obj(start=async_true, aclose=lambda: async_true())
    backend = services(tmp_path, forward=forward)
    await backend.begin_run("ABC123", "a"*32)
    backend.obs = FakeOutput(FakeClient()); backend.obs._owned_stream = True
    async def stuck(): raise ObsError("obs_stop_unconfirmed")
    backend.obs.emergency_stop = stuck
    backend._release = True
    backend.admission = Obj(release=lambda *a: record(calls, "delete"))
    with pytest.raises(GuardianServiceError, match="cleanup_pending"): await backend.cleanup_run()
    assert calls == ["delete"] and backend.session == "a"*32 and backend.obs._owned_stream
    assert backend._forward_started


async def record(calls, value): calls.append(value)


@pytest.mark.parametrize("op,args", [("StartStream", {}), ("start_stream", {"command":"shell"}),
    ("show_output", {"scene":"foreign"}), ("start_record", {"directory":True}),
    ("prepare_output", {"binding":{}})])
async def test_mutation_vocabulary_is_closed_before_native_io(tmp_path, op, args):
    backend = services(tmp_path)
    with pytest.raises((GuardianServiceError, ObsError)): await backend.call(op, args)
    assert backend.obs_client is None and backend.admission is None


async def test_controller_image_connection_rejects_all_mutating_and_secret_requests():
    underlying = FakeClient(); read = ReadOnlyObsClient(underlying)
    for name in ("StartStream", "StopStream", "StartRecord", "SetRecordDirectory", "GetOutputSettings", "GetStreamServiceSettings"):
        with pytest.raises(ObsError, match="guardian_mutation_required"): await read.request(name)
    await read.request("GetSourceScreenshot", {"imageWidth":800})
    assert underlying.calls == ["GetSourceScreenshot"]


def test_group_inheritance_requires_verified_live_in_memory_context(monkeypatch):
    from actor_broadcast import guardian_client as module
    monkeypatch.setenv("ACTOR_BROADCAST_GUARDIAN_FD", "99")
    monkeypatch.setattr(module, "_context", None)
    assert module.inherited_group_options() == {"start_new_session":True}
    client = GuardianClient(None, controller_pid=123)
    monkeypatch.setattr(module, "_context", client)
    monkeypatch.setattr(module.os, "getpid", lambda:123)
    monkeypatch.setattr(module.os, "getpgrp", lambda:123)
    assert module.inherited_group_options() == {"start_new_session":False}
    client._terminal = "guardian_connection_lost"
    with pytest.raises(GuardianError): module.inherited_group_options()


async def test_client_close_failure_still_closes_transport_and_retains_terminal_spawn_fence(monkeypatch):
    from actor_broadcast import guardian_client as module
    transport = Obj(aclose=async_true)
    client = GuardianClient(transport, controller_pid=123)
    async def reject(*a, **k): raise GuardianError("guardian_operation_failed")
    client._request = reject
    monkeypatch.setattr(module, "_context", client)
    with pytest.raises(GuardianError): await client.aclose()
    assert client._closed and module._context is client
    with pytest.raises(GuardianError): module.inherited_group_options()


class RecordBackend(FakeClient):
    def __init__(self):
        super().__init__(); self.active = False; self.start_gate = asyncio.Event(); self.rejected = False
        self.directory = "original"
    async def request(self, name, data=None, **kwargs):
        self.calls.append(name)
        if name == "GetRecordDirectory": return {"recordDirectory":self.directory}
        if name == "SetRecordDirectory": self.directory = data["recordDirectory"]; return {}
        if name == "StartRecord":
            await self.start_gate.wait()
            if self.rejected: raise ObsError("obs_request_rejected",request_status_code=500)
            self.active = True; return {}
        if name == "StopRecord": self.active = False; return {"outputPath":"private-owned.mov"}
        if name == "GetRecordStatus": return {"outputActive":self.active}
        return {"outputActive":False}


@pytest.mark.parametrize("reject",[False,True])
async def test_real_obs_record_retains_start_task_and_never_stops_rejected_foreign_record(reject):
    backend = RecordBackend(); backend.rejected = reject
    controller = ObsController(backend, window_guard=lambda _:True, channel_guard=async_true)
    controller._prepared = controller._graph_ok = True
    controller._expected_scene = "ACTOR Match"; controller._last_audit_at = time.monotonic()
    controller.require_idle = async_true
    task = asyncio.create_task(controller.start_record(Path("/private/fake")))
    for _ in range(4): await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert controller._record_owned and controller._record_start_task is not None
    backend.start_gate.set(); await asyncio.sleep(0); await asyncio.sleep(0)
    if reject: backend.active = True  # A different recording won the race.
    await controller.stop_record()
    assert ("StopRecord" in backend.calls) is (not reject)
    if not reject:
        await controller.restore_record_directory()
        assert backend.directory == "original"


@pytest.mark.parametrize("receipt_failure",[False,True])
async def test_cli_guardian_registers_isolated_controller_and_keeps_secrets_out_of_child_bootstrap(tmp_path, monkeypatch,receipt_failure):
    from actor_broadcast import cli, guardian_services, native, secrets, supervisor
    events = []; processes = []; signals = {}
    class Socket:
        def fileno(self): return 91
        def close(self): events.append("fd-close")
    monkeypatch.setattr(cli.socket, "socketpair", lambda:(Socket(),Socket()))
    def spawn(command, **options):
        events.append("spawn")
        assert command[1:3] == ["-m","actor_broadcast.guardian_entry"]
        assert options["start_new_session"] is True and options["pass_fds"] == (91,)
        assert not any("private-token" in str(x) for x in command)
        assert options["env"]["ACTOR_BROADCAST_GUARDIAN_FD"] == "91"
        assert "ACTOR_BRIDGE_TOKEN" not in options["env"] and "TWITCH_TOKEN" not in options["env"]
        process = Obj(pid=321,returncode=None); processes.append(process); return process
    monkeypatch.setenv("ACTOR_BRIDGE_TOKEN","private-token")
    monkeypatch.setenv("TWITCH_TOKEN","private-token")
    monkeypatch.setattr(cli.subprocess,"Popen",spawn)
    monkeypatch.setattr(cli,"process_identity",lambda pid:{"pid":pid,"started":"fake-start","command_sha256":"a"*64})
    if receipt_failure:monkeypatch.setattr(cli,"_write_guardian_state",lambda *args:False)
    class Services:
        cleanup_error_codes = {"output":"obs_owned_stream_start_pending"}
        def __init__(self,*a,**k): pass
        async def start_forward(self): events.append("forward")
        async def close(self): events.append("services-close")
    class Retained:
        def __init__(self,process,**k):
            assert process is processes[0]; events.append("registered"); self.closed=False
        async def terminate(self): self.closed=True; return True
    class Host:
        def __init__(self,channel,group,services):
            self.group=group; self.services=services; self.closed=False; self.reason="supervisor_shutdown"
            self.terminal=False;self.cleanup_failures=()
        def fail(self,*a): pass
        async def serve(self,transport):
            assert events.index("registered") > events.index("spawn")
            await self.group.terminate(); await self.services.close(); self.closed=True; return True
    monkeypatch.setattr(guardian_services,"GuardianServices",Services)
    monkeypatch.setattr(secrets,"MacOSKeychainSecretStore",lambda:object())
    monkeypatch.setattr(supervisor,"RetainedControllerGroup",Retained)
    monkeypatch.setattr(supervisor,"GuardianHost",Host)
    monkeypatch.setattr(supervisor,"FramedSocket",lambda _:Obj(aclose=async_true))
    loop=asyncio.get_running_loop()
    monkeypatch.setattr(loop,"add_signal_handler",lambda number,callback:signals.__setitem__(number,callback))
    monkeypatch.setattr(loop,"remove_signal_handler",lambda number:signals.pop(number))
    args=Obj(output=tmp_path/"run",config=tmp_path/"config.json",port=0,broadcast_action="ui")
    installation=Obj(state_directory=tmp_path,ssh=None,channel="fedorr_codex")
    if receipt_failure:
        with pytest.raises(cli.CoordinatorError,match="guardian_receipt_failed"):await cli._supervised_serve(args,installation)
    else:
        assert await cli._supervised_serve(args,installation)==0
        import json
        report=json.loads((args.output/"guardian.json").read_text())
        assert report["closed"]is True and report["identity_complete"]is True
        assert report["cleanup_error_codes"]=={"output":"obs_owned_stream_start_pending"}
        assert (args.output/"guardian.json").stat().st_mode&0o077==0
    assert events.index("forward")<events.index("spawn")<events.index("registered")
    assert not signals


@pytest.mark.parametrize("stage",["group","transport"])
def test_independent_guardian_receipt_reports_pending_owner_without_child_or_secret_store(tmp_path,monkeypatch,capsys,stage):
    from actor_broadcast import cli,secrets
    guardian={"pid":321,"started":"fake","command_sha256":"a"*64}
    value={"schema":"actor.broadcast-guardian.v1","owner_uid":cli.os.geteuid(),"processes":{"guardian":guardian,"controller":None},
        "closed":False,"lease_held":True,"reason":"supervisor_controller_eof","cleanup_failures":[stage]}
    assert cli._write_guardian_state(tmp_path,value)
    monkeypatch.setattr(cli,"owned_process",lambda identity:identity==guardian)
    monkeypatch.setattr(secrets,"MacOSKeychainSecretStore",lambda:pytest.fail("receipt status read credentials"))
    assert cli._control(Obj(run=tmp_path,broadcast_action="status"))==0
    import json
    result=json.loads(capsys.readouterr().out)
    assert result["guardian"]=={"lease_held":True,"closed":False,"cleanup_failures":[stage]}
    assert "pid"not in str(result)and"command_sha256"not in str(result)


async def test_stale_run_unsafe_notice_cannot_revoke_current_proxy_route():
    a,b=transport_pair(); client=GuardianClient(b,controller_pid=321)
    client.run_id="b"*32; client.output_status={"route_verified":True};client._status_at=client.clock()
    notices=[];client.on_unsafe(notices.append)
    reader=asyncio.create_task(client._receive())
    await a.send({"v":1,"event":"unsafe","run_id":"a"*32,"error":"obs_graph_changed"})
    await asyncio.sleep(0)
    assert client.route_verified and notices==[]
    reader.cancel();await asyncio.gather(reader,return_exceptions=True)


@pytest.mark.parametrize("members,reported,accepted", [([321],1,True),([321,654,655,656],4,True),
    ([321],4096,False),([321],-1,False),([321,321],2,False),([0],1,False),([1],1,False)])
def test_darwin_group_convenience_api_reports_pid_count_not_bytes(monkeypatch,members,reported,accepted):
    from actor_broadcast import guardian_services as module
    class Query:
        def __call__(self,pid,buffer,size):
            assert pid==321 and size==4096*4
            for index,value in enumerate(members):buffer[index]=value
            return reported
    monkeypatch.setattr(module.sys,"platform","darwin")
    monkeypatch.setattr(module.ctypes,"CDLL",lambda *a,**k:Obj(proc_listpgrppids=Query()))
    if accepted: assert module.controller_group_members(321)==tuple(members)
    else:
        with pytest.raises(GuardianServiceError):module.controller_group_members(321)


async def test_real_native_guardian_branch_uses_remote_lease_and_mutations_and_closes_before_end(tmp_path,monkeypatch):
    from test_broadcast_native import Fakes
    from actor_broadcast import guardian_client as module
    from actor_broadcast.native import NativeBroadcastFactory
    from actor_broadcast.config import CommentaryConfig
    fake=Fakes(tmp_path);deps=fake.dependencies();operations=[]
    class Guardian:
        run_id=None;controller_pid=321
        output_status={"route_verified":False,"startup_pending":True}
        def require_alive(self):pass
        def on_unsafe(self,callback):pass
        def remove_unsafe(self,callback):pass
        @property
        def route_verified(self):return self.output_status.get("route_verified") is True
        async def begin_run(self,room,scope):self.run_id=scope;operations.append("begin")
        async def end_run(self):
            assert fake.process.exited and "voice-close"in fake.calls and "model-close"in fake.calls and "bridge-stop"in fake.calls
            operations.append("end");self.run_id=None
        async def call(self,op,args=None):
            operations.append(op)
            if op=="prepare_output":self.output_status={"route_verified":True,"startup_pending":True};return asdict(GraphAudit(True,10,"ok"))
            if op in("complete_output_startup","audit_output"):
                self.output_status["startup_pending"]=False
                return asdict(GraphAudit(True,10,"ok"))
            if op=="cleanup_output":self.output_status={"route_verified":False,"startup_pending":False}
            return True
    guardian=Guardian()
    # Admission transport/DTO validation is tested separately; the Native seam
    # must not read its old CP operator key or acquire a second canonical lease.
    monkeypatch.setattr(module,"GuardianAdmission",lambda _:deps.admission(None,None))
    monkeypatch.setattr(module,"_context",guardian)
    monkeypatch.setattr(module.os,"getpid",lambda:321)
    monkeypatch.setattr(module.os,"getpgrp",lambda:321)
    old_client=deps.obs_client
    def client(config):
        result=old_client(config);result.on_event=None;return result
    deps.obs_client=client
    old_spawn=deps.spawn
    def spawn(*args,**kwargs):
        assert kwargs["start_new_session"]is False
        return old_spawn(*args,**kwargs)
    deps.spawn=spawn
    store=Obj(get=lambda name:pytest.fail("child read admission key")if name=="broadcast-server-operator-key"else{
        "obs-websocket-password":"private-test-password", "obs-service-binding":__import__("json").dumps({"channel":"fedorr_codex",
            "profile":"ACTOR Broadcast","collection":"ACTOR Broadcast","fingerprint":"a"*64})}.get(name))
    factory=NativeBroadcastFactory(fake.config,store,dependencies=deps,guardian=guardian)
    run=await factory.prepare("ABC123","a"*32,CommentaryConfig(),on_ready=lambda _:None,on_unsafe=lambda _:None,on_terminal=lambda _:None)
    assert fake.lease is None and operations[0]=="begin" and "prepare_output"in operations
    await run.close()
    assert run._closed and guardian.run_id is None and operations[-1]=="end"


@pytest.mark.parametrize("takeover_at",["identity_reply","mute_reply"])
async def test_takeover_during_cleanup_is_latched_before_next_wire_mutation(tmp_path,takeover_at):
    backend=services(tmp_path);backend.bind_controller(Group())
    await backend.begin_run("ABC123","a"*32);await backend.call("connect_output",{})
    await backend.call("prepare_output",{"binding":asdict(WindowBinding(8,123,800,664)),"renderer_url":"private",
        "native_size":[800,600],"protected_backup_confirmed":True,"adopt_empty_collection":True})
    await backend.call("complete_output_startup",{})
    backend.obs._owned_stream=backend.obs._stream_confirmed=True
    backend.obs._owner_nonce="owner";backend.obs._owned_inputs={backend.obs.VOICE:"voice-id"}
    backend.obs_client.calls.clear();original=backend.obs_client.request
    async def request(name,*args,**kwargs):
        if name=="GetInputSettings":return {"inputSettings":{"actor_broadcast_owner":"owner"}}
        value=await original(name,*args,**kwargs)
        if (takeover_at=="identity_reply"and name=="GetProfileList"
                or takeover_at=="mute_reply"and name=="SetInputMute"):
            backend.obs_client.on_event("CurrentProfileChanged",{})
        return value
    backend.obs_client.request=request
    with pytest.raises(GuardianServiceError,match="ownership_unknown"):
        await backend.call("emergency_output",{})
    assert backend._output_identity_lost and backend.obs._owned_stream
    assert "SetCurrentProgramScene"not in backend.obs_client.calls and "StopStream"not in backend.obs_client.calls
    assert ("SetInputMute"in backend.obs_client.calls)is(takeover_at=="mute_reply")


async def test_real_obs_wire_lock_rechecks_guard_after_wait_without_sending():
    sent=[];client=ObsClient(ObsConnection("ws://127.0.0.1:4455","private-password",firewall_verified=True))
    async def send(raw):sent.append(raw)
    client._ws=Obj(send=send);allowed=True
    client.mutation_guard=lambda kind,data:allowed
    await client._send_lock.acquire()
    task=asyncio.create_task(client.request("StopStream"))
    await asyncio.sleep(0);allowed=False;client._send_lock.release()
    with pytest.raises(ObsError,match="guardian_ownership_lost"):await task
    assert sent==[] and client._pending=={}
