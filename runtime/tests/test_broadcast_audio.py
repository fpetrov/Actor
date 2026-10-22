"""Synthetic PCM only: render ACK semantics, not OBS/viewer audibility."""
import asyncio
from dataclasses import asdict, replace
import json
import struct
import time

import pytest

from actor_broadcast.audio import AudioError, BrowserAudioSink, SpeechAudioOutput
from actor_broadcast.contracts import Cancel, FlushAck, PcmChunk, SynthesisDone, Scope, SegmentKey, SpeechSegment
from actor_broadcast.speech import decode_header, parse_header


def segment(**changes):
    return SpeechSegment(**(dict(scope=Scope("broadcast", "match", 1, 1), key=SegmentKey("speech", 1, 0),
        priority=2, text="Короткая проверка.", evidence_versions=(), semantic_revision=1,
        expires_at_monotonic_ns=time.monotonic_ns() + 10_000_000_000, max_audio_frames=24000) | changes))


def packet(kind, s, consumed=1):
    return {"type": kind, "scope": asdict(s.scope), "key": asdict(s.key),
            "consumed_frames": consumed, "underrun_frames": 0}


async def test_forged_completion_before_done_and_after_truncation_rejected():
    sink = BrowserAudioSink(route_guard=lambda: True); s = segment()
    sink._active = s; sink._sent = 100; sink._terminal = asyncio.get_running_loop().create_future()
    with pytest.raises(AudioError, match="unproven"): await sink._message(packet("complete", s, 100))
    sink._done = SynthesisDone(s.scope, s.key, 100, 5, True, "duration_bound")
    with pytest.raises(AudioError, match="unproven"): await sink._message(packet("complete", s, 100))
    assert not sink._terminal.done() and sink.metrics["played_segments"] == 0
    await sink._message(packet("interrupted", s, 100))
    assert sink._terminal.result().status == "interrupted"


async def test_stale_scope_frame_bounds_and_route_revocation_cannot_cover_text():
    sink = BrowserAudioSink(route_guard=lambda: False); s = segment()
    sink._active = s; sink._sent = 100; sink._done = SynthesisDone(s.scope, s.key, 100, 5, False, "generator_completed")
    other = replace(s, scope=replace(s.scope, view_epoch=2))
    await sink._message(packet("complete", other, 100))
    assert sink.metrics["stale_packets"] == 1 and sink._consumed == 0
    for invalid in (True, 101, -1):
        with pytest.raises(AudioError, match="frame_bound"): await sink._message(packet("progress", s, invalid))
    with pytest.raises(AudioError, match="route_lost"): await sink._message(packet("complete", s, 100))


async def synthetic_renderer(sink, *, truncate=False):
    websockets = pytest.importorskip("websockets.asyncio.client")
    from urllib.parse import urlsplit, parse_qs
    parsed = urlsplit(sink.renderer_url)
    async with websockets.connect(sink._origin.replace("http:", "ws:") + "/audio", origin=sink._origin,
                                  proxy=None, compression=None) as ws:
        await ws.send(json.dumps({"type": "authenticate", "capability": parse_qs(parsed.fragment)["cap"][0]}))
        await ws.send(json.dumps({"type": "ready", "rate": 48000, "worklet": True, "context_state": "running"}))
        total = 0
        async for raw in ws:
            h, p = parse_header(raw[:8]); kind, value = decode_header(raw[8:8+h])
            if kind == "pcm":
                total += p // 4
                await ws.send(json.dumps({"type": "progress", "scope": value["scope"], "key": value["key"],
                    "consumed_frames": total, "underrun_frames": 0}))
            elif kind == "end":
                await ws.send(json.dumps({"type": "complete" if value["complete"] else "interrupted",
                    "scope": value["scope"], "key": value["key"], "consumed_frames": total, "underrun_frames": 0}))
            elif kind == "flush":
                await ws.send(json.dumps({"type": "flushed", "scope": value["scope"],
                    "utterance_id": value["utterance_id"], "utterance_epoch": value["utterance_epoch"]}))


