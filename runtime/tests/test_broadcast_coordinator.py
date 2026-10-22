"""Lifecycle tests with owned fake integrations; no models/OBS/accounts/UI."""
import asyncio

import pytest

from actor_broadcast.coordinator import BroadcastCoordinator, CoordinatorError


FLAGS = dict.fromkeys(("observer", "model", "tts", "obs", "audio", "chat"), True)


class Scheduler:
    finished = False
    def ready(self): return True
    def report(self): return {"terminal_outro_finished": self.finished}
    def configure(self, _): pass


class Obs:
    def __init__(self):
        self.starts = 0; self.stops = 0; self.active = False; self.shown = []
        self.start_entered = asyncio.Event(); self.start_release = asyncio.Event(); self.block_start = False
    async def show(self, scene): self.shown.append(scene)
    async def start_stream(self, _):
        self.starts += 1; self.start_entered.set()
        if self.block_start: await self.start_release.wait()
        self.active = True
    async def emergency_stop(self): self.stops += 1; self.active = False


class Run:
    def __init__(self):
        self.scheduler = Scheduler(); self.obs = Obs()
        self.activations = 0; self.close_calls = 0; self.close_failures = 0; self.closed = False
    def readiness(self): return dict(FLAGS)
    async def activate(self): self.activations += 1
    async def status(self): return {}
    async def close(self, **kwargs):
        self.close_calls += 1
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError("owned cleanup incomplete")
        await self.obs.emergency_stop(); self.closed = True


class Factory:
    def __init__(self, run=None):
        self.run = run or Run(); self.calls = 0; self.entered = asyncio.Event(); self.release = asyncio.Event()
        self.hold = False; self.late_after_cancel = False; self.callbacks = None
    async def rooms(self): return []
    async def prepare(self, *args, **kwargs):
        self.calls += 1; self.callbacks = kwargs; self.entered.set()
        if self.hold:
            try: await self.release.wait()
            except asyncio.CancelledError:
                if not self.late_after_cancel: raise
                await self.release.wait()
        kwargs["on_ready"](dict(FLAGS))
        return self.run


async def preview(controller):
    await controller.execute("preview", {"room_code": "ABC123"})
    await asyncio.wait_for(controller._prepare_task, 1)
    assert controller.state == "PreviewReady"


def permit(controller):
    return {"channel": "fedorr_codex", "broadcast_session_id": controller.broadcast_session_id,
            "confirmation": "START PUBLIC STREAM"}


async def stopped(controller):
    if controller._stop_task is not None: await asyncio.wait_for(controller._stop_task, 2)


async def test_double_preview_rejected_and_preview_never_publishes():
    factory = Factory(); controller = BroadcastCoordinator(factory)
    try:
        await preview(controller)
        with pytest.raises(CoordinatorError, match="already_owned"):
            await controller.execute("preview", {"room_code": "OTHER1"})
        assert factory.calls == 1 and factory.run.obs.starts == 0
        assert (await controller.status())["live"] is False
    finally: await controller.aclose()
    assert factory.run.closed


async def test_public_start_inflight_and_live_repeated_request_are_idempotent():
    factory = Factory(); controller = BroadcastCoordinator(factory); factory.run.obs.block_start = True
    try:
        await preview(controller)
        await controller.execute("public_start", permit(controller))
        await factory.run.obs.start_entered.wait()
        await controller.execute("public_start", permit(controller))
        factory.run.obs.start_release.set(); await controller._start_task
        await controller.execute("public_start", permit(controller))
        assert factory.run.obs.starts == 1 and controller.state == "Live"
    finally: await controller.aclose()


async def test_stop_during_late_prepare_closes_the_late_run_before_next_preview():
    factory = Factory(); factory.hold = factory.late_after_cancel = True
    controller = BroadcastCoordinator(factory)
    await controller.execute("preview", {"room_code": "ABC123"}); await factory.entered.wait()
    await controller.execute("stop", {})
    await asyncio.sleep(0); factory.release.set(); await stopped(controller)
    assert factory.run.closed and factory.run.activations == 0 and factory.run.obs.starts == 0
    assert controller.state == "Stopped"
    await controller.aclose()


