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
        self.profiles = {"currentProfileName": "ACTOR Broadcast", "profiles": ["ACTOR Broadcast"]}
        self.video_settings = {"baseWidth": 1280, "baseHeight": 720}
        self.transform = {"positionX": 0, "positionY": 0, "rotation": 0, "alignment": 5,
            "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0, "boundsWidth": 1280,
            "boundsHeight": 720, "cropTop": 0, "cropBottom": 0, "cropLeft": 0, "cropRight": 0,
            "sourceWidth": 800, "sourceHeight": 600}
        full_canvas = {"sourceWidth": 1280.0, "sourceHeight": 720.0, "width": 1280.0, "height": 720.0,
            "positionX": 0.0, "positionY": 0.0, "scaleX": 1.0, "scaleY": 1.0, "rotation": 0.0,
            "alignment": 5, "boundsType": "OBS_BOUNDS_NONE", "boundsAlignment": 0,
            "boundsWidth": 0.0, "boundsHeight": 0.0,
            "cropTop": 0, "cropBottom": 0, "cropLeft": 0, "cropRight": 0, "cropToBounds": False}
        self.voice_transforms = {scene: dict(full_canvas) for scene in ("ACTOR Match", "ACTOR Ending")}
        self.inputs = [dict(inputName=ObsController.VIDEO, inputKind="screen_capture"),
                       dict(inputName=ObsController.VOICE, inputKind="browser_source")]
        self.settings = {ObsController.VIDEO: {"type": 1, "window": 123}, ObsController.VOICE: {
            "url": URL, "is_local_file": False, "reroute_audio": True, "webpage_control_level": 0,
            "width": 1280, "height": 720, "shutdown": False, "restart_when_active": False, "css": ""}}
        self.tracks = {ObsController.VIDEO: {str(i): False for i in range(1, 7)},
                       ObsController.VOICE: {str(i): i == 1 for i in range(1, 7)}}
        self.items = {"ACTOR Preparing": [], "ACTOR Safe": [], "ACTOR Ending": [self.item(ObsController.VOICE)],
                      "ACTOR Match": [self.item(ObsController.VIDEO), self.item(ObsController.VOICE, 1)]}
        self.scene_ids = {scene:str(uuid4()) for scene in self.items}
    @staticmethod
    def item(name, index=0, source_uuid=None):
        return {"sourceName": name, "sceneItemId": 1 if name == ObsController.VIDEO else 2,
            "sourceUuid":source_uuid,"sceneItemIndex": index, "sceneItemBlendMode": "OBS_BLEND_NORMAL", "sceneItemEnabled": True, "isGroup": False}
    async def request(self, kind, data=None, **kwargs):
        data = data or {}; self.calls.append((kind, data)); name = data.get("inputName")
        if kind.endswith("Status"): return {"outputActive": self.active or self.foreign, "outputReconnecting": False}
        if kind == "GetVideoSettings": return self.video_settings
        if kind == "GetSceneCollectionList": return {"currentSceneCollectionName": "ACTOR Broadcast", "sceneCollections": ["ACTOR Broadcast"]}
        if kind == "GetProfileList": return copy.deepcopy(self.profiles)
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
        if kind == "GetSceneList": return {"scenes": [{"sceneName": x,"sceneUuid":self.scene_ids[x]} for x in self.items]}
        if kind in ("GetSceneItemList","RemoveSceneItem"):
            scene=data.get("sceneName") or next(name for name,identity in self.scene_ids.items() if identity==data.get("sceneUuid"))
            if kind=="GetSceneItemList": return {"sceneItems": self.items[scene]}
            self.items[scene]=[row for row in self.items[scene] if row["sceneItemId"]!=data["sceneItemId"]]
        if kind == "GetCurrentProgramScene": return {"currentProgramSceneName": self.current_scene}
        if kind == "GetSceneItemTransform":
            return {"sceneItemTransform": self.voice_transforms[data["sceneName"]] if data["sceneItemId"] == 2 else self.transform}
        if kind == "SetCurrentProgramScene": self.current_scene = data["sceneName"]
        if kind == "CreateInput":
            identity = str(uuid4()); self.inputs.append({"inputName": name, "inputKind": data["inputKind"], "inputUuid": identity})
            self.settings[name] = data["inputSettings"]
            self.items[data["sceneName"]].append(self.item(name, len(self.items[data["sceneName"]]), identity))
            return {"inputUuid": identity, "sceneItemId": 1}
        if kind == "CreateSceneItem":
            identity=next(row["inputUuid"] for row in self.inputs if row["inputName"]==data["sourceName"])
            self.items[data["sceneName"]].append(self.item(data["sourceName"], len(self.items[data["sceneName"]]),identity))
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
    c._startup_pending = False
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


