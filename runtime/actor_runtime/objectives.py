"""Objective episodes: one Brain decision point between fully completed Skills."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import StrEnum
import logging
import math
import json
import inspect
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from .brains import DECISION_ADAPTER, FinishObjective, InvalidBrainDecision, build_decision_schema
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


class ObjectiveRunner:
    def __init__(self, session, skill_registry, brain, *, decision_timeout_seconds=120,
                 objective_timeout_seconds=600, max_decisions=12, repair_limit=1, scheduler=None):
        if (not math.isfinite(decision_timeout_seconds) or decision_timeout_seconds <= 0 or
                not math.isfinite(objective_timeout_seconds) or objective_timeout_seconds <= 0 or
                type(max_decisions) is not int or max_decisions < 1 or
                type(repair_limit) is not int or repair_limit < 0):
            raise ValueError("Objective execution limits must be positive")
        from .skills import SkillScheduler
        if any(not inspect.iscoroutinefunction(getattr(brain, name, None))
               for name in ("start", "decide", "interrupt", "close")):
            raise ActorError("brain_plugin_invalid", "Brain lifecycle methods must be async")
        self.session, self.registry, self.brain = session, skill_registry, brain
        self.scheduler = scheduler or SkillScheduler(session, skill_registry)
        self.decision_timeout_seconds = decision_timeout_seconds
        self.objective_timeout_seconds = objective_timeout_seconds
        self.max_decisions, self.repair_limit = max_decisions, repair_limit
        self.objective = None
        self.result = None
        self.cancel_requested = asyncio.Event()
        self._run_task = None
        self._running = False
        self._finished = asyncio.Event()
        self._abort_lock = asyncio.Lock()
        self._cleanup_errors = []
        self._active_task = None
        self._active_phase = None
        self._event_cursor = 0
        self._decisions = []
        self._skill_results = []
        self._recorded_skill_tasks = set()
        self._lifecycle = []
        self._started_at = 0.0

    def _log(self, event, **fields):
        record = {"event": event, "elapsed_seconds": round(time.monotonic() - self._started_at, 6), **fields}
        self._lifecycle.append(record)
        LOG.info("%s %s", event, fields)

    async def _abort_active(self, *, interrupt=False):
        async with self._abort_lock:
            if interrupt:
                await self._cleanup_call(self.brain.interrupt(), "brain_interrupt")
            await self._cleanup_call(self.scheduler.cancel(), "skill_cancel")
            active = self._active_task
            if active and active is not asyncio.current_task():
                if not active.done():
                    if self._active_phase in ("brain", "start"):
                        active.cancel()
                    done, _ = await asyncio.wait((active,), timeout=5)
                    if active not in done:
                        active.cancel()
                        active.add_done_callback(lambda finished: None if finished.cancelled() else finished.exception())
                        self._cleanup_errors.append("active_task_cleanup_timeout")
                # SDK interrupt may finish the provider task with an exception
                # before cancellation resumes here. Retrieve that outcome too.
                if active.done():
                    await asyncio.gather(active, return_exceptions=True)
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

    async def _cleanup_call(self, operation, name):
        task = asyncio.create_task(operation)
        try:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except asyncio.CancelledError:
                self.cancel_requested.set()
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except Exception as error:
            self._cleanup_errors.append(name + "_failed")
            LOG.error("Objective cleanup failed operation=%s exception_type=%s", name, type(error).__name__)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _wait(self, operation, phase, timeout=None):
        if self.cancel_requested.is_set() or self.session.closed.is_set():
            operation.close()
            raise _Terminal(ObjectiveStatus.CANCELLED if self.cancel_requested.is_set() else ObjectiveStatus.FAILED,
                            "objective_cancelled" if self.cancel_requested.is_set() else "session_disconnected")
        remaining = self._started_at + self.objective_timeout_seconds - time.monotonic()
        if remaining <= 0:
            operation.close()
            raise _Terminal(ObjectiveStatus.FAILED, "objective_timeout")
        effective_timeout = min(timeout, remaining) if timeout is not None else remaining
        self._active_task = asyncio.create_task(operation)
        self._active_phase = phase
        cancellation = asyncio.create_task(self.cancel_requested.wait())
        disconnected = asyncio.create_task(self.session.closed.wait())
        try:
            done, _ = await asyncio.wait((self._active_task, cancellation, disconnected),
                                         timeout=effective_timeout, return_when=asyncio.FIRST_COMPLETED)
            if self.cancel_requested.is_set():
                await self._abort_active(interrupt=True)
                raise _Terminal(ObjectiveStatus.CANCELLED, "objective_cancelled")
            if self.session.closed.is_set():
                await self._abort_active(interrupt=True)
                raise _Terminal(ObjectiveStatus.FAILED, "session_disconnected")
            episode_expired = time.monotonic() - self._started_at >= self.objective_timeout_seconds
            if self._active_task not in done or episode_expired:
                await self._abort_active(interrupt=True)
                raise _Terminal(ObjectiveStatus.FAILED, "objective_timeout" if episode_expired else "brain_timeout")
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
            await asyncio.gather(cancellation, disconnected, return_exceptions=True)
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

    async def run(self, objective: Objective) -> ObjectiveResult:
        if self._running:
            raise ActorError("objective_busy", "An Objective is already running")
        if objective.status != ObjectiveStatus.PENDING:
            raise ActorError("objective_invalid", "An Objective episode must begin Pending")
        self._run_task = asyncio.current_task()
        self._running = True
        self._finished.clear()
        self._cleanup_errors = []
        self.objective = objective.model_copy(update={"status": ObjectiveStatus.RUNNING})
        self.cancel_requested.clear()
        self._started_at = time.monotonic()
        self._event_cursor = 0
        self._decisions, self._skill_results, self._lifecycle = [], [], []
        self._recorded_skill_tasks = set()
        terminal_status, reason, message = ObjectiveStatus.FAILED, "brain_failure", None
        try:
            if self.session.closed.is_set():
                raise _Terminal(ObjectiveStatus.FAILED, "session_disconnected")
            catalog = self.registry.catalog(self.session)
            build_decision_schema(catalog)
            self._log("ObjectiveStarted", objective_id=objective.id)
            await self._wait(self.brain.start(self.objective, catalog), "start", self.decision_timeout_seconds)
            validation_error = None
            repairs = 0
            previous_skill_result = None
            for index in range(1, self.max_decisions + 1):
                from .snapshots import build_decision_context
                catalog = self.registry.catalog(self.session)
                context = build_decision_context(self.session, self.objective, catalog,
                    decision_index=index, event_cursor=self._event_cursor,
                    previous_skill_result=previous_skill_result, validation_error=validation_error)
                self._event_cursor = max((event.sequence for event in self.session.world.events),
                                         default=self._event_cursor)
                started = time.monotonic()
                record = {"index": index, "duration_seconds": 0.0, "status": "running"}
                self._decisions.append(record)
                provider_validation_error = None
                try:
                    raw = await self._wait(self.brain.decide(context), "brain", self.decision_timeout_seconds)
                except InvalidBrainDecision as error:
                    provider_validation_error = error
                    raw = None
                except BaseException:
                    record["status"] = "interrupted" if self.cancel_requested.is_set() else "failed"
                    raise
                finally:
                    record["duration_seconds"] = time.monotonic() - started
                try:
                    if provider_validation_error is not None:
                        raise provider_validation_error
                    decision = (DECISION_ADAPTER.validate_json(raw) if isinstance(raw, (str, bytes))
                                else DECISION_ADAPTER.validate_python(raw))
                    if not isinstance(decision, FinishObjective):
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
                if isinstance(decision, FinishObjective):
                    self._log("BrainDecision", kind="finish", status=decision.status)
                    terminal_status = (ObjectiveStatus.SUCCEEDED if decision.status == "success"
                                       else ObjectiveStatus.FAILED)
                    reason = "finished" if decision.status == "success" else "brain_reported_failure"
                    message = decision.reason
                    break
                self._decisions[-1]["skill_id"] = decision.skill_id
                self._log("BrainDecision", kind="skill", skill_id=decision.skill_id)
                result = await self._wait(self.scheduler.run(decision.skill_id, decision.arguments), "skill")
                previous_skill_result = result.model_dump(mode="json")
            else:
                reason = "decision_limit"
        except _Terminal as terminal:
            terminal_status, reason = terminal.status, terminal.reason
        except asyncio.CancelledError:
            self.cancel_requested.set()
            await self._abort_active(interrupt=True)
            terminal_status, reason = ObjectiveStatus.CANCELLED, "objective_cancelled"
        except Exception as error:
            await self._abort_active(interrupt=True)
            reason = "brain_failure"
            message = error.message[:512] if isinstance(error, ActorError) else type(error).__name__
            LOG.error("Objective failed code=%s exception_type=%s", reason, type(error).__name__)
        finally:
            await self._cleanup_call(self.scheduler.cancel(), "skill_cancel")
            await self._cleanup_call(self.brain.close(), "brain_close")
        if self._cleanup_errors and terminal_status == ObjectiveStatus.SUCCEEDED:
            terminal_status, reason = ObjectiveStatus.FAILED, "cleanup_failed"
        if self.cancel_requested.is_set():
            terminal_status, reason = ObjectiveStatus.CANCELLED, "objective_cancelled"
        self.objective = self.objective.model_copy(update={"status": terminal_status})
        self.result = ObjectiveResult(objective_id=objective.id, status=terminal_status, reason=reason,
            message=message, duration_seconds=time.monotonic() - self._started_at,
            decision_count=len(self._decisions), skill_results=self._skill_results)
        self._log({ObjectiveStatus.SUCCEEDED: "ObjectiveSucceeded", ObjectiveStatus.FAILED: "ObjectiveFailed",
                   ObjectiveStatus.CANCELLED: "ObjectiveCancelled"}[terminal_status], reason=reason)
        self._running = False
        self._run_task = None
        self._active_task = self._active_phase = None
        self._finished.set()
        return self.result

    async def cancel(self):
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
        return {"objective": self.objective.model_dump(mode="json") if self.objective else None,
                "result": self.result.model_dump(mode="json") if self.result else None,
                "decisions": list(self._decisions), "skills": list(self._skill_results),
                "lifecycle": list(self._lifecycle), "scheduler": self.scheduler.report(),
                "brain": brain_report, "cleanup_errors": list(self._cleanup_errors)}
