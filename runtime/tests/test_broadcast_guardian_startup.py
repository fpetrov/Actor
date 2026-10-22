"""Actual proxy/Native probe code with fake IO only, no capture/process/audio."""
import asyncio
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace as Obj

import pytest

from actor_broadcast import native
from actor_broadcast.audio import AudioError, BrowserAudioSink
from actor_broadcast.guardian_client import GuardianError, GuardianObsController
from actor_broadcast.obs import GraphAudit, WindowBinding


class Guardian:
    def __init__(self, read):
        self.read=read;self.callbacks=[];self.fault=None;self.fresh=True;self.operations=[]
        self.output_status={"startup_pending":True,"route_verified":False,"record_owned":False}
    @property
    def route_verified(self):return self.fresh and self.output_status["route_verified"]
    def on_unsafe(self,callback):self.callbacks.append(callback)
    def remove_unsafe(self,callback):self.callbacks.remove(callback)
    async def call(self,op,args=None):
        self.operations.append(op)
        if op=="prepare_output":
            self.read.on_event("InputCreated",{})
            return asdict(GraphAudit(True,10,"ok"))
        if op=="show_output":
            self.output_status["route_verified"]=True
            # Different websocket connection: an earlier owned setup event
            # arrives after the parent's successful quiet audit response.
            asyncio.get_running_loop().call_soon(self.late_event)
        if op=="start_record":self.output_status["record_owned"]=True
        if op=="stop_record":
            self.output_status["record_owned"]=False
            return {"outputPath":str(self.recording)}
        if op=="complete_output_startup":
            self.output_status["startup_pending"]=False
            return asdict(GraphAudit(True,10,"ok"))
        if op=="audit_output":return asdict(GraphAudit(True,10,"ok"))
        return True
    def late_event(self):
        if self.fault=="parent_route":self.output_status["route_verified"]=False
        elif self.fault=="parent_unsafe":
            for callback in self.callbacks:callback("guardian_output_unsafe")
        elif self.fault in {"ConnectionLost","CurrentProfileChanged","CurrentSceneCollectionChanged"}:
            self.read.on_event(self.fault,{})
        else:self.read.on_event("SceneItemTransformChanged",{})


async def scenario(tmp_path):
    read=Obj(on_event=None);guardian=Guardian(read);unsafe=[]
    run=object.__new__(native.PreparedNativeRun)
    run._flags={name:False for name in ("observer","model","tts","obs","audio","chat")}
    run._failure=None;run._closing=False;run.attestor=Obj(verified=True)
    run._failed=asyncio.Event();run._tasks=set();run.on_unsafe=unsafe.append
    proxy=GuardianObsController(guardian,read,window_guard=lambda _:True,channel_guard=None,on_unsafe=run._obs_event)
    run.obs=proxy
    await proxy.prepare(WindowBinding(8,123,800,664),"test-renderer",native_size=(800,600))
    assert run._failure is None and not unsafe
    run.directory=tmp_path.resolve();run.factory=Obj(guardian=guardian)
    run.config=Obj(ffmpeg_executable=Path("/not-executed-fake-decoder"))
    guardian.recording=run.directory/"audio-probe"/"fake-record.mkv"
    class Read:
        async def request(self,name,data=None):
            if name=="GetRecordDirectory":
                await asyncio.sleep(0)  # Deliver the pending second-connection event.
                return {"recordDirectory":"fake-owned-directory"}
            if name=="GetRecordStatus":return {"outputActive":True}
            raise AssertionError("unexpected request")
    run.obs_client=Read()
    return run,guardian,read,unsafe


