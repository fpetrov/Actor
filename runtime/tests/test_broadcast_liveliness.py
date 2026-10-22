"""Editorial timing, grounded sizing and ACK history; no real SDK/audio/apps."""
import asyncio
from dataclasses import replace

import pytest
from pydantic import ValidationError

from actor_broadcast.config import CommentaryConfig, IdleConfig
from actor_broadcast.context import build_context, topic_key
from actor_broadcast.contracts import PlaybackAck, SegmentKey, SpeechSegment
from actor_broadcast.scheduler import CommentaryScheduler, INTRO_COURTESY, OUTRO_COURTESY, OUTRO_FALLBACK
from actor_broadcast.spoken import SpokenLedger
from actor_codex.commentary import CodexCommentaryProvider
from test_broadcast_codex import SpeechSDK
from test_broadcast_commentary import Clock, Provider, Speech, fact, fresh, pump, system, view


class RandomSamples:
    def __init__(self,*values):self.values=iter(values);self.calls=0
    def __call__(self):self.calls+=1;return next(self.values)


def test_jitter_is_one_sample_per_quiet_anchor_or_cadence_config_not_poll_or_heartbeat():
    clock=Clock();samples=RandomSamples(0,1,.5)
    scheduler=CommentaryScheduler(Provider(),Speech(),clock=clock.monotonic,wall_clock=clock.wall,rng=samples)
    original=fact(clock)
    scheduler.observe(view(clock,facts=[original]));scheduler.ready()
    assert scheduler.config.idle.jitter_fraction==.25 and scheduler.idle_deadline==15
    for index in range(1000):
        clock.advance(.01)
        scheduler.observe(view(clock,sequence=index+2,facts=[original],current={'x':index}))
        assert scheduler.idle_deadline==15
    assert samples.calls==1
    policy=scheduler.config.model_copy(update={'idle':scheduler.config.idle.model_copy(update={'after_seconds':24.0})})
    scheduler.configure(policy)
    assert scheduler.idle_deadline==30 and samples.calls==2
    scheduler.configure(policy.model_copy(update={'idle':policy.idle.model_copy(update={'allow_memory_callbacks':False})}))
    assert scheduler.idle_deadline==30 and samples.calls==2
    scheduler.observe(view(clock,sequence=1002,facts=[fact(clock,'new',priority=2)]))
    assert scheduler.idle_deadline==clock.now+24 and samples.calls==3


@pytest.mark.parametrize('base,sample,delay',[(20,0,15),(20,1,25),(5,0,5),(120,1,120)])
async def test_default_jitter_admits_only_at_sampled_bounded_deadline(base,sample,delay):
    clock=Clock();provider=Provider();samples=RandomSamples(sample)
    scheduler=CommentaryScheduler(provider,Speech(),clock=clock.monotonic,wall_clock=clock.wall,
        config=CommentaryConfig(idle=IdleConfig(after_seconds=base)),rng=samples)
    scheduler.observe(view(clock,facts=[fact(clock,unresolved=True)]));scheduler.ready()
    try:
        assert scheduler.idle_deadline==delay
        clock.advance(delay-.01);await fresh(scheduler,clock)
        assert not provider.requests
        clock.advance(.01);await fresh(scheduler,clock)
        assert len(provider.requests)==1 and samples.calls==1
    finally:await scheduler.stop()


def test_zero_jitter_keeps_exact_boundaries_without_calling_rng_and_invalid_fraction_rejects():
    clock=Clock()
    scheduler=CommentaryScheduler(Provider(),Speech(),clock=clock.monotonic,wall_clock=clock.wall,
        config=CommentaryConfig(idle=IdleConfig(jitter_fraction=0.0)),rng=lambda:pytest.fail('unneeded random call'))
    scheduler.observe(view(clock));scheduler.ready();assert scheduler.idle_deadline==20
    for value in (-.1,1.1,True,'0.25',float('nan')):
        with pytest.raises(ValidationError):IdleConfig(jitter_fraction=value)


