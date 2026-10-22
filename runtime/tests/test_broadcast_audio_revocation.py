"""Production audio state machines with an in-memory synthetic renderer.

No GPU/model/OBS/device/network. Flush ACKs are explicitly fake render replies;
the tests never substitute them into a native acceptance report.
"""
import asyncio
from dataclasses import asdict,replace
import json
import time
from types import SimpleNamespace

import pytest

from actor_broadcast.audio import AudioError,BrowserAudioSink,SpeechAudioOutput
from actor_broadcast.contracts import Cancel,CancelAck,PcmChunk,SynthesisDone
from actor_broadcast.speech import decode_header,parse_header
from test_broadcast_audio import segment,packet


class Renderer:
    def __init__(self,sink,*,consume=True,flush=True):
        self.sink=sink;self.consume=consume;self.flush=flush;self.sent=[];self.closes=[]
        self.request=SimpleNamespace(path='/audio',headers={'Origin':sink._origin})
        self.incoming=asyncio.Queue();self.flush_sent=asyncio.Event();self.pcm_sent=asyncio.Event()
        self.flush_gate=None;self.deliveries=[];self.total=0
        self.incoming.put_nowait(json.dumps({'type':'ready','rate':48000,'worklet':True,'context_state':'running'}))
    async def recv(self):return json.dumps({'type':'authenticate','capability':self.sink._capability})
    def __aiter__(self):return self
    async def __anext__(self):
        value=await self.incoming.get()
        if value is None:raise StopAsyncIteration
        if isinstance(value,BaseException):raise value
        return value
    def message(self,value):self.incoming.put_nowait(json.dumps(value))
    async def send(self,wire):
        size,n=parse_header(wire[:8]);kind,value=decode_header(wire[8:8+size])
        self.sent.append((kind,value,n))
        if kind=='begin':self.total=0
        elif kind=='pcm':
            self.total+=n//4;self.pcm_sent.set()
            if self.consume:self.message(dict(type='progress',scope=value['scope'],key=value['key'],consumed_frames=self.total,underrun_frames=0))
        elif kind=='end':
            self.message(dict(type='complete' if value['complete'] else 'interrupted',scope=value['scope'],
                key=value['key'],consumed_frames=self.total,underrun_frames=0))
        elif kind=='flush':
            self.flush_sent.set()
            if self.flush:
                async def reply():
                    if self.flush_gate is not None:await self.flush_gate.wait()
                    self.message(dict(type='flushed',scope=value['scope'],utterance_id=value['utterance_id'],utterance_epoch=value['utterance_epoch']))
                self.deliveries.append(asyncio.create_task(reply()))
    async def close(self,**kwargs):self.closes.append(kwargs);self.incoming.put_nowait(None)


async def attached(*,route=lambda:True,timeout=.5,**options):
    sink=BrowserAudioSink(route_guard=route,ack_timeout=timeout);sink._origin='http://127.0.0.1:1'
    renderer=Renderer(sink,**options);handler=asyncio.create_task(sink._handle(renderer))
    await asyncio.wait_for(sink._ready.wait(),1)
    return sink,renderer,handler


async def finish(sink,renderer,handler):
    if renderer.flush_gate is not None:renderer.flush_gate.set()
    await sink.aclose()
    await asyncio.wait_for(handler,2)
    if renderer.deliveries:await asyncio.gather(*renderer.deliveries,return_exceptions=True)


async def until(predicate,timeout=1):
    async with asyncio.timeout(timeout):
        while not predicate():await asyncio.sleep(.001)


@pytest.mark.parametrize('cause',['semantic','expired'])
async def test_producer_revocation_flushes_exact_scope_without_fatal_renderer_loss(cause,monkeypatch):
    current=[True];ns=[0]
    if cause=='expired':monkeypatch.setattr('actor_broadcast.audio.time.monotonic_ns',lambda:ns[0])
    sink,renderer,handler=await attached()
    s=segment(expires_at_monotonic_ns=100) if cause=='expired' else segment()
    async def chunks():
        yield PcmChunk(s.scope,s.key,0,0,b'\0'*400)
        if cause=='semantic':current[0]=False
        else:ns[0]=101
        yield PcmChunk(s.scope,s.key,1,100,b'\0'*400)
        yield SynthesisDone(s.scope,s.key,200,5,False,'generator_completed')
    try:
        result=await sink.play(s,chunks(),validate=lambda _:current[0])
        assert result.status=='interrupted' and result.consumed_source_frames<=100
        assert len([x for x in renderer.sent if x[0]=='pcm'])==1
        assert len([x for x in renderer.sent if x[0]=='flush'])==1
        assert sink.public_status['renderer_ready'] and renderer.closes==[]
        assert sink.metrics['scope_flush_confirmed']==1 and sink.metrics['played_segments']==0
        assert sink.metrics['segment_expirations']==(cause=='expired')
    finally:await finish(sink,renderer,handler)


