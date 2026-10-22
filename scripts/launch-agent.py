#!/usr/bin/env python3
"""Launch a Brain-driven ACTOR Runtime and an ordinary multiplayer game client."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit


PLAYER_NAMES = ("Аня", "Борис", "Вера", "Глеб", "Даша", "Егор", "Жанна", "Зоя",
    "Илья", "Кира", "Лена", "Мира", "Нина", "Олег", "Павел", "Рита",
    "Саша", "Тимур", "Уля", "Фёдор", "Элла", "Юля", "Яна", "Лев")


def participant_name(explicit=None, *, sample=None):
    """One local default; multiple independent launches may supply distinct names."""
    return explicit if explicit is not None else (sample or secrets.SystemRandom().sample)(PLAYER_NAMES, 1)[0]


class _RegisteredSpawn:
    """Defer an interrupt only until a newly created child is owned by cleanup."""

    def __init__(self):
        self._registering = False
        self._pending = False

    def interrupted(self, _signal, _frame):
        if self._registering:
            self._pending = True
        else:
            raise KeyboardInterrupt

    def spawn(self, command, *, register, **kwargs):
        if self._pending:
            raise KeyboardInterrupt
        self._registering = True
        try:
            process = subprocess.Popen(command, **kwargs)
            register(process)
        finally:
            self._registering = False
        if self._pending:
            raise KeyboardInterrupt
        return process


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room", required=True, help="Six-character room code from the Unity lobby or website")
    parser.add_argument("--brain", default="scripted", help="Installed actor.brains provider, e.g. scripted or codex")
    parser.add_argument("--operation", choices=("agent", "match"), default="agent",
        help="One bounded Objective or the installed match-lifetime policy")
    parser.add_argument("--match-policy", default="agents-arena", help="Installed lifecycle policy, used only with --operation match")
    parser.add_argument("--objective", required=True, help="Objective passed only to the Python Runtime")
    parser.add_argument("--model", help="Optional Brain model override")
    parser.add_argument("--config", type=Path, help="Validated local cognitive JSON configuration")
    parser.add_argument("--classifier", help="Classifier provider; default rules, optional jev")
    parser.add_argument("--memory", choices=("sqlite", "none"))
    parser.add_argument("--memory-database", type=Path)
    parser.add_argument("--agent-profile", help="Explicit persistent identity for local episodic memory")
    parser.add_argument("--no-event-routing", action="store_true")
    parser.add_argument("--player-name", help="Public name; default picks a short Russian name using OS randomness")
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--decision-timeout", type=float, default=120)
    parser.add_argument("--objective-timeout", type=float, default=600)
    parser.add_argument("--max-decisions", type=int, default=16)
    parser.add_argument("--headless-client", action="store_true", help="Run the ordinary Unity client without a graphics window for unattended acceptance")
    parser.add_argument("--output", type=Path, default=root / "build/cognitive-validation")
    parser.add_argument("--client", type=Path, default=root / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena")
    parser.add_argument("--api-url", help="Optional absolute HTTP(S) lobby API base URL for this ordinary client")
    args = parser.parse_args()
    args.player_name = participant_name(args.player_name)
    if len(args.room) != 6 or not args.room.isascii() or not args.room.isalnum() or not (1024 <= args.port <= 65535):
        parser.error("Invalid room code or local bridge port")
    if not args.objective.strip() or len(args.objective) > 4096:
        parser.error("Objective must contain 1-4096 characters")
    if args.api_url is not None:
        try:
            url = urlsplit(args.api_url)
            valid_url = (0 < len(args.api_url) <= 2048 and url.scheme in ("http", "https") and url.hostname
                and url.username is None and url.password is None and not url.query and not url.fragment
                and all(ord(c) >= 33 for c in args.api_url) and (url.port is None or 1 <= url.port <= 65535))
        except ValueError:
            valid_url = False
        if not valid_url:
            parser.error("--api-url must be an absolute HTTP(S) base URL without credentials, query or fragment")
    args.output = args.output.resolve()
    args.client = args.client.resolve()
    runtime = root / "build/runtime-venv/bin/actor-runtime"
    if not runtime.exists():
        parser.error("Install Runtime first: python3 -m venv build/runtime-venv; build/runtime-venv/bin/pip install -e 'runtime[dev,codex]'")
    if not args.client.exists():
        parser.error("Build the game client first with scripts/build-client.sh")
    # Refuse an occupied endpoint before launching, rather than joining another Runtime.
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error("Local bridge port is already in use")
    args.output.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, ACTOR_BRIDGE_TOKEN=secrets.token_hex(32))
    processes = []
    runtime_args = [str(runtime), args.operation, "--brain", args.brain, "--objective", args.objective,
        "--port", str(args.port), "--decision-timeout", str(args.decision_timeout),
        "--objective-timeout", str(args.objective_timeout), "--report", str(args.output / "runtime.json"),
        "--max-decisions", str(args.max_decisions),
        "--measure-allocations", "--exit-on-complete"]
    if args.operation == "match":
        runtime_args += ["--match-policy", args.match_policy]
    if args.model:
        runtime_args += ["--model", args.model]
    for flag, value in (("--config", args.config.resolve() if args.config else None),
                        ("--classifier", args.classifier), ("--memory", args.memory),
                        ("--memory-database", args.memory_database.resolve() if args.memory_database else None),
                        ("--agent-profile", args.agent_profile)):
        if value is not None:
            runtime_args += [flag, str(value)]
    if args.no_event_routing:
        runtime_args += ["--no-event-routing"]
    spawning = _RegisteredSpawn()
    signal.signal(signal.SIGTERM, spawning.interrupted)
    signal.signal(signal.SIGINT, spawning.interrupted)
    try:
        with (args.output / "runtime.log").open("w") as runtime_log:
            server = spawning.spawn(runtime_args, register=processes.append,
                cwd=root, env=environment, stdout=runtime_log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 15
        while True:
            if server.poll() is not None:
                raise RuntimeError("Runtime did not start; inspect runtime.log")
            try:
                with socket.create_connection(("127.0.0.1", args.port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Local Runtime did not start")
                time.sleep(0.1)
        with (args.output / "client-stdout.log").open("w") as client_stdout:
            client_command = [str(args.client), "--auto-room", args.room.upper(), "--player-name", args.player_name,
                "--control-mode", "actor", "--actor-endpoint", f"http://127.0.0.1:{args.port}",
                "--actor-report", str(args.output / "client.json"),
                "-screen-fullscreen", "0", "-screen-width", "800", "-screen-height", "600",
                "-logFile", str(args.output / "client.log")]
            if args.headless_client:
                client_command += ["-batchmode", "-nographics"]
            if args.api_url is not None:
                client_command += ["--api-url", args.api_url]
            client = spawning.spawn(client_command, register=processes.append,
                cwd=root, env=environment, stdout=client_stdout, stderr=subprocess.STDOUT)
        # Process/isolation evidence only; neither credential nor game payload
        # is recorded. Each launcher independently generates its own token.
        (args.output / "launch.json").write_text(json.dumps({
            "launcher_pid": os.getpid(), "runtime_pid": server.pid, "client_pid": client.pid,
            "bridge_port": args.port, "brain": args.brain,
            "operation": args.operation,
            "player_name": args.player_name,
            "headless_client": args.headless_client,
            "bridge_credential_sha256": hashlib.sha256(environment["ACTOR_BRIDGE_TOKEN"].encode()).hexdigest(),
            "memory_database": str(args.memory_database.resolve()) if args.memory_database else None,
            "agent_profile": args.agent_profile,
        }, indent=2) + "\n")
        print(f"Brain={args.brain}; client joining {args.room.upper()}; local bridge port={args.port}.", flush=True)
        while server.poll() is None:
            if client.poll() is not None:
                raise RuntimeError("Game client exited before the objective completed")
            time.sleep(0.2)
        if server.returncode != 0:
            raise RuntimeError("Objective failed safely; inspect runtime.json/runtime.log")
        print("Objective completed. Client is stopped; click Human to use the keyboard. Ctrl-C closes this launch.", flush=True)
        client.wait()
    except KeyboardInterrupt:
        pass
    except (RuntimeError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        # Interrupt the cognitive loop before closing Unity, so Skill cleanup can run.
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=16)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