@pytest.mark.parametrize("truncated", [False, True])
async def test_real_loopback_binary_pcm_stream_and_actual_ack_roundtrip(truncated):
    pytest.importorskip("websockets.asyncio.client")
    acks = []; sink = BrowserAudioSink(route_guard=lambda: True, on_ack=acks.append)
    await sink.start(); renderer = asyncio.create_task(synthetic_renderer(sink)); s = segment()
    async def chunks():
        for i in range(2): yield PcmChunk(s.scope, s.key, i, i * 120, struct.pack("<120f", *([.1] * 120)))
        yield SynthesisDone(s.scope, s.key, 240, 6, truncated, "duration_bound" if truncated else "generator_completed")
    try:
        result = await sink.play(s, chunks(), validate=lambda _: True)
        assert result.status == ("interrupted" if truncated else "completed")
        assert acks[0].status == "started" and acks[-1].consumed_source_frames == 240
        assert all(a.clock_domain == "browser_audio_render" and a.received_at_monotonic <= time.monotonic() for a in acks)
        assert sink._server.sockets[0].getsockname()[0] == "127.0.0.1"
    finally:
        await sink.aclose(); renderer.cancel(); await asyncio.gather(renderer, return_exceptions=True)


async def test_real_loopback_wrong_origin_and_capability_do_not_attach():
    client = pytest.importorskip("websockets.asyncio.client")
    sink = BrowserAudioSink(route_guard=lambda: True); await sink.start()
    try:
        async with client.connect(sink._origin.replace("http:", "ws:") + "/audio", origin="https://foreign.invalid", proxy=None) as ws:
            with pytest.raises(Exception): await ws.recv()
        async with client.connect(sink._origin.replace("http:", "ws:") + "/audio", origin=sink._origin, proxy=None) as ws:
            await ws.send(json.dumps({"type": "authenticate", "capability": "wrong"}))
            with pytest.raises(Exception): await ws.recv()
        assert not sink._ready.is_set() and sink._ws is None
    finally: await sink.aclose()


async def test_heartbeat_revokes_audio_during_stalled_generator():
    pytest.importorskip("websockets.asyncio.client")
    allowed = True
    sink = BrowserAudioSink(route_guard=lambda: allowed)
    await sink.start(); renderer = asyncio.create_task(synthetic_renderer(sink)); s = segment()
    entered = asyncio.Event(); release = asyncio.Event()
    async def chunks():
        yield PcmChunk(s.scope, s.key, 0, 0, struct.pack("<f", .1))
        entered.set(); await release.wait()
        yield SynthesisDone(s.scope, s.key, 1, 1, False, "generator_completed")
    play = asyncio.create_task(sink.play(s, chunks(), validate=lambda _: True))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        allowed = False
        for _ in range(60):
            if sink._ws is None: break
            await asyncio.sleep(.01)
        assert sink._ws is None and not sink._ready.is_set()
    finally:
        release.set(); await asyncio.gather(play, return_exceptions=True)
        await sink.aclose(); renderer.cancel(); await asyncio.gather(renderer, return_exceptions=True)


async def test_output_cancel_drains_worker_even_when_browser_flush_fails():
    class Voice:
        cancelled = 0
        async def cancel(self, _): self.cancelled += 1
    class Sink:
        async def cancel(self, _, **k): raise AudioError("audio_renderer_disconnected")
    voice = Voice(); output = SpeechAudioOutput(voice, Sink(), validate=lambda _: True)
    s = segment(); await output.submit(s)
    # No worker is started until the scheduler gives the event loop a turn.
    with pytest.raises(AudioError): await output.cancel(Cancel(s.scope, "speech", 1))
    assert voice.cancelled == 1 and output._queue.empty()
    output._run_task.cancel(); await asyncio.gather(output._run_task, return_exceptions=True)