@pytest.mark.parametrize('mode',['intro','idle'])
def test_retained_versions_with_identical_identity_and_original_timestamp_sort_without_comparing_reference_objects(mode):
    clock=Clock();scheduler,_,_=system(clock)
    first=fact(clock,'same',age=120,unresolved=True)
    second=replace(first,version='2')
    scheduler.observe(view(clock,facts=[first]))
    scheduler.observe(view(clock,sequence=2,facts=[second]));scheduler.ready()
    if mode=='intro':
        scheduler.public_started();scheduler._queue_public_intro()
        refs=scheduler._offered[0][1]
    else:refs=scheduler._select_idle().evidence
    assert refs==(second.ref,first.ref)
    assert scheduler._candidate(refs,'idle_recap',3).topic_key==scheduler._candidate((first.ref,),'idle_recap',3).topic_key


async def test_actual_terminal_audio_ack_resamples_but_generation_and_progress_do_not():
    clock=Clock();samples=RandomSamples(0,1);provider=Provider();speech=Speech()
    scheduler=CommentaryScheduler(provider,speech,clock=clock.monotonic,wall_clock=clock.wall,rng=samples)
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    try:
        assert scheduler.idle_deadline==15
        clock.advance(15);await fresh(scheduler,clock)
        assert len(speech.segments)==1 and scheduler.idle_deadline==15
        clock.advance(3);await fresh(scheduler,clock)
        assert speech.acknowledge(scheduler,clock,status='progress',frames=12000)
        assert scheduler.idle_deadline==15 and samples.calls==1
        clock.advance(2);await fresh(scheduler,clock)
        assert speech.acknowledge(scheduler,clock)
        assert scheduler.idle_deadline==45 and samples.calls==2
    finally:await scheduler.stop()


@pytest.mark.parametrize('mode,priority,words,preferred,maximum',[
    ('reactive',0,[5,16],[1,1],1),('reactive',2,[25,45],[2,3],4),
    ('idle_recap',3,[35,55],[3,3],4),('memory_callback',3,[35,55],[3,3],4),
    ('intro',3,[8,16],[1,1],1),('outro',0,[8,16],[1,1],1),
])
def test_trusted_mode_targets_and_fresh_view_vs_historical_age_enter_actual_context(mode,priority,words,preferred,maximum):
    clock=Clock();scheduler,_,_=system(clock)
    source=fact(clock,priority=priority,age=120,unresolved=True)
    current=view(clock,facts=[source],phase='Waiting')
    scheduler.observe(current)
    candidate=scheduler._candidate([source.ref],mode,priority)
    request=build_context(candidate,current,scheduler.evidence,scheduler.spoken,scheduler.config,utterance_id='u',epoch=1)
    external=request.external_state;policy=external['speech_presentation']
    assert policy['words_target']==words and policy['clauses_preferred']==preferred
    assert policy['max_segments']==maximum and policy['padding_allowed']is False
    assert external['phase']=='Waiting' and external['presented_view_observed_at_unix_ms']==1000000
    assert external['evidence'][0]['observed_at_unix_ms']==880000
    assert external['evidence'][0]['age_at_presented_view_seconds']==120
    assert request.max_output_tokens==512  # Reservation, not an SDK cap.


def test_custom_word_targets_reach_context_and_clock_skew_is_not_fabricated_zero_age():
    clock=Clock();scheduler,_,_=system(clock,config=CommentaryConfig(spoken_words_target=(30,40),
        idle=IdleConfig(words_target=(40,60))))
    source=replace(fact(clock),observed_at_unix_ms=1000500)
    current=view(clock,facts=[source]);scheduler.observe(current)
    for mode,words in [('reactive',[30,40]),('idle_recap',[40,60])]:
        candidate=scheduler._candidate([source.ref],mode,2)
        request=build_context(candidate,current,scheduler.evidence,scheduler.spoken,scheduler.config,utterance_id='u',epoch=1)
        assert request.external_state['speech_presentation']['words_target']==words
        assert request.external_state['evidence'][0]['age_at_presented_view_seconds']is None


def heard(ledger,current,source,index,*,mode='idle_recap',topic=None,status='completed',text=None):
    segment=SpeechSegment(current.scope,SegmentKey('heard-'+str(index),1,0),3,
        text or 'Разрешённая фраза '+str(index),(source.ref,),current.semantic_revision,30000000000)
    ledger.register(segment,topic_key=topic or str(index),mode=mode)
    ledger.acknowledge(PlaybackAck(segment.scope,segment.key,status,24000,24000,float(index)))
    return segment


