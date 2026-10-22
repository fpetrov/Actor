"""OBS protocol/source-graph fakes only; never connects to a running OBS."""
import asyncio
import base64
import copy
import hashlib
import json
import time

import pytest

from actor_broadcast.obs import (ObsClient, ObsConnection, ObsController, ObsError,
    PublicStartPermit, WindowBinding, authentication)
from uuid import uuid4


URL = "http://127.0.0.1:12345/renderer/#cap=" + "a" * 43


class Graph:
    on_event = None
    def __init__(self):
        self.calls = []; self.active = False; self.filters = {}; self.special = {}; self.foreign = False
        self.current_scene = "ACTOR Match"
        self.transform = {"positionX": 0, "positionY": 0, "rotation": 0, "alignment": 5,
            "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0, "boundsWidth": 1280,
            "boundsHeight": 720, "cropTop": 0, "cropBottom": 0, "cropLeft": 0, "cropRight": 0,
            "sourceWidth": 800, "sourceHeight": 600}
        self.inputs = [dict(inputName=ObsController.VIDEO, inputKind="screen_capture"),
                       dict(inputName=ObsController.VOICE, inputKind="browser_source")]
        self.settings = {ObsController.VIDEO: {"type": 1, "window": 123}, ObsController.VOICE: {
            "url": URL, "is_local_file": False, "reroute_audio": True, "webpage_control_level": 0}}
        self.tracks = {ObsController.VIDEO: {str(i): False for i in range(1, 7)},
                       ObsController.VOICE: {str(i): i == 1 for i in range(1, 7)}}
        self.items = {"ACTOR Preparing": [], "ACTOR Safe": [], "ACTOR Ending": [self.item(ObsController.VOICE)],
                      "ACTOR Match": [self.item(ObsController.VIDEO), self.item(ObsController.VOICE)]}
    @staticmethod
    def item(name): return {"sourceName": name, "sceneItemId": 1, "sceneItemEnabled": True, "isGroup": False}
    async def request(self, kind, data=None, **kwargs):
        data = data or {}; self.calls.append((kind, data)); name = data.get("inputName")
        if kind.endswith("Status"): return {"outputActive": self.active or self.foreign, "outputReconnecting": False}
        if kind == "GetSceneCollectionList": return {"currentSceneCollectionName": "ACTOR Broadcast", "sceneCollections": ["ACTOR Broadcast"]}
        if kind == "GetProfileList": return {"currentProfileName": "ACTOR Broadcast", "profiles": [{"profileName": "ACTOR Broadcast"}]}
        if kind == "GetSpecialInputs": return self.special
        if kind == "GetInputList": return {"inputs": self.inputs}
        if kind == "GetInputSettings":
            if name is None: name = next(x["inputName"] for x in self.inputs if x.get("inputUuid") == data.get("inputUuid"))
            return {"inputKind": next(x["inputKind"] for x in self.inputs if x["inputName"] == name), "inputSettings": self.settings[name]}
        if kind == "GetInputMute": return {"inputMuted": name == ObsController.VIDEO}
        if kind == "GetInputVolume": return {"inputVolumeMul": 1.0}
        if kind == "GetInputAudioTracks": return {"inputAudioTracks": self.tracks[name]}
        if kind == "GetInputAudioMonitorType": return {"monitorType": "OBS_MONITORING_TYPE_NONE"}
        if kind == "GetSourceFilterList": return {"filters": self.filters.get(data["sourceName"], [])}
        if kind == "GetSceneList": return {"scenes": [{"sceneName": x} for x in self.items]}
        if kind == "GetSceneItemList": return {"sceneItems": self.items[data["sceneName"]]}
        if kind == "GetCurrentProgramScene": return {"currentProgramSceneName": self.current_scene}
        if kind == "GetSceneItemTransform": return {"sceneItemTransform": self.transform}
        if kind == "SetCurrentProgramScene": self.current_scene = data["sceneName"]
        if kind == "CreateInput":
            identity = str(uuid4()); self.inputs.append({"inputName": name, "inputKind": data["inputKind"], "inputUuid": identity})
            self.settings[name] = data["inputSettings"]; self.items[data["sceneName"]].append(self.item(name))
            return {"inputUuid": identity, "sceneItemId": 1}
        if kind == "CreateSceneItem": self.items[data["sceneName"]].append(self.item(data["sourceName"]))
        if kind == "RemoveInput":
            target = next(x for x in self.inputs if x.get("inputUuid") == data["inputUuid"])
            self.inputs.remove(target)
            for key in self.items: self.items[key] = [x for x in self.items[key] if x["sourceName"] != target["inputName"]]
        if kind == "StartStream": self.active = True
        if kind == "StopStream": self.active = False
        return {}
    async def close(self): pass


