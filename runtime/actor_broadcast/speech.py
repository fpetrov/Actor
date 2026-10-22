"""Optional local speech worker. No MLX, sound device or account import here."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import socket
import struct
import subprocess
import time
from typing import AsyncIterator

from .contracts import (Cancel, CancelAck, EvidenceRef, PcmChunk, Scope,
                        SegmentKey, SpeechSegment, SynthesisDone, WorkerGenerationMetrics)

MODEL_ID = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16"
MODEL_REVISION = "52f4770fd9726457eae3d3b6aa92047a25a10776"
MAX_HEADER = 16384
MAX_PCM = 131072
_PREFIX = struct.Struct("!II")


class SpeechError(RuntimeError):
    """Fixed machine code only: never include the text, model exception or PCM."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SpeechReady:
    model_id: str
    revision: str
    voice: str
    language: str
    rate: int
    warm: bool
    load_seconds: float
    warmup_seconds: float
    active_memory_bytes: int
    cache_memory_bytes: int
    peak_memory_bytes: int


@dataclass(frozen=True)
class MlxSpeechConfig:
    python_executable: Path
    model_path: Path
    startup_timeout: float = 90.0
    cancel_timeout: float = 3.0
    max_generation_seconds: float = 60.0
    cache_limit_bytes: int = 268435456
    memory_limit_bytes: int = 6442450944
    warmup_tokens: int = 48

    def __post_init__(self):
        # Preserve the venv entry point: resolving its python symlink loses
        # pyvenv.cfg discovery and silently selects the base interpreter.
        object.__setattr__(self, "python_executable", Path(self.python_executable).expanduser().absolute())
        object.__setattr__(self, "model_path", Path(self.model_path).expanduser().resolve())
        if not self.python_executable.is_file() or not self.model_path.is_dir():
            raise SpeechError("speech_environment_missing")
        for v in (self.startup_timeout, self.cancel_timeout, self.max_generation_seconds):
            if type(v) not in (float, int) or not math.isfinite(v) or not 0 < v <= 180:
                raise SpeechError("speech_timeout_invalid")
        if type(self.warmup_tokens) is not int or not 1 <= self.warmup_tokens <= 64:
            raise SpeechError("speech_warmup_bound_invalid")
        for v in (self.cache_limit_bytes, self.memory_limit_bytes):
            if type(v) is not int or not 0 <= v <= 8 * 1024**3:
                raise SpeechError("speech_memory_bound_invalid")


def _exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise SpeechError("speech_frame_shape")
    return value


def scope_from(value):
    return Scope(**_exact(value, ("broadcast_session_id", "match_id", "connection_generation", "view_epoch")))


def key_from(value):
    return SegmentKey(**_exact(value, ("utterance_id", "utterance_epoch", "segment_index")))


def segment_from(value):
    x = dict(_exact(value, ("scope", "key", "priority", "text", "evidence_versions",
        "semantic_revision", "expires_at_monotonic_ns", "max_tokens", "max_audio_frames")))
    x["scope"] = scope_from(x["scope"]); x["key"] = key_from(x["key"])
    if not isinstance(x["evidence_versions"], list) or len(x["evidence_versions"]) > 8:
        raise SpeechError("speech_evidence_bound")
    x["evidence_versions"] = tuple(EvidenceRef(**_exact(r, ("id", "version"))) for r in x["evidence_versions"])
    return SpeechSegment(**x)


def cancel_from(value):
    x = dict(_exact(value, ("scope", "utterance_id", "utterance_epoch")))
    x["scope"] = scope_from(x["scope"])
    SegmentKey(x["utterance_id"], x["utterance_epoch"], 0)
    return Cancel(**x)


def generation_metrics_from(value):
    x = dict(_exact(value,WorkerGenerationMetrics.__dataclass_fields__))
    x["scope"] = scope_from(x["scope"]); x["key"] = key_from(x["key"])
    return WorkerGenerationMetrics(**x)


