#!/usr/bin/env python3
"""Offline production Runtime comparison; fake Brain, no Unity, gRPC server or ML.

Run with the installed Runtime environment, for example:
  build/runtime-venv/bin/python scripts/compare-routing.py
The default really waits twenty seconds in the routing-disabled baseline.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from importlib import resources
import json
import logging
import math
from pathlib import Path
import statistics
import time
import tracemalloc

from google.protobuf.any_pb2 import Any

from actor.core.v1 import actor_pb2 as core
from actor.core.v1 import bridge_pb2 as wire
from actor_agents_arena import (ROOM_CHANGED, STATION_SIGNAL, ROUTINE_NOTICE, URGENT_RECALL,
                                STATION_WIDE, RoomChangedEvent, StationSignalEvent)
from actor_runtime.brains import FinishObjective, SkillCall
from actor_runtime.builtin_skills import skill_package
from actor_runtime.classifiers import EventAssessment, EventHints
from actor_runtime.config import CognitiveConfig
from actor_runtime.events import EventRouter, RoutedEvent
from actor_runtime.extensions import ExtensionRegistry
from actor_runtime.memory import MemoryStore
from actor_runtime.objectives import Objective, ObjectiveRunner
from actor_runtime.session import RuntimeSession
from actor_runtime.skills import SkillRegistry
from actor_runtime.snapshots import build_decision_context
from actor_standard import MOTION, Motion2DObservation


async def new_session(identity):
    """Negotiate the same generated extension manifests as an ordinary client."""
    session = RuntimeSession(ExtensionRegistry.discover())
    manifest = core.CapabilityManifest(game_id="agents-arena", game_version="0.5.0",
        self_actor_id="comparison-actor", protocol_version=core.ProtocolVersion(major=1))
    for resource, filename in (("actor.metadata", "standard.v1.json"),
                               ("extensions.agents_arena.metadata", "agents_arena.v1.json")):
        package = json.loads(resources.files(resource).joinpath(filename).read_bytes())
        manifest.extensions.add(**{key: package[key] for key in (
            "extension_id", "extension_version", "supported_protocol_major", "schema_hash")}, required=True)
        for definition in package["capabilities"]:
            item = dict(definition)
            item["kind"] = {"observation": 1, "command": 2, "event": 3}[item["kind"]]
            item["command_delivery"] = {"latest": 2, "ordered": 1}.get(item.get("command_delivery"), 0)
            manifest.capabilities.add(**item)
    await session.accept(wire.GameToRuntime(session_id=identity, sequence=1, hello=core.GameHello(
        game_id=manifest.game_id, game_version=manifest.game_version, self_actor_id=manifest.self_actor_id,
        protocol_version=manifest.protocol_version)))
    await session.accept(wire.GameToRuntime(session_id=identity, sequence=2, manifest=manifest))
    return session


class GameFixture:
    """Typed game fixture emits through RuntimeSession.accept, never Router.ingest."""

    def __init__(self, session):
        self.session, self.sequence = session, 2
        self.peak_queue = self.peak_pending = self.peak_dedup = self.peak_world_events = 0

    def sample_bounds(self):
        report = self.session.event_router.report()
        self.peak_queue = max(self.peak_queue, report["queue_size"])
        self.peak_pending = max(self.peak_pending, report["pending_events"])
        self.peak_dedup = max(self.peak_dedup, report["dedup_keys"])
        self.peak_world_events = max(self.peak_world_events, len(self.session.world.events))

    def envelope_metadata(self, capability):
        item = self.session.capabilities[capability]
        return {"capability_id": capability, "extension_id": item.extension_id,
                "extension_version": self.session.extensions[item.extension_id].extension_version}

    async def event(self, capability, payload, *, entity=None):
        self.sequence += 1
        packed = Any()
        packed.Pack(payload)
        envelope = core.GameEventEnvelope(revision=self.sequence, **self.envelope_metadata(capability),
            entity_id=entity or self.session.world.self_actor_id, payload=packed)
        await self.session.accept(wire.GameToRuntime(session_id=self.session.session_id,
            sequence=self.sequence, event=envelope))
        self.sample_bounds()

    async def signal(self, kind, identity):
        await self.event(STATION_SIGNAL, StationSignalEvent(signal_id=identity, signal_kind=kind,
            source_actor_id="fixture-human", target_room_id="main-hall", message=(
                "Routine station status" if kind == ROUTINE_NOTICE else "Return to Main Hall immediately"),
            server_unix_ms=int(time.time() * 1000), audience=STATION_WIDE,
            server_revision=self.sequence + 1))

    async def observe(self, entities):
        self.sequence += 1
        frame = core.ObservationFrame(revision=self.sequence)
        for entity in entities:
            packed = Any()
            packed.Pack(Motion2DObservation())
            frame.observations.add(**self.envelope_metadata(MOTION), entity_id=entity, payload=packed)
        await self.session.accept(wire.GameToRuntime(session_id=self.session.session_id,
                                                    sequence=self.sequence, observation=frame))


class SignalAwareFakeBrain:
    """Game-specific deterministic fixture, not a production cognitive provider."""

    def __init__(self, wait_seconds):
        self.wait_seconds, self.turn_count = wait_seconds, 0
        self.contexts = []
        self.important_seen_at = None
        self.closed = False

    async def start(self, objective, catalog):
        assert any(item["id"] == "actor.wait" for item in catalog)

    async def decide(self, context):
        self.turn_count += 1
        self.contexts.append(context)
        if self.turn_count == 1:
            return SkillCall(skill_id="actor.wait", arguments={"duration_seconds": self.wait_seconds})
        important = any(entry.capability_id == STATION_SIGNAL and isinstance(entry.value, dict)
            and entry.value.get("signalKind") == "URGENT_RECALL"
            for entry in (*context.trigger_events, *context.recent_events))
        if important:
            self.important_seen_at = time.monotonic()
        return FinishObjective(status="success" if important else "failure",
            reason="Observed urgent typed signal after safe Skill cleanup" if important else "Urgent evidence missing")

    async def interrupt(self):
        pass

    async def close(self):
        self.closed = True


async def wait_until(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.001)


async def routing_case(enabled, args):
    name = "rules" if enabled else "disabled"
    session = await new_session("routing-comparison-" + name)
    config = CognitiveConfig()
    config = config.model_copy(update={"router": config.router.model_copy(update={"enabled": enabled}),
        "brain": config.brain.model_copy(update={"objective_timeout": args.wait_seconds + 15,
                                                   "decision_timeout": 3.0, "max_decisions": 4})})
    registry = SkillRegistry()
    for definition in skill_package():
        registry.register(definition)
    memory = MemoryStore.from_config(config.memory, session)
    router = session.event_router
    router.configure(config=config.router, memory=memory)
    fixture, brain = GameFixture(session), SignalAwareFakeBrain(args.wait_seconds)
    runner = ObjectiveRunner(session, registry, brain, config=config, memory=memory)
    baseline_bytes, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    started = time.monotonic()
    task = asyncio.create_task(runner.run(Objective(
        text=f"Wait for {args.wait_seconds:g} seconds; react to an urgent recall, ignore routine station notices.")))
    try:
        await wait_until(lambda: runner.scheduler.report()["active_skill_id"] == "actor.wait")
        noise_started = time.monotonic()
        for index in range(args.noise_events):
            await fixture.signal(ROUTINE_NOTICE, f"routine-{index}")
        await asyncio.sleep(.05)
        routine_turn_count = brain.turn_count
        wait_active_after_routine = runner.scheduler.report()["active_skill_id"] == "actor.wait"
        for index in range(args.unrelated_events):
            await fixture.event(ROOM_CHANGED, RoomChangedEvent(previous_room_id="irrelevant-before",
                current_room_id=f"unrelated-fixture-room-{index}"))
        important_at = time.monotonic()
        await fixture.signal(URGENT_RECALL, "unique-urgent-recall")
        for _ in range(args.duplicates):
            await fixture.signal(URGENT_RECALL, "unique-urgent-recall")
        result = await asyncio.wait_for(task, timeout=args.wait_seconds + 10)
        elapsed = time.monotonic() - started
        fixture.sample_bounds()
        _, peak_bytes = tracemalloc.get_traced_memory()
        routing = router.report()
        memory_stats = await memory.stats()
        skill = result.skill_results[0]
        checks = {"objective_succeeded": result.status == "succeeded", "exactly_two_decisions": brain.turn_count == 2,
            "routine_extra_brain_calls_zero": routine_turn_count == 1 and wait_active_after_routine,
            "important_evidence_reached_brain": brain.important_seen_at is not None,
            "queue_bounded": fixture.peak_queue <= config.router.max_queue_size,
            "pending_bounded": fixture.peak_pending <= config.router.max_pending_events,
            "dedup_bounded": fixture.peak_dedup <= config.router.max_dedup_keys,
            "world_history_bounded": fixture.peak_world_events <= session.world.events.maxlen,
            "memory_bounded": memory_stats["global_entries"] <= config.memory.max_entries,
            "no_commands": session.next_command_id == 0,
            "routing_not_degraded": not router.degraded,
            "expected_wait_termination": (skill["status"] == "cancelled" and skill["reason"] == "preempted_by_event")
                if enabled else (skill["status"] == "succeeded" and skill["duration_seconds"] >= args.wait_seconds - .05)}
        return {"routing": name, "declared_wait_seconds": args.wait_seconds,
            "objective_duration_seconds": elapsed, "skill_result": skill,
            "important_reaction_delay_seconds": (brain.important_seen_at - important_at
                if brain.important_seen_at is not None else None),
            "noise_duration_seconds": important_at - noise_started,
            "brain_decision_count": brain.turn_count, "brain_calls_after_routine": routine_turn_count,
            "routine_extra_brain_calls": max(0, routine_turn_count - 1),
            "classifier_calls": routing["counts"].get("classifier_calls", 0),
            "classifier_latency_median_ms": (statistics.median(routing["classifier_latency_ms"])
                if routing["classifier_latency_ms"] else None),
            "context_sizes_bytes": [context.external_bytes for context in brain.contexts],
            "estimated_context_tokens": [context.estimated_tokens for context in brain.contexts],
            "trigger_event_counts": [len(context.trigger_events) for context in brain.contexts],
            "received_wire_events": session.events_received,
            "routine_events": args.noise_events, "unrelated_low_priority_events": args.unrelated_events,
            "duplicate_urgent_events": args.duplicates,
            "queue_counts": routing["counts"], "peak_queue_entries": fixture.peak_queue,
            "peak_pending_entries": fixture.peak_pending, "peak_dedup_entries": fixture.peak_dedup,
            "peak_world_event_entries": fixture.peak_world_events,
            "python_peak_additional_bytes": max(0, peak_bytes - baseline_bytes),
            "memory": memory_stats, "checks": checks, "passed": all(checks.values())}
    finally:
        if not task.done():
            await runner.cancel()
            await task
        await router.close()
        await memory.close()
        session.close()


async def context_comparison():
    session = await new_session("context-comparison")
    # Selection is measured separately from routing, with 512 current entities
    # and a full historical ring. No alternate cache or fixture WorldState.
    session.event_router.configure(config=CognitiveConfig().router.model_copy(update={"enabled": False}))
    fixture = GameFixture(session)
    await fixture.observe([session.world.self_actor_id, *[f"unrelated-entity-{i:03}" for i in range(255)]])
    await fixture.observe([f"unrelated-entity-{i:03}" for i in range(255, 511)])
    for index in range(63):
        await fixture.event(ROOM_CHANGED, RoomChangedEvent(previous_room_id="unrelated", current_room_id=(
            f"irrelevant-location-{index}:" + "x" * 700)))
    await fixture.signal(URGENT_RECALL, "context-critical-signal")
    raw_trigger = session.world.events[-1]
    trigger = RoutedEvent("context-trigger", session.session_id, raw_trigger, "agents-arena",
        session.extensions["agents-arena"].extension_version, session.capabilities[STATION_SIGNAL].output_type_url,
        hints=EventHints(priority=100, potentially_critical=True, semantic_id="context-critical-signal"),
        assessment=EventAssessment(relevance="relevant", urgency="critical", should_remember=True,
            should_interrupt_skill=True, should_request_brain_review=True, confidence=1.0))
    objective = Objective(text="React to urgent station recall by returning to Main Hall.")
    common = {"trigger_events": [trigger], "decision_reason": "critical_event", "cognitive_revision": 1,
        "previous_skill_result": {"skill_id": "actor.wait", "status": "cancelled", "reason": "preempted_by_event"}}
    default = build_decision_context(session, objective, [], **common)
    config = CognitiveConfig().context
    selected = build_decision_context(session, objective, [], max_external_bytes=config.max_external_context_bytes,
        max_observations=config.max_observations, max_events=config.max_events,
        max_memories=config.max_memory_items, max_event_payload_bytes=config.max_event_payload_bytes,
        stale_after_seconds=config.stale_observation_seconds, **common)
    def measurements(context):
        return {"external_bytes": context.external_bytes, "estimated_tokens": context.estimated_tokens,
            "observations_selected": len(context.observations), "observations_omitted": context.observations_omitted,
            "recent_events_selected": len(context.recent_events), "events_omitted": context.events_omitted,
            "trigger_events_selected": len(context.trigger_events), "truncated_payloads": context.payloads_truncated,
            "trigger_payload_preserved": bool(context.trigger_events and context.trigger_events[0].value
                and context.trigger_events[0].value.get("signalId") == "context-critical-signal"),
            "controlled_actor_observation_preserved": any(item.entity_id == session.world.self_actor_id
                for item in context.observations),
            "previous_skill_reason": context.previous_skill_result["reason"]}
    before, after = measurements(default), measurements(selected)
    report = {"scope": "Offline synthetic selection over the production ContextBuilder; not model usage or live game evidence",
        "observations_available": len(session.world.observations), "historical_events_available": len(session.world.events),
        "default_budget_bytes": 65536, "configured_budget_bytes": config.max_external_context_bytes,
        "default_64k": before, "configured_32k": after,
        "external_bytes_reduction": before["external_bytes"] - after["external_bytes"],
        "external_bytes_reduction_fraction": 1 - after["external_bytes"] / before["external_bytes"],
        "checks": {"both_preserve_trigger_payload": before["trigger_payload_preserved"] and after["trigger_payload_preserved"],
            "selected_within_budget": after["external_bytes"] <= config.max_external_context_bytes,
            "selected_context_smaller": after["external_bytes"] < before["external_bytes"],
            "selected_preserves_controlled_actor": after["controlled_actor_observation_preserved"],
            "selected_preserves_preemption_reason": after["previous_skill_reason"] == "preempted_by_event"}}
    report["passed"] = all(report["checks"].values())
    await session.event_router.close()
    session.close()
    return report


async def main(args):
    if (not math.isfinite(args.wait_seconds) or not 1 <= args.wait_seconds <= 300 or
            not 0 <= args.noise_events <= 10000 or not 0 <= args.unrelated_events <= 10000 or
            not 0 <= args.duplicates <= 40):
        raise ValueError("Use a finite wait in[1,300], noise counts in[0,10000] and duplicates in[0,40]")
    logging.basicConfig(level=logging.WARNING)
    tracemalloc.start()
    try:
        disabled = await routing_case(False, args)
        rules = await routing_case(True, args)
        routing = {"scope": "Offline deterministic fake Brain; real RuntimeSession/ObjectiveRunner/SkillScheduler/SQLite; no Unity, Codex, gRPC socket or ML",
            "created_at": datetime.now(timezone.utc).isoformat(), "external_model_calls": 0,
            "python_memory_scope": "tracemalloc additional peak allocations per case; excludes native libraries and other processes",
            "disabled": disabled, "rules": rules,
            "reaction_delay_reduction_seconds": disabled["important_reaction_delay_seconds"] - rules["important_reaction_delay_seconds"],
            "passed": disabled["passed"] and rules["passed"]}
        context = await context_comparison()
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "routing-comparison.json").write_text(json.dumps(routing, indent=2) + "\n")
        (args.output / "context-comparison.json").write_text(json.dumps(context, indent=2) + "\n")
        print(json.dumps({"passed": routing["passed"] and context["passed"],
            "disabled_reaction_seconds": disabled["important_reaction_delay_seconds"],
            "rules_reaction_seconds": rules["important_reaction_delay_seconds"],
            "brain_decisions": [disabled["brain_decision_count"], rules["brain_decision_count"]],
            "routine_extra_brain_calls": [disabled["routine_extra_brain_calls"], rules["routine_extra_brain_calls"]],
            "context_bytes_default_selected": [context["default_64k"]["external_bytes"], context["configured_32k"]["external_bytes"]],
            "output": str(args.output.resolve())}, indent=2))
        return int(not routing["passed"] or not context["passed"])
    finally:
        tracemalloc.stop()


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-seconds", type=float, default=20.0)
    parser.add_argument("--noise-events", type=int, default=1000)
    parser.add_argument("--unrelated-events", type=int, default=1000)
    parser.add_argument("--duplicates", type=int, default=25)
    parser.add_argument("--output", type=Path, default=root / "build/event-validation")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
