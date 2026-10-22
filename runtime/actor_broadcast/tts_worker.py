"""Private offline MLX process. Only an inherited socket is its control channel.

No microphone, output device, voice reference, account or network operation.
Run via MlxSpeechSynthesizer, not as a per-phrase CLI.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
from dataclasses import asdict
import importlib.metadata
import json
from pathlib import Path
import queue
import socket
import struct
import threading
import time

from actor_broadcast.contracts import CancelAck, SynthesisDone, WorkerGenerationMetrics
from actor_broadcast.speech import (MODEL_ID, MODEL_REVISION, SpeechError, SpeechReady,
    MAX_PCM, _exact, _PREFIX, cancel_from, decode_header, frame_bytes,
    parse_header, segment_from)


def _read(sock, n):
    data = bytearray()
    while len(data) < n:
        part = sock.recv(n - len(data))
        if not part: raise EOFError
        data.extend(part)
    return bytes(data)


def verify_local_bf16(path):
    """Metadata only, before loading; rejects quantized/foreign model paths."""
    path = Path(path).resolve()
    if not path.is_dir(): raise SpeechError("tts_model_missing")
    cfg = json.loads((path / "config.json").read_text())
    if cfg.get("tts_model_type") != "custom_voice" or cfg.get("model_type") != "qwen3_tts":
        raise SpeechError("tts_model_kind")
    if "quantization" in cfg or "quantization_config" in cfg:
        raise SpeechError("tts_quantized_model_forbidden")
    if "ryan" not in cfg.get("talker_config", {}).get("spk_id", {}):
        raise SpeechError("tts_voice_missing")
    if "russian" not in cfg.get("talker_config", {}).get("codec_language_id", {}):
        raise SpeechError("tts_language_missing")
    weights = path / "model.safetensors"
    with weights.open("rb") as stream:
        size = struct.unpack("<Q", stream.read(8))[0]
        if not 0 < size <= 2 * 1024 * 1024: raise SpeechError("tts_weight_header_bound")
        header = json.loads(stream.read(size))
    dtypes = {value["dtype"] for key, value in header.items() if key != "__metadata__"}
    if "BF16" not in dtypes or not dtypes <= {"BF16", "F32"}:
        raise SpeechError("tts_weights_not_bf16")
    for name in ("tokenizer_config.json", "vocab.json", "merges.txt",
                 "speech_tokenizer/config.json", "speech_tokenizer/model.safetensors"):
        if not (path / name).is_file(): raise SpeechError("tts_model_incomplete")
    return path


class MlxModel:
    """All MLX work stays on this process's single generation thread."""
    def __init__(self, config):
        _exact(config, ("model_path", "model_id", "revision", "voice", "language",
                        "cache_limit_bytes", "memory_limit_bytes", "warmup_tokens"))
        if (config["model_id"], config["revision"], config["voice"], config["language"]) != (
                MODEL_ID, MODEL_REVISION, "Ryan", "Russian"):
            raise SpeechError("tts_identity_invalid")
        path = verify_local_bf16(config["model_path"])
        if path.name != MODEL_REVISION: raise SpeechError("tts_revision_invalid")
        if importlib.metadata.version("mlx-audio") != "0.6.1" or importlib.metadata.version("mlx") != "0.32.4":
            raise SpeechError("tts_environment_version")
        import mlx.core as mx
        import numpy as np
        from mlx_audio.tts.utils import load_model
        self.mx = mx; self.np = np
        for name in ("cache_limit_bytes", "memory_limit_bytes"):
            if type(config[name]) is not int or not 0 <= config[name] <= 8 * 1024**3:
                raise SpeechError("tts_memory_limit")
        tokens = config["warmup_tokens"]
        if type(tokens) is not int or not 1 <= tokens <= 64: raise SpeechError("tts_warmup_bound")
        mx.set_cache_limit(config["cache_limit_bytes"])
        mx.set_memory_limit(config["memory_limit_bytes"])
        start = time.monotonic()
        self.model = load_model(path, lazy=False, strict=True)
        load_seconds = time.monotonic() - start
        if self.model.sample_rate != 24000: raise SpeechError("tts_rate_mismatch")
        start = time.monotonic()
        warmup = self.model.generate_custom_voice(text="Проверка русского голоса.", speaker="Ryan",
            language="Russian", stream=True, streaming_interval=1.0, max_tokens=tokens, verbose=False)
        try:
            for result in warmup:
                mx.eval(result.audio)
        finally:
            try: warmup.close()
            finally: self.reset()
        self.ready = SpeechReady(MODEL_ID, MODEL_REVISION, "Ryan", "Russian", 24000, True,
            load_seconds, time.monotonic() - start, mx.get_active_memory(),
            mx.get_cache_memory(), mx.get_peak_memory())

    def generate(self, segment):
        return self.model.generate_custom_voice(text=segment.text, speaker="Ryan", language="Russian",
            stream=True, streaming_interval=1.0, max_tokens=segment.max_tokens, verbose=False)

    def pcm(self, result):
        if result.sample_rate != 24000: raise SpeechError("tts_rate_mismatch")
        data = self.np.asarray(result.audio, dtype=self.np.dtype("<f4")).reshape(-1)
        if not self.np.isfinite(data).all() or self.np.any(self.np.abs(data) > 1.0001):
            raise SpeechError("tts_pcm_invalid")
        return data.tobytes()

    def reset(self):
        # The pinned library's generator doesn't put this in a finally block.
        self.model.speech_tokenizer.decoder.reset_streaming_state()
        self.mx.clear_cache()

    def reset_generation_peak(self):
        # Pinned MLX 0.32.4: reset the allocator high-water mark per utterance.
        self.mx.reset_peak_memory()

    def generation_memory(self):
        return self.mx.get_active_memory(), self.mx.get_cache_memory(), self.mx.get_peak_memory()


