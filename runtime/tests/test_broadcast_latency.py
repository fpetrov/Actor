"""Real callback plumbing with deterministic fake times/PCM; no native or model."""
import asyncio
from dataclasses import replace
import json
import struct
import time

import pytest

from actor_broadcast.latency import LatencyTelemetry
from actor_broadcast.contracts import (CommentaryCandidate,CommentaryRequest,SegmentKey,SpeechSegment,
    PcmChunk,SynthesisDone,PlaybackAck,Cancel)
from actor_broadcast.audio import BrowserAudioSink,SpeechAudioOutput
from actor_broadcast.scheduler import CommentaryScheduler
from test_broadcast_commentary import Clock,view,fact,Provider,pump


def pipeline(clock, *, mode='reactive', identity='private-utterance', telemetry=None, priority=0):
    telemetry=telemetry or LatencyTelemetry(clock=clock.monotonic,wall_clock=clock.wall)
    frame=view(clock,facts=(fact(clock,'private-evidence',priority=priority),))
    telemetry.presented(frame)
    request=CommentaryRequest(CommentaryCandidate(frame.scope,frame.sequence,'private-candidate',mode,priority,
        'private-topic',tuple(f.ref for f in frame.facts),9999999),{},256,identity,1)
    telemetry.admitted(request,frame)
    key=SegmentKey(request.utterance_id,1,0)
    segment=SpeechSegment(frame.scope,key,priority,'PRIVATE TEXT',request.candidate.evidence,1,999999999999)
    return telemetry,frame,request,segment


def metric(report,name,mode='reactive',order='first_in_run'):
    return report['groups'].get(mode,{}).get(order,{}).get(name)


def test_real_stage_chain_distinguishes_final_text_pcm_and_positive_consumption():
    clock=Clock();clock.advance(1)
    telemetry,frame,request,segment=pipeline(clock)
    clock.advance(.1);telemetry.provider_started(request)
    clock.advance(.2);telemetry.validated_text(segment.scope,segment.key)
    clock.advance(.1);telemetry.submitted(segment);telemetry.queued(segment)
    clock.advance(.2);telemetry.dequeued(segment)
    clock.advance(.1);telemetry.synthesis_started(segment)
    clock.advance(.4)
    telemetry.pcm(PcmChunk(segment.scope,segment.key,0,0,struct.pack('<24000f',*([.1]*24000))))
    zero=PlaybackAck(segment.scope,segment.key,'progress',0,24000,clock.monotonic())
    telemetry.playback(zero)
    assert metric(telemetry.report(),'admission_to_first_positive_consumption_ms') is None
    clock.advance(.2)
    ack=replace(zero,consumed_source_frames=128,received_at_monotonic=clock.monotonic())
    telemetry.playback(ack);telemetry.playback(ack)
    clock.advance(.1);telemetry.synthesis_done(SynthesisDone(segment.scope,segment.key,24000,20,False,'generator_completed'))
    result=telemetry.report()
    assert metric(result,'provider_dispatch_to_first_validated_text_ms')['p50']==pytest.approx(200)
    assert metric(result,'speech_queue_wait_ms')['p95']==pytest.approx(200)
    assert metric(result,'synthesis_iteration_to_first_pcm_ms')['p50']==pytest.approx(400)
    assert metric(result,'first_pcm_to_positive_consumption_ms')['p50']==pytest.approx(200)
    assert metric(result,'admission_to_first_positive_consumption_ms')['count']==1
    assert metric(result,'synthesis_pipeline_rtf')['p50']==pytest.approx(.7)
    assert metric(result,'synthesis_pipeline_speedup')['p50']==pytest.approx(1/.7)
    encoded=json.dumps(result)
    for private in ('private-utterance','private-evidence','private-candidate','private-topic','PRIVATE TEXT'):
        assert private not in encoded
    assert result['worker_compute_timing_available'] is False
    assert result['twitch_viewer_latency_measured'] is False