async def test_safe_overlay_uses_explicit_layer_indices_not_array_order_and_checks_both_scenes():
    c, graph = controller()
    graph.items["ACTOR Match"].reverse()  # A JSON reorder does not change compositing order.
    assert (await c.audit()).passed and c.route_verified
    queried = [data["sceneName"] for kind,data in graph.calls
        if kind == "GetSceneItemTransform" and data["sceneItemId"] == 2]
    assert queried == ["ACTOR Match", "ACTOR Ending"]


@pytest.mark.parametrize("video", [{"baseWidth": 1920, "baseHeight": 1080},
    {"baseWidth": 1280, "baseHeight": 721}, {"baseWidth": 1280.0, "baseHeight": 720}, {}])
async def test_canvas_mismatch_blocks_prepare_before_any_graph_or_video_mutation(video):
    graph = Graph(); graph.video_settings = video
    c = ObsController(graph, window_guard=lambda _: True, channel_guard=lambda _: True)
    with pytest.raises(ObsError, match="^obs_safe_canvas_unverified$"):
        await c.prepare(WindowBinding(123, 456), URL, protected_backup_confirmed=True, adopt_empty_collection=True)
    assert graph.calls == [("GetVideoSettings", {})]
    c, _ = controller(graph)
    result = await c.audit()
    assert not result.passed and result.reason == "obs_safe_canvas_unverified" and not c.route_verified


@pytest.mark.parametrize("scene", ["ACTOR Match", "ACTOR Ending"])
@pytest.mark.parametrize("mutation", [
    lambda t: t.update(positionX=1),
    lambda t: t.update(scaleX=.5, width=640),
    lambda t: t.update(cropTop=1),
    lambda t: t.update(alignment=0),
    lambda t: t.update(boundsType="OBS_BOUNDS_SCALE_INNER"),
    lambda t: t.update(sourceWidth=1279, width=1279),
    lambda t: t.update(cropToBounds=True),
    lambda t: t.update(rotation=float("nan")),
    lambda t: t.update(height=float("inf")),
    lambda t: t.update(scaleY=True),  # True == 1 must not satisfy numeric proof.
    lambda t: t.pop("scaleY"),
])
async def test_partial_cropped_nonfinite_or_unknown_browser_coverage_is_not_safe(scene, mutation):
    c, graph = controller(); mutation(graph.voice_transforms[scene])
    result = await c.audit()
    assert not result.passed and result.reason == "obs_safe_overlay_geometry_unverified" and not c.route_verified
    assert all(kind.startswith("Get") for kind,_ in graph.calls)


@pytest.mark.parametrize("indices", [(1, 0), (0, 0), (False, 1), (0, None)])
async def test_browser_below_video_or_unknown_layer_order_is_not_safe(indices):
    c, graph = controller()
    for item,index in zip(graph.items["ACTOR Match"], indices): item["sceneItemIndex"] = index
    result = await c.audit()
    assert not result.passed and result.reason == "obs_safe_overlay_order_unverified" and not c.route_verified


@pytest.mark.parametrize("scene", ["ACTOR Match", "ACTOR Ending"])
async def test_disabled_or_additive_browser_cannot_claim_opaque_safe_coverage(scene):
    c, graph = controller(); voice = next(x for x in graph.items[scene] if x["sourceName"] == c.VOICE)
    voice["sceneItemBlendMode"] = "OBS_BLEND_ADDITIVE"
    result = await c.audit()
    assert not result.passed and result.reason == "obs_safe_overlay_blend_unverified"
    voice["sceneItemBlendMode"] = "OBS_BLEND_NORMAL"; voice["sceneItemEnabled"] = False
    result = await c.audit()
    assert not result.passed and result.reason == "obs_safe_overlay_geometry_unverified"