def test_six_heard_clauses_exclude_generated_progress_and_interrupted_then_trim_to_context_budget():
    clock=Clock();scheduler,_,_=system(clock);source=fact(clock);current=view(clock,facts=[source])
    scheduler.observe(current)
    for index in range(8):heard(scheduler.spoken,current,source,index)
    heard(scheduler.spoken,current,source,8,status='interrupted')
    heard(scheduler.spoken,current,source,9,status='progress')
    candidate=scheduler._candidate([source.ref],'idle_recap',3)
    request=build_context(candidate,current,scheduler.evidence,scheduler.spoken,scheduler.config,utterance_id='u',epoch=1)
    assert [row['text'] for row in request.external_state['actually_spoken']]==[
        'Разрешённая фраза '+str(index) for index in range(7,1,-1)]
    scheduler.spoken=SpokenLedger()
    for index in range(6):heard(scheduler.spoken,current,source,index,text='а'*1000)
    smaller=scheduler.config.model_copy(update={'idle':scheduler.config.idle.model_copy(update={'context_target_tokens':600})})
    request=build_context(candidate,current,scheduler.evidence,scheduler.spoken,smaller,utterance_id='u',epoch=1)
    assert len(request.external_state['actually_spoken'])<6 and request.external_state['evidence'][0]['ref']['id']==source.id


async def test_completed_idle_semantic_topic_blocks_after_repeat_ttl_and_version_refresh_but_new_connection_can_speak():
    clock=Clock();scheduler,provider,speech=system(clock)
    source=fact(clock,unresolved=True,lifetime=900)
    scheduler.observe(view(clock,facts=[source]));scheduler.ready()
    try:
        clock.advance(20);await fresh(scheduler,clock)
        assert speech.acknowledge(scheduler,clock)
        clock.advance(200);await fresh(scheduler,clock)
        assert len(provider.requests)==1 and scheduler.report()['silence_reason']=='already_covered'
        refreshed=fact(clock,source.id,version='2',unresolved=True,lifetime=900)
        await fresh(scheduler,clock,facts=[refreshed])
        clock.advance(20);await fresh(scheduler,clock)
        assert len(provider.requests)==1
        corrected=replace(refreshed,version='3',content={'room_id':'hall','text':'Исправленная разрешённая деталь'})
        await fresh(scheduler,clock,facts=[corrected])
        clock.advance(20);await fresh(scheduler,clock)
        assert len(provider.requests)==2 and corrected.ref in provider.requests[-1].candidate.evidence
        assert speech.acknowledge(scheduler,clock)
        clock.advance(130)  # Recall may use that historical material once it is no longer recently covered.
        await fresh(scheduler,clock,facts=[fact(clock,'new-connection',unresolved=True)])
        clock.advance(20);await fresh(scheduler,clock)
        assert len(provider.requests)==3
        assert {ref.id for ref in provider.requests[-1].candidate.evidence}=={source.id,'new-connection'}
    finally:await scheduler.stop()


async def test_previously_reactive_fact_can_receive_one_historical_callback_not_unlimited_recap():
    clock=Clock();scheduler,provider,speech=system(clock)
    source=fact(clock,priority=0,unresolved=True,lifetime=900)
    scheduler.observe(view(clock,facts=[source]));scheduler.ready()
    try:
        await pump(scheduler);assert provider.requests[-1].candidate.mode=='reactive'
        assert speech.acknowledge(scheduler,clock)
        clock.advance(121);await fresh(scheduler,clock)
        assert len(provider.requests)==2 and provider.requests[-1].candidate.mode=='memory_callback'
        assert speech.acknowledge(scheduler,clock)
        clock.advance(140);await fresh(scheduler,clock)
        assert len(provider.requests)==2
    finally:await scheduler.stop()


async def test_interrupted_idle_audio_is_not_completed_topic_coverage():
    clock=Clock();scheduler,provider,speech=system(clock)
    scheduler.observe(view(clock,facts=[fact(clock,unresolved=True)]));scheduler.ready()
    try:
        clock.advance(20);await fresh(scheduler,clock)
        assert speech.acknowledge(scheduler,clock,status='interrupted')
        assert not scheduler.spoken.idle_topics
        clock.advance(21);await fresh(scheduler,clock)
        assert len(provider.requests)==2
    finally:await scheduler.stop()