def test_cancelled_foreign_replayed_and_missing_stages_do_not_create_latency_zeros():
    clock=Clock();telemetry,frame,request,segment=pipeline(clock)
    telemetry.validated_text(segment.scope,segment.key)
    telemetry.cancelled(Cancel(segment.scope,segment.key.utterance_id,1))
    clock.advance(1)
    telemetry.pcm(PcmChunk(segment.scope,segment.key,0,0,b'\0'*4))
    telemetry.playback(PlaybackAck(segment.scope,segment.key,'completed',1,1,clock.monotonic()))
    telemetry.synthesis_done(SynthesisDone(segment.scope,segment.key,1,1,False,'generator_completed'))
    assert metric(telemetry.report(),'synthesis_pipeline_rtf') is None
    assert metric(telemetry.report(),'admission_to_first_positive_consumption_ms') is None
    telemetry.cancelled(Cancel(segment.scope,segment.key.utterance_id,1))
    assert telemetry.report()['counts']['cancelled_utterances']==1
    foreign=replace(segment,scope=replace(segment.scope,view_epoch=2))
    telemetry.validated_text(foreign.scope,foreign.key);telemetry.submitted(foreign)
    assert len(telemetry._segments)==1


def test_truncated_generation_is_not_full_rtf_and_no_ack_is_inferred_from_done():
    clock=Clock();telemetry,_,_,s=pipeline(clock)
    telemetry.validated_text(s.scope,s.key);telemetry.synthesis_started(s)
    clock.advance(1);telemetry.pcm(PcmChunk(s.scope,s.key,0,0,b'\0'*400))
    telemetry.synthesis_done(SynthesisDone(s.scope,s.key,100,10,True,'duration_bound'))
    report=telemetry.report()
    assert metric(report,'generated_audio_seconds')['p50']==pytest.approx(100/24000)
    assert metric(report,'synthesis_pipeline_rtf') is None
    assert metric(report,'admission_to_first_positive_consumption_ms') is None
    assert report['counts']['truncated_or_incomplete_synthesis']==1


def test_modes_order_and_idle_old_fact_age_are_explicit_and_clock_skew_not_zeroed():
    clock=Clock();telemetry=LatencyTelemetry(clock=clock.monotonic,wall_clock=clock.wall)
    for index,mode in enumerate(('reactive','idle_recap','memory_callback')):
        _,frame,request,s=pipeline(clock,mode=mode,identity=str(index),telemetry=telemetry)
        telemetry.provider_started(request);clock.advance(.1);telemetry.validated_text(s.scope,s.key)
    report=telemetry.report()
    assert metric(report,'provider_dispatch_to_first_validated_text_ms','reactive','first_in_run')['count']==1
    for mode in ('idle_recap','memory_callback'):
        assert metric(report,'provider_dispatch_to_first_validated_text_ms',mode,'subsequent_in_run')['count']==1
    assert report['event_wall_age_clock_dependent'] is True and report['idle_event_age_is_reaction_slo'] is False
    skewed=LatencyTelemetry(clock=clock.monotonic,wall_clock=lambda:0)
    skewed.presented(frame);skewed.admitted(request,frame)
    assert metric(skewed.report(),'selected_event_to_presentation_receipt_wall_ms','memory_callback') is None
    assert skewed.report()['counts']['invalid_wall_age_samples']>=1