async def test_unverified_overlay_blocks_scene_transition_with_explicit_safe_reason():
    c, graph = controller(); graph.voice_transforms["ACTOR Match"]["positionX"] = 1
    with pytest.raises(ObsError, match="^obs_safe_overlay_geometry_unverified$"):
        await c.show("ACTOR Ending")
    assert not any(kind == "SetCurrentProgramScene" for kind,_ in graph.calls)


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
    lambda g: g.settings[ObsController.VOICE].update(css="#safe { display:none !important; }"),
    lambda g: g.settings[ObsController.VOICE].pop("css"),
    lambda g: g.settings[ObsController.VOICE].update(shutdown=True),
    lambda g: g.settings[ObsController.VOICE].update(restart_when_active=True),
    lambda g: g.settings[ObsController.VOICE].update(width=640),
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


class DisabledReplayGraph(Graph):
    """Real disabled-frontend shape from OBS 32.2.2 / obs-websocket 5.7.4."""
    def __init__(self):
        super().__init__()
        self.mode = "Simple"; self.replay_enabled = "false"
        self.replay_error = ObsError("obs_request_rejected", request_status_code=604)
        self.outputs = [
            {"outputName": "simple_file_output", "outputKind": "mov_output", "outputActive": False},
            {"outputName": "virtualcam_output", "outputKind": "virtualcam_output", "outputActive": False},
        ]

    async def request(self, kind, data=None, **kwargs):
        if kind in ("GetReplayBufferStatus", "GetProfileParameter", "GetOutputList"):
            self.calls.append((kind, data))
            if kind == "GetReplayBufferStatus": raise self.replay_error
            if kind == "GetOutputList": return {"outputs": copy.deepcopy(self.outputs)}
            assert data["parameterName"] in ("Mode", "RecRB")
            return {"parameterValue": self.mode if data["parameterName"] == "Mode" else self.replay_enabled,
                    "defaultParameterValue": None}
        return await super().request(kind, data, **kwargs)


@pytest.mark.parametrize("mode,category", [("Simple", "SimpleOutput"), ("Advanced", "AdvOut")])
async def test_disabled_replay_requires_selected_profile_and_all_enumerated_outputs_idle(mode, category):
    graph = DisabledReplayGraph(); graph.mode = mode
    c, _ = controller(graph)
    await c.require_idle()
    assert graph.calls == [("GetStreamStatus", {}), ("GetRecordStatus", {}), ("GetVirtualCamStatus", {}),
        ("GetReplayBufferStatus", None),
        ("GetProfileParameter", {"parameterCategory": "Output", "parameterName": "Mode"}),
        ("GetProfileParameter", {"parameterCategory": category, "parameterName": "RecRB"}),
        ("GetOutputList", None)]
    # The entire compatibility path is read-only, including failed checks.
    assert not any(name.startswith(("Start", "Stop", "Set")) for name,_ in graph.calls)


@pytest.mark.parametrize("mutation", [
    lambda g: setattr(g, "mode", None),
    lambda g: setattr(g, "mode", "unknown"),
    lambda g: setattr(g, "replay_enabled", "true"),
    lambda g: setattr(g, "replay_enabled", None),
    lambda g: setattr(g, "replay_enabled", False),  # protocol returns strings, not bool coercion
    lambda g: setattr(g, "outputs", []),
    lambda g: setattr(g, "outputs", None),
    lambda g: g.outputs.append({"outputName": "foreign_recording", "outputKind": "ffmpeg_muxer", "outputActive": True}),
    lambda g: g.outputs[0].update(outputActive=0),
    lambda g: g.outputs[0].pop("outputActive"),
    lambda g: g.outputs[0].pop("outputKind"),
    lambda g: g.outputs.append({"outputName": "Replay Buffer", "outputKind": "replay_buffer", "outputActive": False}),
])
async def test_604_does_not_establish_disabled_replay_with_unknown_or_contradictory_proof(mutation):
    graph = DisabledReplayGraph(); mutation(graph)
    c, _ = controller(graph)
    with pytest.raises(ObsError, match="^obs_replay_disabled_unverified$"):
        await c.require_idle()
    assert not any(name.startswith(("Start", "Stop", "Set")) for name,_ in graph.calls)


@pytest.mark.parametrize("error", [ObsError("obs_request_failed"),
    ObsError("obs_request_rejected"), ObsError("obs_request_rejected", request_status_code=500),
    ObsError("obs_request_failed", request_status_code=604),
    ObsError("obs_request_rejected", request_status_code="604")])
