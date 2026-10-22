"""Deterministic editorial admission, one provider and one audible utterance.

observe() performs only bounded local work. step() admits at most one request;
run() supplies one cheap cancellable timer. Tests can drive step with a clock.
"""
from collections import Counter, OrderedDict
from dataclasses import replace
import asyncio
import hashlib
import json
import time
from uuid import uuid4

from .budget import BudgetLedger, IDLE_MODES
from .config import CommentaryConfig
from .contracts import (Cancel, CommentaryCandidate, EvidenceRef, PlaybackAck, PresentedView,
    ProviderSegment, ProviderNote, ProviderTerminal, SegmentKey, SpeechSegment)
from .context import build_context, reservation_input_tokens, topic_key
from .evidence import EvidenceLedger
from .spoken import SpokenLedger
from .latency import LatencyTelemetry


class CommentaryScheduler:
    def __init__(self, provider, speech, budget=None, *, config=None,
                 clock=time.monotonic, wall_clock=time.time, telemetry=None):
        self.provider, self.speech = provider, speech
        self.config = config or CommentaryConfig()
        self.clock, self.wall_clock = clock, wall_clock
        self.latency = telemetry or LatencyTelemetry(clock=clock, wall_clock=wall_clock)
        bind = getattr(speech, "bind_latency", None)
        if bind is not None: bind(self.latency)
        self.budget = budget or BudgetLedger(config=self.config, clock=wall_clock)
        self._owns_budget = budget is None
        self.view = self.evidence = None
        self.spoken = SpokenLedger()
        self._pending = OrderedDict()
        self._offered = []
        self._last_chat_admission = -float("inf")
        self._speech_pending = OrderedDict()
        self._active = None
        self._active_request = None
        self._epoch = 1
        self._ready = False
        self._closed = False
        self._stop_task = None
        self._stop_complete = False
        self._stop_failure_stages = ()
        self._dependency_tasks = set()
        self._provider_failed = False
        self._needs_cancel = False
        self._cancel_reason = None
        self._p1_deadline = None
        self._p1_preempting_refs = set()
        self._p1_cooldown_bypass = set()
        self._coalesce_deadline = 0.0
        self._last_admission = -float("inf")
        self.last_noteworthy_observation_at = 0.0
        self.last_commentary_playback_end_at = 0.0
        self.preview_or_live_ready_at = 0.0
        self.next_idle_retry_at = 0.0
        self._consecutive_idle = 0
        self._idle_material = frozenset()
        self._terminal_outro_started = False
        self._outro_utterance_id=None
        self._outro_provider_complete=False
        self._outro_needs_fallback=False
        self._wake = asyncio.Event()
        self._capacity = asyncio.Event()
        self._preempt_lock = asyncio.Lock()
        self._step_lock = asyncio.Lock()
        self._counts = Counter()
        self._calls_by_mode = Counter()
        self._suppressions = Counter()
        self._contexts = []
        self.last_suppression = None

    def ready(self):
        """Call only after preview dependencies and a fresh presented view exist."""
        if self._closed or not self._fresh(): return False
        self._ready = True
        self.preview_or_live_ready_at = self.clock()
        self._wake.set()
        return True

    def configure(self, config):
        self.config = config
        self.budget.config = config
        self._wake.set()  # Existing single timer recomputes, never duplicates.

    def admit_viewer_claim(self, fact):
        """Trusted controller supplies already-filtered, attributed chat data.

        This grants no control authority, priority, automatic provider call or
        native game observation status. Native and viewer origins stay distinct.
        """
        if not self._fresh() or self._closed or fact.origin!="viewer_chat" or fact.kind!="player_claim" or fact.priority!=3:
            return False
        return self.evidence.remember(fact,self.view)

    def offer(self, mode, evidence_refs, *, priority=3, analysis_key=None):
        if mode not in ("intro","chat_response","repair","analysis") or not 0<=priority<=3:
            raise ValueError("Only trusted optional commentary modes may be offered")
        refs=tuple(dict.fromkeys(evidence_refs))
        if self._closed or not self._fresh() or self.view.terminal or not self.evidence.valid(refs):return False
        if len(refs)>8 or len(self._offered)>=2:return False
        if mode=="analysis" and not self.config.analysis_enabled:return False
        if any(row[0]==mode and row[1]==refs for row in self._offered):return False
        self._offered.append((mode,refs,priority,analysis_key))
        self._wake.set()
        return True

    def validate_segment(self, segment):
        """Public dependency gate used before TTS, every PCM chunk and playback."""
        if not isinstance(segment,SpeechSegment):return False
        pending=self._speech_pending.get((segment.scope,segment.key))
        if pending is None:return False
        original=pending[0]
        if (segment.text!=original.text or segment.evidence_versions!=original.evidence_versions or
            segment.expires_at_monotonic_ns!=original.expires_at_monotonic_ns or
            segment.max_audio_frames>original.max_audio_frames):return False
        return (not self._closed and self._fresh() and
            segment.scope==self.view.scope and segment.key.utterance_epoch==self._epoch and
            segment.semantic_revision<=self.view.semantic_revision and
            segment.expires_at_monotonic_ns>int(self.clock()*1e9) and
            (self.evidence.valid(segment.evidence_versions) if segment.evidence_versions else
                self.view.terminal and segment.text=="Матч завершён. Спасибо, что были с нами.") and
            (not self.view.terminal or self._terminal_outro_started))

    def _fresh(self):
        return (self.view is not None and
            self.clock() - self.view.presentation.received_at_monotonic <= self.config.feed_stale_seconds and
            self.view.expires_at_unix_ms > int(self.wall_clock()*1000))

    def _invalidate(self, reason):
        if reason not in ('important_event_boundary','important_event_deadline'):
            self._p1_deadline = None
            self._p1_preempting_refs.clear(); self._p1_cooldown_bypass.clear()
        self._epoch += 1  # Synchronous fencing precedes any await/provider IPC.
        self._needs_cancel = True
        self._cancel_reason = reason
        self._wake.set(); self._capacity.set()

    def observe(self, view: PresentedView):
        if self._closed: return False
        now, wall = self.clock(), int(self.wall_clock()*1000)
        if (view.presentation.received_at_monotonic > now + .1 or
                view.observed_at_unix_ms > wall + 1000 or view.expires_at_unix_ms <= wall):
            self._counts["rejected_views"] += 1
            return False
        previous = self.view
        if previous and previous.scope == view.scope and view.sequence <= previous.sequence:
            self._counts["duplicate_views"] += 1
            return False
        if previous and previous.scope==view.scope and view.semantic_revision<previous.semantic_revision:
            self._counts["rejected_views"]+=1;return False
        if previous and previous.scope != view.scope:
            # Generations cannot go backwards within a session; a new match
            # additionally requires explicit readiness, not automatic broadcast.
            if (previous.scope.broadcast_session_id == view.scope.broadcast_session_id and
                previous.scope.match_id == view.scope.match_id and
                (view.scope.connection_generation,view.scope.view_epoch) <
                (previous.scope.connection_generation,previous.scope.view_epoch)):
                self._counts["rejected_views"] += 1; return False
            self._invalidate("view_changed")
            self._pending.clear()
            self._offered.clear()
            if previous.scope.match_id != view.scope.match_id or previous.scope.broadcast_session_id != view.scope.broadcast_session_id:
                self.evidence = None; self.spoken = SpokenLedger(); self._ready = False
                self._consecutive_idle = 0; self._terminal_outro_started = False
                self._outro_utterance_id=None;self._outro_provider_complete=False;self._outro_needs_fallback=False
        if previous and previous.phase!=view.phase:
            self._invalidate("phase_changed");self._pending.clear()
        if self.evidence is None:
            identity=hashlib.sha256((view.scope.broadcast_session_id+":"+view.scope.match_id).encode()).hexdigest()
            self.evidence = EvidenceLedger("broadcast:"+identity, clock=self.wall_clock)
        self.view = view
        if view.revoked_evidence_ids:
            self.evidence.revoke(view.revoked_evidence_ids)
            self.spoken.prune(self.evidence)
        if self._active_request and not self.evidence.valid(self._active_request.candidate.evidence):
            self._invalidate("evidence_revoked")
        for fact in view.facts:
            if fact.observed_at_unix_ms > wall + 1000 or fact.expires_at_unix_ms <= wall:
                continue
            if not self.evidence.remember(fact,view):
                self._counts["deduplicated_facts"] += 1
                continue
            self._counts["new_facts"] += 1
            if fact.priority <= 2:
                self.last_noteworthy_observation_at = now
                self._consecutive_idle = 0
                self._pending[fact.ref] = (fact.priority, now)
                self._coalesce_deadline = now + self.config.coalesce_ms/1000
                while len(self._pending) > self.config.max_recent_public_events:
                    victim=max(self._pending,key=lambda ref:self._pending[ref][0])
                    if self._pending[victim][0] == 0:
                        self._provider_failed=True; self._invalidate("critical_overflow"); break
                    self._pending.pop(victim); self._counts["coalesced_dropped"] += 1
                if fact.priority == 0:
                    self._invalidate("critical_event")
                elif fact.priority == 1 and not self._needs_cancel and self._lower_priority_active():
                    self._p1_preempting_refs.add(fact.ref)
                    self._p1_deadline = min(self._p1_deadline or float("inf"), now+self.config.idle.max_preemption_wait_seconds)
        self._p1_preempting_refs.intersection_update(self._pending)
        if view.terminal and (previous is None or not previous.terminal):
            self._invalidate("match_ended")
            self._offered.clear()
            # Normal pending work is invalid after a terminal boundary. Only
            # explicitly delivered terminal facts may support the final output.
            self._pending={ref:value for ref,value in self._pending.items()
                if (self.evidence.resolve(ref).sequence if self.evidence.resolve(ref) else -1)==view.sequence}
        self.latency.presented(view)
        self._wake.set()
        return True

    @property
    def idle_deadline(self):
        return max(self.last_noteworthy_observation_at,self.last_commentary_playback_end_at,
            self.preview_or_live_ready_at)+self.config.idle.after_seconds

    def _suppress(self, reason):
        if self.last_suppression != reason: self._suppressions[reason] += 1
        self.last_suppression = reason

    def _lower_priority_active(self):
        priorities=[segment.priority for segment,_ in self._speech_pending.values()]
        if self._active is not None and not self._active.done() and self._active_request is not None:
            priorities.append(self._active_request.candidate.priority)
        return bool(priorities) and min(priorities)>1

    def _candidate(self, refs, mode, priority):
        facts=[(ref,self.evidence.resolve(ref).model_dump(mode="json")) for ref in refs if self.evidence.resolve(ref)]
        if not facts: return None
        topic=topic_key(mode,facts)
        return CommentaryCandidate(self.view.scope,self.view.sequence,uuid4().hex,mode,priority,topic,
            tuple(ref for ref,_ in facts),min(value["expires_at_unix_ms"] for _,value in facts))

    def _select_idle(self):
        now,wall=self.clock(),int(self.wall_clock()*1000)
        visible=set(self.view.current.get("visible_actor_ids",()))
        room=self.view.current.get("room_id")
        recent=[]; callbacks=[]
        noted=self.evidence.noted_refs()
        for ref,value in self.evidence.facts():
            metadata=value["content"]["editorial"]
            age=(wall-value["observed_at_unix_ms"])/1000
            if age < 0 or self.spoken.covered(ref,now,self.config.idle.same_topic_repeat_seconds): continue
            score=(metadata["priority"]*-10 + len(visible.intersection(metadata["subjects"]))*3 +
                (2 if metadata["unresolved"] else 0) + (1 if room and value["content"]["value"].get("room_id")==room else 0))
            row=(score,-age,ref.id,ref)
            if age <= self.config.idle.recent_window_seconds: recent.append(row)
            elif self.config.idle.allow_memory_callbacks and (metadata["unresolved"] or ref in noted or visible.intersection(metadata["subjects"]) or room and value["content"]["value"].get("room_id")==room):
                callbacks.append(row)
        recent.sort(reverse=True);callbacks.sort(reverse=True)
        selected=[];claims=0
        for row in recent:
            source=self.evidence.resolve(row[-1])
            if source.kind=="player_claim":
                if claims>=self.config.max_recent_chat_messages:continue
                claims+=1
            selected.append(row[-1])
            if len(selected)>=self.config.idle.max_recent_facts:break
        refs=selected
        if refs:
            refs.extend(row[-1] for row in callbacks[:min(1,self.config.idle.max_callback_facts)])
            candidate=self._candidate(refs,"idle_recap",3)
        else:
            candidate=self._candidate([row[-1] for row in callbacks[:self.config.idle.max_callback_facts]],"memory_callback",3)
        if candidate and self.spoken.topic_recent(candidate.topic_key,now,self.config.idle.same_topic_repeat_seconds):
            self._suppress("already_covered"); return None
        if candidate:
            material=frozenset(candidate.evidence)
            if material==self._idle_material and self._consecutive_idle>=self.config.idle.max_consecutive_without_new_material:
                self._suppress("already_covered");return None
        return candidate

    def _valid(self, request):
        return (not self._closed and self._fresh() and request.utterance_epoch==self._epoch and
            request.candidate.scope==self.view.scope and
            request.candidate.expires_at_unix_ms>int(self.wall_clock()*1000) and
            self.evidence.valid(request.candidate.evidence) and
            (not self.view.terminal or request.candidate.mode=="outro"))

    def playback_ack(self, ack: PlaybackAck):
        key=(ack.scope,ack.key)
        pending=self._speech_pending.get(key)
        if (pending is None or ack.key.utterance_epoch!=self._epoch or self.view is None or
                ack.scope!=self.view.scope or ack.received_at_monotonic>self.clock()+.1):
            self._counts["stale_playback_acks"]+=1;return False
        if not self.spoken.acknowledge(ack): return False
        self.latency.playback(ack)
        if ack.status in ("completed","interrupted"):
            self._speech_pending.pop(key,None)
            self.last_commentary_playback_end_at=self.clock()
            self._capacity.set();self._wake.set()
            if self._p1_deadline is not None:
                self._invalidate("important_event_boundary")
        return True

    async def _capacity_for(self, request, seconds):
        while self._valid(request):
            if (len(self._speech_pending)<self.config.max_pending_speech_segments and
                sum(row[1] for row in self._speech_pending.values())+seconds<=self.config.max_pending_speech_seconds):
                return True
            self._capacity.clear()
            try: await asyncio.wait_for(self._capacity.wait(),timeout=.25)
            except TimeoutError: pass
        return False

    async def _perform(self, request, reservation):
        terminal=None; indexes=set();notes=0
        try:
            self.latency.provider_started(request)
            async with asyncio.timeout(self.config.provider_timeout_seconds):
                async for event in self.provider.generate(request):
                    if terminal is not None:raise ValueError("Provider emitted after terminal")
                    if isinstance(event,ProviderTerminal): terminal=event;continue
                    if isinstance(event,ProviderNote):
                        if notes>=2 or not self._valid(request):continue
                        if not set(event.evidence_versions).issubset(request.candidate.evidence):raise ValueError("Foreign note evidence")
                        try:self.evidence.note(event.kind,event.statement,event.confidence,event.evidence_versions)
                        except Exception:self._counts["rejected_notes"]+=1
                        else:notes+=1;self._counts["public_notes"]+=1
                        continue
                    if not isinstance(event,ProviderSegment): raise ValueError("Unknown provider event")
                    if event.index in indexes: continue
                    if event.index!=len(indexes): raise ValueError("Speech segments must be ordered")
                    indexes.add(event.index)
                    if any(ref not in request.candidate.evidence for ref in event.evidence_versions):
                        raise ValueError("Provider cited unavailable evidence")
                    # Reserve the full actual frame ceiling. Word-count timing
                    # guesses cut a real Russian sentence at exactly six seconds.
                    # The next segment waits for real playback ACK capacity.
                    seconds=float(self.config.max_pending_speech_seconds)
                    key=SegmentKey(request.utterance_id,request.utterance_epoch,event.index)
                    if self._valid(request): self.latency.validated_text(request.candidate.scope,key)
                    if not await self._capacity_for(request,seconds): break
                    if not self._valid(request): break
                    # Current-frame TTL is refreshed by the continuing feed;
                    # pinning speech to one three-second frame would truncate
                    # every normal sentence. Source retention bounds speech,
                    # while the loop separately revokes it on feed staleness.
                    ttl=min(30,(request.candidate.expires_at_unix_ms-int(self.wall_clock()*1000))/1000)
                    if ttl<=0: break
                    segment=SpeechSegment(request.candidate.scope,key,
                        request.candidate.priority,event.text,event.evidence_versions,self.view.semantic_revision,
                        int((self.clock()+ttl)*1e9),max_audio_frames=int(seconds*24000))
                    self.spoken.register(segment,topic_key=request.candidate.topic_key,mode=request.candidate.mode)
                    self._speech_pending[(segment.scope,segment.key)]=(segment,seconds)
                    self.latency.submitted(segment)
                    await self.speech.submit(segment)
                    self._counts["submitted_segments"]+=1
            if terminal is None: self._counts["provider_missing_terminal"]+=1
            elif terminal.status!="completed": self._counts["provider_"+terminal.status]+=1
            if terminal is None or terminal.status!="completed" or not indexes:
                self.next_idle_retry_at=self.clock()+self.config.idle.empty_candidate_backoff_seconds
                if self._valid(request):self._invalidate("provider_incomplete")
                if request.candidate.mode=="outro":self._outro_needs_fallback=True
            elif request.candidate.mode=="outro":self._outro_provider_complete=True
        except asyncio.CancelledError:
            self._counts["cancelled_turns"]+=1
            raise
        except Exception:
            # No provider message, input, output or hidden reasoning is logged.
            self._counts["provider_failures"]+=1
            if self._valid(request): self._invalidate("provider_failure")
            self.next_idle_retry_at=self.clock()+self.config.idle.empty_candidate_backoff_seconds
        finally:
            candidate=getattr(self.provider,"last_terminal",None)
            if terminal is None and isinstance(candidate,ProviderTerminal): terminal=candidate
            await self.budget.reconcile(reservation,terminal)
            self._wake.set();self._capacity.set()

    async def _cancel_active(self, *, emergency=False):
        async with self._preempt_lock:
            cancelled=True
            preemption_epoch=self._epoch
            preemption_reason=self._cancel_reason
            preemption_refs=set(self._p1_preempting_refs)
            request=self._active_request
            cancellations={(segment.scope,segment.key.utterance_id,segment.key.utterance_epoch)
                for segment,_ in self._speech_pending.values()}
            if request:cancellations.add((request.candidate.scope,request.utterance_id,request.utterance_epoch))
            for scope,utterance_id,epoch in cancellations:
                cancel=Cancel(scope,utterance_id,epoch)
                self.latency.cancelled(cancel)
                try:
                    ack=await self._bounded_dependency(self.speech.cancel(cancel,fade_seconds=0 if emergency else .08))
                    if (ack.scope!=cancel.scope or ack.utterance_id!=cancel.utterance_id or
                            ack.utterance_epoch!=cancel.utterance_epoch or not ack.flushed):raise ValueError("Audio flush was not confirmed")
                except Exception:
                    cancelled=False;self._provider_failed=True;self._counts["audio_flush_failed"]+=1
                else:
                    for key,(segment,_) in tuple(self._speech_pending.items()):
                        if (segment.scope,segment.key.utterance_id,segment.key.utterance_epoch)==(scope,utterance_id,epoch):
                            self._speech_pending.pop(key)
                self.spoken.interrupted(cancel.scope,cancel.utterance_id,cancel.utterance_epoch,self.clock())
            self._capacity.set()
            if self._active and not self._active.done():
                try:
                    await self._bounded_dependency(self.provider.interrupt())
                except Exception: self._provider_failed=True;self._counts["provider_interrupt_failed"]+=1
                done,_=await asyncio.wait((self._active,),timeout=.5)
                if not done:
                    self._active.cancel()
                    done,_=await asyncio.wait((self._active,),timeout=.5)
                    if not done:
                        self._provider_failed=True
                        cancelled=False
                        self._active.add_done_callback(lambda task:None if task.cancelled() else task.exception())
            if self._active and self._active.done():
                if not self._active.cancelled(): self._active.exception()
            if self._active is None or self._active.done():self._active=None
            if cancelled:self._active_request=None
            if (cancelled and self._epoch==preemption_epoch and
                    preemption_reason in ('important_event_boundary','important_event_deadline')):
                # Only the exact facts which displaced lower-priority work can
                # bypass one cooldown. No blanket P1 flood exemption or quota.
                self._p1_cooldown_bypass={ref for ref in preemption_refs
                    if ref in self._pending and self._pending[ref][0]==1}
            self._p1_preempting_refs.clear()
            self._p1_deadline=None;self._needs_cancel=False
            self._counts["preemptions"]+=1
            return cancelled

    async def _bounded_dependency(self, awaitable):
        task=asyncio.create_task(awaitable)
        self._dependency_tasks.add(task)
        def done(value):
            self._dependency_tasks.discard(value)
            if not value.cancelled():value.exception()
        task.add_done_callback(done)
        try:
            done,_=await asyncio.wait((task,),timeout=self.config.interrupt_timeout_seconds)
            if task not in done:
                task.cancel()
                await asyncio.wait((task,),timeout=.1)
                raise TimeoutError("Commentary dependency did not stop")
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                task.add_done_callback(lambda value:None if value.cancelled() else value.exception())
            elif not task.cancelled():task.exception()

    async def step(self):
        async with self._step_lock:
            await self._step()

    async def _step(self):
        if self._closed:return
        now=self.clock()
        if self.evidence:self.spoken.prune(self.evidence)
        if any(now*1e9>=segment.expires_at_monotonic_ns for segment,_ in self._speech_pending.values()):
            self._invalidate("speech_timeout")
            self.next_idle_retry_at=now+self.config.idle.empty_candidate_backoff_seconds
        if self._p1_deadline is not None and now>=self._p1_deadline:
            self._invalidate("important_event_deadline")
        if not self._fresh() and (self._active or self._speech_pending): self._invalidate("feed_stale")
        if self._active_request and not self.evidence.valid(self._active_request.candidate.evidence):
            self._invalidate("evidence_expired")
        if self._needs_cancel: await self._cancel_active()
        if self._active and self._active.done():
            if not self._active.cancelled():self._active.exception()
            self._active=None
            if not self._speech_pending:self._active_request=None
        if not self._ready:return
        if not self._fresh():self._suppress("feed_stale");return
        if self._provider_failed:self._suppress("provider_unavailable");return
        if self._active or self._speech_pending:self._suppress("speaking" if self._speech_pending else "llm_active");return
        self._pending=OrderedDict((ref,row) for ref,row in self._pending.items() if self.evidence.resolve(ref))
        self._p1_cooldown_bypass.intersection_update(self._pending)
        candidate=None;analysis_key=None;offered=None;preemption_bypass=False
        if self.view.terminal:
            if self._terminal_outro_started:
                if self._outro_needs_fallback:
                    self._outro_needs_fallback=False
                    await self._fallback_outro()
                return
            candidate=self._candidate(list(self._pending)[:8],"outro",0)
            self._terminal_outro_started=True
            if candidate is None:
                await self._fallback_outro();return
        elif self._pending:
            priority=min(row[0] for row in self._pending.values())
            refs=sorted(self._pending,key=lambda ref:(self._pending[ref][0],self._pending[ref][1],ref.id))[:8]
            preemption_bypass=priority==1 and bool(self._p1_cooldown_bypass.intersection(refs))
            if priority>0 and not preemption_bypass and now<self._coalesce_deadline:self._suppress("coalescing");return
            if priority>0 and not preemption_bypass and now-self._last_admission<self.config.normal_cooldown_seconds:self._suppress("cooldown");return
            candidate=self._candidate(refs,"reactive",priority)
        elif self._offered and (self._offered[0][0]!="chat_response" or now-self._last_chat_admission>=60):
            offered=self._offered.pop(0)
            mode,refs,priority,analysis_key=offered
            if priority>0 and now-self._last_admission<self.config.normal_cooldown_seconds:
                self._offered.insert(0,offered);self._suppress("cooldown");return
            candidate=self._candidate(refs,mode,priority)
        else:
            if not self.config.idle.enabled or self.view.phase not in self.config.idle.allowed_phases:return
            if now<self.idle_deadline or now<self.next_idle_retry_at:return
            candidate=self._select_idle()
            if candidate is None:
                if self.last_suppression!="already_covered":self._suppress("no_useful_facts")
                self.next_idle_retry_at=now+self.config.idle.empty_candidate_backoff_seconds;return
        if candidate is None:return
        try:
            request=build_context(candidate,self.view,self.evidence,self.spoken,self.config,utterance_id=uuid4().hex,epoch=self._epoch)
        except ValueError:
            self._suppress("context_budget");self.next_idle_retry_at=now+self.config.idle.empty_candidate_backoff_seconds;return
        calls=getattr(self.provider,"max_provider_calls",1)
        try:reserved_input=reservation_input_tokens(self.provider,request)
        except ValueError:
            self._provider_failed=True;self._suppress("provider_reservation_invalid");return
        # Attempt once through the unchanged shared budget. A refusal consumes
        # the bypass too; later P1 facts cannot reuse it to hammer admission.
        if preemption_bypass:self._p1_cooldown_bypass.clear()
        decision=await self.budget.reserve(self.view.scope.match_id,candidate.mode,candidate.priority,
            reserved_input,request.max_output_tokens,calls=calls,analysis_key=analysis_key)
        # An observation can arrive during durable I/O. Fence again before any
        # SDK call. A cancelled reservation remains charged conservatively.
        if not self._valid(request):
            self._counts["stale_admissions"]+=1;return
        if any(priority<candidate.priority and self.evidence.resolve(ref) is not None
                for ref,(priority,_) in self._pending.items()):
            # A higher-priority fact can arrive during durable reservation even
            # before there is an active turn to preempt. Do not launch the old
            # lower-priority candidate. Its reservation stays conservatively
            # charged; the next tick selects the real pending priority.
            self._counts['priority_superseded_admissions']+=1
            self._suppress('higher_priority_arrived');self._wake.set();return
        if decision.reservation is None:
            self._suppress("budget");self._counts["budget_"+decision.reason]+=1
            self.next_idle_retry_at=now+self.config.idle.empty_candidate_backoff_seconds
            if candidate.mode=="outro":await self._fallback_outro()
            return
        for ref in candidate.evidence:self._pending.pop(ref,None)
        self._active_request=request
        self.latency.admitted(request,self.view)
        if candidate.mode=="outro":
            self._outro_utterance_id=request.utterance_id;self._outro_provider_complete=False
        self._active=asyncio.create_task(self._perform(request,decision.reservation))
        self._last_admission=now;self.last_suppression=None
        self._calls_by_mode[candidate.mode]+=1
        if candidate.mode=="chat_response":self._last_chat_admission=now
        self._contexts.append(len(json.dumps(request.external_state,ensure_ascii=False,separators=(",",":")).encode("utf-8")))
        self._contexts=self._contexts[-256:]
        if candidate.mode in IDLE_MODES:
            material=frozenset(candidate.evidence)
            self._consecutive_idle=self._consecutive_idle+1 if material==self._idle_material else 1
            self._idle_material=material
            self.next_idle_retry_at=now+self.config.idle.after_seconds

    async def _fallback_outro(self):
        segment=SpeechSegment(self.view.scope,SegmentKey(uuid4().hex,self._epoch,0),0,
            "Матч завершён. Спасибо, что были с нами.",(),self.view.semantic_revision,int((self.clock()+10)*1e9))
        self._active_request=None
        self._outro_utterance_id=segment.key.utterance_id;self._outro_provider_complete=True
        self.spoken.register(segment,topic_key="neutral-outro",mode="outro")
        self._speech_pending[(segment.scope,segment.key)]=(segment,3)
        await self.speech.submit(segment)
        self._counts["neutral_outros"]+=1

    async def run(self):
        try:
            while not self._closed:
                self._wake.clear()
                await self.step()
                try:await asyncio.wait_for(self._wake.wait(),timeout=.25)
                except TimeoutError:pass
        finally:
            await self.stop(emergency=True)

    async def stop(self, *, emergency=True):
        if self._stop_complete:return
        self._closed=True;self._ready=False;self._epoch+=1
        self._pending.clear();self._offered.clear();self._wake.set();self._capacity.set()
        self._p1_preempting_refs.clear();self._p1_cooldown_bypass.clear();self._p1_deadline=None
        if self._stop_task is None or self._stop_task.done():
            self._stop_task=asyncio.create_task(self._finish_stop(emergency=emergency))
            self._stop_task.add_done_callback(lambda task:None if task.cancelled() else task.exception())
        await asyncio.shield(self._stop_task)

    async def _finish_stop(self, *, emergency):
        clean=True;failures=set()
        flush_failures=self._counts["audio_flush_failed"]
        try:
            clean=await self._cancel_active(emergency=emergency)
            if not clean:
                failures.add("audio_flush" if self._counts["audio_flush_failed"]>flush_failures else "cancellation")
        except BaseException:
            clean=False;failures.add("cancellation");self._counts["cancel_cleanup_failed"]+=1
        try:await self._bounded_dependency(self.provider.close())
        except BaseException:
            clean=False;failures.add("provider");self._counts["provider_close_failed"]+=1
        finally:
            if self._owns_budget:
                try:await self.budget.close()
                except BaseException:
                    clean=False;failures.add("budget");self._counts["budget_close_failed"]+=1
        if self._active is not None:
            done,_=await asyncio.wait((self._active,),timeout=.5)
            if self._active not in done:clean=False;failures.add("provider_task")
            else:
                if not self._active.cancelled():self._active.exception()
                self._active=None
        if any(not task.done() for task in self._dependency_tasks):
            clean=False;failures.add("dependency_task")
        self._stop_complete=clean
        self._stop_failure_stages=tuple(sorted(failures))
        if not clean:raise RuntimeError("commentary_cleanup_failed")

    def recover_terminal_cleanup(self):
        """Owner-only terminal recovery after all external dependencies closed.

        Native invokes this only after audio, provider, OBS and transport owners
        actually close. An unavailable render ACK stays unavailable: this marks
        abandoned speech interrupted, without manufacturing playback or coverage.
        """
        if self._stop_complete:return True
        if (not self._closed or self._stop_task is None or not self._stop_task.done() or
                not self._stop_failure_stages or
                not set(self._stop_failure_stages).issubset({"audio_flush","dependency_task"}) or
                not self._counts["audio_flush_failed"] or self._step_lock.locked() or
                self._preempt_lock.locked() or any(not task.done() for task in self._dependency_tasks) or
                self._active is not None and not self._active.done() or
                getattr(self.budget,"closed",False) is not True):return False
        try:provider=self.provider.report()
        except Exception:return False
        if (not isinstance(provider,dict) or provider.get("closed") is not True or
                provider.get("decision_active") is not False):return False
        for segment,_ in self._speech_pending.values():
            self.spoken.interrupted(segment.scope,segment.key.utterance_id,segment.key.utterance_epoch,self.clock())
        self._speech_pending.clear();self._active=None;self._active_request=None;self._needs_cancel=False
        self._stop_complete=True;self._counts["terminal_audio_flush_recovered"]+=1
        return True

    def report(self):
        if self.evidence:self.spoken.prune(self.evidence)
        outro=[row for row in self.spoken.records.values() if row.mode=="outro" and row.segment.key.utterance_id==self._outro_utterance_id]
        finished=bool(outro) and self._outro_provider_complete and all(row.status=="completed" for row in outro)
        last=self.spoken.recent(1)
        return {"calls_by_mode":dict(self._calls_by_mode),"counts":dict(self._counts),
            "suppressed":dict(self._suppressions),"silence_reason":self.last_suppression,
            "provider_active":self._active is not None and not self._active.done(),
            "pending_segments":len(self._speech_pending),"pending_facts":len(self._pending),
            "context_bytes_max":max(self._contexts,default=0),"spoken":self.spoken.report(),
            "evidence":self.evidence.report() if self.evidence else None,"stopped":self._stop_complete,
            "latency":self.latency.report(),
            "terminal_outro_started":self._terminal_outro_started,
            "terminal_outro_finished":finished,"terminal_outro_pending":self._terminal_outro_started and not finished and not self._closed,
            "last_spoken":{"text":last[0]["text"],"completed_at":last[0]["completed_at_monotonic"],"mode":last[0]["mode"]} if last else None}