def controller(graph=None):
    graph = graph or Graph(); c = ObsController(graph, window_guard=lambda w: w == WindowBinding(123, 456), channel_guard=lambda login: login == "fedorr_codex")
    c._prepared = True; c._binding = WindowBinding(123, 456); c._renderer_url = URL
    c._expected_scene = "ACTOR Match"
    return c, graph


def test_endpoint_requires_listener_isolation_and_password_is_not_repr():
    with pytest.raises(ObsError, match="isolation"): ObsConnection(password="secret-secret")
    for endpoint in ("ws://0.0.0.0:4455", "ws://localhost:4455", "ws://127.0.0.1:4455/?x=1", "ws://u:p@127.0.0.1:4455"):
        with pytest.raises(ObsError): ObsConnection(endpoint, "secret-secret", True)
    assert "secret-secret" not in repr(ObsConnection(password="secret-secret", local_listener_verified=True))
    expected = base64.b64encode(hashlib.sha256((base64.b64encode(hashlib.sha256(b"secretsecretsalt").digest()).decode() + "challenge").encode()).digest()).decode()
    assert authentication("secretsecret", "salt", "challenge") == expected


async def test_exact_window_browser_only_graph_accepts_and_public_start_is_separate():
    c, graph = controller(); result = await c.audit()
    assert result.passed and c.route_verified
    assert not any(x[0] == "StartStream" for x in graph.calls)
    with pytest.raises(ObsError, match="not_authorized"):
        await c.start_stream(PublicStartPermit("fedorr_codex", time.monotonic() + 30, False))
    await c.start_stream(PublicStartPermit("fedorr_codex", time.monotonic() + 30, True))
    assert c._owned_stream and graph.active
    await c.stop_stream(); assert not graph.active


async def test_normal_poll_preserves_fresh_route_but_drift_or_expiry_revokes_immediately():
    c, graph = controller(); assert (await c.audit()).passed
    entered, proceed = asyncio.Event(), asyncio.Event(); request = graph.request
    async def delayed(kind, *args, **kwargs):
        if kind == "GetSceneCollectionList": entered.set(); await proceed.wait()
        return await request(kind, *args, **kwargs)
    graph.request = delayed
    audit = asyncio.create_task(c.audit()); await entered.wait()
    assert c.route_verified
    c._last_audit_at -= 3; assert not c.route_verified
    proceed.set(); assert (await audit).passed and c.route_verified
    c._event("SceneNameChanged", {})
    assert not c.route_verified


async def test_monitor_waits_for_complete_owned_program_transition():
    c, graph = controller(); assert (await c.audit()).passed
    entered, proceed = asyncio.Event(), asyncio.Event(); request = graph.request
    async def delayed(kind, *args, **kwargs):
        if kind == "SetCurrentProgramScene": entered.set(); await proceed.wait()
        return await request(kind, *args, **kwargs)
    graph.request = delayed
    show = asyncio.create_task(c.show("ACTOR Ending")); await entered.wait()
    poll = asyncio.create_task(c.audit()); await asyncio.sleep(0)
    assert not poll.done()
    proceed.set(); await show
    assert (await poll).passed and c.route_verified


@pytest.mark.parametrize("mutation", [
    lambda g: g.special.update(desktop1="system-audio"),
    lambda g: g.inputs.append({"inputName": "Hidden microphone", "inputKind": "coreaudio_input_capture"}),
    lambda g: g.settings[ObsController.VIDEO].update(type=0),
    lambda g: g.settings[ObsController.VIDEO].update(window=999),
    lambda g: g.tracks[ObsController.VIDEO].update({"2": True}),
    lambda g: g.settings[ObsController.VOICE].update(webpage_control_level=True),
    lambda g: g.settings[ObsController.VOICE].update(url="https://foreign.invalid"),
    lambda g: g.filters.update({ObsController.VOICE: [{"filterName": "unreviewed"}]}),
    lambda g: g.filters.update({"ACTOR Match": [{"filterName": "nested output"}]}),
    lambda g: g.items["ACTOR Match"].append(Graph.item("Hidden Scene")),
    lambda g: g.transform.update(cropTop=20),
    lambda g: g.transform.update(rotation=90),
])
async def test_source_graph_privacy_negatives_fail_closed(mutation):
    c, graph = controller(); mutation(graph)
    assert not (await c.audit()).passed and not c.route_verified


