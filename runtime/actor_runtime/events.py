"""Session-local bounded event ingestion, assessment and cognitive acknowledgements.

Ingestion is synchronous and bounded; no model, SQLite or await is on the gRPC
receive path. Only installed rules supply priority/identity semantics.
"""
from __future__ import annotations

import asyncio
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
import logging
import time
from dataclasses import replace

from actor.core.v1 import actor_pb2 as core

from .classifiers import (ClassificationContext, EventAssessment, EventHints,
                          EventRuleRegistry, RuleBasedEventClassifier)
from .config import RouterConfig, ClassifierConfig
from .errors import ActorError
from .extensions import OpaquePayload, type_url
from .world import CachedPayload

LOG = logging.getLogger("actor_runtime")


@dataclass
class RoutedEvent:
    id: str
    session_id: str
    entry: CachedPayload
    extension_id: str
    extension_version: str
    type_url: str
    hints: EventHints = field(default_factory=EventHints)
    assessment: EventAssessment | None = None
    aggregate_count: int = 1
    received: bool = True
    assessed: bool = False
    remembered: bool = False
    context_included: bool = False
    handled: bool = False
    trigger_revision: int = 0
    observation_plan_id: str | None = None


class EventRouter:
    def __init__(self, session, *, config=None, classifier=None, rules=None,
                 memory=None, policy_tags=(), classifier_config=None):
        self.session = session
        self.config = config or RouterConfig()
        self.classifier_config = classifier_config or ClassifierConfig()
        self.rules = rules or EventRuleRegistry.discover()
        self.classifier = classifier or RuleBasedEventClassifier(self.rules)
        self.memory = memory
        self.policy_tags = tuple(policy_tags)
        self.replan = asyncio.Event()
        self.preempt = asyncio.Event()
        self.cognitive_revision = 0
        self.degraded = False
        self.degraded_reason = None
        self.objective = None
        self.current_skill = lambda: None
        self._pending = OrderedDict()
        self._queue = OrderedDict()
        self._dedup = OrderedDict()
        self._wake = asyncio.Event()
        self._worker = None
        self._closed = False
        self._last_replan = -float("inf")
        self._deferred = False
        self._counts = Counter()
        self._latencies = deque(maxlen=2048)
        self._audit = deque(maxlen=128)
        self._context_revision = 0
        self._in_flight_event = None
        for rule in self.rules.rules.values():
            if rule.inbox_policy is not None:
                session.world.configure_inbox(rule.capability_id, rule.inbox_policy)

    def prepare_entry(self, entry):
        capability = self.session.capabilities[entry.capability_id]
        url = entry.payload.type_url if isinstance(entry.payload, OpaquePayload) else type_url(type(entry.payload))
        rule = self.rules.rules.get((entry.capability_id, capability.extension_id, url))
        if rule is None:
            return entry
        if rule.accept is not None and not rule.accept(self.session, entry):
            self._counts["events_rejected"] += 1
            return None
        expiry = getattr(entry.payload, rule.expiry_field, 0) if rule.expiry_field else 0
        entry = replace(entry, data_policy=rule.data_policy, expires_at_unix_ms=expiry)
        if rule.data_policy != "public":
            self.session.privacy.mark_private()
        for field in rule.secret_fields:
            value = getattr(entry.payload, field, "")
            if isinstance(value, str) and value:
                self.session.register_known_secret(value, expires_at_unix_ms=getattr(entry.payload, "expires_at_unix_ms", None))
        return entry

    def context_priority(self, event, selection):
        entry = getattr(event, "entry", event)
        capability = self.session.capabilities.get(entry.capability_id)
        if capability is None:
            return 0
        url = entry.payload.type_url if isinstance(entry.payload, OpaquePayload) else type_url(type(entry.payload))
        rule = self.rules.rules.get((entry.capability_id, capability.extension_id, url))
        if rule is None or rule.context_priority is None:
            return 0
        try:
            return max(-100, min(100, float(rule.context_priority(selection, event))))
        except Exception:
            return 0

    def configure(self, *, config=None, classifier=None, memory=None,
                  policy_tags=(), classifier_config=None):
        if self._worker is not None:
            raise RuntimeError("Configure routing before Objective start")
        if config is not None:
            self.config = config
        if classifier is not None:
            self.classifier = classifier
        self.memory = memory
        self.policy_tags = tuple(policy_tags)
        if classifier_config is not None:
            self.classifier_config = classifier_config
        bind = getattr(self.classifier, "bind_router", None)
        if callable(bind):
            bind(self)

    def classification_context(self):
        world = self.session.world
        # Compact self-only current state, fresh copies supplied by snapshotting
        # providers. Never a reference to the full game history.
        relevant = tuple(item for item in world.observations.values()
                         if item.entity_id == world.self_actor_id)[:24]
        plan = getattr(self.session, "executable_plan", None)
        return ClassificationContext(self.objective, self.current_skill(), relevant, self.policy_tags,
                                     plan.snapshot() if plan is not None else None)

    def _context(self):
        return self.classification_context()

    def _record(self, event, action):
        self._audit.append({"event_id": event.id, "sequence": event.entry.sequence,
            "action": action, "revision": self.cognitive_revision,
            "reason": event.assessment.reason_code if event.assessment else None,
            "aggregate_count": event.aggregate_count})
        self.session.emit_trace("router", event_id=event.id, sequence=event.entry.sequence,
            action=action, cognitive_revision=self.cognitive_revision,
            assessment=event.assessment.model_dump(mode="json") if event.assessment else None,
            mandatory_review=event.hints.mandatory_review, trigger_revision=event.trigger_revision,
            handled=event.handled, aggregate_count=event.aggregate_count)

    def _fail_safe(self, reason):
        self.degraded = True
        self.degraded_reason = reason
        self._counts["degraded_transitions"] += 1
        self.cognitive_revision += 1
        self.replan.set()
        self.preempt.set()
        LOG.error("Event routing degraded session=%s reason=%s", self.session.session_id, reason)

    def ingest(self, entry, *, plan=None):
        """Called before the historical WorldState ring can evict this event."""
        if not self.config.enabled or self._closed or self.degraded:
            return
        capability = self.session.capabilities[entry.capability_id]
        if plan is not None and (plan is not self.session.executable_plan
                or capability.kind != core.CAPABILITY_KIND_OBSERVATION
                or entry.entity_id != self.session.world.self_actor_id):
            return
        self._counts["events_received"] += 1
        extension = self.session.extensions[capability.extension_id]
        url = entry.payload.type_url if isinstance(entry.payload, OpaquePayload) else type_url(type(entry.payload))
        # Stable identity from the wire, plus optional game-native logical ID.
        event = RoutedEvent(f"{self.session.session_id}:{entry.sequence}:{entry.capability_id}",
            self.session.session_id, entry, capability.extension_id, extension.extension_version, url)
        if plan is not None:
            # The same observation can need a new assessment under a newly
            # accepted plan. Ordinary wire EVENT identities remain unchanged.
            event.observation_plan_id = plan.id
            event.id += f":plan:{plan.id}"
        rule = self.rules.lookup(event)
        if rule is not None:
            try:
                event.hints = rule.hints(event)
                if not isinstance(event.hints, EventHints):
                    raise TypeError("Invalid event hints")
            except Exception:
                self._fail_safe("event_hint_failure")
                return
        logical = event.hints.semantic_id[:256]
        key = (self.session.session_id, entry.capability_id, logical or str(entry.sequence))
        if plan is not None:
            key += (plan.id,)
        if key in self._dedup:
            self._counts["events_duplicated"] += 1
            return
        self._dedup[key] = event.hints.potentially_critical
        while len(self._dedup) > self.config.max_dedup_keys:
            victim = next((key for key, protected in self._dedup.items() if not protected), None)
            if victim is None:
                self._dedup.pop(key)
                self._fail_safe("critical_dedup_overflow")
                return
            self._dedup.pop(victim)
        payload_size = (len(entry.payload.value) if isinstance(entry.payload, OpaquePayload)
                        else entry.payload.ByteSize())
        if payload_size > self.config.max_event_bytes:
            self._counts["events_oversized"] += 1
            self._fail_safe("event_payload_too_large")
            return
        # Copy the typed payload: downstream game code cannot mutate stored evidence.
        if not isinstance(entry.payload, OpaquePayload):
            payload = type(entry.payload)()
            payload.CopyFrom(entry.payload)
            event.entry = CachedPayload(entry.entity_id, entry.capability_id, entry.sequence,
                                        entry.revision, payload, entry.received_at, entry.data_policy, entry.expires_at_unix_ms)
        now = time.monotonic()
        if event.hints.aggregation_key and not event.hints.potentially_critical:
            for previous in reversed(self._pending.values()):
                if (previous.hints.aggregation_key == event.hints.aggregation_key
                        and previous.extension_id == event.extension_id
                        and previous.entry.capability_id == event.entry.capability_id
                        and previous.entry.entity_id == event.entry.entity_id
                        and previous.observation_plan_id == event.observation_plan_id
                        and now - previous.entry.received_at <= self.config.aggregation_window
                        and not previous.handled):
                    previous.aggregate_count += 1
                    self._counts["events_aggregated"] += 1
                    return
        # Never evict a trigger or a potentially critical unassessed event.
        while len(self._pending) >= self.config.max_pending_events:
            victim = next((eid for eid, old in self._pending.items()
                if not old.trigger_revision and not old.hints.potentially_critical), None)
            if victim is None:
                self._counts["critical_overflows"] += 1
                self._fail_safe("critical_pending_overflow")
                return
            self._pending.pop(victim)
            self._queue.pop(victim, None)
            self._counts["events_evicted"] += 1
        if len(self._queue) >= self.config.max_queue_size:
            victim = next((eid for eid in self._queue
                if not self._pending[eid].hints.potentially_critical), None)
            if victim is None:
                self._counts["critical_overflows"] += 1
                self._fail_safe("critical_queue_overflow")
                return
            self._queue.pop(victim)
            self._pending.pop(victim)
            self._counts["queue_dropped"] += 1
        self._pending[event.id] = event
        self._queue[event.id] = None
        if plan is not None:
            plan.observation_admitted(entry)
        self._counts["queue_high_watermark"] = max(self._counts["queue_high_watermark"], len(self._queue))
        self._record(event, "received")
        # Trusted critical rules are tiny pure functions, evaluated immediately.
        # This invalidates a stale Brain result even while a slow semantic call
        # occupies the worker. SQLite/model work remains exclusively asynchronous.
        if self.objective is not None and event.hints.potentially_critical and rule is not None:
            started = time.monotonic()
            try:
                context = self.classification_context()
                assessment = EventAssessment.model_validate(rule.assess(context, event))
                observer = getattr(self.classifier, "observe_rule_assessment", None)
                if callable(observer):
                    try:
                        observer(context, event, assessment)
                    except Exception:
                        self._counts["observer_errors"] += 1
                self._apply(event, assessment)
                self._counts["classifier_calls"] += 1
                self._latencies.append((time.monotonic() - started) * 1000)
            except Exception:
                self._fail_safe("critical_rule_failure")
        self._wake.set()

    def _current_plan_observation(self, event):
        plan = getattr(self.session, "executable_plan", None)
        return (event.observation_plan_id is None
                or plan is not None and plan.id == event.observation_plan_id)

    async def start(self, objective, current_skill=None):
        if self._worker is not None:
            raise RuntimeError("Event router already started")
        self.objective = objective
        if current_skill is not None:
            self.current_skill = current_skill
        self._closed = False
        if self.config.enabled:
            self._worker = asyncio.create_task(self._run(), name="actor-event-router")
            self._wake.set()

    def update_context(self, objective, current_skill=None):
        """Change the bounded episode without restarting a session-owned worker."""
        if self._closed:
            raise ActorError("event_router_closed", "A closed router cannot be reused")
        self.objective = objective
        if current_skill is not None:
            self.current_skill = current_skill

    def _apply(self, event, assessment):
        if not self._current_plan_observation(event):
            event.handled = True
            self._record(event, "plan_observation_superseded")
            return
        # Provider opinion is input to policy, never direct actuator authority.
        if event.hints.mandatory_review:
            assessment = assessment.model_copy(update={"urgency": "critical", "should_buffer": True,
                "should_remember": True, "should_interrupt_skill": True,
                "should_request_brain_review": True, "reason_code": "mandatory_system_review"})
        if (assessment.confidence < self.classifier_config.confidence_threshold
                and event.hints.potentially_critical):
            assessment = assessment.model_copy(update={"should_buffer": True, "should_remember": True,
                "should_interrupt_skill": True, "should_request_brain_review": True,
                "reason_code": "uncertain_critical_review"})
        event.assessment = assessment
        event.assessed = True
        self._counts["events_classified"] += 1
        if not assessment.should_request_brain_review:
            self._counts["events_ignored"] += 1
        self._record(event, "assessed")
        plan = getattr(self.session, "executable_plan", None)
        if not assessment.should_request_brain_review and plan is not None:
            if plan.offer(event, self.session.world.self_actor_id):
                self._counts["planned_reactions"] += 1
                self._record(event, "plan_reaction_queued")
        if assessment.should_request_brain_review:
            critical = assessment.urgency == "critical" or event.hints.potentially_critical
            if critical or time.monotonic() - self._last_replan >= self.config.decision_cooldown:
                self._request(event)
            else:
                self._deferred = True

    def _request(self, event):
        if not self._current_plan_observation(event):
            event.handled = True
            self._record(event, "plan_observation_superseded")
            return
        if event.trigger_revision or event.handled:
            return
        self.cognitive_revision += 1
        event.trigger_revision = self.cognitive_revision
        # An already pending request represents one logical wakeup/burst.
        if not self.replan.is_set():
            self._counts["replans_requested"] += 1
        self.replan.set()
        if event.assessment.should_interrupt_skill:
            self.preempt.set()
        self._last_replan = time.monotonic()
        self._record(event, "review_requested")

    async def _run(self):
        try:
            while not self._closed:
                while self._queue:
                    eid = max(self._queue, key=lambda key: (self._pending[key].hints.priority,
                                                           -self._pending[key].entry.sequence))
                    self._queue.pop(eid)
                    event = self._pending.get(eid)
                    if event is None:
                        continue
                    if not self._current_plan_observation(event):
                        self._pending.pop(eid, None)
                        continue
                    self._in_flight_event = event
                    if not event.assessed:
                        started = time.monotonic()
                        try:
                            async with asyncio.timeout(self.classifier_config.timeout):
                                assessment = await self.classifier.classify(self._context(), event)
                            assessment = EventAssessment.model_validate(assessment)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            self._counts["classifier_fallbacks"] += 1
                            assessment = await RuleBasedEventClassifier(self.rules).classify(self._context(), event)
                        self._counts["classifier_calls"] += 1
                        self._latencies.append((time.monotonic() - started) * 1000)
                        if time.monotonic() - event.entry.received_at > self.config.stale_event_seconds:
                            # Old routine information waits for the next natural
                            # decision. Potentially critical facts still get review.
                            if not event.hints.potentially_critical:
                                assessment = assessment.model_copy(update={"should_request_brain_review": False,
                                    "should_interrupt_skill": False, "reason_code": "stale_event_buffered"})
                        self._apply(event, assessment)
                    if self._needs_memory(event):
                        try:
                            stored = await self.memory.remember_event(event, tags=event.hints.tags)
                            event.remembered = stored is not None
                            self._counts["events_remembered"] += int(event.remembered)
                            self._record(event, "remembered" if event.remembered else "memory_disabled")
                        except Exception:
                            self._fail_safe("memory_write_failed")
                    if event.handled or (not event.assessment.should_buffer and not event.trigger_revision):
                        event.handled = True
                        self._pending.pop(eid, None)
                    self._in_flight_event = None
                if self._deferred:
                    await asyncio.sleep(min(0.1, max(0.001, self.config.decision_cooldown)))
                    if time.monotonic() - self._last_replan >= self.config.decision_cooldown:
                        self._deferred = False
                        for event in list(self._pending.values()):
                            if event.assessment and event.assessment.should_request_brain_review:
                                self._request(event)
                    continue
                self._wake.clear()
                await self._wake.wait()
        except asyncio.CancelledError:
            pass
        except Exception:
            self._fail_safe("event_worker_failure")
        finally:
            self._in_flight_event = None

    def take_trigger_events(self):
        return [event for event in self._pending.values() if event.trigger_revision and not event.handled]

    def context_events(self):
        return sorted((event for event in self._pending.values() if event.assessed and not event.handled),
                      key=lambda event: (-(event.assessment.relevance == "relevant"),
                                         -event.hints.priority, -event.entry.sequence))

    def acknowledge_context(self, event_ids, revision):
        for eid in event_ids:
            event = self._pending.get(eid)
            if event is not None:
                event.context_included = True
                self._record(event, "context_included")
        self._context_revision = revision

    def acknowledge_decision(self, event_ids, revision):
        # Never acknowledge an unseen/newer strategic event using an old answer.
        if revision != self.cognitive_revision:
            return False
        for eid in event_ids:
            event = self._pending.get(eid)
            if event is not None and event.context_included and event.trigger_revision <= revision:
                event.handled = True
                self._record(event, "handled")
                # Worker may still be persisting critical evidence; keep it until
                # that queue entry has been consumed, otherwise release now.
                if (eid not in self._queue and event is not self._in_flight_event
                        and not self._needs_memory(event)):
                    self._pending.pop(eid, None)
        return True

    def clear_replan(self, revision):
        if revision == self.cognitive_revision:
            self.replan.clear()
            self.preempt.clear()

    async def close(self):
        self._closed = True
        self._wake.set()
        in_flight = self._in_flight_event
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        try:
            # Cancelling a slow advisory classifier must not cancel queued
            # critical evidence that has already caused a successful replan.
            async with asyncio.timeout(2):
                candidates = {event.id: event for event in self._pending.values()}
                if in_flight is not None:
                    candidates[in_flight.id] = in_flight
                for event in candidates.values():
                    if event.assessment is None and event.hints.potentially_critical:
                        event.assessment = EventAssessment(urgency="critical", should_remember=True,
                            provider="rules", reason_code="critical_close_evidence")
                    if self._needs_memory(event):
                        stored = await self.memory.remember_event(event, tags=event.hints.tags)
                        if stored is None:
                            raise ActorError("memory_unavailable", "Required event evidence was not stored")
                        event.remembered = True
                        self._counts["events_remembered"] += 1
                        self._record(event, "remembered")
                    if event.handled:
                        self._pending.pop(event.id, None)
                        self._queue.pop(event.id, None)
        except Exception as error:
            self._fail_safe("memory_flush_failed")
            raise ActorError("memory_unavailable", "EventRouter could not preserve required event evidence") from error
        finally:
            try:
                async with asyncio.timeout(2):
                    await self.classifier.close()
            except Exception:
                self._counts["classifier_close_errors"] += 1

    def _needs_memory(self, event):
        return (self.memory is not None and getattr(self.memory, "enabled", True)
                and event.entry.data_policy == "public"
                and event.assessment is not None and event.assessment.should_remember and not event.remembered)

    def report(self):
        provider_report = getattr(self.classifier, "report", lambda: {})()
        return {"session_id": self.session.session_id,
                "objective_id": getattr(self.objective, "id", None),
                "enabled": self.config.enabled, "cognitive_revision": self.cognitive_revision,
                "degraded": self.degraded, "degraded_reason": self.degraded_reason,
                "queue_size": len(self._queue), "pending_events": len(self._pending),
                "dedup_keys": len(self._dedup), "counts": dict(self._counts),
                "classifier_latency_ms": list(self._latencies), "provider": provider_report,
                "acknowledgements": list(self._audit)}
