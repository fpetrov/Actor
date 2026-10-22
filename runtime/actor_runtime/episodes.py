"""A bounded participation lifetime composed of ordinary Objective episodes.

Only installed policies interpret game states. This coordinator never chooses a
Skill, creates game truth or retains a model thread across episode boundaries.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from importlib import metadata
import inspect
import time
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .errors import ActorError
from .knowledge import WorkingKnowledge
from .objectives import Objective, ObjectiveRunner, ObjectiveStatus
from .skills import SkillScheduler
from .telemetry import provider_telemetry

TRUSTED_MATCH_OBJECTIVE = (
    "Play the match autonomously. Preserve your life, pursue your own side's victory conditions, "
    "and make decisions based only on observations, memory and communication legitimately available to your character. "
    "A finished Objective ends one bounded episode; only the authoritative game determines the match outcome."
)


class EpisodeDirective(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    scope_id: str = Field(default="", max_length=128)
    episode_key: str = Field(default="", max_length=256)
    state: Literal["waiting", "active", "suspended", "terminal"]
    reason: str = Field(default="", max_length=128)
    outcome: dict[str, JsonValue] = Field(default_factory=dict)
    terminal_complete: bool = True


class ParticipationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    scope_id: str
    status: Literal["completed", "failed", "cancelled"]
    reason: str
    authoritative_terminal: bool
    outcome: dict[str, JsonValue] = Field(default_factory=dict)
    duration_seconds: float
    episodes: int
    decisions: int
    measured_tokens: int


@dataclass(frozen=True)
class EpisodeLimits:
    duration_seconds: float = 1800
    max_episodes: int = 32
    max_decisions: int = 160
    max_tokens: int = 1500000
    restart_cooldown_seconds: float = 2
    terminal_grace_seconds: float = 1

    def __post_init__(self):
        import math
        if (not math.isfinite(self.duration_seconds) or not 1 <= self.duration_seconds <= 14400 or
                not math.isfinite(self.restart_cooldown_seconds) or not 0.01 <= self.restart_cooldown_seconds <= 60 or
                not math.isfinite(self.terminal_grace_seconds) or not 0 <= self.terminal_grace_seconds <= 5 or
                type(self.max_episodes) is not int or not 1 <= self.max_episodes <= 256 or
                type(self.max_decisions) is not int or not 1 <= self.max_decisions <= 4096 or
                type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 20000000):
            raise ValueError("Participation execution limits must be finite and bounded")


class EpisodePolicy(Protocol):
    def evaluate(self, session) -> EpisodeDirective: ...
    def observe(self, session, knowledge: WorkingKnowledge) -> None: ...


class MatchPolicyRegistry:
    @staticmethod
    def create(name):
        points = list(metadata.entry_points(group="actor.match_policies", name=name))
        if len(points) != 1:
            raise ActorError("match_policy_unavailable", "Exactly one installed lifecycle policy must match")
        factory = points[0].load()
        if not callable(factory) or inspect.iscoroutinefunction(factory):
            raise ActorError("match_policy_invalid", "Lifecycle policy factories must be synchronous")
        policy = factory()
        if inspect.iscoroutine(policy):
            policy.close()
        if not callable(getattr(policy, "evaluate", None)) or inspect.iscoroutinefunction(policy.evaluate):
            raise ActorError("match_policy_invalid", "Lifecycle policies need synchronous read-only evaluation")
        if any(getattr(policy, method, None) is not None and (
                not callable(getattr(policy, method)) or inspect.iscoroutinefunction(getattr(policy, method)))
                for method in ("observe", "skill_available", "context_selection")):
            raise ActorError("match_policy_invalid", "Optional lifecycle policy hooks must be synchronous")
        return policy


class MatchEpisodeController:
    def __init__(self, session, registry, brain_factory, policy, *, scheduler=None,
                 event_router=None, memory=None, config=None, limits=None):
        self.session, self.registry, self.brain_factory, self.policy = session, registry, brain_factory, policy
        self.scheduler = scheduler or SkillScheduler(session, registry)
        self.router = event_router if event_router is not None else session.event_router
        self.memory, self.config = memory, config
        self.limits = limits or EpisodeLimits()
        self.knowledge = None
        self.result = self.runner = None
        self._cancel = asyncio.Event()
        self._finished = asyncio.Event()
        self._running = False
        self._run_task = None
        self._reports = []
        self._activity_ids = []
        self._decisions = self._tokens = 0
        self._started = 0
        self._authority_terminal = False
        self._guidance_omitted = False
        self._cleanup_errors = []
        self._collected = set()
        self._providers = []
        self._old_filter = registry.availability_filter
        self._old_command_guard = self.scheduler.execution_guard

    def _directive(self):
        value = self.policy.evaluate(self.session)
        if not isinstance(value, EpisodeDirective):
            raise ActorError("match_policy_invalid", "Policy must return a typed EpisodeDirective")
        if value.state != "waiting" and (not value.scope_id or not value.episode_key):
            raise ActorError("match_policy_invalid", "Active policy state requires scope and episode identities")
        if self.knowledge is not None and value.scope_id and value.scope_id != self.knowledge.scope_id:
            raise ActorError("match_identity_changed", "A participation cannot silently enter another scope")
        return value

    async def _update(self, *, timeout=0.2):
        sequence = self.session.last_received_sequence
        async def changed():
            async with self.session.world.changed:
                await self.session.world.changed.wait_for(lambda: self.session.last_received_sequence != sequence)
        tasks = [asyncio.create_task(changed()), asyncio.create_task(self.session.closed.wait()),
                 asyncio.create_task(self._cancel.wait())]
        try:
            await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _observe(self, directive):
        if directive.scope_id and self.knowledge is None:
            self.knowledge = WorkingKnowledge(directive.scope_id)
            self.session.working_knowledge = self.knowledge
            if self.memory is not None:
                self.memory.bind_scope(directive.scope_id)
        observer = getattr(self.policy, "observe", None)
        if self.knowledge is not None and callable(observer):
            observer(self.session, self.knowledge)

    def _budget_reason(self):
        if time.monotonic() - self._started >= self.limits.duration_seconds:
            return "participation_timeout"
        if self._decisions >= self.limits.max_decisions:
            return "participation_decision_limit"
        active_tokens = (self.runner.brain.report().get("usage_totals", {}).get("total_tokens", 0)
                         if self.runner is not None and self.runner._running and callable(getattr(self.runner.brain, "report", None)) else 0)
        if self._tokens + (active_tokens if type(active_tokens) is int else 0) >= self.limits.max_tokens:
            return "participation_usage_limit"
        return None

    def _collect_runner(self, task):
        if task is None or task in self._collected or not task.done():
            return
        self._collected.add(task)
        result = task.result()
        report = self.runner.report()
        self._reports.append(report)
        # Keep only installed identifiers privately, never arguments, payloads,
        # provider inputs or outputs. Public reports of a closed episode were
        # already masked; the terminal participation report can restore these
        # exact positional identifiers without inferring an action from counts.
        known = self.registry.definitions
        self._activity_ids.append({
            "decisions": tuple(value.get("skill_id") if value.get("skill_id") in known else None
                               for value in self.runner._decisions),
            "results": tuple(value.get("skill_id") if value.get("skill_id") in known else None
                             for value in self.runner._skill_results)})
        self._decisions += result.decision_count
        tokens = report.get("brain", {}).get("usage_totals", {}).get("total_tokens", 0)
        self._tokens += tokens if type(tokens) is int and tokens > 0 else 0

    async def _stop_runner(self, task, reason):
        if self.runner is not None and task is not None and not task.done():
            await self.runner.cancel(reason=reason)
        if task is not None:
            await task
            self._collect_runner(task)

    async def _bounded_cleanup(self, operation, name, *, timeout=5):
        """Cancellation never bypasses release of the participation's ownership."""
        task = asyncio.create_task(operation)
        deadline = time.monotonic() + timeout
        while not task.done() and time.monotonic() < deadline:
            try:
                await asyncio.wait((task,), timeout=max(0, deadline - time.monotonic()))
            except asyncio.CancelledError:
                self._cancel.set()
        if not task.done():
            task.cancel()
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            self._cleanup_errors.append(name + "_timeout")
            return False
        try:
            task.result()
            return True
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            self._cleanup_errors.append(name + "_failed")
            return False

    async def run(self, trusted_objective=TRUSTED_MATCH_OBJECTIVE):
        if self._running or self.result is not None:
            raise ActorError("participation_busy", "Use a fresh controller for each participation")
        if getattr(self.session, "participation_controller", None) is not None:
            raise ActorError("participation_busy", "This session already has a participation controller")
        # Static installed game mechanics may explain available procedures. Own
        # state, received claims and dynamic facts still use external snapshots.
        guidance = getattr(self.policy, "objective_instructions", "")
        if not isinstance(guidance, str) or len(guidance) > 2048:
            raise ActorError("match_policy_invalid", "Installed objective guidance must be a bounded static string")
        episode_text = trusted_objective + ("\n\n" + guidance if guidance else "")
        if len(episode_text) > 4096:
            # Preserve a valid full-length user objective rather than truncate
            # it to make room for optional installed game guidance.
            episode_text = trusted_objective
            self._guidance_omitted = bool(guidance)
        objective = Objective(text=episode_text)
        self.session.participation_controller = self
        availability = getattr(self.policy, "skill_available", None)
        self._old_filter = self.registry.availability_filter
        if callable(availability):
            self.registry.availability_filter = lambda session, definition: (
                (self._old_filter is None or self._old_filter(session, definition) is True)
                and availability(session, definition) is True)
        self._running, self._run_task, self._started = True, asyncio.current_task(), time.monotonic()
        self.session.privacy.private_activity = True
        status, reason, outcome, active, scope_id = "failed", "participation_failure", {}, None, ""
        next_start = 0
        try:
            self._observe(self._directive())
            await self.router.start(objective, self.scheduler.working_skill_metadata)
            while True:
                if self._cancel.is_set():
                    status, reason = "cancelled", "participation_cancelled"
                    break
                if self.session.closed.is_set():
                    reason = "session_disconnected"
                    break
                directive = self._directive()
                self._observe(directive)
                scope_id = directive.scope_id or scope_id
                if directive.state == "terminal":
                    await self._stop_runner(active, "participation_terminal")
                    active = None
                    # No further cognition: allow a bounded terminal detail
                    # delivery to follow its already-authoritative boundary.
                    terminal_deadline = time.monotonic() + self.limits.terminal_grace_seconds
                    directive = self._directive()
                    while not directive.terminal_complete and time.monotonic() < terminal_deadline and not (
                            self._cancel.is_set() or self.session.closed.is_set()):
                        await self._update(timeout=min(.05, terminal_deadline - time.monotonic()))
                        directive = self._directive()
                    if directive.state != "terminal":
                        raise ActorError("terminal_state_regressed", "An authoritative terminal boundary cannot regress")
                    self._authority_terminal = self.session.privacy.activity_revealed = True
                    status, reason, outcome = "completed", directive.reason or "authoritative_terminal", directive.outcome
                    break
                if self.router.degraded:
                    reason = "event_routing_degraded"
                    break
                if budget := self._budget_reason():
                    reason = budget
                    break
                if active is not None and (directive.state != "active" or directive.episode_key != episode_key):
                    await self._stop_runner(active, "episode_superseded")
                if active is not None and active.done():
                    result = await active
                    self._collect_runner(active)
                    active = None
                    next_start = time.monotonic() + self.limits.restart_cooldown_seconds
                    if budget := self._budget_reason():
                        reason = budget
                        break
                    if result.status == ObjectiveStatus.FAILED and result.reason not in (
                            "decision_limit", "objective_timeout", "replan_limit", "brain_reported_failure"):
                        reason = result.reason
                        break
                if active is None and directive.state == "active" and time.monotonic() >= next_start:
                    if len(self._reports) >= self.limits.max_episodes:
                        reason = "participation_episode_limit"
                        break
                    brain = self.brain_factory()
                    if inspect.isawaitable(brain):
                        if inspect.iscoroutine(brain): brain.close()
                        raise ActorError("brain_plugin_invalid", "Brain factory must return a fresh provider synchronously")
                    if any(brain is previous for previous in self._providers):
                        raise ActorError("brain_provider_reused", "Every bounded episode requires a fresh Brain instance")
                    self._providers.append(brain)
                    episode_key = directive.episode_key
                    def guard(key=episode_key):
                        current = self._directive()
                        return (not self._cancel.is_set() and current.state == "active" and current.episode_key == key
                                and (self._old_command_guard is None or self._old_command_guard() is True))
                    self.scheduler.execution_guard = guard
                    remaining_decisions = self.limits.max_decisions - self._decisions
                    episode_config = self.config
                    if self.config is not None:
                        episode_config = self.config.model_copy(update={"brain": self.config.brain.model_copy(update={
                            "max_decisions": min(self.config.brain.max_decisions, remaining_decisions)})})
                    self.runner = ObjectiveRunner(self.session, self.registry, brain, scheduler=self.scheduler,
                        event_router=self.router, config=episode_config, memory=self.memory,
                        max_decisions=min(12, remaining_decisions),
                        manage_router=False, run_finalizers=False, execution_guard=guard,
                        working_knowledge=self.knowledge,
                        context_selection=getattr(self.policy, "context_selection", None))
                    active = asyncio.create_task(self.runner.run(Objective(text=episode_text)))
                await self._update()
        except asyncio.CancelledError:
            self._cancel.set()
            status, reason = "cancelled", "participation_cancelled"
        except Exception as error:
            reason = error.code if isinstance(error, ActorError) else "participation_failure"
        finally:
            try:
                await self._bounded_cleanup(self.scheduler.cancel(), "participation_skill_cancel")
                await self._bounded_cleanup(self._stop_runner(active, reason), "participation_episode_close", timeout=12)
                self.scheduler.execution_guard = None
                if self.runner is not None:
                    final_status = ObjectiveStatus.CANCELLED if status == "cancelled" or self._cancel.is_set() else (
                        ObjectiveStatus.SUCCEEDED if status == "completed" else ObjectiveStatus.FAILED)
                    await self._bounded_cleanup(self.runner._finalize_objective(final_status, reason),
                                                "participation_finalizers", timeout=2)
                await self._bounded_cleanup(self.scheduler.cancel(), "finalizer_skill_cancel")
                await self._bounded_cleanup(self.router.close(), "event_router_close")
            finally:
                self.session.working_knowledge = None
                self.registry.availability_filter = self._old_filter
                self.scheduler.execution_guard = self._old_command_guard
                self.session.participation_controller = None
                self._running, self._run_task = False, None
                self._finished.set()
        if self._cleanup_errors and status == "completed":
            status, reason = "failed", "cleanup_failed"
        if self._cancel.is_set():
            status, reason = "cancelled", "participation_cancelled"
        if not self._authority_terminal:
            self._activity_ids.clear()
        self.result = ParticipationResult(scope_id=scope_id, status=status, reason=reason,
            authoritative_terminal=self._authority_terminal, outcome=outcome,
            duration_seconds=time.monotonic() - self._started, episodes=len(self._reports),
            decisions=self._decisions, measured_tokens=self._tokens)
        return self.result

    async def cancel(self):
        if self.result is not None:
            return
        self._cancel.set()
        if self.runner is not None:
            await self.runner.cancel(reason="participation_cancelled")
        if self._running and self._run_task is not asyncio.current_task():
            await self._finished.wait()

    def report(self):
        # Activity identifiers and private provider context are not realtime
        # public telemetry. Full safe episode reports become available postgame.
        reports = deepcopy(self._reports) if self._authority_terminal else []
        if self._authority_terminal and len(reports) == len(self._activity_ids):
            def restore(rows, identifiers):
                if len(rows) != len(identifiers):
                    return
                for row, identity in zip(rows, identifiers):
                    if identity is not None and row.get("skill_id") == "[private_activity]":
                        row["skill_id"] = identity
            for report, identifiers in zip(reports, self._activity_ids):
                restore(report.get("decisions", []), identifiers["decisions"])
                restore(report.get("skills", []), identifiers["results"])
                restore(report.get("result", {}).get("skill_results", []), identifiers["results"])
        return self.session.privacy.public({"participation": self.result.model_dump(mode="json") if self.result else None,
            "episodes": reports,
            "episode_count": len(self._reports), "decision_count": self._decisions,
            "provider_telemetry": provider_telemetry(self._reports),
            "measured_tokens": self._tokens, "active_episode": self.runner is not None and self.runner._running,
            "knowledge": self.knowledge.report() if self.knowledge is not None else {},
            "guidance_omitted": self._guidance_omitted,
            "cleanup_errors": self._cleanup_errors})