def test_end_to_end_percentile_is_direct_not_the_sum_of_stage_percentiles():
    clock=Clock();clock.now=100
    telemetry,_,_,_=pipeline(clock,mode='intro',identity='prime')
    for index,(start,wait,audio_wait) in enumerate(((100,9,1),(120,1,9))):
        clock.now=start
        item=fact(clock,str(index),priority=0)
        telemetry.presented(view(clock,facts=[item]))
        clock.advance(wait)
        frame=view(clock,sequence=2,facts=[item]);telemetry.presented(frame)
        request=CommentaryRequest(CommentaryCandidate(frame.scope,2,str(index),'reactive',0,'topic',
            (item.ref,),9999999),{},256,str(index),1)
        telemetry.admitted(request,frame)
        key=SegmentKey(str(index),1,0)
        telemetry.validated_text(frame.scope,key)
        clock.advance(audio_wait)
        telemetry.playback(PlaybackAck(frame.scope,key,'progress',1,1,clock.monotonic()))
    report=telemetry.report();order='subsequent_in_run'
    first=metric(report,'evidence_first_presentation_to_admission_ms',order=order)['p95']
    second=metric(report,'admission_to_first_positive_consumption_ms',order=order)['p95']
    direct=metric(report,'evidence_first_presentation_to_first_positive_consumption_ms',order=order)
    event=metric(report,'selected_event_to_first_positive_consumption_wall_ms',order=order)
    assert direct['count']==2 and direct['p95']==pytest.approx(10000)
    assert event['p95']==pytest.approx(10000)
    assert first+second==pytest.approx(18000) and direct['p95']!=pytest.approx(first+second)


def test_old_callback_direct_event_age_is_labelled_clock_dependent_and_bad_wall_is_missing():
    clock=Clock();clock.now=200
    telemetry=LatencyTelemetry(clock=clock.monotonic,wall_clock=clock.wall)
    bad=LatencyTelemetry(clock=clock.monotonic,wall_clock=lambda:0)
    item=fact(clock,'old',age=60)
    initial=view(clock,facts=[item])
    for registry in (telemetry,bad): registry.presented(initial)
    clock.advance(9);frame=view(clock,sequence=2,facts=[item])
    request=CommentaryRequest(CommentaryCandidate(frame.scope,2,'callback','memory_callback',3,
        'topic',(item.ref,),9999999),{},256,'callback',1)
    key=SegmentKey('callback',1,0)
    for registry in (telemetry,bad):
        registry.presented(frame);registry.admitted(request,frame);registry.validated_text(frame.scope,key)
    clock.advance(1)
    ack=PlaybackAck(frame.scope,key,'progress',1,1,clock.monotonic())
    for registry in (telemetry,bad): registry.playback(ack)
    name='selected_event_to_first_positive_consumption_wall_ms'
    assert metric(telemetry.report(),name,'memory_callback')['p95']==pytest.approx(70000)
    assert metric(telemetry.report(),'evidence_first_presentation_to_first_positive_consumption_ms','memory_callback')['p95']==pytest.approx(10000)
    assert metric(bad.report(),name,'memory_callback') is None
    assert telemetry.report()['event_wall_age_clock_dependent'] is True
    assert telemetry.report()['idle_event_age_is_reaction_slo'] is False


def test_scope_reset_and_bounded_memory_samples_preserve_counts_not_raw_keys():
    clock=Clock();telemetry=LatencyTelemetry(clock=clock.monotonic,wall_clock=clock.wall,capacity=2,fact_capacity=2,sample_capacity=2)
    for index in range(7):
        _,frame,request,s=pipeline(clock,identity=str(index),telemetry=telemetry)
        telemetry.provider_started(request);clock.advance(index/10);telemetry.validated_text(s.scope,s.key)
    report=telemetry.report();sample=metric(report,'provider_dispatch_to_first_validated_text_ms',order='subsequent_in_run')
    assert sample['count']==6 and sample['retained_count']==2
    assert sample['p50']==pytest.approx(500) and sample['p95']==pytest.approx(600)
    assert all(value<=2 for value in report['retained'].values())
    newer=view(clock,scope=replace(frame.scope,connection_generation=2),facts=())
    telemetry.presented(newer)
    assert telemetry.report()['retained']=={'utterances':0,'segments':0,'fact_timings':0}


