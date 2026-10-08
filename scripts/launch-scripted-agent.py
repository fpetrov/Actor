#!/usr/bin/env python3
"""Launch a local scripted Runtime and an ACTOR-controlled multiplayer client."""
import argparse
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--room", required=True, help="Six-character room code from the website or Unity lobby")
parser.add_argument("--player-name", default="Agent Pilot")
parser.add_argument("--port", type=int, default=50051)
parser.add_argument("--output", type=Path, default=root / "build/actor-validation")
parser.add_argument("--client", type=Path, default=root / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena")
args = parser.parse_args()
if len(args.room) != 6 or not args.room.isalnum() or not (1024 <= args.port <= 65535):
    parser.error("Invalid room code or local bridge port")
runtime = root / "build/runtime-venv/bin/actor-runtime"
if not runtime.exists():
    parser.error("Install runtime first: python3 -m venv build/runtime-venv; build/runtime-venv/bin/pip install -e 'runtime[dev]'")
if not args.client.exists():
    parser.error("Build the client first with scripts/build-client.sh")
args.output.mkdir(parents=True, exist_ok=True)
environment = dict(os.environ, ACTOR_BRIDGE_TOKEN=secrets.token_hex(32))
processes = []
try:
    runtime_log = (args.output / "runtime.log").open("w")
    server = subprocess.Popen([str(runtime), "scripted", "agents-arena", "--port", str(args.port),
        "--report", str(args.output / "runtime.json"), "--measure-allocations", "--exit-on-complete"], cwd=root, env=environment,
        stdout=runtime_log, stderr=subprocess.STDOUT)
    processes.append(server)
    deadline = time.monotonic() + 10
    while True:
        if server.poll() is not None:
            raise RuntimeError("Runtime did not start; inspect the development log.")
        try:
            with socket.create_connection(("127.0.0.1", args.port), timeout=0.2):
                break
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Local Runtime did not start")
            time.sleep(0.1)
    client = subprocess.Popen([str(args.client), "--auto-room", args.room.upper(), "--player-name", args.player_name,
        "--control-mode", "actor", "--actor-endpoint", f"http://127.0.0.1:{args.port}",
        "--actor-report", str(args.output / "client.json"), "-logFile", str(args.output / "client.log")], cwd=root, env=environment)
    processes.append(client)
    print(f"Runtime listening on loopback:{args.port}; client joining {args.room.upper()}. Token stays in process environments.", flush=True)
    while server.poll() is None:
        if client.poll() is not None:
            raise RuntimeError("Game client exited before the scripted run completed")
        time.sleep(0.2)
    if server.returncode != 0:
        raise RuntimeError("Scripted run failed; inspect the Runtime report/log.")
    print("Scripted run completed. The client remains available in stopped ACTOR mode; use Human to resume keyboard control.", flush=True)
    # Keep the launcher alive so Ctrl-C still owns and cleans up its development processes.
    client.wait()
except KeyboardInterrupt:
    pass
finally:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
