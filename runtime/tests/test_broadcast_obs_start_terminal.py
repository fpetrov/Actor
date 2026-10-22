"""Failed queued frontend starts: real controllers over in-memory OBS IO."""
import asyncio
import time

import pytest

from actor_broadcast.guardian_services import GuardianServiceError, _cleanup_error_code
from actor_broadcast.obs import ObsController, ObsError, PublicStartPermit, WindowBinding
from test_broadcast_obs import Graph, InactiveScenes, URL, controller
from test_broadcast_guardian_adapters import Group, services


def stream_event(controller, state, active):
    controller._event("StreamStateChanged", {"outputState":"OBS_WEBSOCKET_OUTPUT_" + state,
        "outputActive":active})


class FailedStart(Graph):
    async def request(self, kind, data=None, **kwargs):
        if kind == "StartStream":
            self.calls.append((kind, data or {}))
            self.on_event("StreamStateChanged", {"outputState":"OBS_WEBSOCKET_OUTPUT_STARTING", "outputActive":False})
            self.on_event("StreamStateChanged", {"outputState":"OBS_WEBSOCKET_OUTPUT_STOPPED", "outputActive":False})
            return {}
        return await super().request(kind, data, **kwargs)


async def test_accepted_starting_then_stopped_retires_only_after_fresh_inactive_status():
    c, graph = controller(FailedStart()); unsafe = []; c.on_unsafe = unsafe.append
    with pytest.raises(ObsError, match="^obs_owned_stream_start_failed$"):
        await c.start_stream(PublicStartPermit("fedorr_codex", time.monotonic()+5, True))
    assert c._stream_start_terminal and c._stream_start_progressed
    assert not c._owned_stream and not c._stream_start_pending and not c._stream_confirmed
    assert c._stream_start_task is None
    assert unsafe == ["obs_owned_stream_start_failed"]
    assert not c.route_verified and not any(kind == "StopStream" for kind, _ in graph.calls)
    await c.stop_stream()
    with pytest.raises(ObsError, match="^obs_owned_stream_start_failed$"):
        await c.start_stream(PublicStartPermit("fedorr_codex", time.monotonic()+5, True))


@pytest.mark.parametrize("events", [
    [("STOPPED",False)], [("STARTING",True),("STOPPED",False)],
    [("STARTING",False),("STOPPING",False)], [("STARTING",False),("STOPPED",True)],
])
async def test_unknown_or_incomplete_start_lifecycle_never_retires_pending_intent(events):
    c, graph = controller(); c._owned_stream = c._stream_start_pending = True
    c.START_SETTLE_TIMEOUT_SECONDS = .02
    for state, active in events: stream_event(c, state, active)
    with pytest.raises(ObsError, match="^obs_owned_stream_start_pending$"): await c.stop_stream()
    assert c._owned_stream and c._stream_start_pending and not c._stream_start_terminal
    assert not any(kind == "StopStream" for kind, _ in graph.calls)


async def test_terminal_event_cannot_release_unsettled_start_rpc():
    entered, release = asyncio.Event(), asyncio.Event()
    class Delayed(FailedStart):
        async def request(self, kind, data=None, **kwargs):
            value = await super().request(kind, data, **kwargs)
            if kind == "StartStream":
                entered.set(); await asyncio.wait_for(release.wait(), 1)
            return value
    c, graph = controller(Delayed()); c.START_SETTLE_TIMEOUT_SECONDS = .02
    starting = asyncio.create_task(c.start_stream(PublicStartPermit("fedorr_codex",time.monotonic()+5,True)))
    try:
        await asyncio.wait_for(entered.wait(), .5)
        retained = c._stream_start_task
        with pytest.raises(ObsError, match="^obs_owned_stream_start_pending$"): await c.stop_stream()
        assert c._owned_stream and c._stream_start_pending and c._stream_start_task is retained
        assert not retained.done() and not retained.cancelled()
        release.set()
        with pytest.raises(ObsError, match="^obs_owned_stream_start_failed$"):
            await asyncio.wait_for(starting, .5)
        assert not c._owned_stream and c._stream_start_task is None
        assert not any(kind == "StopStream" for kind, _ in graph.calls)
    finally:
        release.set(); await asyncio.gather(starting, return_exceptions=True)


@pytest.mark.parametrize("state", [
    {"outputActive":True,"outputReconnecting":False},
    {"outputActive":False,"outputReconnecting":True},
    {"outputActive":False},
])
async def test_terminal_start_requires_current_inactive_nonreconnecting_proof_and_never_stops_replacement(state):
    c, graph = controller(); c._owned_stream = c._stream_start_pending = True
    c._stream_single_start_verified = True
    stream_event(c,"STARTING",False); stream_event(c,"STOPPED",False)
    original = graph.request
    async def request(kind, data=None, **kwargs):
        if kind == "GetStreamStatus": return state
        return await original(kind, data, **kwargs)
    graph.request = request
    with pytest.raises(ObsError): await c.stop_stream()
    assert c._owned_stream and c._stream_start_pending and not any(kind == "StopStream" for kind, _ in graph.calls)