def test_negative_and_nonfinite_times_are_absent_not_zero_samples():
    clock=Clock();telemetry,_,request,s=pipeline(clock)
    telemetry.provider_started(request);clock.now=-1;telemetry.validated_text(s.scope,s.key)
    assert metric(telemetry.report(),'provider_dispatch_to_first_validated_text_ms') is None
    clock.now=float('nan');telemetry.synthesis_started(s)
    assert metric(telemetry.report(),'synthesis_pipeline_wall_ms') is None


@pytest.mark.parametrize('truncated',[False,True])
async def test_actual_scheduler_audio_callbacks_complete_eight_second_pcm_or_keep_truncated_interrupted(truncated):
    class WS:
        def __init__(self,sink):self.sink=sink
        async def close(self,**_): pass
        async def send(self,wire):
            from actor_broadcast.speech import parse_header,decode_header
            size,_=parse_header(wire[:8]);kind,value=decode_header(wire[8:8+size])
            sink=self.sink
            if kind in ('pcm','end'):
                status='progress' if kind=='pcm' else 'complete' if value['complete'] else 'interrupted'
                await sink._message({'type':status,'scope':value['scope'],'key':value['key'],
                    'consumed_frames':sink._sent,'underrun_frames':0})
            elif kind=='flush':
                await sink._message({'type':'flushed','scope':value['scope'],
                    'utterance_id':value['utterance_id'],'utterance_epoch':value['utterance_epoch']})
    class Sink(BrowserAudioSink):
        def __init__(self):
            super().__init__(route_guard=lambda:True);self._ws=WS(self)
        async def wait_ready(self,timeout=10): pass
    class Voice:
        async def synthesize(self,s):
            frames=0
            for sequence in range(8):
                yield PcmChunk(s.scope,s.key,sequence,frames,b'\0'*(24000*4));frames+=24000
            yield SynthesisDone(s.scope,s.key,frames,77,truncated,'duration_bound' if truncated else 'generator_completed')
        async def cancel(self,*_): pass
        async def aclose(self): pass
    sink=Sink();holder={}
    output=SpeechAudioOutput(Voice(),sink,validate=lambda s:holder['scheduler'].validate_segment(s))
    scheduler=holder['scheduler']=CommentaryScheduler(Provider(),output)
    sink.on_ack=scheduler.playback_ack
    now=time.monotonic()
    class RealClock:
        monotonic=staticmethod(time.monotonic);wall=staticmethod(time.time)
    clock=RealClock()
    scheduler.observe(view(clock,facts=[fact(clock,priority=0)]));scheduler.ready()
    try:
        for _ in range(200):
            await scheduler.step();await asyncio.sleep(0)
            if scheduler.spoken.report()['completed'] or scheduler.spoken.report()['interrupted']: break
        report=scheduler.report();latency=report['latency']
        assert report['spoken']['completed']==(0 if truncated else 1)
        assert report['spoken']['interrupted']==(1 if truncated else 0)
        assert metric(latency,'provider_dispatch_to_first_validated_text_ms')['count']==1
        assert metric(latency,'synthesis_iteration_to_first_pcm_ms')['count']==1
        assert metric(latency,'admission_to_first_positive_consumption_ms')['count']==1
        assert latency['priority_groups']['reactive_p0']['first_in_run']==latency['groups']['reactive']['first_in_run']
        assert (metric(latency,'synthesis_pipeline_rtf') is None)==truncated
        assert sink.metrics['consumed_frames']==192000
        assert sink.latency is scheduler.latency is output.latency
    finally:
        await scheduler.stop();await output.close()


