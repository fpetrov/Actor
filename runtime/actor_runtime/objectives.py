"""Objective episodes: one Brain decision point between fully completed Skills."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import StrEnum
import logging
import math
import json
import inspect
from importlib import metadata
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from .brains import DECISION_ADAPTER, FinishObjective, InvalidBrainDecision, SkillPlan, build_decision_schema
from .errors import ActorError

LOG = logging.getLogger("actor_runtime")


class ObjectiveStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Objective(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4096)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ObjectiveStatus = ObjectiveStatus.PENDING


class ObjectiveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    objective_id: str
    status: ObjectiveStatus
    reason: str
    message: str | None = None
    duration_seconds: float
    decision_count: int
    skill_results: list[dict[str, JsonValue]]


class _Terminal(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


class _Replan(Exception):
    """A strategic revision invalidated this operation, not its Objective."""


class _PlanReaction(Exception):
    """An already accepted plan, rather than the Brain, supplies the next action."""


class ObjectiveRunner:
    def __init__(self, session, skill_registry, brain, *, decision_timeout_seconds=120,
                 objective_timeout_seconds=600, cleanup_timeout_seconds=5,
                 max_decisions=12, repair_limit=1, scheduler=None,
                 event_router=None, config=None, memory=None, max_replans=None,
                 manage_router=True, run_finalizers=True, execution_guard=None,
                 working_knowledge=None, context_selection=None):
        if config is not None:
            decision_timeout_seconds = config.brain.decision_timeout
            objective_timeout_seconds = config.brain.objective_timeout
            max_decisions = config.brain.max_decisions
        if max_replans is None:
            max_replans = config.router.max_replans if config is not None else 8
        if (not math.isfinite(decision_timeout_seconds) or decision_timeout_seconds <= 0 or
                not math.isfinite(objective_timeout_seconds) or objective_timeout_seconds <= 0 or
                not math.isfinite(cleanup_timeout_seconds) or cleanup_timeout_seconds <= 0 or
                type(max_decisions) is not int or max_decisions < 1 or
                type(repair_limit) is not int or repair_limit < 0 or
                type(max_replans) is not int or max_replans < 0):
            raise ValueError("Objective execution limits must be positive")
        from .skills import SkillScheduler
        if any(not inspect.iscoroutinefunction(getattr(brain, name, None))
               for name in ("start", "decide", "interrupt", "close")):
            raise ActorError("brain_plugin_invalid", "Brain lifecycle methods must be async")
        if (getattr(brain, "interrupt_decision", None) is not None and
                not inspect.iscoroutinefunction(brain.interrupt_decision)):
            raise ActorError("brain_plugin_invalid", "Optional decision interruption must be async")
        self.session, self.registry, self.brain = session, skill_registry, brain
        self.scheduler = scheduler or SkillScheduler(session, skill_registry)
        self.event_router = event_router if event_router is not None else getattr(session, "event_router", None)
        self.config, self.memory = config, memory
        self.manage_router, self.run_finalizers = manage_router, run_finalizers
        self.execution_guard, self.working_knowledge = execution_guard, working_knowledge
        if context_selection is not None and (not callable(context_selection) or inspect.iscoroutinefunction(context_selection)):
            raise ActorError("context_selection_invalid", "Installed context selection must be synchronous")
        self.context_selection = context_selection
        self._cancel_reason = "objective_cancelled"
        self.max_replans = max_replans
        self._replans = 0
        self._decision_epoch = 0
        self._persisted_skill_count = 0
        self.decision_timeout_seconds = decision_timeout_seconds
        self.objective_timeout_seconds = objective_timeout_seconds
        self.cleanup_timeout_seconds = cleanup_timeout_seconds
        self.cleanup_drain_seconds = min(0.5, cleanup_timeout_seconds)
        self.max_decisions, self.repair_limit = max_decisions, repair_limit
        self.objective = None
        self.result = None
        self.cancel_requested = asyncio.Event()
        self._run_task = None
        self._running = False
        self._finished = asyncio.Event()
        self._abort_lock = asyncio.Lock()
        self._cleanup_errors = []
        self._pending_cleanup_tasks = set()
        self._interrupt_requested = False
        self._active_task = None
        self._active_phase = None
        self._event_cursor = 0
        self._decisions = []
        self._skill_results = []
        self._recorded_skill_tasks = set()
        self._lifecycle = []
        self._started_at = 0.0
        self._last_skill_metadata = None
        self._finalizer_results = []
        self._plan = None
        self._plan_reports = []

    def _log(self, event, **fields):
        self.session.emit_trace("objective_lifecycle", **{
            "event": event, "objective_id": getattr(self.objective, "id", None), **fields})
        if "skill_id" in fields:
            fields["skill_id"] = self.session.privacy.activity_id(fields["skill_id"])
        record = {"event": event, "elapsed_seconds": round(time.monotonic() - self._started_at, 6), **fields}
        self._lifecycle.append(record)
        LOG.info("%s %s", event, fields)

    def _public_failure_code(self,error):
        """Optional installed-provider constants, never arbitrary exception data."""
        try:
            allowed=getattr(self.brain,"public_error_codes",frozenset())
            if (isinstance(error,ActorError) and type(allowed) is frozenset and len(allowed)<=64 and
                    all(type(code) is str and 1<=len(code)<=64 and code.isascii() and
                        all(character in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in code)
                        for code in allowed) and error.code in allowed):
                return error.code
        except Exception:
            pass
        return "unavailable"

    async def _abort_active(self, *, interrupt=False):
        async with self._abort_lock:
            # Revoke actuator ownership and zero foreground intent before any
            # potentially slow provider IPC interruption.
            await self._cleanup_call(self.scheduler.cancel(), "skill_cancel")
            if interrupt and not self._interrupt_requested:
                self._interrupt_requested = True
                await self._cleanup_call(self.brain.interrupt(), "brain_interrupt")
            active = self._active_task
            if active and active is not asyncio.current_task():
                if not active.done() and active not in self._pending_cleanup_tasks:
                    if self._active_phase in ("brain", "start"):
                        active.cancel()
                    done, _ = await asyncio.wait((active,), timeout=self.cleanup_timeout_seconds)
                    if active not in done:
                        active.cancel()
                        self._observe_pending(active)
                        self._cleanup_errors.append("active_task_cleanup_timeout")
                # SDK interrupt may finish the provider task with an exception
                # before cancellation resumes here. Retrieve that outcome too.
                if active.done():
                    self._consume_completed(active)
            if active and self._active_phase == "skill" and active.done():
                self._record_skill(active)

    def _record_skill(self, task):
        if task in self._recorded_skill_tasks or task.cancelled():
            return
        self._recorded_skill_tasks.add(task)
        try:
            result = task.result()
        except Exception:
            return
        if callable(getattr(result, "model_dump", None)):
            self._skill_results.append(result.model_dump(mode="json"))
            self.session.emit_trace("skill_result", objective_id=self.objective.id,
                result=self.session.privacy.public(result))

    async def _cleanup_call(self, operation, name, *, timeout_seconds=None):
        task = asyncio.create_task(operation)
        succeeded = False
        try:
            try:
                done, _ = await asyncio.wait((task,), timeout=min(self.cleanup_timeout_seconds, timeout_seconds)
                                             if timeout_seconds is not None else self.cleanup_timeout_seconds)
            except asyncio.CancelledError:
                self.cancel_requested.set()
                return
            if task not in done:
                raise TimeoutError("Provider lifecycle deadline elapsed")
            if task.cancelled():
                self._cleanup_errors.append(name + "_failed")
            else:
                task.result()
                succeeded = True
        except Exception as error:
            self._cleanup_errors.append(name + "_failed")
            LOG.error("Objective cleanup failed operation=%s exception_type=%s", name, type(error).__name__)
        finally:
            if not task.done():
                task.cancel()
                try:
                    ended, _ = await asyncio.wait((task,), timeout=self.cleanup_drain_seconds)
                except asyncio.CancelledError:
                    self.cancel_requested.set()
                    ended = set()
                if task not in ended:
                    self._observe_pending(task)
            if task.done():
                self._consume_completed(task)
        return succeeded

    async def _finalize_objective(self, status, reason):
        """Let installed extensions release server resources through this Scheduler.

        This trusted lifecycle hook runs after foreground ownership is revoked.
        Extensions decide whether their negotiated capabilities need cleanup;
        Core has no knowledge of game tasks or their cancellation commands.
        """
        points = sorted(metadata.entry_points(group="actor.objective_finalizers"), key=lambda point: point.name)
        if len(points) > 16 or len({point.name for point in points}) != len(points):
            raise ActorError("objective_finalizers_invalid", "Finalizer entry points must be unique and bounded")
        for point in points:
            record = {"name": point.name, "status": "running"}
            self._finalizer_results.append(record)
            try:
                finalizer = point.load()
                if not inspect.iscoroutinefunction(finalizer):
                    raise ActorError("objective_finalizer_invalid", "Objective finalizers must be async functions")
                await finalizer(session=self.session, objective=self.objective,
                    status=status, reason=reason, scheduler=self.scheduler)
                record["status"] = "completed"
            except asyncio.CancelledError:
                record["status"] = "cancelled"
                raise
            except Exception as error:
                record["status"] = "failed"
                self._cleanup_errors.append("objective_finalizer_failed")
                LOG.error("Objective finalizer failed name=%s exception_type=%s", point.name, type(error).__name__)

    @staticmethod
    def _consume_completed(task):
        if not task.cancelled():
            task.exception()

    def _observe_pending(self, task):
        self._pending_cleanup_tasks.add(task)
        def completed(finished):
            self._consume_completed(finished)
            self._pending_cleanup_tasks.discard(finished)
        task.add_done_callback(completed)

    def _revision(self):
        return self.event_router.cognitive_revision if self.event_router is not None else 0

    def _triggers(self):
        return self.event_router.take_trigger_events() if self.event_router is not None else []

    def _trigger_ids(self):
        return [item.id for item in self._triggers()]

    def _replan_reason(self):
        return "critical_event" if any(
            getattr(getattr(event, "assessment", None), "urgency", None) == "critical" or
            getattr(getattr(event, "hints", None), "potentially_critical", False)
            for event in self._triggers()) else "replan_requested"

    def _note_replan(self, phase):
        if self._replans >= self.max_replans:
            raise _Terminal(ObjectiveStatus.FAILED, "replan_limit")
        self._replans += 1
        self._log("ReplanRequested", phase=phase, cognitive_revision=self._revision(),
                  trigger_event_ids=self._trigger_ids())

    async def _dispatch_skill(self, decision, epoch):
        # This check runs in the scheduled operation, after any already-ready
        # receive callback, immediately before the Scheduler can reserve a lease.
        self._decision_guard(epoch)
        if self._plan is not None and self._plan.pending is not None:
            raise _PlanReaction
        return await self.scheduler.run(decision.skill_id, decision.arguments)

    async def _execute_plan(self, decision, epoch):
        from .plans import ExecutablePlan
        plan = self._plan = ExecutablePlan(decision)
        self.session.executable_plan = plan
        self.session.emit_trace("plan_accepted", objective_id=self.objective.id,
                                plan=plan.snapshot())
        self._log("PlanAccepted", plan_id=plan.id, steps=len(decision.steps), conditions=len(decision.conditions))
        if self.event_router is not None:
            for entry in plan.observation_candidates(self.session.world.observations.values(), self.session.world.self_actor_id):
                self.event_router.ingest(entry)
        try:
            while True:
                plan.take_reaction()
                call = plan.current
                self.session.emit_trace("plan_step", objective_id=self.objective.id,
                                        plan=plan.snapshot())
                definition = self.registry.definition(call.skill_id)
                self._last_skill_metadata = {"skill_id": call.skill_id, "arguments": {
                    key: call.arguments[key] for key in definition.context_fields if key in call.arguments}}
                try:
                    result = await self._wait(self._dispatch_skill(call, epoch), "skill", epoch=epoch)
                except _PlanReaction:
                    self._decision_guard(epoch)
                    plan.take_reaction()
                    self._log("PlanReaction", plan_id=plan.id, condition=plan.triggered)
                    continue
                self._check_plan_cleanup(result)
                if not plan.advance(result):
                    return result
        finally:
            # Keep the accepted plan visible to lifecycle guards until the
            # foreground lease has been revoked and cleanup has drained.
            if self._active_phase == "skill" and self._active_task is not None and not self._active_task.done():
                await self._abort_active()
            if plan.state not in ("completed", "failed"):
                plan.state = "invalidated"
            self._plan_reports.append({"plan_id": plan.id, "state": plan.state,
                "step_index": plan.index, "conditions_used": sorted(plan.used_conditions),
                "fallback_used": plan.fallback_used})
            self._log("PlanStopped", plan_id=plan.id, state=plan.state)
            self.session.executable_plan = self._plan = None

    @staticmethod
    def _check_plan_cleanup(result):
        if result.details.get("cleanup_reason") or result.details.get("execution_cleanup_reason"):
            raise _Terminal(ObjectiveStatus.FAILED, "plan_cleanup_failed")

    def _decision_guard(self, epoch):
        # _wait's waiter cleanup itself awaits. A receive/cancellation callback
        # can run there after its first epoch check; acceptance and Finish need
        # a final synchronous guard, just like deferred Skill dispatch.
        if self.cancel_requested.is_set():
            raise _Terminal(ObjectiveStatus.CANCELLED, self._cancel_reason)
        if self.execution_guard is not None and self.execution_guard() is not True:
            raise _Terminal(ObjectiveStatus.CANCELLED, "episode_superseded")
        if self.session.closed.is_set():
            raise _Terminal(ObjectiveStatus.FAILED, "session_disconnected")
        if self.event_router is not None and self.event_router.degraded:
            raise _Terminal(ObjectiveStatus.FAILED, "event_routing_degraded")
        if time.monotonic() - self._started_at >= self.objective_timeout_seconds:
            raise _Terminal(ObjectiveStatus.FAILED, "objective_timeout")
        if self._revision() != epoch:
            self._note_replan("acceptance")
            raise _Replan

    async def _preempt_active(self, phase):
        """Stop one operation completely before the existing loop replans."""
        async with self._abort_lock:
            if self.cancel_requested.is_set() or self.session.closed.is_set():
                return  # _wait rechecks terminal conditions after this drain.
            if self._replans >= self.max_replans:
                await self._cleanup_call(self.scheduler.cancel(), "skill_cancel")
            self._note_replan(phase)
            trigger_ids = self._trigger_ids()
            active = self._active_task
            if phase == "skill":
                ok = await self._cleanup_call(self.scheduler.cancel(reason="preempted_by_event", details={
                    "trigger_event_ids": trigger_ids[:16], "trigger_event_count": len(trigger_ids),
                    "decision_reason": self._replan_reason(),
                    "cognitive_revision": self._revision()}), "skill_preempt")
                if not ok:
                    raise _Terminal(ObjectiveStatus.FAILED, "skill_preemption_failed")
            else:
                interrupt = getattr(self.brain, "interrupt_decision", None)
                if callable(interrupt):
                    if not await self._cleanup_call(interrupt(), "brain_decision_interrupt"):
                        raise _Terminal(ObjectiveStatus.FAILED, "brain_interruption_failed")
                    if active is not None and not active.done():
                        active.cancel()
            if active is not None:
                done, _ = await asyncio.wait((active,), timeout=self.cleanup_timeout_seconds)
                if active not in done:
                    active.cancel()
                    self._observe_pending(active)
                    raise _Terminal(ObjectiveStatus.FAILED, "brain_interruption_failed")
                self._consume_completed(active)
                if phase == "skill":
                    self._record_skill(active)

    async def _wait(self, operation, phase, timeout=None, *, epoch=None):
        if self.execution_guard is not None and phase in ("start", "brain", "skill"):
            try:
                permitted = self.execution_guard()
            except BaseException:
                operation.close()
                raise
            if permitted is not True:
                operation.close()
                raise _Terminal(ObjectiveStatus.CANCELLED, "episode_superseded")
        if self.cancel_requested.is_set() or self.session.closed.is_set():
            operation.close()
            raise _Terminal(ObjectiveStatus.CANCELLED if self.cancel_requested.is_set() else ObjectiveStatus.FAILED,
                            self._cancel_reason if self.cancel_requested.is_set() else "session_disconnected")
        remaining = self._started_at + self.objective_timeout_seconds - time.monotonic()
        if remaining <= 0:
            operation.close()
            raise _Terminal(ObjectiveStatus.FAILED, "objective_timeout")
        effective_timeout = min(timeout, remaining) if timeout is not None else remaining
        self._active_task = asyncio.create_task(operation)
        self._active_phase = phase
        cancellation = asyncio.create_task(self.cancel_requested.wait())
        disconnected = asyncio.create_task(self.session.closed.wait())
        replan = None
        plan_change = None
        if self.event_router is not None and (phase == "skill" or
                phase == "brain" and callable(getattr(self.brain, "interrupt_decision", None))):
            signal = getattr(self.event_router, "preempt", self.event_router.replan) if phase == "skill" else self.event_router.replan
            replan = asyncio.create_task(signal.wait())
        if phase == "skill" and self._plan is not None:
            plan_change = asyncio.create_task(self._plan.changed.wait())
        try:
            waiters = ((self._active_task, cancellation, disconnected) + ((replan,) if replan else ())
                       + ((plan_change,) if plan_change else ()))
            done, _ = await asyncio.wait(waiters,
                                         timeout=effective_timeout, return_when=asyncio.FIRST_COMPLETED)
            if self.cancel_requested.is_set():
                await self._abort_active(interrupt=True)
                raise _Terminal(ObjectiveStatus.CANCELLED, self._cancel_reason)
            if self.session.closed.is_set():
                await self._abort_active(interrupt=True)
                raise _Terminal(ObjectiveStatus.FAILED, "session_disconnected")
            if self.event_router is not None and self.event_router.degraded:
                await self._abort_active(interrupt=True)
                raise _Terminal(ObjectiveStatus.FAILED, "event_routing_degraded")
            episode_expired = time.monotonic() - self._started_at >= self.objective_timeout_seconds
            operation_timed_out = (self._active_task not in done and (replan is None or replan not in done)
                                   and (plan_change is None or plan_change not in done))
            if episode_expired or operation_timed_out:
                await self._abort_active(interrupt=True)
                raise _Terminal(ObjectiveStatus.FAILED, "objective_timeout" if episode_expired else "brain_timeout")
            stale_brain = phase == "brain" and epoch is not None and self._revision() != epoch
            preempt_skill = (phase == "skill" and replan is not None and replan in done
                             and self._active_task not in done)
            if not episode_expired and (stale_brain or preempt_skill):
                await self._preempt_active(phase)
                if self.cancel_requested.is_set():
                    await self._abort_active(interrupt=True)
                    raise _Terminal(ObjectiveStatus.CANCELLED, self._cancel_reason)
                if self.session.closed.is_set():
                    await self._abort_active(interrupt=True)
                    raise _Terminal(ObjectiveStatus.FAILED, "session_disconnected")
                raise _Replan
            if plan_change is not None and plan_change in done and self._active_task not in done:
                active = self._active_task
                if not await self._cleanup_call(self.scheduler.cancel(reason="planned_reaction"), "plan_skill_cancel"):
                    raise _Terminal(ObjectiveStatus.FAILED, "skill_preemption_failed")
                ended, _ = await asyncio.wait((active,), timeout=self.cleanup_timeout_seconds)
                if active not in ended:
                    active.cancel()
                    self._observe_pending(active)
                    raise _Terminal(ObjectiveStatus.FAILED, "skill_preemption_failed")
                self._record_skill(active)
                self._check_plan_cleanup(active.result())
                self._decision_guard(epoch)
                raise _PlanReaction
            try:
                result = await self._active_task
                if phase == "skill":
                    self._record_skill(self._active_task)
                return result
            except asyncio.CancelledError as error:
                raise ActorError("brain_cancelled", "Provider operation cancelled without an Objective cancellation") from error
        finally:
            cancellation.cancel()
            disconnected.cancel()
            if replan is not None:
                replan.cancel()
            if plan_change is not None:
                plan_change.cancel()
            await asyncio.gather(cancellation, disconnected, *((replan,) if replan else ()),
                                 *((plan_change,) if plan_change else ()), return_exceptions=True)
            if self._active_task and self._active_task.done():
                self._active_task = self._active_phase = None

    @staticmethod
    def _validation_error(error):
        if isinstance(error, ValidationError):
            return {"code": "invalid_brain_decision", "details": json.loads(error.json(
                include_input=False, include_context=False, include_url=False))[:16]}
        if isinstance(error, ActorError):
            return {"code": error.code, "message": error.message[:512],
                    "details": {str(key)[:128]: str(value)[:512] for key, value in list(error.details.items())[:16]}}
        return {"code": "invalid_brain_decision", "message": str(error)[:512]}

    async def _remember_skills(self):
        if self.memory is None:
            return
        for result in self._skill_results[self._persisted_skill_count:]:
            try:
                await self._wait(self.memory.remember_skill_result(self.session.privacy.public(result),
                    objective_id=self.objective.id), "memory")
            except ActorError as error:
                raise _Terminal(ObjectiveStatus.FAILED, "memory_failure") from error
            self._persisted_skill_count += 1

    async def _context(self, catalog, index, previous, validation_error, reason):
        from .snapshots import build_decision_context
        router = self.event_router
        current = self.scheduler.working_skill_metadata() or self._last_skill_metadata
        memories = ()
        if self.memory is not None:
            try:
                triggers = self._triggers()
                memories = await self._wait(self.memory.retrieve(objective=self.objective.text,
                    current_skill=current,
                    capability_ids=tuple({event.entry.capability_id for event in triggers}),
                    type_urls=tuple({event.type_url for event in triggers}),
                    tags=tuple({tag for event in triggers for tag in getattr(event.hints, "tags", ())})), "memory")
            except ActorError as error:
                raise _Terminal(ObjectiveStatus.FAILED, "memory_failure") from error
        # Capture after asynchronous retrieval, so an event arriving during IO
        # belongs to this snapshot rather than immediately invalidating it.
        epoch, triggers = self._revision(), self._triggers()
        options = {}
        if router is not None or self.memory is not None or self.config is not None:
            options.update(trigger_events=triggers,
                routed_events=router.context_events() if router is not None and router.config.enabled else None,
                memories=memories, decision_reason=self._replan_reason() if triggers else reason,
                cognitive_revision=epoch, current_skill=current)
        if self.config is not None:
            bounds = self.config.context
            options.update(max_external_bytes=bounds.max_external_context_bytes,
                max_observations=bounds.max_observations, max_events=bounds.max_events,
                max_memories=bounds.max_memory_items, max_event_payload_bytes=bounds.max_event_payload_bytes,
                stale_after_seconds=bounds.stale_observation_seconds,
                max_working_knowledge=bounds.max_working_knowledge,
                max_working_knowledge_bytes=bounds.max_working_knowledge_bytes)
        if self.context_selection is not None:
            options["selection"] = self.context_selection(self.session)
        context = build_decision_context(self.session, self.objective, catalog,
            decision_index=index, event_cursor=self._event_cursor,
            previous_skill_result=previous, validation_error=validation_error,
            working_knowledge=self.working_knowledge.context() if self.working_knowledge is not None else (), **options)
        self._event_cursor = max((event.sequence for event in self.session.world.events), default=self._event_cursor)
        included = list(dict.fromkeys(event.event_id for event in (
            *getattr(context, "trigger_events", ()), *context.recent_events) if event.event_id))
        if router is not None:
            router.acknowledge_context(included, epoch)
            router.clear_replan(epoch)
        return context, epoch, included

    async def run(self, objective: Objective) -> ObjectiveResult:
        if self._running:
            raise ActorError("objective_busy", "An Objective is already running")
        if self.manage_router and getattr(self.session, "participation_controller", None) is not None:
            raise ActorError("objective_scope_busy", "A participation already owns this session's cognition")
        if any(not task.done() for task in self._pending_cleanup_tasks):
            raise ActorError("cleanup_pending", "Provider cleanup is still pending; use a fresh provider")
        if objective.status != ObjectiveStatus.PENDING:
            raise ActorError("objective_invalid", "An Objective episode must begin Pending")
        self._run_task = asyncio.current_task()
        self._running = True
        self._finished.clear()
        self._cleanup_errors = []
        self._interrupt_requested = False
        self.objective = objective.model_copy(update={"status": ObjectiveStatus.RUNNING})
        self.cancel_requested.clear()
        self._cancel_reason = "objective_cancelled"
        self._started_at = time.monotonic()
        self._event_cursor = 0
        self._decisions, self._skill_results, self._lifecycle = [], [], []
        self._recorded_skill_tasks = set()
        self._replans = self._persisted_skill_count = 0
        self._last_skill_metadata = None
        self._finalizer_results = []
        self._plan_reports = []
        terminal_status, reason, message = ObjectiveStatus.FAILED, "brain_failure", None
        try:
            if self.session.closed.is_set():
                raise _Terminal(ObjectiveStatus.FAILED, "session_disconnected")
            catalog = self.registry.catalog(self.session)
            build_decision_schema(catalog)
            self._log("ObjectiveStarted", objective_id=objective.id)
            if self.event_router is not None:
                if self.manage_router:
                    await self._wait(self.event_router.start(self.objective,
                        current_skill=self.scheduler.working_skill_metadata), "router")
                else:
                    self.event_router.update_context(self.objective, self.scheduler.working_skill_metadata)
            await self._wait(self.brain.start(self.objective, catalog), "start", self.decision_timeout_seconds)
            validation_error = None
            repairs = 0
            previous_skill_result = None
            decision_reason = "objective_started"
            for index in range(1, self.max_decisions + 1):
                if self.event_router is not None and self.event_router.degraded:
                    raise _Terminal(ObjectiveStatus.FAILED, "event_routing_degraded")
                await self._remember_skills()
                catalog = self.registry.catalog(self.session)
                context, epoch, included = await self._context(catalog, index, previous_skill_result,
                    validation_error, self._replan_reason() if self._triggers() else decision_reason)
                self._decision_epoch = epoch
                started = time.monotonic()
                record = {"index": index, "duration_seconds": 0.0, "status": "running", "cognitive_revision": epoch}
                record.update(context_bytes=len(context.external_json().encode("utf-8")),
                    estimated_tokens=getattr(context, "estimated_tokens", None),
                    trigger_event_ids=[event.event_id for event in context.trigger_events if event.event_id],
                    decision_reason=getattr(context, "decision_reason", decision_reason))
                self._decisions.append(record)
                provider_validation_error = None
                try:
                    raw = await self._wait(self.brain.decide(context), "brain", self.decision_timeout_seconds, epoch=epoch)
                except _Replan:
                    record["status"] = "stale"
                    decision_reason, validation_error, repairs = self._replan_reason(), None, 0
                    continue
                except InvalidBrainDecision as error:
                    provider_validation_error = error
                    raw = None
                except BaseException:
                    record["status"] = "interrupted" if self.cancel_requested.is_set() else "failed"
                    raise
                finally:
                    record["duration_seconds"] = time.monotonic() - started
                try:
                    self._decision_guard(epoch)
                except _Replan:
                    record["status"] = "stale"
                    decision_reason, validation_error, repairs = self._replan_reason(), None, 0
                    continue
                except _Terminal:
                    record["status"] = "interrupted" if self.cancel_requested.is_set() else "failed"
                    raise
                try:
                    if provider_validation_error is not None:
                        raise provider_validation_error
                    decision = (DECISION_ADAPTER.validate_json(raw) if isinstance(raw, (str, bytes))
                                else DECISION_ADAPTER.validate_python(raw))
                    if isinstance(decision, SkillPlan):
                        from .plans import validate_plan
                        validate_plan(decision, self.registry, self.session)
                    elif not isinstance(decision, FinishObjective):
                        self.registry.validate_call(self.session, decision.skill_id, decision.arguments)
                except (ValidationError, ActorError, ValueError, TypeError) as error:
                    validation_error = self._validation_error(error)
                    self._decisions[-1]["status"] = "rejected"
                    self._log("SkillCallRejected", code=validation_error["code"])
                    if repairs >= self.repair_limit:
                        raise _Terminal(ObjectiveStatus.FAILED, "brain_failure")
                    repairs += 1
                    continue
                repairs = 0
                validation_error = None
                self._decisions[-1]["status"] = "accepted"
                self.session.emit_trace("decision_accepted", objective_id=self.objective.id,
                    decision_index=index, cognitive_revision=epoch,
                    trigger_event_ids=included, decision=decision.model_dump(mode="json"))
                if self.event_router is not None:
                    self.event_router.acknowledge_decision(included, epoch)
                if isinstance(decision, FinishObjective):
                    self._log("BrainDecision", kind="finish", status=decision.status)
                    terminal_status = (ObjectiveStatus.SUCCEEDED if decision.status == "success"
                                       else ObjectiveStatus.FAILED)
                    reason = "finished" if decision.status == "success" else "brain_reported_failure"
                    message = decision.reason
                    break
                if isinstance(decision, SkillPlan):
                    record["kind"] = "plan"
                    try:
                        result = await self._execute_plan(decision, epoch)
                    except _Replan:
                        previous_skill_result = self._skill_results[-1] if self._skill_results else previous_skill_result
                        decision_reason = self._replan_reason()
                        continue
                    previous_skill_result = result.model_dump(mode="json")
                    decision_reason = "plan_completed" if result.status == "succeeded" else "plan_failed"
                    continue
                self._decisions[-1]["skill_id"] = decision.skill_id
                definition = self.registry.definition(decision.skill_id)
                self._last_skill_metadata = {"skill_id": decision.skill_id, "arguments": {
                    key: decision.arguments[key] for key in definition.context_fields if key in decision.arguments}}
                self._log("BrainDecision", kind="skill", skill_id=decision.skill_id)
                prior_skill_count = len(self._skill_results)
                try:
                    result = await self._wait(self._dispatch_skill(decision, epoch), "skill", epoch=epoch)
                except _Replan:
                    if len(self._skill_results) == prior_skill_count:
                        record["status"] = "stale"
                    previous_skill_result = self._skill_results[-1] if self._skill_results else previous_skill_result
                    decision_reason = self._replan_reason()
                    continue
                previous_skill_result = result.model_dump(mode="json")
                decision_reason = "skill_succeeded" if result.status == "succeeded" else "skill_failed"
            else:
                reason = "decision_limit"
        except _Terminal as terminal:
            if self._active_task is not None:
                await self._abort_active(interrupt=True)
            terminal_status, reason = terminal.status, terminal.reason
        except asyncio.CancelledError:
            self.cancel_requested.set()
            await self._abort_active(interrupt=True)
            terminal_status, reason = ObjectiveStatus.CANCELLED, self._cancel_reason
        except Exception as error:
            await self._abort_active(interrupt=True)
            reason = "brain_failure"
            message = error.message[:512] if isinstance(error, ActorError) else type(error).__name__
            provider_type = error.details.get("exception_type") if isinstance(error, ActorError) else None
            if not isinstance(provider_type, str) or not provider_type.isascii() or not provider_type.isidentifier() or len(provider_type) > 128:
                provider_type = "unavailable"
            self.session.emit_trace("provider_failure", objective_id=self.objective.id,
                provider_code=self._public_failure_code(error), exception_type=provider_type)
            LOG.error("Objective failed code=%s exception_type=%s provider_code=%s",
                      reason, type(error).__name__, self._public_failure_code(error))
        finally:
            await self._cleanup_call(self.scheduler.cancel(), "skill_cancel")
            if self.run_finalizers:
                await self._cleanup_call(self._finalize_objective(
                    ObjectiveStatus.CANCELLED if self.cancel_requested.is_set() else terminal_status,
                    self._cancel_reason if self.cancel_requested.is_set() else reason),
                    "objective_finalizers", timeout_seconds=2)
            await self._cleanup_call(self.scheduler.cancel(), "finalizer_skill_cancel")
            if self.event_router is not None and self.manage_router:
                await self._cleanup_call(self.event_router.close(), "event_router_close")
            await self._cleanup_call(self.brain.close(), "brain_close")
        if self._cleanup_errors and terminal_status == ObjectiveStatus.SUCCEEDED:
            terminal_status, reason = ObjectiveStatus.FAILED, "cleanup_failed"
        if self.cancel_requested.is_set():
            terminal_status, reason = ObjectiveStatus.CANCELLED, self._cancel_reason
        self.objective = self.objective.model_copy(update={"status": terminal_status})
        self.result = ObjectiveResult(objective_id=objective.id, status=terminal_status, reason=reason,
            message=message, duration_seconds=time.monotonic() - self._started_at,
            decision_count=len(self._decisions), skill_results=self._skill_results)
        projected = self.session.privacy.public(self.result)
        self.result = self.result.model_copy(update={"message": projected["message"],
                                                    "skill_results": projected["skill_results"]})
        if self.memory is not None:
            recorded_outcome = self.result
            async def persist_outcome():
                for skill_result in self._skill_results[self._persisted_skill_count:]:
                    await self.memory.remember_skill_result(self.session.privacy.public(skill_result), objective_id=self.objective.id)
                    self._persisted_skill_count += 1
                await self.memory.remember_objective_result(self.result.model_dump(mode="json"))
            memory_ok = await self._cleanup_call(persist_outcome(), "memory_finalize")
            if not memory_ok and terminal_status != ObjectiveStatus.CANCELLED:
                terminal_status, reason = ObjectiveStatus.FAILED, "memory_failure"
            if self.cancel_requested.is_set():
                terminal_status, reason = ObjectiveStatus.CANCELLED, self._cancel_reason
            self.objective = self.objective.model_copy(update={"status": terminal_status})
            self.result = self.result.model_copy(update={"status": terminal_status, "reason": reason})
            # A cancellation can arrive after the SQLite worker committed the
            # provisional outcome. Objective evidence supports an idempotent
            # final-status correction; observed game events remain immutable.
            # Failure followed by cancellation needs at most two corrections.
            for _ in range(2):
                if self.result == recorded_outcome:
                    break
                recorded_outcome = self.result
                await self._cleanup_call(self.memory.remember_objective_result(
                    recorded_outcome.model_dump(mode="json")), "memory_reconcile")
                if self.cancel_requested.is_set() and terminal_status != ObjectiveStatus.CANCELLED:
                    terminal_status, reason = ObjectiveStatus.CANCELLED, self._cancel_reason
                    self.objective = self.objective.model_copy(update={"status": terminal_status})
                    self.result = self.result.model_copy(update={"status": terminal_status, "reason": reason})
        self._log({ObjectiveStatus.SUCCEEDED: "ObjectiveSucceeded", ObjectiveStatus.FAILED: "ObjectiveFailed",
                   ObjectiveStatus.CANCELLED: "ObjectiveCancelled"}[terminal_status], reason=reason)
        self._running = False
        self._run_task = None
        self._active_task = self._active_phase = None
        self._finished.set()
        return self.result

    async def cancel(self, *, reason="objective_cancelled"):
        if not isinstance(reason, str) or not reason or len(reason) > 128:
            raise ValueError("Cancellation reason must be a bounded nonempty code")
        if not self.cancel_requested.is_set():
            self._cancel_reason = reason
        self.cancel_requested.set()
        if self._running:
            await self._abort_active(interrupt=True)
            if self._run_task is not asyncio.current_task():
                await self._finished.wait()

    def report(self):
        report_method = getattr(self.brain, "report", None)
        try:
            brain_report = report_method() if callable(report_method) else {}
        except Exception:
            brain_report = {"telemetry_error": "brain_report_failed"}
        return self.session.privacy.public({"objective": self.objective.model_dump(mode="json") if self.objective else None,
                "result": self.result.model_dump(mode="json") if self.result else None,
                "decisions": list(self._decisions), "skills": list(self._skill_results),
                "lifecycle": list(self._lifecycle), "scheduler": self.scheduler.report(),
                "brain": brain_report, "cleanup_errors": list(self._cleanup_errors),
                "finalizers": list(self._finalizer_results),
                "plans": list(self._plan_reports),
                "replan_count": self._replans, "cognitive_revision": self._revision(),
                "event_router": self.event_router.report() if self.event_router is not None else None,
                "pending_cleanup_tasks": sum(not task.done() for task in self._pending_cleanup_tasks),
                "privacy": self.session.privacy.report()})