async def test_guardian_wire_mutation_fence_rejects_late_terminal_before_stop(tmp_path):
    backend = services(tmp_path); c, _ = controller()
    backend.obs = c; c._owned_stream = c._stream_start_pending = True
    assert backend._mutation_allowed("StopStream", {})
    stream_event(c,"STARTING",False); stream_event(c,"STOPPED",False)
    assert not backend._mutation_allowed("StopStream", {})
    assert backend._mutation_allowed("GetStreamStatus", {})


async def test_real_guardian_cleanup_removes_exact_inputs_after_failed_start_without_extra_stop(tmp_path):
    class Backend(InactiveScenes):
        async def request(self, kind, data=None, **kwargs):
            if kind == "StartStream":
                self.calls.append((kind,data or {}))
                self.on_event("StreamStateChanged", {"outputState":"OBS_WEBSOCKET_OUTPUT_STARTING","outputActive":False})
                self.on_event("StreamStateChanged", {"outputState":"OBS_WEBSOCKET_OUTPUT_STOPPED","outputActive":False})
                return {}
            return await super().request(kind,data,**kwargs)
    backend = services(tmp_path); backend.bind_controller(Group())
    await backend.begin_run("ABC123","a"*32)
    graph = Backend(); graph.on_event = backend._watch_output_event
    c = ObsController(graph,window_guard=lambda _:True,channel_guard=lambda _:True,on_unsafe=backend._output_unsafe)
    backend.obs, backend.obs_client = c, graph
    backend._scene_ids = dict(graph.scene_ids)
    await c.prepare(WindowBinding(123,456),URL,protected_backup_confirmed=True,adopt_empty_collection=True)
    await c.show("ACTOR Match"); await c.complete_startup()
    identities = set(c._owned_inputs.values()); assert len(identities) == 2
    with pytest.raises(ObsError, match="^obs_owned_stream_start_failed$"):
        await c.start_stream(PublicStartPermit("fedorr_codex",time.monotonic()+5,True))
    assert await backend.cleanup_run() is True
    assert backend.obs is None and backend.obs_client is None and backend.session is None
    assert not graph.inputs and not any(graph.items.values()) and not backend.cleanup_error_codes
    assert {data["inputUuid"] for kind,data in graph.calls if kind == "RemoveInput"} == identities
    assert not any(kind == "StopStream" for kind,_ in graph.calls)


@pytest.mark.parametrize("error,expected", [
    (ObsError("obs_owned_stream_start_pending"),"obs_owned_stream_start_pending"),
    (RuntimeError("private-token-or-raw-OBS-message"),"guardian_cleanup_error_unclassified"),
    (ObsError("private-token-or-raw-OBS-message"),"guardian_cleanup_error_unclassified"),
])
async def test_cleanup_diagnostic_fixed_codes_preserve_pending_owner_and_scrub_unknown(tmp_path,error,expected):
    backend = services(tmp_path); await backend.begin_run("ABC123","a"*32)
    async def failed():
        raise GuardianServiceError("guardian_owned_output_cleanup_pending") from error
    backend._close_output = failed
    with pytest.raises(GuardianServiceError, match="^guardian_cleanup_pending$"): await backend.cleanup_run()
    expected = expected if expected != "guardian_cleanup_error_unclassified" else "guardian_owned_output_cleanup_pending"
    assert backend.cleanup_error_codes == {"output":expected}
    assert backend.session == "a"*32 and "private-" not in repr(backend.cleanup_error_codes)
    assert _cleanup_error_code(error) in {"obs_owned_stream_start_pending","guardian_cleanup_error_unclassified"}


@pytest.mark.parametrize("enabled", ["true", "0", "False", False, None, 0, {}])
async def test_multitrack_disabled_must_be_explicit_string_false_before_any_start(enabled):
    c, graph = controller(); original = graph.request
    async def request(kind, data=None, **kwargs):
        if kind == "GetProfileParameter": return {"parameterValue":enabled}
        return await original(kind,data,**kwargs)
    graph.request = request
    with pytest.raises(ObsError, match="^obs_stream_start_policy_unverified$"):
        await c.start_stream(PublicStartPermit("fedorr_codex",time.monotonic()+5,True))
    assert not c._owned_stream and not any(kind == "StartStream" for kind,_ in graph.calls)


async def test_profile_or_multitrack_policy_drift_after_terminal_keeps_pending_owner():
    c, graph = controller(); c._owned_stream = c._stream_start_pending = True
    c._stream_single_start_verified = True
    stream_event(c,"STARTING",False); stream_event(c,"STOPPED",False)
    graph.profiles["currentProfileName"] = "foreign-profile"
    graph.profiles["profiles"].append("foreign-profile")
    with pytest.raises(ObsError, match="^obs_stream_start_policy_unverified$"): await c.stop_stream()
    assert c._owned_stream and c._stream_start_pending
    assert not any(kind == "StopStream" for kind,_ in graph.calls)


async def test_later_starting_even_if_currently_inactive_cannot_heal_terminal_ownership():
    c, graph = controller(); c._owned_stream = c._stream_start_pending = True
    c._stream_single_start_verified = True
    stream_event(c,"STARTING",False); stream_event(c,"STOPPED",False)
    stream_event(c,"STARTING",False); stream_event(c,"STOPPED",False)
    with pytest.raises(ObsError, match="^obs_owned_stream_ownership_unknown$"): await c.stop_stream()
    assert c._stream_start_superseded and c._owned_stream and c._stream_start_pending
    assert not any(kind == "StopStream" for kind,_ in graph.calls)