async def test_real_scheduler_p0_heartbeat_then_cancel_preserves_renderer_and_next_utterance():
    from actor_broadcast.scheduler import CommentaryScheduler
    from test_broadcast_commentary import Provider,view,fact
    class Clock:
        monotonic=staticmethod(time.monotonic);wall=staticmethod(time.time)
    clock=Clock();sink,renderer,handler=await attached();first=asyncio.Event();release=asyncio.Event();errors=[]
    class Voice:
        count=0
        async def synthesize(self,s):
            self.count+=1
            yield PcmChunk(s.scope,s.key,0,0,b'\0'*400)
            if self.count==1:first.set();await release.wait()
            yield PcmChunk(s.scope,s.key,1,100,b'\0'*400)
            yield SynthesisDone(s.scope,s.key,200,5,False,'generator_completed')
        async def cancel(self,c):release.set();return CancelAck(c.scope,c.utterance_id,c.utterance_epoch,0,True)
        async def aclose(self):release.set()
    holder={};output=SpeechAudioOutput(Voice(),sink,validate=lambda s:holder['scheduler'].validate_segment(s),on_error=errors.append)
    scheduler=holder['scheduler']=CommentaryScheduler(Provider(),output)
    sink.on_ack=scheduler.playback_ack
    scheduler.observe(view(clock,facts=[fact(clock,'initial',priority=0)]));scheduler.ready()
    try:
        await scheduler.step();await asyncio.wait_for(first.wait(),1)
        await until(lambda:sink._consumed>0)
        old=sink._active
        scheduler.observe(view(clock,sequence=2,facts=[fact(clock,'critical',priority=0)]))
        # Deliberately let the actual heartbeat win the race before step/flush.
        await until(lambda:sink.metrics['scope_flush_confirmed']==1)
        assert sink.public_status['renderer_ready'] and renderer.closes==[]
        for _ in range(100):
            await scheduler.step();await asyncio.sleep(.001)
            if scheduler.spoken.report()['completed']:break
        assert errors==[] and sink.metrics['played_segments']==1
        assert scheduler.spoken.records[(old.scope,old.key)].status=='interrupted'
        assert scheduler.spoken.report()['completed']==1 and output.synthesizer.count==2
        assert sink.metrics['route_lost']==sink.metrics['transport_lost']==sink.metrics['protocol_invalid']==0
    finally:
        release.set();await scheduler.stop();await output.close();await finish(sink,renderer,handler)


async def test_backpressure_and_send_lock_waits_recheck_fence_before_late_pcm():
    for wait in ('backpressure','send_lock'):
        valid=[True];sink,renderer,handler=await attached(consume=False)
        s=segment(max_audio_frames=120000);gate=asyncio.Event();waiting=asyncio.Event()
        initial=3 if wait=='backpressure' else 1
        async def chunks():
            for index in range(initial):yield PcmChunk(s.scope,s.key,index,index*24000,b'\0'*(24000*4))
            waiting.set();await gate.wait()
            yield PcmChunk(s.scope,s.key,initial,initial*24000,b'\0'*(24000*4))
            yield SynthesisDone(s.scope,s.key,(initial+1)*24000,9,False,'generator_completed')
        play=asyncio.create_task(sink.play(s,chunks(),validate=lambda _:valid[0]))
        try:
            await asyncio.wait_for(waiting.wait(),1)
            if wait=='send_lock':await sink._send_lock.acquire()
            gate.set();await asyncio.sleep(.01)
            valid[0]=False
            cancel=asyncio.create_task(sink.cancel(Cancel(s.scope,s.key.utterance_id,s.key.utterance_epoch),fade_seconds=0))
            await asyncio.sleep(0)
            if wait=='send_lock':sink._send_lock.release()
            assert (await asyncio.wait_for(cancel,1)).flushed
            result=await asyncio.wait_for(play,1)
            assert result.status=='interrupted'
            assert len([x for x in renderer.sent if x[0]=='pcm'])==initial
            assert sink.metrics['sent_frames']==initial*24000
            assert len([x for x in renderer.sent if x[0]=='flush'])==1 and renderer.closes==[]
        finally:
            gate.set()
            if sink._send_lock.locked():sink._send_lock.release()
            if not play.done():play.cancel()
            await asyncio.gather(play,return_exceptions=True)
            await finish(sink,renderer,handler)