async def test_replay_unknown_failures_never_fall_back_to_profile_or_output_enumeration(error):
    graph = DisabledReplayGraph(); graph.replay_error = error
    c, _ = controller(graph)
    with pytest.raises(ObsError): await c.require_idle()
    assert not any(kind in ("GetProfileParameter", "GetOutputList") for kind,_ in graph.calls)


async def test_active_known_output_is_rejected_before_disabled_replay_fallback():
    graph = DisabledReplayGraph(); graph.foreign = True
    c, _ = controller(graph)
    with pytest.raises(ObsError, match="^obs_foreign_output_active$"):
        await c.require_idle()
    assert graph.calls == [("GetStreamStatus", {})]


async def test_available_but_active_replay_is_not_treated_as_disabled():
    class ActiveReplay(Graph):
        async def request(self, kind, data=None, **kwargs):
            result = await super().request(kind, data, **kwargs)
            return {"outputActive": True} if kind == "GetReplayBufferStatus" else result
    c, graph = controller(ActiveReplay())
    with pytest.raises(ObsError, match="^obs_foreign_output_active$"):
        await c.require_idle()
    assert not any(kind in ("GetProfileParameter", "GetOutputList") for kind,_ in graph.calls)


async def test_actual_profile_string_array_supports_owned_empty_adoption_without_profile_creation():
    graph = Graph(); graph.inputs = []; graph.settings = {}; graph.current_scene = "ACTOR Preparing"
    graph.items = {key: [] for key in ObsController.SCENES}
    c = ObsController(graph, window_guard=lambda _: True, channel_guard=lambda _: True)
    assert (await c.prepare(WindowBinding(123, 456), URL, protected_backup_confirmed=True, adopt_empty_collection=True)).passed
    assert not any(kind == "CreateProfile" for kind, _ in graph.calls)


@pytest.mark.parametrize("profiles", [None, {},
    {"currentProfileName": "ACTOR Broadcast", "profiles": None},
    {"currentProfileName": "ACTOR Broadcast", "profiles": {"ACTOR Broadcast": {}}},
    {"currentProfileName": "ACTOR Broadcast", "profiles": [{"profileName": "ACTOR Broadcast"}]},
    {"currentProfileName": "ACTOR Broadcast", "profiles": []},
    {"currentProfileName": "ACTOR Broadcast", "profiles": [True]},
    {"currentProfileName": "ACTOR Broadcast", "profiles": [""]},
    {"currentProfileName": "ACTOR Broadcast", "profiles": ["ACTOR Broadcast", "ACTOR Broadcast"]},
    {"currentProfileName": "ACTOR Broadcast", "profiles": ["Foreign"]},
    {"currentProfileName": None, "profiles": ["ACTOR Broadcast"]},
    {"currentProfileName": "x" * 257, "profiles": ["x" * 257]},
    {"currentProfileName": "ACTOR Broadcast", "profiles": ["ACTOR Broadcast", "bad\x00name"]},
    {"currentProfileName": "ACTOR Broadcast", "profiles": ["ACTOR Broadcast"] + [str(i) for i in range(128)]},
])
async def test_malformed_or_fictional_profile_shape_fails_before_setup_cleanup_or_audit(profiles):
    graph = Graph(); graph.profiles = profiles
    c = ObsController(graph, window_guard=lambda _: True, channel_guard=lambda _: True)
    with pytest.raises(ObsError, match="^obs_profile_list_invalid$"):
        await c.prepare(WindowBinding(123, 456), URL, protected_backup_confirmed=True, adopt_empty_collection=True)
    with pytest.raises(ObsError, match="^obs_profile_list_invalid$"): await c.cleanup_owned_inputs()
    prepared, _ = controller(graph)
    audit = await prepared.audit()
    assert not audit.passed and audit.reason == "obs_profile_list_invalid" and not prepared.route_verified
    assert all(kind.startswith("Get") for kind, _ in graph.calls)


async def test_adopting_root_created_collection_requires_explicit_empty_proof():
    graph = Graph(); c = ObsController(graph, window_guard=lambda _: True, channel_guard=lambda _: True)
    with pytest.raises(ObsError, match="explicit_recovery"):
        await c.prepare(WindowBinding(123, 456), URL, protected_backup_confirmed=True)
    with pytest.raises(ObsError, match="not_empty"):
        await c.prepare(WindowBinding(123, 456), URL, protected_backup_confirmed=True, adopt_empty_collection=True)
    assert not any(x[0].startswith("Create") for x in graph.calls)