def frame_bytes(kind: str, value: dict, pcm: bytes = b"") -> bytes:
    if not isinstance(kind, str) or len(kind) > 32 or not isinstance(pcm, bytes):
        raise SpeechError("speech_frame_shape")
    header = json.dumps({"type": kind, "value": value}, ensure_ascii=False,
                        separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(header) > MAX_HEADER or len(pcm) > MAX_PCM:
        raise SpeechError("speech_frame_bound")
    return _PREFIX.pack(len(header), len(pcm)) + header + pcm


def parse_header(prefix: bytes):
    if len(prefix) != _PREFIX.size: raise SpeechError("speech_frame_prefix")
    h, p = _PREFIX.unpack(prefix)
    if not 0 < h <= MAX_HEADER or p > MAX_PCM: raise SpeechError("speech_frame_bound")
    return h, p


def decode_header(header: bytes):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out: raise SpeechError("speech_frame_duplicate_key")
            out[k] = v
        return out
    x = json.loads(header.decode("utf-8"), object_pairs_hook=pairs,
                   parse_constant=lambda _: (_ for _ in ()).throw(SpeechError("speech_frame_nonfinite")))
    _exact(x, ("type", "value"))
    if not isinstance(x["type"], str) or not isinstance(x["value"], dict):
        raise SpeechError("speech_frame_shape")
    return x["type"], x["value"]


def validate_pcm(pcm: bytes):
    if not pcm or len(pcm) > MAX_PCM or len(pcm) % 4:
        raise SpeechError("speech_pcm_shape")
    if any(not math.isfinite(x[0]) or abs(x[0]) > 1.0001 for x in struct.iter_unpack("<f", pcm)):
        raise SpeechError("speech_pcm_nonfinite_or_clipped")


async def _read_exact(sock, count):
    loop = asyncio.get_running_loop(); pieces = []; remaining = count
    while remaining:
        part = await loop.sock_recv(sock, remaining)
        if not part: raise SpeechError("speech_worker_closed")
        pieces.append(part); remaining -= len(part)
    return b"".join(pieces)


class MlxSpeechSynthesizer:
    """One owned persistent process, one generator, bounded framed socketpair.

    The process is offline. Tokens/text/reference audio never enter argv/logs.
    MLX's memory limit is a guideline, not an OS hard memory guarantee.
    """
    def __init__(self, config: MlxSpeechConfig, *, process_factory=subprocess.Popen,
                 on_generation_metrics=None):
        self.config = config; self._process_factory = process_factory
        self._process = None; self._socket = None; self._reader = None
        self._ready = None; self._ready_value = None; self._closed = False
        self._send_lock = asyncio.Lock(); self._single = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._queue = asyncio.Queue(maxsize=12)
        self._active = None; self._cancel_futures = {}; self._fenced = set()
        self._seen = OrderedDict()
        self.on_generation_metrics = on_generation_metrics
        self._pending_generation_metrics = None; self._worker_done_received = False
        self._active_request = None
        self.metrics = {"segments": 0, "chunks": 0, "stale_chunks": 0, "cancelled": 0,
                        "worker_restarts": 0, "pcm_frames": 0, "generation_metrics": 0,
                        "discarded_generation_metrics": 0, "generation_metrics_callback_errors": 0}

    async def _send(self, kind, value):
        if self._socket is None: raise SpeechError("speech_worker_unavailable")
        async with self._send_lock:
            await asyncio.wait_for(asyncio.get_running_loop().sock_sendall(
                self._socket, frame_bytes(kind, value)), 3.0)

    async def warmup(self) -> SpeechReady:
        if self._closed: raise SpeechError("speech_closed")
        if self._ready_value is not None: return self._ready_value
        if self._ready is None:
            self._ready = asyncio.get_running_loop().create_future()
            parent, child = socket.socketpair()
            parent.setblocking(False)
            try:
                command = [str(self.config.python_executable), "-I", "-B",
                    str(Path(__file__).with_name("tts_worker.py")), "--fd", str(child.fileno())]
                env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "PYTHONUTF8": "1",
                       "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                       "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "TOKENIZERS_PARALLELISM": "false"}
                from .guardian_client import inherited_group_options
                self._process = self._process_factory(command, pass_fds=(child.fileno(),),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    **inherited_group_options(), env=env)
            except BaseException:
                parent.close(); self._ready = None; raise
            finally: child.close()
            self._socket = parent
            self._reader = asyncio.create_task(self._receive(), name="actor-broadcast-tts-reader")
            try:
                await self._send("initialize", {"model_path": str(self.config.model_path),
                    "model_id": MODEL_ID, "revision": MODEL_REVISION, "voice": "Ryan", "language": "Russian",
                    "cache_limit_bytes": self.config.cache_limit_bytes,
                    "memory_limit_bytes": self.config.memory_limit_bytes,
                    "warmup_tokens": self.config.warmup_tokens})
            except BaseException:
                await self._stop_process(); raise
        try:
            self._ready_value = await asyncio.wait_for(asyncio.shield(self._ready), self.config.startup_timeout)
            return self._ready_value
        except asyncio.CancelledError: raise
        except BaseException:
            await self._stop_process(); raise SpeechError("speech_warmup_failed") from None

    async def _receive(self):
        try:
            while True:
                h, p = parse_header(await _read_exact(self._socket, _PREFIX.size))
                kind, body = decode_header(await _read_exact(self._socket, h))
                pcm = await _read_exact(self._socket, p) if p else b""
                if kind == "ready":
                    if pcm or self._ready.done(): raise SpeechError("speech_unexpected_ready")
                    ready = SpeechReady(**_exact(body, SpeechReady.__dataclass_fields__))
                    if (ready.model_id, ready.revision, ready.voice, ready.language, ready.rate, ready.warm) != (
                            MODEL_ID, MODEL_REVISION, "Ryan", "Russian", 24000, True):
                        raise SpeechError("speech_worker_identity_mismatch")
                    if type(ready.rate) is not int or ready.warm is not True:
                        raise SpeechError("speech_ready_shape")
                    for value in (ready.load_seconds, ready.warmup_seconds):
                        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                            raise SpeechError("speech_ready_shape")
                    for value in (ready.active_memory_bytes, ready.cache_memory_bytes, ready.peak_memory_bytes):
                        if type(value) is not int or not 0 <= value <= 64 * 1024**3:
                            raise SpeechError("speech_ready_shape")
                    self._ready.set_result(ready)
                elif kind == "cancel_ack":
                    if pcm: raise SpeechError("speech_frame_shape")
                    x = dict(_exact(body, CancelAck.__dataclass_fields__)); x["scope"] = scope_from(x["scope"])
                    ack = CancelAck(**x)
                    key = (ack.scope, ack.utterance_id, ack.utterance_epoch)
                    fut = self._cancel_futures.get(key)
                    if fut is not None and not fut.done(): fut.set_result(ack)
                elif kind == "generation_metrics":
                    if pcm: raise SpeechError("speech_frame_shape")
                    item = generation_metrics_from(body)
                    fence = (item.scope,item.key.utterance_id,item.key.utterance_epoch)
                    if (self._active != (item.scope,item.key) or fence in self._fenced or
                            self._worker_done_received or self._pending_generation_metrics is not None):
                        self.metrics["discarded_generation_metrics"] += 1; continue
                    if self._active_request is None or item.total_source_frames>self._active_request.max_audio_frames:
                        raise SpeechError("speech_generation_metrics_bound")
                    self._pending_generation_metrics = item
                elif kind in ("pcm", "done"):
                    x = dict(body); scope = scope_from(x.pop("scope")); key = key_from(x.pop("key"))
                    if self._active != (scope, key):
                        self.metrics["stale_chunks"] += 1; continue
                    if kind == "pcm":
                        if (scope, key.utterance_id, key.utterance_epoch) in self._fenced:
                            self.metrics["stale_chunks"] += 1; continue
                        _exact(x, ("chunk_seq", "source_frame_offset", "rate", "channels", "format"))
                        validate_pcm(pcm); item = PcmChunk(scope=scope, key=key, pcm=pcm, **x)
                    else:
                        if pcm: raise SpeechError("speech_frame_shape")
                        _exact(x, ("total_source_frames", "token_count", "may_be_truncated", "terminal_reason"))
                        item = SynthesisDone(scope=scope, key=key, **x)
                        self._worker_done_received = True
                        metrics = self._pending_generation_metrics
                        if metrics is not None and any(getattr(metrics,name)!=getattr(item,name) for name in (
                                "total_source_frames","token_count","may_be_truncated","terminal_reason")):
                            raise SpeechError("speech_generation_metrics_mismatch")
                    try: self._queue.put_nowait(item)
                    except asyncio.QueueFull: raise SpeechError("speech_backpressure_overflow") from None
                elif kind == "error":
                    raise SpeechError("speech_worker_failed")
                else: raise SpeechError("speech_unknown_frame")
        except asyncio.CancelledError: pass
        except BaseException:
            error = SpeechError("speech_worker_failed")
            if self._ready is not None and not self._ready.done(): self._ready.set_exception(error)
            for fut in self._cancel_futures.values():
                if not fut.done(): fut.set_exception(error)
            while not self._queue.empty(): self._queue.get_nowait()
            self._queue.put_nowait(error)
            await self._stop_process()

    async def synthesize(self, segment: SpeechSegment) -> AsyncIterator[PcmChunk | SynthesisDone]:
        if not isinstance(segment, SpeechSegment): raise SpeechError("speech_segment_type")
        async with self._single:
            await self.warmup()
            identity = (segment.scope, segment.key)
            utterance = (segment.scope, segment.key.utterance_id, segment.key.utterance_epoch)
            if identity in self._seen or utterance in self._fenced:
                raise SpeechError("speech_segment_replayed")
            if segment.expires_at_monotonic_ns <= time.monotonic_ns(): raise SpeechError("speech_segment_expired")
            self._seen[identity] = None
            while len(self._seen) > 256: self._seen.popitem(last=False)
            while not self._queue.empty(): self._queue.get_nowait()
            self._active = identity; self._active_request = segment
            self._pending_generation_metrics = None; self._worker_done_received = False
            complete = False; expected_seq = 0; frames = 0
            deadline = min(segment.expires_at_monotonic_ns / 1e9,
                           time.monotonic() + self.config.max_generation_seconds)
            self.metrics["segments"] += 1
            try:
                await self._send("synthesize", asdict(segment))
                while True:
                    timeout = deadline - time.monotonic()
                    if timeout <= 0: raise SpeechError("speech_segment_expired")
                    item = await asyncio.wait_for(self._queue.get(), timeout)
                    if isinstance(item, SpeechError): raise item
                    if isinstance(item, PcmChunk):
                        if time.monotonic_ns() >= segment.expires_at_monotonic_ns:
                            raise SpeechError("speech_segment_expired")
                        if item.chunk_seq != expected_seq or item.source_frame_offset != frames:
                            raise SpeechError("speech_pcm_order")
                        frames += len(item.pcm) // 4; expected_seq += 1
                        if frames > segment.max_audio_frames: raise SpeechError("speech_pcm_duration_bound")
                        self.metrics["chunks"] += 1; self.metrics["pcm_frames"] += len(item.pcm) // 4
                        yield item
                    else:
                        if item.total_source_frames != frames and utterance not in self._fenced:
                            raise SpeechError("speech_terminal_frame_mismatch")
                        metrics = self._pending_generation_metrics
                        if metrics is not None and utterance not in self._fenced:
                            self.metrics["generation_metrics"] += 1
                            if self.on_generation_metrics is not None:
                                try: self.on_generation_metrics(metrics)
                                except Exception: self.metrics["generation_metrics_callback_errors"] += 1
                        complete = True; yield item; return
            finally:
                if not complete:
                    try: await _cleanup(self.cancel(Cancel(segment.scope, segment.key.utterance_id,
                                                           segment.key.utterance_epoch)), self.config.cancel_timeout + 4)
                    except BaseException: await _cleanup(self._stop_process(), 4)
                self._active = None
                self._active_request = None; self._pending_generation_metrics = None

    async def cancel(self, request: Cancel) -> CancelAck:
        key = (request.scope, request.utterance_id, request.utterance_epoch)
        self._fenced.add(key)
        # Producer frames already read before the fence must not fill the
        # bounded queue while the consumer is cancelling.
        retained = []
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if not isinstance(item, PcmChunk) or (item.scope, item.key.utterance_id, item.key.utterance_epoch) != key:
                retained.append(item)
        for item in retained: self._queue.put_nowait(item)
        if len(self._fenced) > 512:
            # Never evict a fence and accidentally admit an old utterance.
            await self.aclose(); raise SpeechError("speech_fence_capacity")
        if self._socket is None or self._active is None or self._active[0] != request.scope or (
                self._active[1].utterance_id, self._active[1].utterance_epoch) != key[1:]:
            return CancelAck(*key, 0, True)
        fut = self._cancel_futures.get(key)
        if fut is None:
            fut = asyncio.get_running_loop().create_future(); self._cancel_futures[key] = fut
            await self._send("cancel", asdict(request))
        try:
            ack = await asyncio.wait_for(asyncio.shield(fut), self.config.cancel_timeout)
            if ack.emission_stopped is not True: raise SpeechError("speech_cancel_unconfirmed")
            self.metrics["cancelled"] += 1
            return ack
        except asyncio.CancelledError:
            await _cleanup(self._stop_process(), 4); raise
        except Exception:
            await _cleanup(self._stop_process(), 4)
            raise SpeechError("speech_cancel_timeout") from None
        finally:
            if fut.done(): self._cancel_futures.pop(key, None)

    async def _stop_process(self):
        async with self._stop_lock:
            if self._socket is not None:
                self._socket.close(); self._socket = None
            task = self._reader
            # Do not await a reader that is itself waiting for this lock.
            if task is not None and task is not asyncio.current_task(): task.cancel()
            proc = self._process
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try: await asyncio.to_thread(proc.wait, 1.5)
                except subprocess.TimeoutExpired:
                    proc.kill(); await asyncio.to_thread(proc.wait, 1.5)
            self._process = None
            self._ready_value = None

    async def aclose(self):
        self._closed = True
        await _cleanup(self._stop_process(), 4)


async def _cleanup(awaitable, timeout):
    """Drain owned cleanup under repeated outer cancellation, with a deadline."""
    task = asyncio.create_task(awaitable)
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError
            try: return await asyncio.wait_for(asyncio.shield(task), remaining)
            except asyncio.CancelledError:
                if task.done(): return task.result()
    finally:
        if not task.done():
            task.cancel()
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