def test_idle_topic_ring_is_bounded_ack_only_and_not_public_report_content():
    clock=Clock();source=fact(clock);current=view(clock,facts=[source]);ledger=SpokenLedger()
    for index in range(150):
        segment=heard(ledger,current,source,index,topic='private-topic-'+str(index))
        ledger.declare_idle_complete(current.scope,segment.key.utterance_id,1,1,'private-topic-'+str(index),(source.ref,))
    assert len(ledger.idle_topics)==128 and not ledger.idle_topic_completed('private-topic-0')
    assert ledger.idle_topic_completed('private-topic-149')
    assert 'private-topic-0' not in str(ledger.report())


def test_completed_clause_or_provider_declaration_alone_cannot_promote_permanent_idle_topic():
    clock=Clock();source=fact(clock);current=view(clock,facts=[source]);ledger=SpokenLedger()
    first=heard(ledger,current,source,0,topic='topic')
    assert not ledger.idle_topic_completed('topic')  # No genuine provider terminal declaration yet.
    second=replace(first,key=SegmentKey(first.key.utterance_id,1,1))
    ledger.register(second,topic_key='topic',mode='idle_recap')
    ledger.declare_idle_complete(current.scope,first.key.utterance_id,1,2,'topic',(source.ref,))
    assert not ledger.idle_topic_completed('topic')
    ledger.acknowledge(PlaybackAck(second.scope,second.key,'progress',12000,24000,1))
    assert not ledger.idle_topic_completed('topic')
    ledger.acknowledge(PlaybackAck(second.scope,second.key,'completed',24000,24000,2))
    assert ledger.idle_topic_completed('topic') and not ledger._idle_declarations


@pytest.mark.parametrize('segments',[2,3])
async def test_partial_idle_utterance_does_not_permanently_cover_unheard_candidate_after_120_seconds(segments):
    clock=Clock();scheduler,provider,speech=system(clock,Provider(segments=segments))
    sources=[fact(clock,'a',lifetime=900,unresolved=True),fact(clock,'b',lifetime=900,unresolved=True)]
    scheduler.observe(view(clock,facts=sources));scheduler.ready()
    try:
        clock.advance(20);await fresh(scheduler,clock)
        assert speech.acknowledge(scheduler,clock,index=0)
        await pump(scheduler)
        assert not scheduler.spoken.idle_topics  # First real clause is not the whole candidate.
        for index in range(1,segments):
            assert speech.acknowledge(scheduler,clock,index=index,status='interrupted')
            await pump(scheduler)
        assert not scheduler.spoken.idle_topics and not scheduler.spoken._idle_declarations
        clock.advance(130);await fresh(scheduler,clock)
        assert len(provider.requests)==2 and set(provider.requests[-1].candidate.evidence)=={source.ref for source in sources}
    finally:await scheduler.stop()


def test_pruned_or_cancelled_pending_idle_declaration_never_promotes():
    clock=Clock();source=fact(clock);current=view(clock,facts=[source]);ledger=SpokenLedger()
    segment=heard(ledger,current,source,0,status='progress',topic='unheard')
    ledger.declare_idle_complete(current.scope,segment.key.utterance_id,1,1,'unheard',(source.ref,))
    assert ledger._idle_declarations and not ledger.idle_topics
    class Gone:
        def valid(self,_):return False
        def resolve(self,_):return None
    ledger.prune(Gone())
    assert not ledger._idle_declarations and not ledger.idle_topics
    segment=heard(ledger,current,source,1,status='progress',topic='cancelled')
    ledger.declare_idle_complete(current.scope,segment.key.utterance_id,1,1,'cancelled',(source.ref,))
    ledger.interrupted(current.scope,segment.key.utterance_id,1,1)
    assert not ledger._idle_declarations and not ledger.idle_topics