def test_urgent_and_routine_reactive_direct_samples_split_without_changing_aggregate_or_counters():
    clock=Clock();clock.now=100
    telemetry,_,_,_=pipeline(clock,mode='intro',identity='prime',priority=3)
    name='evidence_first_presentation_to_first_positive_consumption_ms'
    for index,(priority,elapsed) in enumerate(((0,.8),(2,13.9))):
        item=fact(clock,str(index),priority=priority)
        frame=view(clock,facts=[item]);telemetry.presented(frame)
        request=CommentaryRequest(CommentaryCandidate(frame.scope,frame.sequence,str(index),'reactive',priority,
            'topic',(item.ref,),9999999),{},256,str(index),1)
        telemetry.admitted(request,frame)
        key=SegmentKey(str(index),1,0)
        telemetry.validated_text(frame.scope,key)
        # Segment priority is not the admission label; later callbacks identify
        # the original utterance, not a newly inferred priority.
        segment=SpeechSegment(frame.scope,key,3-priority,'PRIVATE TEXT',(item.ref,),1,999999999999)
        telemetry.submitted(segment)
        clock.advance(elapsed)
        ack=PlaybackAck(frame.scope,key,'progress',1,1,clock.monotonic())
        telemetry.playback(ack);telemetry.playback(ack)
    report=telemetry.report();order='subsequent_in_run'
    aggregate=report['groups']['reactive'][order][name]
    urgent=report['priority_groups']['reactive_p0'][order][name]
    routine=report['priority_groups']['reactive_p2'][order][name]
    assert aggregate['count']==2 and aggregate['p50']==pytest.approx(800) and aggregate['p95']==pytest.approx(13900)
    assert urgent['count']==1 and urgent['p95']==pytest.approx(800)
    assert routine['count']==1 and routine['p95']==pytest.approx(13900)
    assert 'reactive_p1' not in report['priority_groups'] and 'reactive_p3' not in report['priority_groups']
    assert report['counts']['admitted_utterances']==3
    assert 'admitted candidate priority' in report['priority_group_scope']
    for private in ('PRIVATE TEXT','private-evidence','private-utterance','topic'):
        assert private not in json.dumps(report)


def test_priority_distributions_bound_samples_keep_counts_and_populate_only_admitted_labels():
    from actor_broadcast.latency import MODES
    clock=Clock();telemetry=LatencyTelemetry(clock=clock.monotonic,wall_clock=clock.wall,
        capacity=2,fact_capacity=2,sample_capacity=2)
    total=0
    for mode in sorted(MODES):
        for priority in range(4):
            for iteration in range(3):
                total+=1
                _,_,request,segment=pipeline(clock,mode=mode,priority=priority,identity=str(total),telemetry=telemetry)
                telemetry.provider_started(request);clock.advance(.1)
                telemetry.validated_text(segment.scope,segment.key)
    report=telemetry.report()
    assert report['counts']['admitted_utterances']==total
    assert len(report['priority_groups'])==len(MODES)*4
    assert all(count<=2 for count in report['retained'].values())
    for mode in MODES:
        for priority in range(4):
            sample=report['priority_groups'][f'{mode}_p{priority}']['subsequent_in_run']['provider_dispatch_to_first_validated_text_ms']
            expected=2 if mode==sorted(MODES)[0] and priority==0 else 3
            assert sample['count']==expected and sample['retained_count']==2
            assert sample['p95']==pytest.approx(100)
    assert all(len(values)<=2 for values in telemetry._priority_samples.values())


def test_cancelled_or_old_scope_callbacks_cannot_add_priority_latency_samples():
    clock=Clock();telemetry,frame,request,segment=pipeline(clock,priority=1)
    telemetry.validated_text(segment.scope,segment.key)
    telemetry.cancelled(Cancel(segment.scope,segment.key.utterance_id,segment.key.utterance_epoch))
    clock.advance(1)
    ack=PlaybackAck(segment.scope,segment.key,'progress',1,1,clock.monotonic())
    telemetry.playback(ack)
    name='admission_to_first_positive_consumption_ms'
    assert name not in telemetry.report()['priority_groups']['reactive_p1']['first_in_run']
    newer=view(clock,scope=replace(frame.scope,connection_generation=2),facts=())
    telemetry.presented(newer);telemetry.playback(ack)
    assert name not in telemetry.report()['priority_groups']['reactive_p1']['first_in_run']
