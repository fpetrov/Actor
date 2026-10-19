"""Developer CLI; examples are discovered plugins, not game branches in Core."""

from __future__ import annotations

import argparse
import asyncio
from importlib import metadata
import json
import logging
import math
import os
from pathlib import Path
import signal
import tracemalloc

from .errors import ActorError
from .extensions import ExtensionRegistry
from .server import RuntimeServer
from .config import CognitiveConfig

LOG = logging.getLogger("actor_runtime")


async def serve(args):
    config = CognitiveConfig.load(getattr(args, "config", None))
    changes = {}
    for argument, field in (("decision_timeout", "decision_timeout"),
                            ("objective_timeout", "objective_timeout"), ("max_decisions", "max_decisions")):
        if getattr(args, argument, None) is not None:
            changes[field] = getattr(args, argument)
    config = config.model_copy(update={"brain": config.brain.model_copy(update=changes)})
    if getattr(args, "classifier", None):
        config = config.model_copy(update={"classifier": config.classifier.model_copy(update={"provider": args.classifier})})
    memory_changes = {}
    if getattr(args, "memory", None):
        memory_changes["enabled"] = args.memory != "none"
    if getattr(args, "memory_database", None):
        memory_changes["path"] = str(args.memory_database.resolve())
    if getattr(args, "agent_profile", None):
        memory_changes["profile"] = args.agent_profile
    if memory_changes:
        config = config.model_copy(update={"memory": config.memory.model_copy(update=memory_changes)})
    if getattr(args, "no_event_routing", False):
        config = config.model_copy(update={"router": config.router.model_copy(update={"enabled": False})})
    config = CognitiveConfig.model_validate(config.model_dump())
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
    objective_runner = None
    pending_objective = None
    memory_store = None
    memory_report = None
    if args.operation == "agent":
        from .objectives import Objective
        pending_objective = Objective(text=args.objective)

    def cancelled_outcome():
        return {"passed": False, "result": {"objective_id": pending_objective.id,
            "status": "cancelled", "reason": "runtime_shutdown", "message": None,
            "duration_seconds": server.report()["elapsed_seconds"], "decision_count": 0, "skill_results": []}}

    def save_report():
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            report = {"runtime": server.report()}
            if outcome is not None:
                report["objective" if args.operation == "agent" else "scripted"] = outcome
            if objective_runner is not None:
                report["cognitive"] = objective_runner.report()
                report["cognitive_config"] = config.model_dump(mode="json")
            if memory_report is not None:
                report["memory"] = memory_report
            if objective_runner is not None:
                report = objective_runner.session.privacy.public(report)
            temporary = args.report.with_suffix(".tmp")
            temporary.write_text(json.dumps(report, indent=2) + "\n")
            temporary.replace(args.report)

    async def scripted():
        nonlocal outcome
        session = None
        try:
            points = metadata.entry_points(group="actor.examples", name=args.example)
            if len(points) != 1:
                raise ActorError("ExampleMissing", "Exactly one installed example must match",
                                 {"example": args.example})
            policy = next(iter(points)).load()
            session = await server.next_session(timeout=args.connect_timeout)
            outcome = await policy(session)
            outcome = session.privacy.public(outcome)
            LOG.info("Scripted example completed: %s", outcome)
        except Exception as error:
            outcome = {"passed": False, "error": (error.as_dict() if isinstance(error, ActorError)
                                                   else {"code": type(error).__name__, "message": str(error)})}
            if session is not None:
                outcome = session.privacy.public(outcome)
            LOG.error("Scripted example failed: %s", outcome)
        finally:
            save_report()
            if args.exit_on_complete:
                stopping.set()

    async def periodic_report():
        while not stopping.is_set():
            await asyncio.sleep(2)
            await refresh_memory_report()
            save_report()

    async def refresh_memory_report():
        nonlocal memory_report
        if memory_store is not None:
            try:
                memory_report = await memory_store.stats()
            except ActorError as error:
                memory_report = {"state": "degraded", "error": error.code}

    async def agent():
        nonlocal outcome, objective_runner, memory_store
        session = None
        from .brains import BrainRegistry
        from .objectives import ObjectiveRunner
        from .skills import SkillRegistry
        try:
            session = await server.next_session(timeout=args.connect_timeout)
            # Negotiation completes before Unity's first observation frame arrives.
            from actor.core.v1 import actor_pb2 as core
            has_observations = any(value.kind == core.CAPABILITY_KIND_OBSERVATION
                and value.extension_id in session.negotiated.available for value in session.capabilities.values())
            if has_observations:
                try:
                    # Initial warmup is optional: conditional/other-entity capabilities
                    # must not prevent a capability-free Skill from starting.
                    await session.world.wait_until(lambda world: world.last_frame_received_at > 0, timeout=1)
                except TimeoutError:
                    pass
            options = {"model": args.model} if args.model else {}
            if args.brain == "codex":
                options.update(max_turns=config.brain.max_decisions,
                    usage_warning_tokens=config.brain.usage_warning_tokens,
                    usage_limit_tokens=config.brain.usage_limit_tokens)
            brain = BrainRegistry.discover().create(args.brain, **options)
            from .classifiers import ClassifierRegistry
            from .memory import MemoryStore
            memory_store = MemoryStore.from_config(config.memory, session)
            router = session.event_router
            classifier = ClassifierRegistry.create(config.classifier.provider,
                config=config.classifier, rules=router.rules)
            router.configure(config=config.router, classifier=classifier, memory=memory_store,
                policy_tags=config.policy_tags, classifier_config=config.classifier)
            LOG.info("Cognition brain=%s classifier=%s memory=%s profile=%s extensions=%s connection=ready objective_id=%s objective=%s",
                args.brain, config.classifier.provider, "sqlite" if config.memory.enabled else "disabled",
                config.memory.profile or "session-isolated", sorted(session.negotiated.available),
                pending_objective.id, pending_objective.text)
            objective_runner = ObjectiveRunner(session, SkillRegistry.discover(), brain,
                config=config, event_router=router, memory=memory_store)
            result = await objective_runner.run(pending_objective)
            outcome = session.privacy.public({"passed": result.status == "succeeded", "result": result.model_dump(mode="json")})
            LOG.info("Agent objective completed: %s", json.dumps(outcome))
        except asyncio.CancelledError:
            outcome = cancelled_outcome()
            raise
        except Exception as error:
            outcome = {"passed": False, "error": (error.as_dict() if isinstance(error, ActorError)
                                                   else {"code": type(error).__name__, "message": str(error)})}
            if session is not None:
                outcome = session.privacy.public(outcome)
            LOG.error("Agent objective failed: %s", json.dumps(outcome))
        finally:
            await refresh_memory_report()
            save_report()
            if args.exit_on_complete:
                stopping.set()

    await server.start()
    reporter = asyncio.create_task(periodic_report())
    if args.operation == "scripted":
        script_task = asyncio.create_task(scripted())
    elif args.operation == "agent":
        script_task = asyncio.create_task(agent())
    try:
        await stopping.wait()
    finally:
        reporter.cancel()
        if objective_runner is not None:
            await objective_runner.cancel()
        if script_task:
            script_task.cancel()
        await asyncio.gather(reporter, *([script_task] if script_task else []), return_exceptions=True)
        await server.stop()
        if memory_store is not None:
            await memory_store.close()
        if args.operation == "agent" and outcome is None:
            outcome = cancelled_outcome()
        save_report()
    return 1 if outcome and not outcome["passed"] else 0