async def test_provider_omitting_unheard_candidate_evidence_cannot_permanently_promote_whole_idle_topic():
    from actor_broadcast.contracts import ProviderSegment,ProviderTerminal
    class SubsetProvider(Provider):
        async def generate(self,request):
            self.requests.append(request)
            yield ProviderSegment(0,'Мы видели только первую деталь.',(request.candidate.evidence[0],))
            yield ProviderTerminal('completed',100,20,0,0)
    clock=Clock();scheduler,provider,speech=system(clock,SubsetProvider())
    sources=[fact(clock,'a',lifetime=900,unresolved=True),fact(clock,'b',lifetime=900,unresolved=True)]
    scheduler.observe(view(clock,facts=sources));scheduler.ready()
    try:
        clock.advance(20);await fresh(scheduler,clock)
        assert speech.acknowledge(scheduler,clock)
        assert len(scheduler.spoken.coverage)==1 and not scheduler.spoken.idle_topics
        assert not scheduler.spoken._idle_declarations
        clock.advance(130);await fresh(scheduler,clock)
        assert len(provider.requests)==2 and set(provider.requests[-1].candidate.evidence)=={source.ref for source in sources}
    finally:await scheduler.stop()


async def test_intro_requires_actual_public_marker_and_real_game_ref_then_is_one_shot():
    clock=Clock();scheduler,provider,speech=system(clock)
    scheduler.observe(view(clock));scheduler.ready()
    try:
        assert not scheduler.offer('intro',())
        await pump(scheduler);assert not provider.requests
        assert scheduler.public_started() and not scheduler.public_started()
        clock.advance(20);await fresh(scheduler,clock)
        assert not provider.requests and not speech.segments  # No invented greeting fact.
        source=fact(clock)
        await fresh(scheduler,clock,facts=[source])
        assert not provider.requests and speech.segments[0].text==INTRO_COURTESY
        assert not speech.segments[0].evidence_versions and scheduler.validate_segment(speech.segments[0])
        assert not scheduler.spoken.coverage and scheduler.latency.report()['counts'].get('admitted_utterances',0)==0
        assert speech.acknowledge(scheduler,clock)
        await pump(scheduler)
        assert len(provider.requests)==1 and provider.requests[0].candidate.mode=='intro'
        assert provider.requests[0].candidate.evidence==(source.ref,)
        assert speech.acknowledge(scheduler,clock)
        assert not scheduler.public_started()
        clock.advance(13);await fresh(scheduler,clock)
        assert len(provider.requests)==1
    finally:await scheduler.stop()


async def test_p0_beats_public_greeting_and_terminal_drops_pending_intro():
    clock=Clock();scheduler,provider,speech=system(clock)
    source=fact(clock,priority=0)
    scheduler.observe(view(clock,facts=[source]));scheduler.ready();scheduler.public_started()
    try:
        await pump(scheduler)
        assert provider.requests[0].candidate.mode=='reactive'
        assert speech.acknowledge(scheduler,clock)
        clock.advance(13);await fresh(scheduler,clock,phase='GameOver',terminal=True)
        assert all(request.candidate.mode!='intro' for request in provider.requests)
        assert not scheduler._public_intro_pending
    finally:await scheduler.stop()


async def test_intro_uses_shared_budget_and_refusal_does_not_create_repeated_reservation_attempts():
    clock=Clock();scheduler,provider,speech=system(clock,config=CommentaryConfig(hard_calls_per_match=1))
    scheduler.observe(view(clock,facts=[fact(clock,priority=0)]));scheduler.ready();scheduler.public_started()
    try:
        await pump(scheduler);assert speech.acknowledge(scheduler,clock)
        clock.advance(13);await fresh(scheduler,clock)
        assert speech.segments[-1].text==INTRO_COURTESY and not scheduler.spoken.idle_topics
        assert speech.acknowledge(scheduler,clock);await pump(scheduler)
        assert not scheduler._public_intro_pending and len(provider.requests)==1
        for _ in range(10):await pump(scheduler)
        assert scheduler.report()['counts']['budget_match_calls']==1
    finally:await scheduler.stop()


