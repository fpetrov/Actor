#!/usr/bin/env python3
"""Offline curated routing evaluation, with no model, API key or live game."""
from __future__ import annotations

import argparse
import asyncio
from importlib import resources
import json
from pathlib import Path
import statistics
import time

from actor.core.v1 import actor_pb2 as core
from actor.core.v1 import bridge_pb2 as wire
from actor_agents_arena import STATION_SIGNAL, StationSignalEvent, STATION_WIDE
from actor_runtime.classifiers import EventRuleRegistry
from actor_runtime.events import EventRouter
from actor_runtime.extensions import ExtensionRegistry
from actor_runtime.objectives import Objective
from actor_runtime.session import RuntimeSession
from actor_runtime.world import CachedPayload


async def run(args):
    cases = json.loads(args.dataset.read_text())
    details = []
    for index, case in enumerate(cases):
        session = RuntimeSession(ExtensionRegistry.discover())
        manifest = core.CapabilityManifest(game_id="agents-arena", game_version="0.5.0",
            self_actor_id="evaluation-actor", protocol_version=core.ProtocolVersion(major=1))
        for resource, filename in (("actor.metadata", "standard.v1.json"),
                ("extensions.agents_arena.metadata", "agents_arena.v1.json")):
            package = json.loads(resources.files(resource).joinpath(filename).read_bytes())
            manifest.extensions.add(**{key: package[key] for key in
                ("extension_id", "extension_version", "supported_protocol_major", "schema_hash")}, required=True)
            for item in package["capabilities"]:
                item = dict(item)
                item["kind"] = {"observation": 1, "command": 2, "event": 3}[item["kind"]]
                item["command_delivery"] = {"latest": 2, "ordered": 1}.get(item.get("command_delivery"), 0)
                manifest.capabilities.add(**item)
        sid = f"eval-{index}"
        await session.accept(wire.GameToRuntime(session_id=sid, sequence=1, hello=core.GameHello(
            game_id=manifest.game_id, game_version=manifest.game_version, self_actor_id=manifest.self_actor_id,
            protocol_version=manifest.protocol_version)))
        await session.accept(wire.GameToRuntime(session_id=sid, sequence=2, manifest=manifest))
        router = EventRouter(session, policy_tags=tuple(case.get("policy_tags", ())),
            rules=EventRuleRegistry() if case.get("unknown_rule") else None)
        await router.start(Objective(text=case["objective"]),
            current_skill=lambda: case.get("current_skill"))
        try:
            value = StationSignalEvent(signal_id="logical-signal", signal_kind=case["kind"],
                source_actor_id="human", target_room_id="main-hall", message=case.get("message", "Station signal"),
                server_unix_ms=int(time.time()*1000), audience=STATION_WIDE, server_revision=1)
            for key in case.get("missing", ()):
                value.ClearField(key)
            started = time.perf_counter()
            for offset in range(case.get("copies", 1)):
                router.ingest(CachedPayload(manifest.self_actor_id, STATION_SIGNAL, 3+offset, 1, value,
                    time.monotonic() - case.get("age_seconds", 0)))
            async with asyncio.timeout(2):
                while router.report()["counts"].get("events_classified", 0) == 0:
                    await asyncio.sleep(0)
            actual = router.preempt.is_set()
            details.append({"id": case["id"], "expected_interrupt": case["expected_interrupt"],
                "actual_interrupt": actual, "correct": actual == case["expected_interrupt"],
                "latency_ms": (time.perf_counter()-started)*1000,
                "classifier_calls": router.report()["counts"].get("classifier_calls", 0),
                "replans": router.report()["counts"].get("replans_requested", 0),
                "duplicates": router.report()["counts"].get("events_duplicated", 0)})
        finally:
            await router.close()
            session.close()
    tp = sum(row["actual_interrupt"] and row["expected_interrupt"] for row in details)
    fp = sum(row["actual_interrupt"] and not row["expected_interrupt"] for row in details)
    fn = sum(not row["actual_interrupt"] and row["expected_interrupt"] for row in details)
    latencies = sorted(row["latency_ms"] for row in details)
    report = {"scope": "Hand-curated typed-event policy checks; not general semantic accuracy or confidence calibration",
        "provider": "rules", "cases": len(details), "correct": sum(row["correct"] for row in details),
        "interruption_precision": tp/(tp+fp) if tp+fp else None,
        "interruption_recall": tp/(tp+fn) if tp+fn else None,
        "important_false_negatives": fn, "unnecessary_interruptions": fp,
        "classifier_invocations": sum(row["classifier_calls"] for row in details),
        "latency_median_ms": statistics.median(latencies),
        "latency_p95_ms": latencies[min(len(latencies)-1, int(len(latencies)*.95))],
        "external_requests": 0, "model_calls": 0, "details": details}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps({key: value for key, value in report.items() if key != "details"}, indent=2))
    return int(fn > 0 or fp > 0)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=root/"runtime/evaluation/events.json")
    parser.add_argument("--output", type=Path, default=root/"build/event-validation/classifier-evaluation.json")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