async def test_graph_mutation_invalidates_audio_guard_immediately():
    c, graph = controller(); assert (await c.audit()).passed
    for event in ("SourceFilterCreated", "InputAudioTracksChanged", "ConnectionLost",
                  "SceneItemTransformChanged", "SceneItemListReindexed"):
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


class InactiveScenes(Graph):
    """libobs-like removal: unrendered scene references retain removed sources."""
    def __init__(self):
        super().__init__();self.inputs=[];self.settings={};self.items={key:[] for key in ObsController.SCENES}
        self.current_scene="ACTOR Preparing";self.removed=set();self.lost=None;self.lost_once=False
    async def request(self,kind,data=None,**kwargs):
        if kind=="RemoveInput":
            self.calls.append((kind,data));self.removed.add(data["inputUuid"]);result={}
        else:result=await super().request(kind,data,**kwargs)
        references={row.get("sourceUuid") for rows in self.items.values() for row in rows}
        self.inputs[:]=[row for row in self.inputs if row["inputUuid"] not in self.removed or row["inputUuid"] in references]
        if kind==self.lost and not self.lost_once:
            self.lost_once=True;raise ObsError("obs_request_failed")
        return result


async def inactive_prepared():
    graph=InactiveScenes();c=ObsController(graph,window_guard=lambda _:True,channel_guard=lambda _:True)
    assert (await c.prepare(WindowBinding(123,456),URL,protected_backup_confirmed=True,adopt_empty_collection=True)).passed
    graph.current_scene="ACTOR Safe"
    return c,graph


async def test_inactive_match_and_ending_detach_all_exact_uuid_references_before_removing_inputs():
    c,graph=await inactive_prepared();owned=dict(c._owned_inputs)
    expected={(graph.scene_ids[scene],row["sceneItemId"]) for scene,rows in graph.items.items() for row in rows}
    before=len(graph.calls)
    await c.cleanup_owned_inputs();calls=graph.calls[before:]
    detached=[(data["sceneUuid"],data["sceneItemId"]) for kind,data in calls if kind=="RemoveSceneItem"]
    assert len(detached)==3 and set(detached)==expected
    assert max(i for i,(kind,_) in enumerate(calls) if kind=="RemoveSceneItem") < min(i for i,(kind,_) in enumerate(calls) if kind=="RemoveInput")
    assert {data["inputUuid"] for kind,data in calls if kind=="RemoveInput"}==set(owned.values())
    assert not graph.inputs and not any(graph.items.values()) and not c._owned_inputs and not c._creating_inputs
    assert graph.current_scene=="ACTOR Safe" and not any(kind=="SetCurrentProgramScene" for kind,_ in calls)


@pytest.mark.parametrize("lost",["RemoveSceneItem","RemoveInput"])
async def test_lost_cleanup_mutation_response_retains_uuid_ownership_until_retry_proves_absence(lost):
    c,graph=await inactive_prepared();owned=dict(c._owned_inputs);graph.lost=lost
    with pytest.raises(ObsError,match="request_failed"):await c.cleanup_owned_inputs()
    assert c._owned_inputs==owned and c._prepared
    first=[data for kind,data in graph.calls if kind==lost][-1]
    await c.cleanup_owned_inputs()
    assert not graph.inputs and not any(graph.items.values()) and not c._owned_inputs
    assert sum(kind==lost and data==first for kind,data in graph.calls)==1


async def test_cleanup_never_detaches_same_name_foreign_scene_item_or_input():
    c,graph=await inactive_prepared();foreign=str(uuid4())
    row=Graph.item(c.VOICE,2,foreign);row["sceneItemId"]=99
    graph.items["ACTOR Match"].append(row)
    graph.inputs.append({"inputName":"Foreign source","inputUuid":foreign,"inputKind":"browser_source"})
    before=len(graph.calls)
    with pytest.raises(ObsError,match="foreign_input"):await c.cleanup_owned_inputs()
    assert row in graph.items["ACTOR Match"] and any(x["inputUuid"]==foreign for x in graph.inputs)
    assert not any(kind=="RemoveSceneItem" and data["sceneItemId"]==99 for kind,data in graph.calls[before:])
    assert not any(kind=="RemoveInput" and data["inputUuid"]==foreign for kind,data in graph.calls[before:])
    assert c._owned_inputs  # no successful completion was claimed