async def test_completed_local_greeting_survives_provider_failure_without_fake_fact_coverage_or_retry():
    from actor_broadcast.contracts import ProviderTerminal
    class FailedProvider(Provider):
        async def generate(self,request):
            self.requests.append(request)
            yield ProviderTerminal('failed',100,0,0,0)
    clock=Clock();scheduler,provider,speech=system(clock,FailedProvider())
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready();scheduler.public_started()
    try:
        await pump(scheduler);assert speech.segments[-1].text==INTRO_COURTESY
        assert speech.acknowledge(scheduler,clock)
        await pump(scheduler)
        assert len(provider.requests)==1 and provider.requests[0].candidate.mode=='intro'
        assert scheduler.spoken.report()['completed']==1 and not scheduler.spoken.coverage
        assert scheduler.report()['counts']['provider_failed']==1
        for _ in range(5):await pump(scheduler)
        assert len(provider.requests)==1 and len(speech.segments)==1
    finally:await scheduler.stop()


async def test_viewer_claim_alone_cannot_supply_public_introduction_prerequisite():
    clock=Clock();scheduler,provider,speech=system(clock)
    source=replace(fact(clock,kind='player_claim'),origin='viewer_chat')
    scheduler.observe(view(clock,facts=[source]));scheduler.ready();scheduler.public_started()
    try:
        await pump(scheduler)
        assert not provider.requests and not speech.segments and scheduler._public_intro_pending
    finally:await scheduler.stop()


async def test_production_provider_receives_p0_schema_bound_and_exact_editorial_external_payload():
    clock=Clock();scheduler,_,_=system(clock);source=fact(clock,'fact',priority=0)
    current=view(clock,facts=[source]);scheduler.observe(current)
    request=build_context(scheduler._candidate([source.ref],'reactive',0),current,scheduler.evidence,
        scheduler.spoken,scheduler.config,utterance_id='u',epoch=1)
    sdk=SpeechSDK();provider=CodexCommentaryProvider(sdk=sdk)
    try:
        events=[event async for event in provider.generate(request)]
        assert events[-1].status=='completed' and sdk.schemas[0]['properties']['segments']['maxItems']==1
        assert '"speech_presentation"' in sdk.inputs[0].content
        assert 'Не объявляй всё устаревшим' in sdk.thread_options[0]['base_instructions']
    finally:await provider.close()


async def test_three_slow_complete_clauses_serialize_ack_without_expanding_audio_or_provider_limits():
    clock=Clock();scheduler,provider,speech=system(clock,Provider(segments=3))
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    try:
        clock.advance(20);await fresh(scheduler,clock)
        for index in range(3):
            assert len(speech.segments)==index+1
            assert sum(seconds for _,seconds in scheduler._speech_pending.values())==12
            assert speech.segments[-1].max_audio_frames==288000
            clock.advance(10);await fresh(scheduler,clock)
            assert speech.acknowledge(scheduler,clock,index=index)
            await pump(scheduler)
        assert scheduler.spoken.report()['completed']==3 and len(provider.requests)==1
        assert scheduler.config.provider_timeout_seconds==45 and provider.max_active==1
    finally:await scheduler.stop()


async def test_slow_ack_still_hits_real_bounded_provider_timeout_instead_of_fabricating_completed_clauses():
    clock=Clock();scheduler,provider,speech=system(clock,Provider(segments=4),
        CommentaryConfig(provider_timeout_seconds=.03))
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready()
    try:
        clock.advance(20);await fresh(scheduler,clock)
        await asyncio.sleep(.06);await pump(scheduler)
        assert len(speech.segments)==1 and scheduler.spoken.report()['completed']==0
        assert scheduler.report()['counts']['provider_failures']==1 and speech.cancellations
        assert scheduler.spoken.report()['interrupted']==1
    finally:await scheduler.stop()


async def test_mode_limit_rejects_extra_stream_object_before_it_can_reach_tts():
    clock=Clock();scheduler,_,_=system(clock);source=fact(clock,'fact',priority=0)
    current=view(clock,facts=[source]);scheduler.observe(current)
    request=build_context(scheduler._candidate([source.ref],'reactive',0),current,scheduler.evidence,
        scheduler.spoken,scheduler.config,utterance_id='u',epoch=1)
    sdk=SpeechSDK();sdk.outputs[0]['segments']*=2
    provider=CodexCommentaryProvider(sdk=sdk)
    try:
        events=[event async for event in provider.generate(request)]
        assert sum(hasattr(event,'text') for event in events)<=1
        assert events[-1].status=='failed'
        assert provider.report()['failure_diagnostic']['reason_code']=='speech_segment_limit'
    finally:await provider.close()