async def test_unsafe_graph_during_start_never_commits_live_and_drains_owned_run():
    factory = Factory(); factory.run.obs.block_start = True; controller = BroadcastCoordinator(factory)
    await preview(controller); await controller.execute("public_start", permit(controller))
    await factory.run.obs.start_entered.wait()
    factory.callbacks["on_unsafe"]("obs_graph_changed")
    await stopped(controller)
    assert controller.state == "Failed" and factory.run.closed and not factory.run.obs.active
    assert controller.metrics["public_starts"] == 0
    await controller.aclose()


async def test_terminal_waits_actual_outro_finished_then_mux_drain():
    factory = Factory(); controller = BroadcastCoordinator(factory, mux_drain_seconds=.05)
    await preview(controller); controller.terminal()
    await asyncio.sleep(.02)
    assert controller.state == "Ending" and not factory.run.closed
    # Provider/TTS completion is deliberately absent: only scheduler's
    # playback-covered terminal signal releases the mux drain.
    factory.run.scheduler.finished = True
    for _ in range(30):
        if controller._stop_task is not None: break
        await asyncio.sleep(.01)
    await stopped(controller)
    assert controller.state == "Stopped" and factory.run.closed
    assert controller.metrics["terminal_drains"] == 1
    assert factory.run.obs.shown == ["ACTOR Ending"]
    await controller.aclose()


async def test_terminal_missing_playback_ack_has_real_bounded_timeout():
    factory = Factory(); controller = BroadcastCoordinator(factory, outro_timeout=5, mux_drain_seconds=0)
    await preview(controller); controller.terminal()
    await asyncio.wait_for(controller._ending_task, 6)
    await stopped(controller)
    assert controller.metrics["outro_timeouts"] == 1 and factory.run.closed
    assert controller.metrics["terminal_drains"] == 0
    await controller.aclose()


async def test_failed_close_retains_exact_owned_handle_for_retry_and_blocks_new_preview():
    factory = Factory(); factory.run.close_failures = 1; controller = BroadcastCoordinator(factory)
    await preview(controller); await controller.execute("stop", {}); await stopped(controller)
    try:
        assert controller.state == "Failed" and not factory.run.closed
        assert controller._run is factory.run, "A failed close cannot discard its only ownership handle"
        with pytest.raises(CoordinatorError, match="already_owned"):
            await controller.execute("preview", {"room_code": "OTHER1"})
        await controller.execute("stop", {}); await stopped(controller)
        assert factory.run.close_calls == 2 and factory.run.closed
    finally:
        # Fake-only emergency teardown also keeps a failing assertion from
        # hiding an orphaned background fixture in the test process.
        if not factory.run.closed: await factory.run.close(emergency=True)
        await controller.aclose()


async def test_late_prepare_close_failure_retains_cleanup_ownership_and_refuses_new_preview():
    # Both the late prepare's first cleanup and the enclosing stop's retry
    # fail. A later explicit Stop must still retain a way to finish cleanup.
    factory = Factory(); factory.hold = factory.late_after_cancel = True; factory.run.close_failures = 2
    controller = BroadcastCoordinator(factory)
    await controller.execute("preview", {"room_code": "ABC123"}); await factory.entered.wait()
    await controller.execute("stop", {}); await asyncio.sleep(0); factory.release.set(); await stopped(controller)
    try:
        assert controller.state == "Failed" and not factory.run.closed
        assert controller._run is factory.run
        with pytest.raises(CoordinatorError): await controller.execute("preview", {"room_code": "OTHER1"})
        await controller.execute("stop", {}); await stopped(controller)
        assert factory.run.closed and factory.run.close_calls == 3
    finally:
        if not factory.run.closed: await factory.run.close(emergency=True)
        await controller.aclose()


async def test_late_prepare_first_cleanup_failure_can_recover_in_enclosing_stop():
    factory = Factory(); factory.hold = factory.late_after_cancel = True; factory.run.close_failures = 1
    controller = BroadcastCoordinator(factory)
    await controller.execute("preview", {"room_code": "ABC123"}); await factory.entered.wait()
    await controller.execute("stop", {}); await asyncio.sleep(0); factory.release.set(); await stopped(controller)
    assert controller.state == "Stopped" and factory.run.closed and factory.run.close_calls == 2
    assert controller._run is None and factory.run.activations == 0
    await controller.aclose()