def main():
    parser = argparse.ArgumentParser(prog="actor-runtime")
    parser.add_argument("operation", choices=("serve", "scripted", "agent", "codex", "memory"))
    parser.add_argument("example", nargs="?", help="Installed actor.examples entry point, or codex status/login")
    parser.add_argument("--port", type=int, default=int(os.environ.get("ACTOR_BRIDGE_PORT", "50051")))
    parser.add_argument("--command-timeout-ms", type=int, default=250)
    parser.add_argument("--connect-timeout", type=float, default=120)
    parser.add_argument("--report", type=Path, help="Optional diagnostics artifact; never an IPC channel")
    parser.add_argument("--exit-on-complete", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--measure-allocations", action="store_true",
                        help="Include traced Python memory in developer reports")
    parser.add_argument("--brain", default="scripted", help="Installed actor.brains entry point")
    parser.add_argument("--objective", help="User objective for the selected Brain")
    parser.add_argument("--model", help="Optional provider model override; otherwise uses its configured default")
    parser.add_argument("--decision-timeout", type=float)
    parser.add_argument("--objective-timeout", type=float)
    parser.add_argument("--max-decisions", type=int)
    parser.add_argument("--config", type=Path, help="Local validated cognitive JSON configuration")
    parser.add_argument("--classifier", help="Installed actor.classifiers provider; default rules, Jev requires explicit selection")
    parser.add_argument("--memory", choices=("sqlite", "none"))
    parser.add_argument("--memory-database", type=Path, help="Persistent SQLite path; requires explicit --agent-profile")
    parser.add_argument("--agent-profile", help="Stable local profile; network ActorId is never persistent identity")
    parser.add_argument("--game-id", help="Memory administration scope")
    parser.add_argument("--no-event-routing", action="store_true", help="Compare the previous Skill-only cognitive loop")
    args = parser.parse_args()
    if args.operation == "scripted" and not args.example:
        parser.error("scripted requires an installed example name")
    if args.operation == "agent" and (not args.objective or not args.objective.strip() or len(args.objective) > 4096):
        parser.error("agent requires a 1-4096 character --objective")
    if args.operation == "codex" and args.example not in ("status", "login"):
        parser.error("codex requires status or login")
    if args.operation == "memory" and (args.example not in ("stats", "clear")
            or not args.memory_database or not args.agent_profile or not args.game_id):
        parser.error("memory stats/clear requires --memory-database, --agent-profile and --game-id")
    if args.memory_database and not args.agent_profile:
        parser.error("Persistent memory requires an explicit --agent-profile")
    if any(value is not None and (not math.isfinite(value) or value <= 0) for value in
           (args.connect_timeout, args.decision_timeout, args.objective_timeout)) or (args.max_decisions is not None and args.max_decisions < 1):
        parser.error("Execution timeouts and decision limit must be finite and positive")
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.measure_allocations:
        tracemalloc.start(1)
    try:
        if args.operation == "codex":
            from actor_codex.auth import command
            result = asyncio.run(command(args.example))
        elif args.operation == "memory":
            from .memory import MemoryStore
            async def administer():
                store = MemoryStore(game_id=args.game_id, session_id="administration",
                    path=str(args.memory_database.resolve()), profile=args.agent_profile, administration=True)
                try:
                    if args.example == "clear":
                        print(json.dumps({"cleared": await store.clear()}))
                    else:
                        print(json.dumps(await store.stats(), indent=2))
                finally:
                    await store.close()
                return 0
            result = asyncio.run(administer())
        else:
            result = asyncio.run(serve(args))
    except ActorError as error:
        LOG.error("%s", json.dumps(error.as_dict()))
        result = 1
    except (ValueError, OSError) as error:
        LOG.error("Configuration failed: %s", type(error).__name__)
        result = 1
    raise SystemExit(result)


if __name__ == "__main__":
    main()