async def test_p0_interrupts_real_registered_intro_courtesy_without_covering_or_forcing_it_to_finish():
    clock=Clock();scheduler,provider,speech=system(clock)
    scheduler.observe(view(clock,facts=[fact(clock)]));scheduler.ready();scheduler.public_started()
    try:
        await pump(scheduler);greeting=speech.segments[-1]
        assert greeting.text==INTRO_COURTESY and not provider.requests
        clock.advance(1);await fresh(scheduler,clock,facts=[fact(clock,'urgent',priority=0)])
        assert speech.cancellations and not scheduler.validate_segment(greeting)
        assert scheduler.spoken.records[(greeting.scope,greeting.key)].status=='interrupted'
        assert provider.requests[0].candidate.mode=='reactive' and not scheduler.spoken.coverage
    finally:await scheduler.stop(emergency=True)


async def test_terminal_generated_result_ack_then_farewell_ack_are_both_real_and_local_text_is_not_provider_latency():
    clock=Clock();scheduler,provider,speech=system(clock)
    scheduler.observe(view(clock));scheduler.ready()
    try:
        await fresh(scheduler,clock,phase='GameOver',terminal=True,facts=[fact(clock,'result',priority=0)])
        result=speech.segments[-1]
        assert result.evidence_versions and len(provider.requests)==1
        assert speech.acknowledge(scheduler,clock)
        assert not scheduler.report()['terminal_outro_finished']
        await pump(scheduler);farewell=speech.segments[-1]
        assert farewell.text==OUTRO_COURTESY and not farewell.evidence_versions
        assert scheduler.spoken.records[(farewell.scope,farewell.key)].topic_key=='neutral-outro'
        assert scheduler.validate_segment(farewell) and not scheduler.report()['terminal_outro_finished']
        assert scheduler.latency._segments.get((farewell.scope,farewell.key))is None
        assert len(scheduler.spoken.coverage)==1
        assert speech.acknowledge(scheduler,clock)
        assert scheduler.report()['terminal_outro_finished']
        assert len(scheduler.spoken.coverage)==1 and len(provider.requests)==1
    finally:await scheduler.stop()


async def test_terminal_fallback_has_goodbye_even_without_refs_or_provider_quota_and_emergency_never_forces_it():
    clock=Clock();scheduler,provider,speech=system(clock)
    scheduler.observe(view(clock));scheduler.ready()
    await fresh(scheduler,clock,phase='GameOver',terminal=True)
    segment=speech.segments[-1]
    assert segment.text==OUTRO_FALLBACK and not provider.requests and not segment.evidence_versions
    assert segment.max_audio_frames==288000 and scheduler.validate_segment(segment)
    await scheduler.stop(emergency=True)
    assert not scheduler.report()['terminal_outro_finished'] and not scheduler.spoken.coverage
    assert speech.cancellations[-1][1]==0 and len(speech.segments)==1


@pytest.mark.parametrize('farewell_status',['completed','interrupted'])
async def test_real_interrupted_result_ack_gets_one_fallback_closer_without_fabricating_result_coverage_or_replaying_closer(farewell_status):
    clock=Clock();scheduler,provider,speech=system(clock)
    scheduler.observe(view(clock));scheduler.ready()
    try:
        await fresh(scheduler,clock,phase='GameOver',terminal=True,facts=[fact(clock,'result',priority=0)])
        result=speech.segments[-1]
        assert speech.acknowledge(scheduler,clock,status='interrupted',frames=12000)
        assert not scheduler.report()['terminal_outro_finished'] and not scheduler.spoken.coverage
        await pump(scheduler)
        fallback=speech.segments[-1]
        assert fallback.text==OUTRO_FALLBACK and len(speech.segments)==2
        assert not scheduler.report()['terminal_outro_finished']
        assert scheduler.spoken.records[(result.scope,result.key)].status=='interrupted'
        assert speech.acknowledge(scheduler,clock,status=farewell_status)
        for _ in range(10):await pump(scheduler)
        assert len(provider.requests)==1 and len(speech.segments)==2
        assert scheduler.report()['terminal_outro_finished']==(farewell_status=='completed')
        assert not scheduler.spoken.coverage and scheduler.report()['counts']['neutral_outros']==1
    finally:await scheduler.stop()
