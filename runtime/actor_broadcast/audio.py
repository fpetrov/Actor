"""One authenticated local Browser Source, with render-frame playback ACKs.

No audio device, desktop audio, camera or microphone is opened by this module.
Browser ACKs certify the local graph only, not Twitch end-user audibility.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from .contracts import Cancel, FlushAck, PcmChunk, PlaybackAck, Scope, SegmentKey, SpeechSegment, SynthesisDone
from .speech import SpeechError, _cleanup, frame_bytes, key_from, scope_from, validate_pcm


class AudioError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


class BrowserAudioSink:
    def __init__(self, *, route_guard, on_ack=None, ack_timeout=4.0, port=0):
        if not callable(route_guard): raise ValueError("An explicit audited route guard is required")
        if type(ack_timeout) not in (int, float) or not math.isfinite(ack_timeout) or not .25 <= ack_timeout <= 10:
            raise ValueError("Bounded playback ACK timeout required")
        if type(port) is not int or not 0 <= port <= 65535: raise ValueError("Invalid loopback port")
        self.route_guard = route_guard; self.on_ack = on_ack; self.ack_timeout = ack_timeout; self.port = port
        self._capability = secrets.token_urlsafe(32)
        self._server = None; self._ws = None; self._ready = asyncio.Event(); self._closed = False
        self._send_lock = asyncio.Lock(); self._single = asyncio.Lock(); self._condition = asyncio.Condition()
        self._active = None; self._sent = 0; self._consumed = 0; self._done = None
        self._terminal = None; self._flushed = {}; self._started = False; self._ended = False
        self._heartbeat = None; self._origin = None
        self._validate = None
        self.metrics = {"played_segments": 0, "interrupted_segments": 0, "underrun_frames": 0,
                        "stale_packets": 0, "sent_frames": 0, "consumed_frames": 0}

    @property
    def renderer_url(self):
        if self._origin is None: raise AudioError("audio_server_not_started")
        # Trusted OBS setup only. Never include this capability URL in logs.
        return self._origin + "/renderer/#cap=" + self._capability

    @property
    def public_status(self):
        return {"started": self._server is not None, "renderer_ready": self._ready.is_set(),
                "active": self._active is not None, **self.metrics}

    async def start(self):
        if self._closed: raise AudioError("audio_closed")
        if self._server is not None: return
        try:
            from websockets.asyncio.server import serve
            from websockets.http11 import Response
            from websockets.datastructures import Headers
        except ImportError: raise AudioError("broadcast_websockets_dependency_missing") from None
        resource = Path(__file__).with_name("resources")
        files = {"/renderer/": ("overlay.html", "text/html; charset=utf-8"),
                 "/renderer/renderer.js": ("renderer.js", "application/javascript"),
                 "/renderer/audio-worklet.js": ("audio-worklet.js", "application/javascript")}

        async def process_request(connection, request):
            if self._origin is None or request.headers.get("Host") != urlsplit(self._origin).netloc:
                return Response(403, "Forbidden", Headers(), b"")
            if request.path == "/audio": return None
            entry = files.get(request.path)
            if entry is None: return Response(404, "Not Found", Headers(), b"")
            body = (resource / entry[0]).read_bytes()
            headers = Headers({"Content-Type": entry[1], "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; worker-src 'self'; style-src 'unsafe-inline'; connect-src " + self._origin.replace("http:", "ws:") + "; frame-ancestors 'none'; base-uri 'none'",
                "Permissions-Policy": "camera=(), microphone=(), display-capture=()",
                "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
            return Response(200, "OK", headers, body)

        self._server = await serve(self._handle, "127.0.0.1", self.port, process_request=process_request,
            max_size=16384, max_queue=8, compression=None, ping_interval=10, ping_timeout=5, close_timeout=2)
        self.port = self._server.sockets[0].getsockname()[1]
        self._origin = "http://127.0.0.1:" + str(self.port)

    async def _send(self, kind, value, pcm=b""):
        if self._ws is None: raise AudioError("audio_renderer_disconnected")
        async with self._send_lock:
            await asyncio.wait_for(self._ws.send(frame_bytes(kind, value, pcm)), self.ack_timeout)

    async def _heartbeats(self):
        try:
            while True:
                if self._active is not None and (not self.route_guard()
                        or time.monotonic_ns() >= self._active.expires_at_monotonic_ns
                        or (self._validate is not None and not self._validate(self._active))):
                    # Stop heartbeat and close the socket. The worklet clears
                    # on close, and also has its independent 1-second deadman.
                    raise AudioError("audio_active_scope_revoked")
                await self._send("heartbeat", {})
                await asyncio.sleep(.25)
        except asyncio.CancelledError: pass
        except BaseException:
            if self._ws is not None: await self._ws.close(code=1011, reason="controller_unavailable")

    async def _handle(self, ws):
        attached = False
        try:
            if ws.request.path != "/audio" or ws.request.headers.get("Origin") != self._origin:
                await ws.close(code=1008, reason="origin_rejected"); return
            raw = await asyncio.wait_for(ws.recv(), 3)
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4096:
                raise AudioError("audio_auth_shape")
            auth = json.loads(raw)
            if set(auth) != {"type", "capability"} or auth["type"] != "authenticate" or not isinstance(auth["capability"], str) or not secrets.compare_digest(auth["capability"], self._capability):
                raise AudioError("audio_auth_rejected")
            if self._ws is not None: raise AudioError("audio_renderer_already_attached")
            self._ws = ws; attached = True
            self._heartbeat = asyncio.create_task(self._heartbeats(), name="actor-broadcast-audio-heartbeat")
            async for raw in ws:
                if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16384:
                    raise AudioError("audio_ack_shape")
                await self._message(json.loads(raw))
        except asyncio.CancelledError: raise
        except BaseException:
            try: await ws.close(code=1008, reason="audio_protocol_rejected")
            except BaseException: pass
        finally:
            if attached and self._ws is ws:
                self._ready.clear(); self._ws = None
                if self._heartbeat is not None:
                    self._heartbeat.cancel(); await asyncio.gather(self._heartbeat, return_exceptions=True)
                if self._terminal is not None and not self._terminal.done():
                    self._terminal.set_exception(AudioError("audio_renderer_disconnected"))
                for fut in self._flushed.values():
                    if not fut.done(): fut.set_exception(AudioError("audio_renderer_disconnected"))
                async with self._condition: self._condition.notify_all()

    def _ack(self, status, consumed, total):
        segment = self._active
        if segment is None: return None
        ack = PlaybackAck(segment.scope, segment.key, status, consumed, max(1, total), time.monotonic())
        if self.on_ack is not None: self.on_ack(ack)
        return ack

    async def _message(self, message):
        if not isinstance(message, dict): raise AudioError("audio_ack_shape")
        kind = message.get("type")
        if kind == "ready":
            if set(message) != {"type", "rate", "worklet", "context_state"} or message["rate"] != 48000 or message["worklet"] is not True or message["context_state"] != "running":
                raise AudioError("audio_renderer_not_ready")
            self._ready.set(); return
        if kind == "not_ready":
            self._ready.clear(); raise AudioError("audio_context_suspended")
        if kind == "flushed":
            if set(message) != {"type", "scope", "utterance_id", "utterance_epoch"}:
                raise AudioError("audio_flush_shape")
            scope = scope_from(message["scope"])
            key = (scope, message["utterance_id"], message["utterance_epoch"])
            fut = self._flushed.get(key)
            if fut is not None and not fut.done(): fut.set_result(FlushAck(*key, True))
            return
        if kind not in ("progress", "complete", "interrupted"):
            raise AudioError("audio_ack_unknown")
        if set(message) != {"type", "scope", "key", "consumed_frames", "underrun_frames"}:
            raise AudioError("audio_ack_shape")
        scope = scope_from(message["scope"]); key = key_from(message["key"])
        if self._active is None or (scope, key) != (self._active.scope, self._active.key):
            self.metrics["stale_packets"] += 1; return
        consumed = message["consumed_frames"]; underrun = message["underrun_frames"]
        if type(consumed) is not int or not self._consumed <= consumed <= self._sent or type(underrun) is not int or not 0 <= underrun <= 48000 * 600:
            raise AudioError("audio_ack_frame_bound")
        if self._ended: self.metrics["stale_packets"] += 1; return
        self.metrics["consumed_frames"] += consumed - self._consumed
        self._consumed = consumed
        self.metrics["underrun_frames"] += underrun
        if consumed and not self._started:
            self._started = True; self._ack("started", consumed, self._sent)
        if kind == "progress": self._ack("progress", consumed, self._sent)
        else:
            if kind == "complete":
                if self._done is None or self._done.may_be_truncated or self._done.terminal_reason != "generator_completed" or consumed != self._done.total_source_frames or not self.route_guard():
                    raise AudioError("audio_completion_unproven")
                status = "completed"; self.metrics["played_segments"] += 1
            else:
                status = "interrupted"; self.metrics["interrupted_segments"] += 1
            self._ended = True
            ack = self._ack(status, consumed, self._sent)
            if self._terminal is not None and not self._terminal.done(): self._terminal.set_result(ack)
        async with self._condition: self._condition.notify_all()

    async def wait_ready(self, timeout=10):
        await asyncio.wait_for(self._ready.wait(), timeout)
        if not self.route_guard(): raise AudioError("audio_obs_route_not_verified")

    async def play(self, segment: SpeechSegment, chunks, *, validate):
        async with self._single:
            await self.wait_ready()
            if not validate(segment) or time.monotonic_ns() >= segment.expires_at_monotonic_ns:
                raise AudioError("audio_segment_stale")
            self._active = segment; self._validate = validate; self._sent = self._consumed = 0
            self._done = None; self._started = self._ended = False
            self._terminal = asyncio.get_running_loop().create_future()
            expected_seq = 0
            try:
                await self._send("begin", {"scope": asdict(segment.scope), "key": asdict(segment.key),
                    "text": segment.text})
                async for item in chunks:
                    if not validate(segment) or time.monotonic_ns() >= segment.expires_at_monotonic_ns or not self.route_guard():
                        raise AudioError("audio_segment_stale")
                    if (item.scope, item.key) != (segment.scope, segment.key): raise AudioError("audio_chunk_identity")
                    if isinstance(item, PcmChunk):
                        if item.chunk_seq != expected_seq or item.source_frame_offset != self._sent:
                            raise AudioError("audio_chunk_order")
                        validate_pcm(item.pcm); expected_seq += 1
                        frames = len(item.pcm) // 4
                        if self._sent + frames > segment.max_audio_frames: raise AudioError("audio_duration_bound")
                        async with self._condition:
                            await asyncio.wait_for(self._condition.wait_for(lambda: self._ended or self._ws is None or self._sent - self._consumed + frames <= 72000), self.ack_timeout)
                        if self._ws is None: raise AudioError("audio_renderer_disconnected")
                        if self._ended: raise AudioError("audio_playback_interrupted")
                        self._sent += frames; self.metrics["sent_frames"] += frames
                        await self._send("pcm", {"scope": asdict(item.scope), "key": asdict(item.key),
                            "chunk_seq": item.chunk_seq, "source_frame_offset": item.source_frame_offset}, item.pcm)
                    elif isinstance(item, SynthesisDone):
                        if self._done is not None or item.total_source_frames != self._sent or not self._sent:
                            raise AudioError("audio_synthesis_end_mismatch")
                        self._done = item
                        await self._send("end", {"scope": asdict(item.scope), "key": asdict(item.key),
                            "total_source_frames": item.total_source_frames,
                            "complete": not item.may_be_truncated and item.terminal_reason == "generator_completed"})
                    else: raise AudioError("audio_chunk_type")
                if self._done is None: raise AudioError("audio_synthesis_end_missing")
                remaining = max(0., (self._sent - self._consumed) / 24000) + self.ack_timeout
                return await asyncio.wait_for(asyncio.shield(self._terminal), remaining)
            finally:
                if not self._ended:
                    try: await _cleanup(self.cancel(Cancel(segment.scope, segment.key.utterance_id,
                                                          segment.key.utterance_epoch), fade_seconds=0), self.ack_timeout + 1)
                    except BaseException: pass
                closer = getattr(chunks, "aclose", None)
                if closer is not None:
                    try: await _cleanup(closer(), 5)
                    except BaseException: pass
                if self._terminal is not None and self._terminal.done() and not self._terminal.cancelled():
                    self._terminal.exception()
                self._active = None; self._validate = None

    async def cancel(self, request: Cancel, *, fade_seconds=0.03):
        if type(fade_seconds) not in (int, float) or not math.isfinite(fade_seconds) or not 0 <= fade_seconds <= .1:
            raise AudioError("audio_fade_bound")
        segment = self._active
        if segment is None or segment.scope != request.scope or (segment.key.utterance_id, segment.key.utterance_epoch) != (request.utterance_id, request.utterance_epoch):
            return FlushAck(request.scope, request.utterance_id, request.utterance_epoch, True)
        key = (request.scope, request.utterance_id, request.utterance_epoch)
        fut = self._flushed.get(key)
        if fut is None:
            fut = asyncio.get_running_loop().create_future(); self._flushed[key] = fut
            await self._send("flush", {"scope": asdict(request.scope), "utterance_id": request.utterance_id,
                "utterance_epoch": request.utterance_epoch, "fade_frames": int(fade_seconds * 48000)})
        try:
            ack = await asyncio.wait_for(asyncio.shield(fut), self.ack_timeout)
            if not self._ended:
                self._ended = True; self.metrics["interrupted_segments"] += 1
                result = self._ack("interrupted", self._consumed, self._sent)
                if self._terminal is not None and not self._terminal.done(): self._terminal.set_result(result)
            async with self._condition: self._condition.notify_all()
            return ack
        finally:
            if fut.done(): self._flushed.pop(key, None)

    async def aclose(self):
        self._closed = True
        if self._active is not None:
            s = self._active
            try: await self.cancel(Cancel(s.scope, s.key.utterance_id, s.key.utterance_epoch), fade_seconds=0)
            except BaseException: pass
        if self._ws is not None: await self._ws.close(code=1000, reason="owned_stop")
        if self._server is not None:
            self._server.close(); await asyncio.wait_for(self._server.wait_closed(), 3)
        self._ready.clear()


class SpeechAudioOutput:
    """Scheduler-facing one-voice adapter; evidence validation stays with owner."""
    def __init__(self, synthesizer, sink: BrowserAudioSink, *, validate, on_error=None):
        self.synthesizer = synthesizer; self.sink = sink; self.validate = validate; self.on_error = on_error
        self._queue = asyncio.Queue(maxsize=2); self._run_task = None; self._job = None; self._active = None
        self._closed = False

    async def prepare(self):
        await self.sink.start()
        return await self.synthesizer.warmup()

    async def submit(self, segment):
        if self._closed or not self.validate(segment) or time.monotonic_ns() >= segment.expires_at_monotonic_ns:
            raise AudioError("speech_admission_stale")
        if self._queue.qsize() + int(self._active is not None) >= 2:
            raise AudioError("speech_queue_full")
        # Safety cap is explicit in the actual request; truncated output can
        # never produce a completed textual-coverage ACK.
        segment = replace(segment, max_audio_frames=min(segment.max_audio_frames, 24000 * 12))
        self._queue.put_nowait(segment)
        if self._run_task is None: self._run_task = asyncio.create_task(self._run(), name="actor-one-voice")

    async def _run(self):
        while not self._closed:
            segment = await self._queue.get(); self._active = segment
            try:
                if not self.validate(segment): continue
                self._job = asyncio.create_task(self.sink.play(segment, self.synthesizer.synthesize(segment),
                                                              validate=self.validate))
                await self._job
            except asyncio.CancelledError:
                if self._closed: return
            except BaseException as error:
                if self.on_error is not None: self.on_error(getattr(error, "code", "speech_output_failed"))
            finally: self._active = None; self._job = None

    async def cancel(self, request, *, fade_seconds=0.03):
        kept = []
        while not self._queue.empty():
            s = self._queue.get_nowait()
            if (s.scope, s.key.utterance_id, s.key.utterance_epoch) != (request.scope, request.utterance_id, request.utterance_epoch): kept.append(s)
        for s in kept: self._queue.put_nowait(s)
        try:
            ack = await self.sink.cancel(request, fade_seconds=fade_seconds)
        finally:
            try: await _cleanup(self.synthesizer.cancel(request), 5)
            finally:
                s = self._active
                if s is not None and (s.scope, s.key.utterance_id, s.key.utterance_epoch) == (request.scope, request.utterance_id, request.utterance_epoch):
                    if self._job is not None:
                        self._job.cancel(); await _drain_task(self._job, 6)
        return ack

    async def close(self):
        self._closed = True
        if self._active is not None:
            s = self._active
            try: await self.cancel(Cancel(s.scope, s.key.utterance_id, s.key.utterance_epoch), fade_seconds=0)
            except BaseException: pass
        if self._run_task is not None:
            self._run_task.cancel(); await _drain_task(self._run_task, 6)
        try: await _cleanup(self.sink.aclose(), 6)
        finally: await _cleanup(self.synthesizer.aclose(), 5)


async def _drain_task(task, timeout):
    done, pending = await asyncio.wait((task,), timeout=timeout)
    if pending:
        task.cancel()
        task.add_done_callback(lambda value: None if value.cancelled() else value.exception())
        raise AudioError("audio_cleanup_unconfirmed")
    if not task.cancelled(): task.exception()