async def test_concurrent_heartbeat_producer_and_scheduler_share_one_owned_flush_and_late_ack_cannot_clear_new():
    valid=[True];sink,renderer,handler=await attached();renderer.flush_gate=asyncio.Event()
    s=segment();release=asyncio.Event()
    async def chunks(item):
        yield PcmChunk(item.scope,item.key,0,0,b'\0'*400)
        await release.wait()
        yield SynthesisDone(item.scope,item.key,100,5,False,'generator_completed')
    play=asyncio.create_task(sink.play(s,chunks(s),validate=lambda _:valid[0]))
    try:
        await asyncio.wait_for(renderer.pcm_sent.wait(),1);valid[0]=False
        await asyncio.wait_for(renderer.flush_sent.wait(),1)
        a=asyncio.create_task(sink.cancel(Cancel(s.scope,s.key.utterance_id,1),fade_seconds=.08))
        b=asyncio.create_task(sink.cancel(Cancel(s.scope,s.key.utterance_id,1),fade_seconds=0))
        # Even an otherwise well-shaped complete event cannot cover a revoked scope.
        sink._done=SynthesisDone(s.scope,s.key,100,5,False,'generator_completed')
        await sink._message(packet('complete',s,100))
        assert sink.metrics['played_segments']==0 and not sink._terminal.done()
        renderer.flush_gate.set();release.set()
        assert all(ack.flushed for ack in await asyncio.gather(a,b))
        assert (await play).status=='interrupted'
        assert len([x for x in renderer.sent if x[0]=='flush'])==1
        valid[0]=True;release.clear();renderer.pcm_sent.clear();renderer.flush_gate=None
        new=replace(s,key=replace(s.key,utterance_epoch=2));next_play=asyncio.create_task(sink.play(new,chunks(new),validate=lambda _:True))
        await asyncio.wait_for(renderer.pcm_sent.wait(),1)
        renderer.message(dict(type='flushed',scope=asdict(s.scope),utterance_id=s.key.utterance_id,utterance_epoch=1))
        renderer.message(packet('complete',s,100));await asyncio.sleep(.01)
        assert sink._active==new and not sink._terminal.done() and sink.metrics['played_segments']==0
        release.set();assert (await next_play).status=='completed'
    finally:
        release.set();renderer.flush_gate.set() if renderer.flush_gate is not None else None
        if not play.done():play.cancel()
        await asyncio.gather(play,return_exceptions=True);await finish(sink,renderer,handler)


async def test_unconfirmed_flush_is_fatal_retained_and_cannot_admit_new_segment():
    valid=[True];sink,renderer,handler=await attached(flush=False,timeout=.25)
    s=segment()
    async def chunks():
        yield PcmChunk(s.scope,s.key,0,0,b'\0'*400)
        valid[0]=False
        yield SynthesisDone(s.scope,s.key,100,5,False,'generator_completed')
    try:
        with pytest.raises(TimeoutError):await sink.play(s,chunks(),validate=lambda _:valid[0])
        assert not sink._ready.is_set() and renderer.closes and sink.metrics['scope_flush_failed']==1
        assert sink.metrics['scope_flush_confirmed']==0 and sink.metrics['played_segments']==0
        retained=sink._flush_task
        new=replace(s,key=replace(s.key,utterance_epoch=2))
        with pytest.raises(AudioError,match='flush_unconfirmed'):await sink.play(new,chunks(),validate=lambda _:True)
        assert sink._flush_task is retained and len([x for x in renderer.sent if x[0]=='begin'])==1
    finally:await finish(sink,renderer,handler)


async def test_matching_retained_cancel_after_active_cleanup_awaits_actual_pending_or_failed_flush():
    sink,renderer,handler=await attached(flush=False,timeout=.25);s=segment()
    sink._active=s
    request=Cancel(s.scope,s.key.utterance_id,s.key.utterance_epoch)
    first=asyncio.create_task(sink.cancel(request,fade_seconds=0))
    try:
        await asyncio.wait_for(renderer.flush_sent.wait(),1)
        retained=sink._flush_task;sink._active=None
        second=asyncio.create_task(sink.cancel(request,fade_seconds=0))
        await asyncio.sleep(.01)
        assert not second.done() and sink._flush_task is retained
        errors=await asyncio.gather(first,second,return_exceptions=True)
        assert all(isinstance(error,TimeoutError) for error in errors)
        with pytest.raises(TimeoutError):await sink.cancel(request)
        other=Cancel(s.scope,'different-empty-utterance',1)
        assert (await sink.cancel(other)).flushed  # Known empty, never the old task's ACK.
        assert sink.metrics['scope_flush_failed']==1 and sink.metrics['scope_flush_confirmed']==0
    finally:
        if not first.done():first.cancel()
        await asyncio.gather(first,return_exceptions=True);await finish(sink,renderer,handler)


