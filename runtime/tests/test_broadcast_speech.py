"""Real framed worker loop with a tiny fake model; never MLX/audio devices."""
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import time

import pytest

from actor_broadcast.contracts import Cancel, EvidenceRef, Scope, SegmentKey, SpeechSegment, PcmChunk, SynthesisDone
from actor_broadcast.speech import (MODEL_ID, MODEL_REVISION, MlxSpeechConfig, MlxSpeechSynthesizer,
    SpeechError, SpeechReady, decode_header, frame_bytes, parse_header, segment_from, validate_pcm)
from actor_broadcast.tts_worker import Worker, verify_local_bf16


def segment(**changes):
    values = dict(scope=Scope("broadcast", "match", 1, 1), key=SegmentKey("utterance", 1, 0),
        priority=2, text="Разрешённая короткая реплика.", evidence_versions=(EvidenceRef("fact", "1"),),
        semantic_revision=1, expires_at_monotonic_ns=time.monotonic_ns() + 10_000_000_000,
        max_tokens=20, max_audio_frames=2400)
    return SpeechSegment(**(values | changes))


class TinyModel:
    def __init__(self, _):
        self.ready = SpeechReady(MODEL_ID, MODEL_REVISION, "Ryan", "Russian", 24000, True, .01, .01, 10, 0, 10)
        self.resets = 0; self.closed_generators = 0
    def generate(self, _):
        try:
            for _ in range(3):
                time.sleep(.015)
                yield type("Chunk", (), {"token_count": 2})()
        finally: self.closed_generators += 1
    def pcm(self, _): return struct.pack("<120f", *([.05] * 120))
    def reset(self): self.resets += 1


class ThreadProcess:
    def __init__(self, command, **kwargs):
        self.kwargs = kwargs; self.command = command; self.model = None
        self.socket = socket.socket(fileno=os.dup(kwargs["pass_fds"][0]))
        def create(value): self.model = TinyModel(value); return self.model
        self.worker = Worker(self.socket, model_factory=create)
        self.thread = threading.Thread(target=self.worker.run, daemon=True); self.thread.start()
        self.terminated = False
    def poll(self): return None if self.thread.is_alive() else 0
    def terminate(self):
        self.terminated = True; self.worker.stop.set()
        try: self.socket.shutdown(socket.SHUT_RDWR)
        except OSError: pass
    kill = terminate
    def wait(self, timeout):
        self.thread.join(timeout)
        if self.thread.is_alive():
            import subprocess
            raise subprocess.TimeoutExpired("owned-fake-worker", timeout)
        return 0


def synthesizer(tmp_path):
    made = []
    def factory(*a, **k): result = ThreadProcess(*a, **k); made.append(result); return result
    config = MlxSpeechConfig(Path(sys.executable), tmp_path, startup_timeout=2, cancel_timeout=.5)
    return MlxSpeechSynthesizer(config, process_factory=factory), made


async def test_persistent_framed_worker_two_segments_and_exact_offline_environment(tmp_path):
    voice, made = synthesizer(tmp_path)
    try:
        ready = await voice.warmup()
        assert ready.voice == "Ryan" and ready.revision == MODEL_REVISION
        first = [item async for item in voice.synthesize(segment())]
        second = [item async for item in voice.synthesize(segment(key=SegmentKey("next", 1, 0)))]
        assert len(made) == 1 and len(first) == len(second) == 4
        assert [x.source_frame_offset for x in first[:-1]] == [0, 120, 240]
        assert first[-1].total_source_frames == 360 and not first[-1].may_be_truncated
        assert made[0].model.resets == 2 and made[0].model.closed_generators == 2
        assert made[0].command[1:3] == ["-I", "-B"]
        assert made[0].kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert not any("TOKEN" in key and key != "HF_HUB_DISABLE_IMPLICIT_TOKEN" and key != "TOKENIZERS_PARALLELISM" for key in made[0].kwargs["env"])
    finally: await voice.aclose()
    assert made[0].poll() == 0


async def test_cancel_fences_buffered_audio_and_confirms_generator_cleanup(tmp_path):
    voice, made = synthesizer(tmp_path); s = segment(); stream = voice.synthesize(s)
    try:
        assert isinstance(await anext(stream), PcmChunk)
        request = Cancel(s.scope, s.key.utterance_id, s.key.utterance_epoch)
        a, b = await asyncio.gather(voice.cancel(request), voice.cancel(request))
        assert a.emission_stopped is True and b.emission_stopped is True
        assert made[0].model.resets == 1 and made[0].model.closed_generators == 1
        tail = [x async for x in stream]
        assert all(isinstance(x, SynthesisDone) for x in tail)
        with pytest.raises(SpeechError, match="replayed"):
            _ = [x async for x in voice.synthesize(s)]
    finally: await stream.aclose(); await voice.aclose()


async def test_hard_frame_cap_is_explicit_truncation_never_complete_text(tmp_path):
    voice, _ = synthesizer(tmp_path)
    try:
        items = [x async for x in voice.synthesize(segment(max_audio_frames=150))]
        assert sum(len(x.pcm) // 4 for x in items if isinstance(x, PcmChunk)) == 150
        assert items[-1].may_be_truncated and items[-1].terminal_reason == "duration_bound"
    finally: await voice.aclose()


async def test_expired_segment_never_starts_generator(tmp_path):
    voice, made = synthesizer(tmp_path)
    try:
        with pytest.raises(SpeechError, match="expired"):
            _ = [x async for x in voice.synthesize(segment(expires_at_monotonic_ns=1))]
        assert made[0].model.resets == 0
    finally: await voice.aclose()


def test_binary_frames_bounds_duplicate_keys_and_finite_pcm():
    wire = frame_bytes("synthesize", asdict(segment()))
    h, p = parse_header(wire[:8]); assert not p
    kind, body = decode_header(wire[8:8+h]); assert kind == "synthesize"
    assert segment_from(body).text == segment().text
    for bad in (b'{"type":"x","type":"y","value":{}}', b'{"type":"x","value":{"n":NaN}}'):
        with pytest.raises((SpeechError, ValueError)): decode_header(bad)
    for prefix in (struct.pack("!II", 0, 0), struct.pack("!II", 16385, 0), struct.pack("!II", 1, 131073)):
        with pytest.raises(SpeechError): parse_header(prefix)
    for value in (float("nan"), float("inf"), 1.5):
        with pytest.raises(SpeechError): validate_pcm(struct.pack("<f", value))


def test_local_metadata_rejects_quantized_or_non_bf16_without_loading_weights(tmp_path):
    cfg = {"tts_model_type": "custom_voice", "model_type": "qwen3_tts", "talker_config": {
        "spk_id": {"ryan": 1}, "codec_language_id": {"russian": 1}}}
    (tmp_path / "config.json").write_text(json.dumps(cfg | {"quantization": {"bits": 8}}))
    with pytest.raises(SpeechError, match="quantized"): verify_local_bf16(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    header = json.dumps({"weight": {"dtype": "I8"}}).encode()
    (tmp_path / "model.safetensors").write_bytes(struct.pack("<Q", len(header)) + header)
    with pytest.raises(SpeechError, match="not_bf16"): verify_local_bf16(tmp_path)


def test_venv_symlink_is_preserved(tmp_path):
    link = tmp_path / "python"; link.symlink_to(sys.executable)
    assert MlxSpeechConfig(link, tmp_path).python_executable == link
