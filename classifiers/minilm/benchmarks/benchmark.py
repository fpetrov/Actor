"""Reproducible local latency probe; no server, Brain or paid model is started."""
import argparse
import asyncio
import json
from pathlib import Path
import resource
import statistics
import tempfile
import time
from types import SimpleNamespace

from google.protobuf import descriptor_pool, message_factory
from google.protobuf.json_format import ParseDict
from actor_minilm.provider import MiniLMClassifier
from actor_runtime.classifiers import ClassificationContext, EventHints, EventRuleRegistry
from actor_runtime.events import RoutedEvent
from actor_runtime.world import CachedPayload


def entries(raw):
    def entry(x):
        name = x["type_url"].split("/", 1)[-1]
        cls = message_factory.GetMessageClass(descriptor_pool.Default().FindMessageTypeByName(name))
        return CachedPayload(x["entity_id"], x["capability_id"], x["sequence"], x["revision"],
            ParseDict(x["payload"], cls()), time.monotonic(), x["data_policy"], x["expires_at_unix_ms"])
    context = raw["context"]
    context = ClassificationContext(context["objective"], context["current_skill"],
        tuple(entry(x) for x in context["relevant_state"]), (), context["current_plan"])
    event = raw["event"]
    event = RoutedEvent(event["id"], event["session_id"], entry(event["entry"]), event["extension_id"],
        event["extension_version"], event["type_url"], EventHints())
    return context, event


def stats(values):
    values = sorted(values)
    return {"count": len(values), "p50_ms": statistics.median(values),
            "p95_ms": values[min(len(values) - 1, int(.95 * len(values)))], "max_ms": max(values)}


async def measure(iterations, capture_root):
    classifier = MiniLMClassifier(EventRuleRegistry.discover(), capture_root=capture_root)
    fixture = json.loads((Path(__file__).parents[1] / "examples/runtime-row.json").read_text())["input"]
    context, event = entries(fixture)
    # This probe measures the provider path; Router ingress is measured separately.
    samples = []
    lag = []
    finished = asyncio.Event()

    async def ticker():
        while not finished.is_set():
            start = time.perf_counter()
            await asyncio.sleep(.005)
            lag.append(max(0., (time.perf_counter() - start) * 1000 - 5))

    ticking = asyncio.create_task(ticker())
    try:
        for i in range(iterations + 20):
            start = time.perf_counter()
            await classifier.classify(context, event)
            if i >= 20:
                samples.append((time.perf_counter() - start) * 1000)
        if classifier.fallbacks:
            raise RuntimeError("Latency probe had classifier fallbacks")
        return {"provider_latency": stats(samples), "event_loop_lag": stats(lag),
                "fallbacks": classifier.fallbacks, "capture": dict(classifier.counts)}
    finally:
        finished.set()
        await ticking
        await classifier.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 20 <= args.iterations <= 10000:
        raise ValueError("Use 20–10000 iterations")
    # Load/install registry metadata once before timing either mode.
    EventRuleRegistry.discover()
    off = await measure(args.iterations, None)
    with tempfile.TemporaryDirectory(prefix="actor-minilm-benchmark-") as private:
        on = await measure(args.iterations, Path(private))
    result = {"method": "Single provider, synthetic visible-body input, 20 warmup calls, two CPU threads",
        "logging_off": off, "logging_on": on, "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "rss_units": "bytes on macOS; KiB on Linux", "covers": "Projection, inference, optional input/output recording",
        "limitations": "Not a concurrent multi-actor or full-game load test; file writes use OS buffering"}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
