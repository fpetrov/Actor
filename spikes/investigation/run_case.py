"""Authorized VPS acceptance fixture; no scenario information reaches a Brain.

Creates an ordinary room, writes trusted match-specific server conditions over
SSH, then starts independent clients through the production launchers. The Crew
operator always adjusts public circuit alpha without observing the active loop.
Raw local artifacts remain under ignored build/; publish only the safe summary.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import uuid


def request(base, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def configure(args, lobby):
    match = uuid.UUID(lobby["lobbyId"]).hex
    # No hidden-origin input exists. The regular physical intervention either
    # touches the active loop or standby; the natural clock is server-owned.
    settings = dict(seed=72, naturalEnabled=args.scenario == "mechanical",
        naturalDelayMs=30000, incidentTtlMs=480000, repairDurationMs=2000,
        activeCircuit="beta" if args.scenario == "mechanical" else "alpha")
    script = """
import json,os,pathlib,pwd,sys,uuid
match,encoded=sys.argv[1:]
assert uuid.UUID(match).hex==match
value=json.loads(encoded);assert set(value)=={'seed','naturalEnabled','naturalDelayMs','incidentTtlMs','repairDurationMs','activeCircuit'}
directory=pathlib.Path('/etc/actor/incident-scenarios');directory.mkdir(mode=0o750,exist_ok=True)
gid=pwd.getpwnam('actor').pw_gid
os.chown(directory,0,gid);os.chmod(directory,0o750)
path=directory/(match+'.json')
with path.open('x') as stream: json.dump(value,stream)
os.chown(path,0,gid);os.chmod(path,0o640)
print('Trusted match conditions installed')
"""
    import shlex
    remote = "python3 - " + shlex.quote(match) + " " + shlex.quote(json.dumps(settings, separators=(",", ":")))
    subprocess.run(["ssh", "-i", str(args.ssh_key.expanduser()), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
        args.ssh_user + "@" + args.host, remote], input=script, text=True, check=True, timeout=25)


def stop_owned(processes):
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 45
    for process in processes:
        try:
            process.wait(timeout=max(.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            else: process.kill()
            process.wait()
    if os.name == "posix":
        # A crashed launcher may already have exited while its children remain
        # in the new process group created exclusively by this fixture.
        for process in processes:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("mechanical", "manual"), required=True)
    parser.add_argument("--engineer-brain", choices=("scripted", "codex"), default="scripted")
    parser.add_argument("--sheriff-brain", choices=("scripted", "codex"), default="scripted")
    parser.add_argument("--claimant", action="store_true")
    parser.add_argument("--human-observer", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--base-port", type=int, default=50051)
    parser.add_argument("--base-url", default="http://5.181.134.236")
    parser.add_argument("--host", default="5.181.134.236")
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument("--ssh-key", type=Path, default=Path("~/.ssh/agents-arena"))
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()): parser.error("Fresh output directory required")
    output.mkdir(parents=True, exist_ok=True)
    processes = []
    def interrupted(_signal, _frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        version = request(args.base_url, "/api/v1/game/version")
        assert version["gameVersion"] == "0.7.0" and version["protocolVersion"] == "3", "Deployment version mismatch"
        lobby = request(args.base_url, "/api/v1/lobbies", dict(displayName="M6 Investigation", maxPlayers=8,
            gameVersion="0.7.0", protocolVersion="3"))
        (output / "lobby.json").write_text(json.dumps(lobby, indent=2) + "\n")
        configure(args, lobby)
        deadline = time.monotonic() + 60
        while True:
            lobby = request(args.base_url, "/api/v1/lobbies/" + lobby["roomCode"])
            if lobby["status"] == "Ready": break
            if time.monotonic() >= deadline: raise TimeoutError("Game server readiness deadline")
            time.sleep(.5)
        print("Ordinary room ready: " + lobby["roomCode"], flush=True)
        def launch(command, name):
            with (output / (name + ".log")).open("w") as log:
                process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            processes.append(process); return process
        python = sys.executable
        if args.human_observer:
            launch([str(root / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena"),
                "--auto-room", lobby["roomCode"], "--player-name", "Human Observer", "--control-mode", "human",
                "--actor-report", str(output / "human.json"), "-logFile", str(output / "human-client.log")], "human-stdout")
        crew = launch([python, str(root / "scripts/launch-agent.py"), "--room", lobby["roomCode"],
            "--brain", "investigation-claimant" if args.claimant else "maintenance-operator",
            "--objective", "Perform ordinary panel maintenance using circuit alpha, then observe the public incident lifecycle.",
            "--player-name", "Crew Operator", "--port", str(args.base_port + 2), "--output", str(output / "crew"),
            "--objective-timeout", "600", "--max-decisions", "12"], "crew-launcher")
        command = [python, str(root / "scripts/launch-investigation-agents.py"), "--room", lobby["roomCode"],
            "--engineer-brain", args.engineer_brain, "--sheriff-brain", args.sheriff_brain,
            "--base-port", str(args.base_port), "--output", str(output / "pair"), "--exit-on-complete"]
        if args.config: command += ["--config", str(args.config.resolve())]
        pair = launch(command, "pair-launcher")
        pair_code = pair.wait(timeout=780)
        print("Investigator pair exit=" + str(pair_code), flush=True)
        # Crew is an ordinary additional actor, not an oracle or dispatcher.
        # Its own completed objective is checked separately from pair acceptance.
        crew_report = output / "crew/runtime.json"
        crew_ok = False
        crew_deadline = time.monotonic() + 12
        while time.monotonic() < crew_deadline:
            try:
                value = json.loads(crew_report.read_text())["objective"]
                status = value["result"]["status"]
                if status in ("succeeded", "failed", "cancelled"):
                    crew_ok = value["passed"] is True and status == "succeeded"
                    break
            except (OSError, ValueError, KeyError, TypeError): pass
            if crew.poll() is not None: break
            time.sleep(.1)
        (output / "fixture-result.json").write_text(json.dumps(dict(scenario=args.scenario, claimant=args.claimant,
            human_observer=args.human_observer, pair_exit_code=pair_code, crew_objective_passed=crew_ok), indent=2) + "\n")
        return 0 if pair_code == 0 and crew_ok else 1
    except KeyboardInterrupt:
        return 130
    finally:
        stop_owned(processes)


if __name__ == "__main__":
    raise SystemExit(main())