@pytest.mark.parametrize("mutation",["marker","unknown_scene","missing_source_uuid"])
async def test_cleanup_rejects_unverified_marker_or_scene_before_destructive_mutation(mutation):
    c,graph=await inactive_prepared();owned=dict(c._owned_inputs)
    if mutation=="marker":graph.settings[c.VIDEO]["actor_broadcast_owner"]="foreign"
    elif mutation=="unknown_scene":
        graph.items["Foreign scene"]=[];graph.scene_ids["Foreign scene"]=str(uuid4())
    else:graph.items["ACTOR Preparing"]=[{"sceneItemId":10,"sourceName":c.VIDEO}]
    before=len(graph.calls)
    with pytest.raises(ObsError):await c.cleanup_owned_inputs()
    assert c._owned_inputs==owned and not any(kind.startswith("Remove") for kind,_ in graph.calls[before:])


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
                d = x["d"]
                if d["requestType"] == "GetReplayBufferStatus":
                    result = {"requestStatus": {"result": False, "code": 604, "comment": "PRIVATE_SERVER_COMMENT"}}
                else:
                    result = {"requestStatus": {"result": True}, "responseData": {"obsVersion": "32.2.2", "rpcVersion": 1,
                        "availableRequests": ["GetVersion", "GetReplayBufferStatus"]}}
                self.q.put_nowait(json.dumps({"op": 7, "d": d | result}))
        async def close(self): self.closed = True
    ws = Ws()
    async def connect(*args, **kwargs): assert kwargs["proxy"] is None; return ws
    client = ObsClient(ObsConnection(password="secret-secret", local_listener_verified=True), connect=connect)
    try:
        assert (await client.connect())["obsVersion"] == "32.2.2"
        assert ws.sent[0]["d"]["authentication"] == authentication("secret-secret", "salt", "challenge")
        assert ws.sent[0]["d"]["eventSubscriptions"] & (1 << 19)  # Actual transform-event category.
        for request in ("GetStreamServiceSettings", "GetOutputSettings"):
            with pytest.raises(ObsError, match="secret"): await client.request(request)
        with pytest.raises(ObsError, match="^obs_request_rejected$") as error:
            await client.request("GetReplayBufferStatus")
        assert error.value.request_status_code == 604
        assert "PRIVATE_SERVER_COMMENT" not in str(error.value) + repr(error.value) + repr(vars(error.value))
    finally: await client.close()
    assert ws.closed


async def test_startup_fast_audits_accumulate_quiet_time_without_exposing_early_route():
    c, graph = controller(); c._startup_pending = True
    c.STABILIZE_TIMEOUT_SECONDS = .5
    # Reproduce effectively instant local audits: the first success must not
    # reset on every 50ms poll, nor become route readiness during the wait.
    task = asyncio.create_task(c.complete_startup())
    await asyncio.sleep(.08)
    assert not task.done() and not c.route_verified and c._startup_pending
    assert (await task).passed and c.route_verified and not c._startup_pending
    assert sum(kind == 'GetCurrentProgramScene' for kind, _ in graph.calls) >= 4
    with pytest.raises(ObsError, match='obs_startup_already_completed'):
        await c.stabilize()


async def test_startup_activation_reaudits_full_graph_after_real_generation_race():
    class Activation(Graph):
        changed = False
        async def request(self, kind, data=None, **kwargs):
            result = await super().request(kind, data, **kwargs)
            if (kind == 'GetSceneItemList' and self.current_scene == 'ACTOR Match'
                    and (data or {}).get('sceneName') == 'ACTOR Match' and not self.changed):
                self.changed = True
                self.on_event('SceneItemTransformChanged', {'sceneName': 'ACTOR Match', 'sceneItemId': 2})
            return result
    c, graph = controller(Activation()); c._startup_pending = True
    c._expected_scene = graph.current_scene = 'ACTOR Preparing'
    await c.show('ACTOR Match')
    assert graph.changed and c.route_verified and c._graph_generation == 2
    assert sum(kind == 'GetCurrentProgramScene' for kind, _ in graph.calls) >= 8
    assert (await c.complete_startup()).passed