class Worker:
    def __init__(self, connection, *, model_factory=MlxModel, clock=time.monotonic):
        self.connection = connection; self.model_factory = model_factory
        self.clock = clock
        self.incoming = queue.Queue(maxsize=2)
        self.stop = threading.Event(); self.lock = threading.Lock(); self.state_lock = threading.Lock()
        self.cancelled = set(); self.active = None; self.last_chunk = 0

    def send(self, kind, value, pcm=b""):
        with self.lock: self.connection.sendall(frame_bytes(kind, value, pcm))

    def reader(self):
        first = True
        try:
            while not self.stop.is_set():
                h, p = parse_header(_read(self.connection, _PREFIX.size))
                kind, value = decode_header(_read(self.connection, h))
                if p: raise SpeechError("tts_control_pcm_forbidden")
                if first:
                    if kind != "initialize": raise SpeechError("tts_initialize_required")
                    first = False
                    self.incoming.put_nowait((kind, value)); continue
                if kind == "cancel":
                    request = cancel_from(value)
                    key = (request.scope, request.utterance_id, request.utterance_epoch)
                    with self.state_lock:
                        self.cancelled.add(key)
                        if len(self.cancelled) > 512: raise SpeechError("tts_fence_capacity")
                        active = self.active; last = self.last_chunk
                    if active != key:
                        self.send("cancel_ack", asdict(CancelAck(*key, last, True)))
                elif kind == "synthesize": self.incoming.put_nowait((kind, value))
                elif kind == "shutdown": self.stop.set()
                else: raise SpeechError("tts_unknown_control")
        except EOFError: self.stop.set()
        except BaseException:
            self.stop.set()
            try: self.send("error", {"code": "tts_control_rejected"})
            except BaseException: pass

    def generate_segment(self, model, segment):
        """One generation; measure disjoint spans without PCM/socket timing mix.

        np.asarray in MlxModel.pcm materializes each result. This is generator
        wall work, not GPU kernel/CPU utilization. Peak includes resident model
        allocations and is sampled before generator close/reset/clear_cache.
        """
        key = (segment.scope, segment.key.utterance_id, segment.key.utterance_epoch)
        started = self.clock()
        frames = seq = tokens = 0
        compute = send_seconds = close_seconds = reset_seconds = 0.0
        first_pcm = None; iterator = None; truncated = False; reason = "generator_completed"
        peak_reset = False; memory = (None,None,None)
        # Telemetry API absence/failure must not change generated speech.
        try:
            model.reset_generation_peak(); peak_reset = True
        except Exception: pass
        try:
            if key in self.cancelled or time.monotonic_ns() >= segment.expires_at_monotonic_ns:
                reason = "cancelled" if key in self.cancelled else "expired"
            else:
                at = self.clock()
                try: iterator = iter(model.generate(segment))
                finally: compute += self.clock()-at
                while True:
                    at = self.clock()
                    try: result = next(iterator)
                    except StopIteration: break
                    finally: compute += self.clock()-at
                    if self.stop.is_set() or key in self.cancelled:
                        reason = "cancelled"; break
                    if time.monotonic_ns() >= segment.expires_at_monotonic_ns:
                        reason = "expired"; break
                    tokens += int(result.token_count)
                    at = self.clock()
                    try: raw = model.pcm(result)
                    finally: compute += self.clock()-at
                    remaining = max(0, segment.max_audio_frames - frames)
                    if len(raw) // 4 > remaining:
                        raw = raw[:remaining * 4]; truncated = True; reason = "duration_bound"
                    if raw and first_pcm is None: first_pcm = self.clock()-started
                    for offset in range(0, len(raw), MAX_PCM):
                        if self.stop.is_set() or key in self.cancelled:
                            reason = "cancelled"; break
                        part = raw[offset:offset + MAX_PCM]
                        at = self.clock()
                        try:
                            self.send("pcm", {"scope": asdict(segment.scope), "key": asdict(segment.key),
                                "chunk_seq": seq, "source_frame_offset": frames,
                                "rate": 24000, "channels": 1, "format": "f32le"}, part)
                        finally: send_seconds += self.clock()-at
                        frames += len(part) // 4
                        with self.state_lock: self.last_chunk = seq
                        seq += 1
                    if truncated or reason == "cancelled": break
            truncated = truncated or tokens >= segment.max_tokens
        finally:
            try:
                memory = model.generation_memory()
                if not isinstance(memory,tuple) or len(memory)!=3: memory = (None,None,None)
                memory = tuple(value if type(value)is int and 0<=value<=64*1024**3 else None for value in memory)
            except Exception: pass
            try:
                at = self.clock()
                try:
                    if iterator is not None: iterator.close()
                finally: close_seconds = self.clock()-at
            finally:
                at = self.clock()
                try: model.reset()
                finally: reset_seconds = self.clock()-at
        if not self.stop.is_set():
            done = SynthesisDone(segment.scope,segment.key,frames,tokens,truncated,reason)
            metrics = WorkerGenerationMetrics(segment.scope,segment.key,frames,tokens,truncated,reason,
                compute,self.clock()-started,send_seconds,first_pcm if frames else None,
                close_seconds,reset_seconds,peak_reset,*memory)
            self.send("generation_metrics",asdict(metrics))
            self.send("done",asdict(done))
            if key in self.cancelled:
                self.send("cancel_ack",asdict(CancelAck(*key,max(0,seq-1),True)))

    def run(self):
        thread = threading.Thread(target=self.reader, daemon=True, name="tts-control")
        thread.start()
        try:
            kind, config = self.incoming.get(timeout=10)
            if kind != "initialize": raise SpeechError("tts_initialize_required")
            model = self.model_factory(config)
            if self.stop.is_set(): return
            self.send("ready", asdict(model.ready))
            while not self.stop.is_set():
                try: kind, value = self.incoming.get(timeout=.1)
                except queue.Empty: continue
                if kind != "synthesize": raise SpeechError("tts_duplicate_initialize")
                segment = segment_from(value)
                key = (segment.scope, segment.key.utterance_id, segment.key.utterance_epoch)
                with self.state_lock: self.active = key; self.last_chunk = 0
                self.generate_segment(model,segment)
                with self.state_lock: self.active = None
        except BaseException:
            try: self.send("error", {"code": "tts_worker_failed"})
            except BaseException: pass
        finally:
            self.stop.set()
            try: self.connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            self.connection.close()
            thread.join(timeout=.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fd", type=int, required=True)
    args = parser.parse_args()
    if args.fd < 3: raise SystemExit(2)
    Worker(socket.socket(fileno=args.fd)).run()


if __name__ == "__main__": main()
