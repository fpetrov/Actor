"""Developer CLI; examples are discovered plugins, not game branches in Core."""

from __future__ import annotations

import argparse
import asyncio
from importlib import metadata
import json
import logging
import os
from pathlib import Path
import signal
import tracemalloc

from .errors import ActorError
from .extensions import ExtensionRegistry
from .server import RuntimeServer

LOG = logging.getLogger("actor_runtime")


async def serve(args):
    registry = ExtensionRegistry.discover()
    server = RuntimeServer(registry, os.environ.get("ACTOR_BRIDGE_TOKEN", ""),
                           port=args.port, command_timeout_ms=args.command_timeout_ms)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stopping.set)
        except NotImplementedError:
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stopping.set))
    outcome = None
    script_task = None

    def save_report():
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            report = {"runtime": server.report()}
            if outcome is not None:
                report["scripted"] = outcome
            temporary = args.report.with_suffix(".tmp")
            temporary.write_text(json.dumps(report, indent=2) + "\n")
            temporary.replace(args.report)

    async def scripted():
        nonlocal outcome
        try:
            points = metadata.entry_points(group="actor.examples", name=args.example)
            if len(points) != 1:
                raise ActorError("ExampleMissing", "Exactly one installed example must match",
                                 {"example": args.example})
            policy = next(iter(points)).load()
            session = await server.next_session(timeout=args.connect_timeout)
            outcome = await policy(session)
            LOG.info("Scripted example completed: %s", outcome)
        except Exception as error:
            outcome = {"passed": False, "error": (error.as_dict() if isinstance(error, ActorError)
                                                   else {"code": type(error).__name__, "message": str(error)})}
            LOG.error("Scripted example failed: %s", outcome)
        finally:
            save_report()
            if args.exit_on_complete:
                stopping.set()

    async def periodic_report():
        while not stopping.is_set():
            await asyncio.sleep(2)
            save_report()

    await server.start()
    reporter = asyncio.create_task(periodic_report())
    if args.operation == "scripted":
        script_task = asyncio.create_task(scripted())
    try:
        await stopping.wait()
    finally:
        reporter.cancel()
        if script_task:
            script_task.cancel()
        await asyncio.gather(reporter, *([script_task] if script_task else []), return_exceptions=True)
        await server.stop()
        save_report()
    return 1 if outcome and not outcome["passed"] else 0


def main():
    parser = argparse.ArgumentParser(prog="actor-runtime")
    parser.add_argument("operation", choices=("serve", "scripted"))
    parser.add_argument("example", nargs="?", help="Installed actor.examples entry point")
    parser.add_argument("--port", type=int, default=int(os.environ.get("ACTOR_BRIDGE_PORT", "50051")))
    parser.add_argument("--command-timeout-ms", type=int, default=250)
    parser.add_argument("--connect-timeout", type=float, default=120)
    parser.add_argument("--report", type=Path, help="Optional diagnostics artifact; never an IPC channel")
    parser.add_argument("--exit-on-complete", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--measure-allocations", action="store_true",
                        help="Include traced Python memory in developer reports")
    args = parser.parse_args()
    if args.operation == "scripted" and not args.example:
        parser.error("scripted requires an installed example name")
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.measure_allocations:
        tracemalloc.start(1)
    try:
        result = asyncio.run(serve(args))
    except ActorError as error:
        LOG.error("%s", json.dumps(error.as_dict()))
        result = 1
    raise SystemExit(result)


if __name__ == "__main__":
    main()