async def test_prepare_waits_for_actual_capture_dimensions_to_materialize():
    class Initializing(InactiveScenes):
        samples = 0
        async def request(self, kind, data=None, **kwargs):
            result = await super().request(kind, data, **kwargs)
            if kind == 'GetSceneItemTransform' and (data or {}).get('sceneItemId') == 1:
                self.samples += 1
                if self.samples <= 2:
                    return {'sceneItemTransform': {**self.transform, 'sourceWidth': 0, 'sourceHeight': 0}}
            return result
    graph = Initializing(); c = ObsController(graph, window_guard=lambda _: True, channel_guard=lambda _: True)
    assert (await c.prepare(WindowBinding(123,456), URL, protected_backup_confirmed=True,
        adopt_empty_collection=True)).passed
    assert graph.samples >= 6 and c._startup_pending
    assert not c.route_verified  # Preparing never authorizes audio playback.


@pytest.mark.parametrize('hung_query', ['GetStreamStatus', 'GetSceneCollectionList'])
async def test_startup_deadline_covers_hung_idle_and_complete_audit_queries(hung_query):
    c, graph = controller(); c._startup_pending = True; c.STABILIZE_TIMEOUT_SECONDS = .1
    original = graph.request; entered = asyncio.Event()
    async def hung(kind, *args, **kwargs):
        if kind == hung_query: entered.set(); await asyncio.Event().wait()
        return await original(kind, *args, **kwargs)
    graph.request = hung
    with pytest.raises(ObsError, match='obs_graph_stabilization_timeout'):
        await asyncio.wait_for(c.complete_startup(), .5)
    assert entered.is_set() and not c.route_verified and c._startup_pending


async def test_startup_continuous_events_timeout_and_cannot_publish_readiness():
    c, graph = controller(); c._startup_pending = True; c.STABILIZE_TIMEOUT_SECONDS = .15
    original = graph.request
    async def changing(kind, *args, **kwargs):
        result = await original(kind, *args, **kwargs)
        if kind == 'GetCurrentProgramScene': c._event('SceneItemTransformChanged', {})
        return result
    graph.request = changing
    with pytest.raises(ObsError, match='obs_graph_stabilization_timeout'): await c.complete_startup()
    assert not c.route_verified and c._startup_pending


async def test_startup_rejects_actual_foreign_output_before_scene_mutation():
    c, graph = controller(); c._startup_pending = True; graph.foreign = True
    with pytest.raises(ObsError, match='obs_foreign_output_active'): await c.show('ACTOR Ending')
    assert not any(kind == 'SetCurrentProgramScene' for kind, _ in graph.calls)
    assert not c.route_verified


@pytest.mark.parametrize('owned_flag', ['_owned_stream', '_record_owned'])
async def test_completed_startup_never_grants_audit_retry_for_live_scene_change(owned_flag):
    c, graph = controller(); setattr(c, owned_flag, True)
    original = graph.request; fired = False
    async def changing(kind, data=None, **kwargs):
        nonlocal fired
        result = await original(kind, data, **kwargs)
        if kind == 'GetSceneItemList' and graph.current_scene == 'ACTOR Ending' and not fired:
            fired = True; c._event('SceneItemTransformChanged', {})
        return result
    graph.request = changing
    with pytest.raises(ObsError, match='obs_graph_audit_failed'): await c.show('ACTOR Ending')
    assert fired and not c.route_verified
    assert sum(kind == 'GetCurrentProgramScene' for kind, _ in graph.calls) == 2


async def test_public_start_preserves_existing_preview_route_and_same_scene_without_settling():
    c, graph = controller(); assert (await c.audit()).passed
    original = graph.request; route_observations = []
    async def checked(kind, *args, **kwargs):
        route_observations.append(c.route_verified)
        return await original(kind, *args, **kwargs)
    graph.request = checked
    await c.start_stream(PublicStartPermit('fedorr_codex', time.monotonic()+5, True))
    assert all(route_observations) and c._owned_stream
    assert not any(kind == 'SetCurrentProgramScene' for kind, _ in graph.calls)


async def test_public_start_cannot_run_before_probe_seals_startup():
    c, graph = controller(); c._startup_pending = True
    with pytest.raises(ObsError, match='obs_startup_incomplete'):
        await c.start_stream(PublicStartPermit('fedorr_codex', time.monotonic()+5, True))
    assert not any(kind == 'StartStream' for kind, _ in graph.calls)
