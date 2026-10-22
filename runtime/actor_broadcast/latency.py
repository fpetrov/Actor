"""Bounded passive pipeline timings; correlation stays in RAM, never in reports.

Controller monotonic receipt/dispatch times are not GPU presentation or Twitch
viewer clocks. Synthesis pipeline wall time includes IPC and backpressure; the
worker's internal compute/start clocks are deliberately reported unavailable.
"""
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
import math
import time

MODES = frozenset(('reactive','idle_recap','memory_callback','chat_response','intro','outro','analysis','repair'))


@dataclass
class _Utterance:
    group: tuple
    admitted: float
    presented: float | None
    evidence_presented: float | None = None
    event_wall_mono: float | None = None
    provider: float | None = None
    first_text: float | None = None
    first_pcm: float | None = None
    consumed: bool = False
    cancelled: bool = False


@dataclass
class _Segment:
    utterance: tuple
    stages: dict = field(default_factory=dict)
    frames: int = 0
    seq: int = 0
    done: bool = False
    closed: bool = False
    consumed: bool = False


class LatencyTelemetry:
    def __init__(self, *, clock=time.monotonic, wall_clock=time.time,
                 capacity=128, fact_capacity=512, sample_capacity=128):
        if any(type(v) is not int or not 1 <= v <= 512 for v in (capacity,fact_capacity,sample_capacity)):
            raise ValueError('Bounded telemetry capacity required')
        self.clock, self.wall_clock = clock, wall_clock
        self.capacity, self.fact_capacity, self.sample_capacity = capacity, fact_capacity, sample_capacity
        self._scope = None
        self._facts, self._utterances, self._segments = OrderedDict(), OrderedDict(), OrderedDict()
        self._samples, self._sample_counts, self._counts = {}, Counter(), Counter()
        self._admissions = 0

    @staticmethod
    def _valid(value):
        return type(value) in (int,float) and math.isfinite(value) and value >= 0

    def _now(self):
        value = self.clock()
        if not self._valid(value): self._counts['invalid_clock_samples'] += 1; return None
        return value

    @staticmethod
    def _identity(scope,key): return (scope,key.utterance_id,key.utterance_epoch)

    def _sample(self, group, name, value):
        if not self._valid(value): self._counts['invalid_timing_samples'] += 1; return
        identity = (*group,name)
        self._sample_counts[identity] += 1
        self._samples.setdefault(identity,deque(maxlen=self.sample_capacity)).append(float(value))

    def _delta(self, group, name, start, end):
        if start is None or end is None: return
        if not self._valid(start) or not self._valid(end) or end < start or end-start > 86400:
            self._counts['invalid_timing_samples'] += 1; return
        self._sample(group,name,(end-start)*1000)

    def presented(self, view):
        now = self._now()
        received = view.presentation.received_at_monotonic
        if now is None or not self._valid(received) or received > now: return
        if self._scope is not None and self._scope != view.scope:
            self._facts.clear(); self._utterances.clear(); self._segments.clear()
            self._counts['scope_resets'] += 1
        self._scope = view.scope
        wall = self.wall_clock()
        for fact in view.facts:
            if fact.origin != 'game': continue
            key = (view.scope,fact.ref)
            if key in self._facts: continue
            # Local wall/monotonic conversion is clock-dependent, not a server
            # to GPU latency. Negative/skewed values are omitted, not zeroed.
            age = wall - (now-received) - fact.observed_at_unix_ms/1000 if self._valid(wall) else None
            if age is not None and (not self._valid(age) or age > 86400):
                self._counts['invalid_wall_age_samples'] += 1; age = None
            self._facts[key] = (received,age)
            while len(self._facts) > self.fact_capacity:
                self._facts.popitem(last=False); self._counts['evicted_fact_timings'] += 1

    def admitted(self, request, view):
        now = self._now(); candidate = request.candidate
        identity = (candidate.scope,request.utterance_id,request.utterance_epoch)
        if now is None or candidate.mode not in MODES or candidate.scope != self._scope or identity in self._utterances: return
        group = (candidate.mode,'first_in_run' if self._admissions == 0 else 'subsequent_in_run')
        self._admissions += 1
        presented = view.presentation.received_at_monotonic
        presented = presented if self._valid(presented) and presented <= now else None
        row = _Utterance(group,now,presented)
        self._utterances[identity] = row
        self._counts['admitted_utterances'] += 1
        self._delta(group,'presentation_ack_receipt_to_admission_ms',presented,now)
        facts = [self._facts.get((candidate.scope,ref)) for ref in candidate.evidence]
        if facts and all(item is not None for item in facts):
            row.evidence_presented = min(item[0] for item in facts)
            self._delta(group,'evidence_first_presentation_to_admission_ms',row.evidence_presented,now)
            ages = [item[1] for item in facts]
            if all(age is not None for age in ages):
                self._sample(group,'selected_event_to_presentation_receipt_wall_ms',max(ages)*1000)
                baseline = min(received-age for received,age in facts)
                if self._valid(baseline): row.event_wall_mono = baseline
        while len(self._utterances) > self.capacity:
            victim,_ = self._utterances.popitem(last=False)
            for key in tuple(self._segments):
                if self._segments[key].utterance == victim: self._segments.pop(key)
            self._counts['evicted_utterances'] += 1

    def provider_started(self, request):
        row = self._utterances.get((request.candidate.scope,request.utterance_id,request.utterance_epoch))
        if row is None or row.cancelled or row.provider is not None: return
        now = self._now()
        if now is None: return
        row.provider = now
        self._delta(row.group,'admission_to_provider_dispatch_ms',row.admitted,now)

    def validated_text(self, scope, key):
        identity = self._identity(scope,key); row = self._utterances.get(identity)
        if row is None or row.cancelled: return
        segment_key = (scope,key)
        if segment_key in self._segments: return
        now = self._now()
        if now is None: return
        segment = _Segment(identity,{'validated_text':now})
        self._segments[segment_key] = segment
        if row.first_text is None:
            row.first_text = now
            self._delta(row.group,'provider_dispatch_to_first_validated_text_ms',row.provider,now)
        while len(self._segments) > self.capacity:
            self._segments.popitem(last=False); self._counts['evicted_segments'] += 1

    def _stage(self, scope, key, name):
        segment = self._segments.get((scope,key))
        row = self._utterances.get(segment.utterance) if segment else None
        if row is None or row.cancelled or segment.closed or name in segment.stages: return None
        now = self._now()
        if now is None: return None
        segment.stages[name] = now
        return row,segment,now

    def submitted(self, segment):
        value = self._stage(segment.scope,segment.key,'submitted')
        if value:
            row,s,now = value
            self._delta(row.group,'validated_text_to_speech_submit_ms',s.stages.get('validated_text'),now)

    def queued(self, segment): self._stage(segment.scope,segment.key,'queued')

    def dequeued(self, segment):
        value = self._stage(segment.scope,segment.key,'dequeued')
        if value:
            row,s,now = value
            self._delta(row.group,'speech_queue_wait_ms',s.stages.get('queued'),now)

    def synthesis_started(self, segment):
        value = self._stage(segment.scope,segment.key,'synthesis_started')
        if value:
            row,s,now = value
            self._delta(row.group,'dequeue_to_synthesis_iteration_ms',s.stages.get('dequeued'),now)

    def pcm(self, chunk):
        s = self._segments.get((chunk.scope,chunk.key)); row = self._utterances.get(s.utterance) if s else None
        if row is None or row.cancelled or s.closed or s.done: return
        if chunk.chunk_seq != s.seq or chunk.source_frame_offset != s.frames: return
        s.seq += 1; s.frames += len(chunk.pcm)//4
        if 'first_pcm' in s.stages: return
        now = self._now()
        if now is None: return
        s.stages['first_pcm'] = now
        self._delta(row.group,'synthesis_iteration_to_first_pcm_ms',s.stages.get('synthesis_started'),now)
        if row.first_pcm is None:
            row.first_pcm = now
            self._delta(row.group,'provider_dispatch_to_first_pcm_ms',row.provider,now)

    def synthesis_done(self, done):
        s = self._segments.get((done.scope,done.key)); row = self._utterances.get(s.utterance) if s else None
        if row is None or row.cancelled or s.closed or s.done or done.total_source_frames != s.frames or not s.frames: return
        s.done = True
        now = self._now()
        if now is None: return
        self._sample(row.group,'generated_audio_seconds',s.frames/24000)
        if done.may_be_truncated or done.terminal_reason != 'generator_completed':
            self._counts['truncated_or_incomplete_synthesis'] += 1; return
        self._counts['completed_synthesis'] += 1
        start = s.stages.get('synthesis_started')
        self._delta(row.group,'synthesis_pipeline_wall_ms',start,now)
        if start is not None and self._valid(start) and now > start:
            duration = now-start; audio = s.frames/24000
            self._sample(row.group,'synthesis_pipeline_rtf',duration/audio)
            self._sample(row.group,'synthesis_pipeline_speedup',audio/duration)

    def playback(self, ack):
        s = self._segments.get((ack.scope,ack.key)); row = self._utterances.get(s.utterance) if s else None
        if row is None or row.cancelled or s.closed or ack.clock_domain != 'browser_audio_render': return
        now = self._now(); at = ack.received_at_monotonic
        if now is None or not self._valid(at) or at > now: return
        if ack.consumed_source_frames > 0 and not s.consumed:
            s.consumed = True
            self._delta(row.group,'first_pcm_to_positive_consumption_ms',s.stages.get('first_pcm'),at)
            if not row.consumed:
                row.consumed = True
                self._delta(row.group,'admission_to_first_positive_consumption_ms',row.admitted,at)
                self._delta(row.group,'presentation_ack_receipt_to_first_positive_consumption_ms',row.presented,at)
                self._delta(row.group,'evidence_first_presentation_to_first_positive_consumption_ms',row.evidence_presented,at)
                self._delta(row.group,'selected_event_to_first_positive_consumption_wall_ms',row.event_wall_mono,at)
                self._delta(row.group,'first_validated_text_to_first_positive_consumption_ms',row.first_text,at)
        if ack.status in ('completed','interrupted'):
            s.closed = True; self._counts['playback_'+ack.status] += 1

    def cancelled(self, request):
        row = self._utterances.get((request.scope,request.utterance_id,request.utterance_epoch))
        if row is not None and not row.cancelled:
            row.cancelled = True; self._counts['cancelled_utterances'] += 1

    def report(self):
        groups = {}
        for (mode,order,metric),values in self._samples.items():
            ordered = sorted(values); count = len(ordered)
            def percentile(q): return ordered[max(0,math.ceil(count*q)-1)] if count else None
            groups.setdefault(mode,{}).setdefault(order,{})[metric] = {
                'count':self._sample_counts[(mode,order,metric)],'retained_count':count,
                'p50':percentile(.5),'p95':percentile(.95)}
        return {'schema':'actor.broadcast-latency.v1','groups':groups,'counts':dict(self._counts),
            'retained':{'utterances':len(self._utterances),'segments':len(self._segments),'fact_timings':len(self._facts)},
            'sample_capacity_per_metric':self.sample_capacity,
            'percentile_scope':'most recent bounded samples; count is whole-run valid samples',
            'utterance_order_scope':'first versus subsequent admitted provider utterance in this telemetry run',
            'presentation_clock':'controller monotonic receipt of coherent native presentation ACK; not GPU render duration',
            'event_wall_age_clock_dependent':True,'idle_event_age_is_reaction_slo':False,
            'provider_start_scope':'scheduler dispatch into provider.generate; not a remote model compute timestamp',
            'synthesis_rtf_scope':'controller pipeline wall including IPC/renderer backpressure, not worker compute',
            'worker_compute_timing_available':False,'twitch_viewer_latency_measured':False,
            'synthetic_route_and_neutral_fallback_included':False}
