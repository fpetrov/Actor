"""Deterministic worker/IPC timing tests; no MLX, GPU, processes or network."""
import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

import pytest

from actor_broadcast.contracts import (Cancel,CancelAck,Scope,SegmentKey,PcmChunk,
    SynthesisDone,WorkerGenerationMetrics)
from actor_broadcast.speech import MlxSpeechConfig,MlxSpeechSynthesizer,SpeechError,frame_bytes
from actor_broadcast.tts_worker import Worker,MlxModel
from test_broadcast_speech import segment
from test_broadcast_latency import pipeline,metric
from test_broadcast_commentary import Clock


def generation(s=None, **changes):
    s=s or segment()
    values=dict(scope=s.scope,key=s.key,total_source_frames=24000,token_count=5,
        may_be_truncated=False,terminal_reason='generator_completed',generation_compute_seconds=1.2,
        worker_wall_seconds=11.1,pcm_send_seconds=9.0,first_pcm_seconds=.3,
        iterator_close_seconds=.4,reset_seconds=.5,peak_reset=True,
        active_memory_bytes=700,cache_memory_bytes=100,peak_memory_bytes=900)
    return WorkerGenerationMetrics(**(values|changes))


class Time:
    now=0.0
    def __call__(self): return self.now
    def advance(self,value): self.now+=value


class MeasuredModel:
    def __init__(self,clock): self.clock=clock; self.events=[]; self.index=0
    def reset_generation_peak(self): self.events.append('peak_reset')
    def generate(self,_): self.events.append('generate'); return self
    def __iter__(self): return self
    def __next__(self):
        if self.index==3:
            self.clock.advance(.3); self.events.append('eos'); raise StopIteration
        self.index+=1; self.clock.advance(.1)
        return type('Result',(),{'token_count':self.index})()
    def pcm(self,_): self.clock.advance(.2); return b'\0'*(8000*4)
    def generation_memory(self): self.events.append('memory'); return (700,100,900)
    def close(self): self.events.append('close'); self.clock.advance(.4)
    def reset(self): self.events.append('reset'); self.clock.advance(.5)


class RecordingWorker(Worker):
    def __init__(self,clock): super().__init__(None,clock=clock); self.sent=[]
    def send(self,kind,value,pcm=b''):
        self.sent.append((kind,value,pcm))
        if kind=='pcm': self.clock.advance(3)


def worker_metric(worker):
    value=next(value for kind,value,_ in worker.sent if kind=='generation_metrics')
    value=dict(value); value['scope']=Scope(**value['scope']); value['key']=SegmentKey(**value['key'])
    return WorkerGenerationMetrics(**value)


def test_real_worker_spans_exclude_send_backpressure_include_eos_and_snapshot_before_cleanup():
    clock=Time(); model=MeasuredModel(clock); worker=RecordingWorker(clock)
    worker.generate_segment(model,segment(max_audio_frames=24000))
    m=worker_metric(worker)
    assert m.generation_compute_seconds==pytest.approx(1.2)
    assert m.pcm_send_seconds==pytest.approx(9)
    assert m.worker_wall_seconds==pytest.approx(11.1)
    assert m.first_pcm_seconds==pytest.approx(.3)
    assert m.iterator_close_seconds==pytest.approx(.4) and m.reset_seconds==pytest.approx(.5)
    assert m.token_count==6  # Each pinned result contains new_tokens, not cumulative tokens.
    assert (m.active_memory_bytes,m.cache_memory_bytes,m.peak_memory_bytes)==(700,100,900)
    assert model.events==['peak_reset','generate','eos','memory','close','reset']
    assert [kind for kind,_,_ in worker.sent]==['pcm','pcm','pcm','generation_metrics','done']
    assert m.total_source_frames==24000 and m.peak_reset and not m.may_be_truncated


def test_worker_duration_cap_and_cancel_are_measured_partial_not_eos_completion():
    for cancel in (False,True):
        clock=Time(); model=MeasuredModel(clock); worker=RecordingWorker(clock)
        s=segment(max_audio_frames=24000 if cancel else 4000)
        if cancel:
            send=worker.send
            def send_cancel(kind,value,pcm=b''):
                send(kind,value,pcm)
                if kind=='pcm': worker.cancelled.add((s.scope,s.key.utterance_id,s.key.utterance_epoch))
            worker.send=send_cancel
        worker.generate_segment(model,s); m=worker_metric(worker)
        assert m.terminal_reason==('cancelled' if cancel else 'duration_bound')
        assert m.total_source_frames==(8000 if cancel else 4000)
        assert m.may_be_truncated is (not cancel)
        assert 'eos' not in model.events and model.events[-3:]==['memory','close','reset']


