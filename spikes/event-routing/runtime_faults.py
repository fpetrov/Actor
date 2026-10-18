"""Manual game 0.5 fault acceptance in one isolated, newly created VPS lobby.

No game diagnostic control API is used. This fixture launches the ordinary client,
then stops either its local Runtime or ONLY its own validated match-server PID.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import time
import urllib.request
import uuid

from actor_runtime.brains import BrainRegistry
from actor_runtime.extensions import ExtensionRegistry
from actor_runtime.objectives import Objective, ObjectiveRunner
from actor_runtime.server import RuntimeServer
from actor_runtime.skills import SkillRegistry


REMOTE_IDENTITY = r'''
import json, os, pathlib, pwd, re, signal, subprocess, sys, uuid
lobby, port, room, operation = sys.argv[1:]
assert uuid.UUID(lobby).hex == lobby and room.isascii() and room.isalnum() and len(room) == 6
port = int(port); assert 1024 <= port <= 65535 and operation in ('inspect', 'terminate')
lines = subprocess.check_output(['journalctl', '-u', 'actor-control-plane', '--since', '-15 minutes',
    '--no-pager', '-o', 'cat', '--grep', 'ServerProcessStarted .*' + room], text=True).splitlines()
matches = []
for line in lines:
    try: item = json.loads(line)
    except ValueError: continue
    state = item.get('State', {})
    if (str(state.get('LobbyId', '')).replace('-', '') == lobby and state.get('RoomCode') == room
            and state.get('Port') == port and 'Pid' in state): matches.append(int(state['Pid']))
assert len(set(matches)) == 1, 'Expected one matching server allocation record'
pid = matches[-1]; proc = pathlib.Path('/proc') / str(pid)
assert pid > 1 and proc.stat().st_uid == pwd.getpwnam('actor').pw_uid
executable = os.readlink(proc / 'exe'); comm = (proc / 'comm').read_text().strip()
assert pathlib.Path(executable).name == 'AgentsArenaServer' and comm in ('AgentsArenaServ', 'Unity Main Thre')
socket = subprocess.check_output(['ss', '-H', '-l', '-u', '-n', '-p', 'sport = :' + str(port)], text=True)
assert {int(x) for x in re.findall(r'pid=(\d+)', socket)} == {pid}, 'UDP port is not exclusively owned by this PID'
log = pathlib.Path('/var/log/actor/matches') / (lobby + '.log')
ready = 'ACTOR_READY match=' + lobby + ' room=' + room + ' port=' + str(port)
assert any(ready in line for line in log.read_text().splitlines()), 'Match READY identity mismatch'
result = dict(lobby_id=lobby, room=room, port=port, pid=pid, comm=comm,
              executable=executable, exact_udp_owner=True, ready_identity_verified=True)
if operation == 'terminate':
    os.kill(pid, signal.SIGTERM); result['signal'] = 'SIGTERM'
print(json.dumps(result))
'''


def request(base, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


async def api(args, path, body=None):
    return await asyncio.to_thread(request, args.base_url.rstrip("/"), path, body)


async def until(predicate, timeout=30):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.025)


def client_report(path):
    try: return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError): return None


def remote(args, lobby, action):
    identity = uuid.UUID(lobby["lobbyId"]).hex
    room, port = lobby["roomCode"], int(lobby["gamePort"])
    assert room != args.protected_room and room.isalnum() and len(room) == 6
    command = ["ssh", "-i", str(args.ssh_key.expanduser()), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        f"{args.ssh_user}@{args.host}", f"python3 - {identity} {port} {room} {action}"]
    result = subprocess.run(command, input=REMOTE_IDENTITY, text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError("Own-match server identity/termination rejected: " + result.stderr[-1000:])
    return json.loads(result.stdout)


async def stop_client(client):
    if client is None or client.poll() is not None: return
    client.terminate()
    try: await asyncio.wait_for(asyncio.to_thread(client.wait), timeout=8)
    except TimeoutError:
        client.kill(); await asyncio.to_thread(client.wait)


async def scenario(args, lobby, kind):
    output = args.output / kind
    output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / ("bridge-disconnect-safety.json" if kind == "bridge" else "multiplayer-disconnect-safety.json")
    report = {"scenario": kind + "_disconnect_during_navigation", "passed": False,
        "lobby": lobby, "protected_room": args.protected_room, "port": args.port}
    token = secrets.token_hex(32)
    server = RuntimeServer(ExtensionRegistry.discover(), token, port=args.port)
    client = task = runner = session = None
    try:
        await server.start()
        root = Path(__file__).resolve().parents[2]
        with (output / "client-stdout.log").open("w") as stream:
            client = subprocess.Popen([str(args.client.resolve()), "--auto-room", lobby["roomCode"],
                "--player-name", "Isolated Safety Pilot", "--control-mode", "actor",
                "--actor-endpoint", f"http://127.0.0.1:{args.port}", "--actor-report", str(output / "client.json"),
                "-screen-fullscreen", "0", "-screen-width", "640", "-screen-height", "360",
                "-logFile", str(output / "client.log")], cwd=root, env=dict(os.environ, ACTOR_BRIDGE_TOKEN=token),
                stdout=stream, stderr=subprocess.STDOUT)
        report["client_pid"] = client.pid
        session = await server.next_session(timeout=40)
        await session.world.wait_until(lambda world: len(world.observations) >= 3, timeout=10)
        runner = ObjectiveRunner(session, SkillRegistry.discover(), BrainRegistry.discover().create("scripted"))
        task = asyncio.create_task(runner.run(Objective(
            text="Go to Engineering, wait there for 2 seconds, then return to Main Hall and finish.")))
        await until(lambda: session.commands_sent >= 12, timeout=20)
        report["commands_before_fault"] = session.commands_sent
        report["unity_before_fault"] = client_report(output / "client.json")
        fault_time = time.monotonic()
        if kind == "bridge":
            await server.stop()
        else:
            report["server_identity"] = await asyncio.to_thread(remote, args, lobby, "terminate")
        result = await asyncio.wait_for(asyncio.shield(task), timeout=25)
        report["objective"] = result.model_dump(mode="json")
        report["seconds_to_objective_terminal"] = time.monotonic() - fault_time
        assert result.status == "failed", result.model_dump(mode="json")
        history = runner.report()["scheduler"]["skills"]
        assert history and history[-1]["status"] in ("failed", "cancelled")
        if kind == "bridge":
            assert result.reason == "session_disconnected"
        else:
            assert history[-1]["reason"] in ("session_disconnected", "stale_world_state", "command_rejected")
        stable_commands = session.commands_sent
        await asyncio.sleep(1)
        assert session.commands_sent == stable_commands, "Commands continued after Skill cleanup"
        report["commands_after_cleanup"] = stable_commands
        report["commands_one_second_later"] = session.commands_sent
        stopped = None
        async with asyncio.timeout(25):
            while True:
                await asyncio.sleep(.1)
                stopped = client_report(output / "client.json")
                if stopped is None: continue
                velocity = stopped["velocity"]
                finite_stopped = all(math.isfinite(velocity[k]) for k in ("x", "y")) and math.hypot(velocity["x"], velocity["y"]) < .05
                disconnected = stopped["state"] == "Error" if kind == "bridge" else not stopped.get("actorId")
                if finite_stopped and disconnected: break
        assert client.poll() is None and stopped is not None
        assert stopped["meanFps"] > 30 and not stopped["keyboardEnabled"]
        if kind == "bridge": assert not stopped["errors"]
        report["unity_continues_running"] = True
        report["unity_safe_stop"] = stopped
        report["seconds_to_unity_stopped_report"] = time.monotonic() - fault_time
        report["cognitive"] = runner.report()
        report["runtime"] = server.report()
        report["passed"] = True
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
        if runner is not None: report["cognitive"] = runner.report()
        report["last_client_report"] = client_report(output / "client.json")
        raise
    finally:
        if task is not None and not task.done():
            task.cancel(); await asyncio.gather(task, return_exceptions=True)
        await server.stop()
        await stop_client(client)
        report["owned_client_cleaned"] = client is None or client.poll() is not None
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"scenario": kind, "passed": report["passed"], "report": str(report_path)}), flush=True)


async def main(args):
    args.output = args.output.resolve(); args.output.mkdir(parents=True, exist_ok=True)
    version = await api(args, "/api/v1/game/version")
    assert version["gameVersion"] == "0.5.0" and version["protocolVersion"] == "3"
    before = await api(args, "/api/v1/lobbies/" + args.protected_room)
    if args.room:
        lobby = await api(args, "/api/v1/lobbies/" + args.room)
        assert lobby["displayName"] == "Milestone4Safety0.5" and lobby["maxPlayers"] == 2
    else:
        lobby = await api(args, "/api/v1/lobbies", {"displayName": "Milestone4Safety0.5", "maxPlayers": 2,
            "gameVersion": version["gameVersion"], "protocolVersion": version["protocolVersion"]})
    assert lobby["roomCode"] != args.protected_room
    (args.output / "safety-lobby.json").write_text(json.dumps(lobby, indent=2) + "\n")
    print(json.dumps({"created_safety_room": lobby["roomCode"], "lobby_id": lobby["lobbyId"],
        "game_port": lobby["gamePort"]}), flush=True)
    terminated = False
    try:
        async with asyncio.timeout(45):
            while True:
                live = await api(args, "/api/v1/lobbies/" + lobby["roomCode"])
                if live["status"] in ("Ready", "Running"): break
                await asyncio.sleep(.5)
        identity = await asyncio.to_thread(remote, args, lobby, "inspect")
        (args.output / "safety-server-identity.json").write_text(json.dumps(identity, indent=2) + "\n")
        await scenario(args, lobby, "bridge")
        await scenario(args, lobby, "multiplayer")
        terminated = True
    finally:
        if not terminated:
            # Never kill by name/port wildcard. The same exact identity guard
            # applies to failure cleanup; refuse cleanup if identity is ambiguous.
            try: await asyncio.to_thread(remote, args, lobby, "terminate")
            except Exception as error: print("Own-match cleanup status: " + type(error).__name__, flush=True)
        after = await api(args, "/api/v1/lobbies/" + args.protected_room)
        assert before["lobbyId"] == after["lobbyId"] and after["status"] in ("Ready", "Running")
        (args.output / "protected-room-unchanged.json").write_text(json.dumps({"before": before, "after": after}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://5.181.134.236")
    parser.add_argument("--host", default="5.181.134.236")
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument("--ssh-key", type=Path, default=Path("~/.ssh/agents-arena"))
    parser.add_argument("--port", type=int, default=50052)
    parser.add_argument("--room", help="Resume this fixture's own named safety lobby after a diagnostic failure")
    parser.add_argument("--protected-room", default="J78LRT")
    parser.add_argument("--output", type=Path, default=Path("build/event-validation"))
    parser.add_argument("--client", type=Path, default=Path("build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena"))
    asyncio.run(main(parser.parse_args()))