async def test_actual_native_record_probe_accepts_delayed_duplicate_only_with_parent_route(monkeypatch,tmp_path):
    run,guardian,read,unsafe=await scenario(tmp_path)
    expected=native.probe_signal();seen=[]
    class Sink:
        async def play(self,segment,chunks,*,validate):
            assert run._route_guard()and validate(segment)
            async for item in chunks:
                assert validate(segment)
                if hasattr(item,"pcm"):seen.append(item.pcm)
            guardian.recording.write_bytes(b"authored fake recording")
            return Obj(status="completed",consumed_source_frames=38400)
    class Decoder:
        returncode=0
        def communicate(self,**kwargs):return expected,None
    run.sink=Sink()
    monkeypatch.setattr(native.subprocess,"Popen",lambda *a,**k:Decoder())
    async def video(*args):return {"passed":True,"scope":"fake IO only"}
    monkeypatch.setattr(native,"video_probe_measurement",video)
    # No child/group state change is performed by this entirely fake decoder.
    from actor_broadcast import guardian_client
    monkeypatch.setattr(guardian_client,"inherited_group_options",lambda:{})
    result=await native.record_audio_route(run)
    assert result["passed"]and b"".join(seen)==expected
    assert run._failure is None and not unsafe and not run.obs._invalid
    assert not guardian.output_status["record_owned"]
    assert run.obs._startup_pending  # Probe alone cannot seal readiness.
    assert (await run.obs.complete_startup()).passed and not run.obs._startup_pending
    run._flags["obs"]=True
    read.on_event("SceneItemTransformChanged",{})
    assert run._failure=="obs_graph_changed"and unsafe==["obs_graph_changed"]
    await asyncio.gather(*run._tasks)


@pytest.mark.parametrize("fault",["parent_route","parent_unsafe","ConnectionLost","CurrentProfileChanged","CurrentSceneCollectionChanged"])
async def test_startup_duplicate_policy_never_heals_parent_or_connection_identity_failure(tmp_path,fault):
    run,guardian,read,unsafe=await scenario(tmp_path);guardian.fault=fault
    sink=run.sink=BrowserAudioSink(route_guard=run._route_guard);sink._ready.set()
    with pytest.raises(AudioError,match="audio_obs_route_not_verified"):
        await native.record_audio_route(run)
    assert not run._route_guard()and not guardian.output_status["record_owned"]
    # Parent attempts/proofs can never heal a hard local fence.
    guardian.fault=None;guardian.output_status["route_verified"]=True
    await run.obs.prepare(WindowBinding(8,123,800,664),"test-renderer",native_size=(800,600))
    await run.obs.show("ACTOR Match");await asyncio.sleep(0);await run.obs.audit()
    if fault=="parent_route":
        assert (await run.obs.complete_startup()).passed
    else:
        assert not run.obs.route_verified
        with pytest.raises(GuardianError,match="guardian_startup_seal_unverified"):
            await run.obs.complete_startup()


@pytest.mark.parametrize("missing",["stale","pending","no_route"])
async def test_successful_audit_alone_cannot_seal_local_startup(tmp_path,missing):
    run,guardian,read,unsafe=await scenario(tmp_path)
    guardian.output_status["route_verified"]=True
    original=guardian.call
    async def call(op,args=None):
        result=await original(op,args)
        if op=="complete_output_startup":
            if missing=="stale":guardian.fresh=False
            if missing=="pending":guardian.output_status["startup_pending"]=True
            if missing=="no_route":guardian.output_status["route_verified"]=False
        return result
    guardian.call=call
    with pytest.raises(GuardianError,match="guardian_startup_seal_unverified"):
        await run.obs.complete_startup()
    assert run.obs._startup_pending


@pytest.mark.parametrize("event",["InputCreated","SceneItemCreated","SceneItemTransformChanged"])
async def test_all_existing_graph_fences_remain_permanent_after_actual_seal(tmp_path,event):
    run,guardian,read,unsafe=await scenario(tmp_path)
    guardian.output_status["route_verified"]=True
    await run.obs.complete_startup();run._flags["obs"]=True
    read.on_event(event,{})
    assert run._failure=="obs_graph_changed"and not run.obs.route_verified
    await run.obs.audit()
    with pytest.raises(GuardianError):await run.obs.complete_startup()
    assert run.obs._invalid
    await asyncio.gather(*run._tasks)