async def test_cancelled_waiter_does_not_cancel_shared_flush_or_admit_new_audio():
    sink,renderer,handler=await attached();s=segment();sink._active=s
    sink._terminal=asyncio.get_running_loop().create_future();renderer.flush_gate=asyncio.Event()
    request=Cancel(s.scope,s.key.utterance_id,s.key.utterance_epoch)
    waiter=asyncio.create_task(sink.cancel(request,fade_seconds=0))
    try:
        await asyncio.wait_for(renderer.flush_sent.wait(),1);waiter.cancel()
        with pytest.raises(asyncio.CancelledError):await waiter
        retained=sink._flush_task
        assert not retained.cancelled() and not retained.done()
        new=replace(s,key=replace(s.key,utterance_epoch=2))
        async def no_chunks():
            if False:yield
        with pytest.raises(AudioError,match='flush_unconfirmed'):await sink.play(new,no_chunks(),validate=lambda _:True)
        retry=asyncio.create_task(sink.cancel(request));renderer.flush_gate.set()
        assert (await retry).flushed and sink._flush_task is retained
        assert len([x for x in renderer.sent if x[0]=='flush'])==1
    finally:await finish(sink,renderer,handler)


@pytest.mark.parametrize('mismatch',['scope','epoch'])
async def test_unrelated_flush_ack_never_confirms_current_scope(mismatch):
    sink,renderer,handler=await attached(flush=False,timeout=.25);s=segment();sink._active=s
    request=Cancel(s.scope,s.key.utterance_id,s.key.utterance_epoch)
    task=asyncio.create_task(sink.cancel(request,fade_seconds=0))
    try:
        await asyncio.wait_for(renderer.flush_sent.wait(),1)
        scope=replace(s.scope,view_epoch=2) if mismatch=='scope' else s.scope
        renderer.message(dict(type='flushed',scope=asdict(scope),utterance_id=s.key.utterance_id,
            utterance_epoch=2 if mismatch=='epoch' else 1))
        with pytest.raises(TimeoutError):await task
        assert sink.metrics['scope_flush_confirmed']==0 and sink.metrics['scope_flush_failed']==1
        assert not sink._ready.is_set()
    finally:
        if not task.done():task.cancel()
        await asyncio.gather(task,return_exceptions=True);await finish(sink,renderer,handler)


async def test_route_loss_wins_over_simultaneous_semantic_revocation_and_remains_fatal():
    route=[True];valid=[True];sink,renderer,handler=await attached(route=lambda:route[0]);s=segment()
    async def chunks():
        yield PcmChunk(s.scope,s.key,0,0,b'\0'*400)
        route[0]=valid[0]=False
        yield SynthesisDone(s.scope,s.key,100,5,False,'generator_completed')
    try:
        with pytest.raises(AudioError,match='route_lost'):await sink.play(s,chunks(),validate=lambda _:valid[0])
        assert not sink._ready.is_set() and renderer.closes and sink.metrics['route_lost']==1
        assert sink.metrics['scope_flush_confirmed']==0 and sink.metrics['played_segments']==0
    finally:await finish(sink,renderer,handler)


async def test_fresh_unknown_pcm_is_not_misclassified_as_expected_revocation():
    sink,renderer,handler=await attached();s=segment()
    async def chunks():yield PcmChunk(s.scope,s.key,99,0,b'\0'*400)
    try:
        with pytest.raises(AudioError,match='chunk_order'):await sink.play(s,chunks(),validate=lambda _:True)
        assert sink.metrics['played_segments']==0
    finally:await finish(sink,renderer,handler)


@pytest.mark.parametrize('failure',['transport','protocol'])
async def test_attached_connection_closed_error_is_transport_while_malformed_ack_stays_protocol(failure):
    sink,renderer,handler=await attached()
    try:
        if failure=='transport':
            exceptions=pytest.importorskip('websockets.exceptions')
            renderer.incoming.put_nowait(exceptions.ConnectionClosedError(None,None))
        else:renderer.message({'type':'progress','unexpected':'invalid'})
        await asyncio.wait_for(handler,1)
        assert not sink.public_status['renderer_ready'] and sink._ws is None and renderer.closes
        assert sink.metrics['transport_lost']==(failure=='transport')
        assert sink.metrics['protocol_invalid']==(failure=='protocol')
        assert sink.metrics['route_lost']==0 and sink.metrics['scope_flush_confirmed']==0
    finally:await finish(sink,renderer,handler)