def test_missing_memory_api_is_missing_not_fabricated_zero_and_mlx_hooks_reset_pinned_allocator():
    clock=Time(); worker=RecordingWorker(clock); model=MeasuredModel(clock)
    model.reset_generation_peak=None; model.generation_memory=None
    worker.generate_segment(model,segment(max_audio_frames=24000)); m=worker_metric(worker)
    assert m.peak_reset is False
    assert (m.active_memory_bytes,m.cache_memory_bytes,m.peak_memory_bytes)==(None,None,None)
    events=[]
    class MX:
        def reset_peak_memory(self): events.append('reset_peak')
        def get_active_memory(self): return 7
        def get_cache_memory(self): return 2
        def get_peak_memory(self): return 10
    real=MlxModel.__new__(MlxModel); real.mx=MX()
    real.reset_generation_peak()
    assert real.generation_memory()==(7,2,10) and events==['reset_peak']


@pytest.mark.parametrize('changes',[
    {'worker_wall_seconds':float('nan')},{'generation_compute_seconds':True},
    {'pcm_send_seconds':-1},{'worker_wall_seconds':3601},{'first_pcm_seconds':None},
    {'first_pcm_seconds':12},{'total_source_frames':24000*60+1},
    {'total_source_frames':True},{'token_count':65537},{'peak_reset':1},
    {'active_memory_bytes':-1},{'cache_memory_bytes':True},{'peak_memory_bytes':64*1024**3+1},
    {'terminal_reason':'private freeform error'},{'generation_compute_seconds':2},
    {'total_source_frames':0},
])
def test_typed_metric_rejects_nonfinite_overlapping_unbounded_or_ambiguous_fields(changes):
    with pytest.raises(ValueError): generation(**changes)


class MemorySynth(MlxSpeechSynthesizer):
    """Use the real framed reader with StreamReader bytes, no OS socket."""
    def __init__(self,tmp_path,wire,callback,*,fence=False):
        super().__init__(MlxSpeechConfig(Path(sys.executable),tmp_path),on_generation_metrics=callback)
        self._socket=asyncio.StreamReader(); self.wire=wire; self.stopped=False; self.fence=fence
    async def warmup(self): pass
    async def _send(self,kind,value):
        if kind=='synthesize':
            if self.fence:
                s=self._active_request; self._fenced.add((s.scope,s.key.utterance_id,s.key.utterance_epoch))
            self._socket.feed_data(self.wire)
    async def _stop_process(self): self.stopped=True
    async def cancel(self,c):
        self._fenced.add((c.scope,c.utterance_id,c.utterance_epoch))
        return CancelAck(c.scope,c.utterance_id,c.utterance_epoch,0,True)


def pcm_wire(s,frames=24000):
    return frame_bytes('pcm',dict(scope=asdict(s.scope),key=asdict(s.key),chunk_seq=0,
        source_frame_offset=0,rate=24000,channels=1,format='f32le'),b'\0'*(frames*4))


async def run_wire(tmp_path,monkeypatch,s,wire,callback,*,fence=False):
    import actor_broadcast.speech as module
    async def read(reader,count): return await reader.readexactly(count)
    monkeypatch.setattr(module,'_read_exact',read)
    voice=MemorySynth(tmp_path,wire,callback,fence=fence)
    task=asyncio.create_task(voice._receive())
    try: return [x async for x in voice.synthesize(s)],voice
    finally:
        task.cancel(); await task


async def test_framed_metrics_are_not_audio_queue_items_and_duplicates_foreign_and_late_are_discarded(tmp_path,monkeypatch):
    s=segment(max_audio_frames=24000); m=generation(s); got=[]
    old=replace(m,key=SegmentKey('old-private-id',1,0))
    done=SynthesisDone(s.scope,s.key,24000,m.token_count,False,'generator_completed')
    wire=(pcm_wire(s)+frame_bytes('generation_metrics',asdict(old))+
        frame_bytes('generation_metrics',asdict(m))*2+frame_bytes('done',asdict(done))+
        frame_bytes('generation_metrics',asdict(m)))
    items,voice=await run_wire(tmp_path,monkeypatch,s,wire,got.append)
    assert len(items)==2 and isinstance(items[0],PcmChunk) and items[1]==done
    assert got==[m] and voice.metrics['generation_metrics']==1
    assert voice.metrics['discarded_generation_metrics']==3 and not voice.stopped


async def test_fenced_metrics_are_not_associated_or_sent_to_callback(tmp_path,monkeypatch):
    s=segment(max_audio_frames=24000); got=[]; m=generation(s)
    done=SynthesisDone(s.scope,s.key,24000,m.token_count,False,'generator_completed')
    items,voice=await run_wire(tmp_path,monkeypatch,s,
        pcm_wire(s)+frame_bytes('generation_metrics',asdict(m))+frame_bytes('done',asdict(done)),got.append,fence=True)
    assert items==[done] and got==[] and voice.metrics['discarded_generation_metrics']==1