async def test_existing_output_never_stopped_or_adopted_by_display_name():
    c, graph = controller(); graph.foreign = True
    with pytest.raises(ObsError, match="foreign_output"):
        await c.start_stream(PublicStartPermit("fedorr_codex", time.monotonic() + 30, True))
    await c.stop_stream()
    assert not any(x[0] in ("StartStream", "StopStream") for x in graph.calls)


async def test_adopting_root_created_collection_requires_explicit_empty_proof():
    graph = Graph(); c = ObsController(graph, window_guard=lambda _: True, channel_guard=lambda _: True)
    with pytest.raises(ObsError, match="explicit_recovery"):
        await c.prepare(WindowBinding(123, 456), URL, protected_backup_confirmed=True)
    with pytest.raises(ObsError, match="not_empty"):
        await c.prepare(WindowBinding(123, 456), URL, protected_backup_confirmed=True, adopt_empty_collection=True)
    assert not any(x[0].startswith("Create") for x in graph.calls)


async def test_graph_mutation_invalidates_audio_guard_immediately():
    c, graph = controller(); assert (await c.audit()).passed
    for event in ("SourceFilterCreated", "InputAudioTracksChanged", "ConnectionLost"):
        c._graph_ok = True; c._event(event, {})
        assert not c.route_verified


async def test_program_scene_switch_to_silent_safe_invalidates_playback_route():
    c, graph = controller(); assert (await c.audit()).passed
    graph.current_scene = "ACTOR Safe"
    c._event("CurrentProgramSceneChanged", {"sceneName": "ACTOR Safe"})
    assert not c.route_verified and not (await c.audit()).passed


async def test_preview_stop_preview_removes_only_exact_created_input_uuids():
    graph = Graph(); graph.inputs = []; graph.settings = {}; graph.current_scene = "ACTOR Preparing"
    graph.items = {key: [] for key in ObsController.SCENES}
    first = ObsController(graph, window_guard=lambda _: True, channel_guard=lambda _: True)
    assert (await first.prepare(WindowBinding(123, 456), URL, protected_backup_confirmed=True, adopt_empty_collection=True)).passed
    identities = set(first._owned_inputs.values()); assert len(identities) == 2
    await first.close()
    removed = [data for kind, data in graph.calls if kind == "RemoveInput"]
    assert {row["inputUuid"] for row in removed} == identities
    assert all(set(row) == {"inputUuid"} for row in removed) and not graph.inputs
    second = ObsController(graph, window_guard=lambda _: True, channel_guard=lambda _: True)
    url = URL.replace("cap=" + "a"*43, "cap=" + "b"*43)
    assert (await second.prepare(WindowBinding(123, 456), url, protected_backup_confirmed=True, adopt_empty_collection=True)).passed
    assert identities.isdisjoint(second._owned_inputs.values())
    await second.close(); assert not graph.inputs and all(not values for values in graph.items.values())


async def test_same_name_foreign_uuid_is_never_removed_during_cleanup():
    graph = Graph(); graph.inputs = []; graph.settings = {}; graph.current_scene = "ACTOR Preparing"
    graph.items = {key: [] for key in ObsController.SCENES}
    c = ObsController(graph, window_guard=lambda _: True, channel_guard=lambda _: True)
    await c.prepare(WindowBinding(123, 456), URL, protected_backup_confirmed=True, adopt_empty_collection=True)
    foreign = graph.inputs[0]; foreign["inputUuid"] = str(uuid4())
    with pytest.raises(ObsError, match="foreign_input"): await c.cleanup_owned_inputs()
    assert foreign in graph.inputs
    assert not any(kind == "RemoveInput" and data.get("inputUuid") == foreign["inputUuid"] for kind, data in graph.calls)


async def test_removed_voice_does_not_prevent_owned_emergency_output_stop():
    class RemovedVoice(Graph):
        async def request(self, kind, data=None, **kwargs):
            if kind == "SetInputMute": raise ObsError("obs_request_rejected")
            return await super().request(kind, data, **kwargs)
    c, graph = controller(RemovedVoice()); c._owned_stream = True; graph.active = True
    with pytest.raises(ObsError, match="route_cleanup_failed"): await c.emergency_stop()
    assert not graph.active and not c._owned_stream
    assert any(x[0] == "StopStream" for x in graph.calls)


async def test_lost_start_response_is_still_owned_and_stopped():
    class LostStart(Graph):
        async def request(self, kind, data=None, **kwargs):
            if kind == "StartStream":
                self.active = True; raise ObsError("obs_request_failed")
            return await super().request(kind, data, **kwargs)
    c, graph = controller(LostStart())
    with pytest.raises(ObsError): await c.start_stream(PublicStartPermit("fedorr_codex", time.monotonic() + 30, True))
    assert not graph.active and not c._owned_stream and any(x[0] == "StopStream" for x in graph.calls)


@pytest.mark.parametrize("output", ["stream", "record"])
async def test_lost_stop_response_can_reconcile_confirmed_idle_without_foreign_stop(output):
    stop_name = "StopStream" if output == "stream" else "StopRecord"
    class LostStop(Graph):
        async def request(self, kind, data=None, **kwargs):
            if kind == stop_name:
                self.calls.append((kind, data)); self.active = False
                raise ObsError("obs_request_failed")
            return await super().request(kind, data, **kwargs)
    c, graph = controller(LostStop()); graph.active = True
    flag = "_owned_stream" if output == "stream" else "_record_owned"
    setattr(c, flag, True)
    stop = c.stop_stream if output == "stream" else c.stop_record
    with pytest.raises(ObsError): await stop()
    assert getattr(c, flag)
    await stop()
    assert not getattr(c, flag) and sum(kind == stop_name for kind,_ in graph.calls) == 1


async def test_lost_create_response_is_removed_only_using_own_marker_and_exact_uuid():
    class LostCreate(Graph):
        async def request(self, kind, data=None, **kwargs):
            result = await super().request(kind, data, **kwargs)
            if kind == "CreateInput": raise ObsError("obs_request_failed")
            return result
    graph = LostCreate(); graph.inputs = []; graph.settings = {}; graph.current_scene = "ACTOR Preparing"
    graph.items = {key: [] for key in ObsController.SCENES}
    c = ObsController(graph, window_guard=lambda _: True, channel_guard=lambda _: True)
    with pytest.raises(ObsError):
        await c.prepare(WindowBinding(123, 456), URL, protected_backup_confirmed=True, adopt_empty_collection=True)
    identity = graph.inputs[0]["inputUuid"]
    await c.cleanup_owned_inputs()
    assert not graph.inputs and ("RemoveInput", {"inputUuid": identity}) in graph.calls


async def test_async_destination_guard_is_awaited_and_drift_before_start_is_rejected():
    c, graph = controller(); calls = []
    async def guard(login): calls.append(login); return len(calls) == 1
    c.channel_guard = guard
    with pytest.raises(ObsError, match="binding_unverified"):
        await c.start_stream(PublicStartPermit("fedorr_codex", time.monotonic() + 30, True))
    assert len(calls) == 2 and not any(x[0] == "StartStream" for x in graph.calls)


async def test_private_service_fingerprint_exposes_only_hash_and_detects_key_change():
    class Client(ObsClient):
        service = {"streamServiceType": "rtmp_common", "streamServiceSettings": {"service": "Twitch", "key": "private-stream-key"}}
        async def _request(self, kind, *args, **kwargs):
            assert kind == "GetStreamServiceSettings"; return copy.deepcopy(self.service)
    client = Client(ObsConnection(password="secret-secret", local_listener_verified=True))
    first = await client.stream_service_fingerprint()
    assert len(first) == 64 and "private" not in first
    client.service["streamServiceSettings"]["key"] = "new-private-stream-key"
    assert await client.stream_service_fingerprint() != first
    with pytest.raises(ObsError, match="secret"): await client.request("GetStreamServiceSettings")


async def test_v5_authenticated_handshake_and_request_correlation_no_secret_settings():
    class Ws:
        def __init__(self):
            self.q = asyncio.Queue(); self.sent = []; self.closed = False
            self.q.put_nowait(json.dumps({"op": 0, "d": {"authentication": {"salt": "salt", "challenge": "challenge"}}}))
        async def recv(self): return await self.q.get()
        async def send(self, raw):
            x = json.loads(raw); self.sent.append(x)
            if x["op"] == 1: self.q.put_nowait(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
            elif x["op"] == 6:
                d = x["d"]; self.q.put_nowait(json.dumps({"op": 7, "d": d | {"requestStatus": {"result": True},
                    "responseData": {"obsVersion": "32.2.2", "rpcVersion": 1, "availableRequests": ["GetVersion"]}}}))
        async def close(self): self.closed = True
    ws = Ws()
    async def connect(*args, **kwargs): assert kwargs["proxy"] is None; return ws
    client = ObsClient(ObsConnection(password="secret-secret", local_listener_verified=True), connect=connect)
    try:
        assert (await client.connect())["obsVersion"] == "32.2.2"
        assert ws.sent[0]["d"]["authentication"] == authentication("secret-secret", "salt", "challenge")
        for request in ("GetStreamServiceSettings", "GetOutputSettings"):
            with pytest.raises(ObsError, match="secret"): await client.request(request)
    finally: await client.close()
    assert ws.closed