@pytest.mark.parametrize('mutation',['terminal','shape','pcm','bound','numeric','frame_count'])
async def test_wire_bad_metrics_fail_closed_before_callback(tmp_path,monkeypatch,mutation):
    s=segment(max_audio_frames=24000); m=generation(s); got=[]; payload=asdict(m)
    done=SynthesisDone(s.scope,s.key,24000,m.token_count,False,'generator_completed'); extra=b''
    if mutation=='terminal': done=replace(done,token_count=99)
    elif mutation=='shape': payload['unknown']=1
    elif mutation=='pcm': extra=b'\0'*4
    elif mutation=='bound': payload['total_source_frames']=24001
    elif mutation=='numeric': payload['active_memory_bytes']=True
    elif mutation=='frame_count': done=replace(done,total_source_frames=12000);payload['total_source_frames']=12000
    wire=pcm_wire(s)+frame_bytes('generation_metrics',payload,extra)+frame_bytes('done',asdict(done))
    with pytest.raises(SpeechError): await run_wire(tmp_path,monkeypatch,s,wire,got.append)
    assert got==[]


def test_registry_complete_compute_rtf_separates_pipeline_and_partial_and_drops_synthetic_or_cancelled():
    clock=Clock(); telemetry,_,_,s=pipeline(clock)
    telemetry.validated_text(s.scope,s.key)
    m=generation(s)
    telemetry.worker_generation(m); telemetry.worker_generation(m)
    report=telemetry.report()
    assert metric(report,'worker_compute_rtf')['p50']==pytest.approx(1.2)
    assert metric(report,'worker_compute_speedup')['p50']==pytest.approx(1/1.2)
    assert metric(report,'worker_complete_wall_ms')['p50']==pytest.approx(11100)
    assert metric(report,'worker_complete_pcm_send_ms')['p50']==pytest.approx(9000)
    assert metric(report,'worker_complete_mlx_peak_bytes')['p50']==900
    assert metric(report,'synthesis_pipeline_rtf') is None and report['worker_compute_timing_available'] is True
    assert report['counts']['worker_generation_completed']==1
    telemetry.worker_generation(replace(m,key=SegmentKey('synthetic-setup',1,0)))
    _,_,_,second=pipeline(clock,identity='partial-private',mode='idle_recap',telemetry=telemetry)
    telemetry.validated_text(second.scope,second.key)
    telemetry.worker_generation(generation(second,may_be_truncated=True,terminal_reason='duration_bound',
        peak_reset=False,active_memory_bytes=None,cache_memory_bytes=None,peak_memory_bytes=None))
    report=telemetry.report()
    assert metric(report,'worker_compute_rtf','idle_recap','subsequent_in_run') is None
    assert metric(report,'worker_incomplete_compute_ms','idle_recap','subsequent_in_run')['p50']==pytest.approx(1200)
    assert metric(report,'worker_incomplete_mlx_peak_bytes','idle_recap','subsequent_in_run') is None
    telemetry.cancelled(Cancel(second.scope,second.key.utterance_id,1))
    third=replace(second,key=SegmentKey(second.key.utterance_id,1,1))
    telemetry.validated_text(third.scope,third.key); telemetry.worker_generation(generation(third))
    assert telemetry.report()['counts']['worker_generation_completed']==1
    encoded=json.dumps(report,allow_nan=False)
    for private in ('private-utterance','partial-private','private-evidence','PRIVATE TEXT','synthetic-setup'):
        assert private not in encoded
    assert report['twitch_viewer_latency_measured'] is False


async def test_audio_latency_binding_receives_real_framed_metric_callback(tmp_path,monkeypatch):
    from actor_broadcast.audio import SpeechAudioOutput
    clock=Clock(); telemetry,_,_,s=pipeline(clock)
    s=replace(s,max_audio_frames=24000,expires_at_monotonic_ns=segment().expires_at_monotonic_ns)
    telemetry.validated_text(s.scope,s.key)
    m=generation(s); done=SynthesisDone(s.scope,s.key,24000,m.token_count,False,'generator_completed')
    import actor_broadcast.speech as module
    async def read(reader,count): return await reader.readexactly(count)
    monkeypatch.setattr(module,'_read_exact',read)
    voice=MemorySynth(tmp_path,pcm_wire(s)+frame_bytes('generation_metrics',asdict(m))+
        frame_bytes('done',asdict(done)),None)
    sink=type('Sink',(),{})()
    output=SpeechAudioOutput(voice,sink,validate=lambda _:True); output.bind_latency(telemetry)
    task=asyncio.create_task(voice._receive())
    try:
        items=[x async for x in voice.synthesize(s)]
        assert len(items)==2 and metric(telemetry.report(),'worker_compute_rtf')['count']==1
    finally: task.cancel(); await task
