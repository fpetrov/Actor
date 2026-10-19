"""Live M5 acceptance through independent launchers and ordinary VPS lobbies.

The supervisor handles startup, metadata-only acceptance and owned cleanup.
Authorization and messages never pass through this process.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
MODES = {"scripted": ("scripted", "scripted"), "mixed-a": ("codex", "scripted"),
         "mixed-b": ("scripted", "codex"), "codex": ("codex", "codex")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--output", type=Path, default=ROOT / "build/cooperative-validation")
    parser.add_argument("--base-url", default="http://5.181.134.236")
    parser.add_argument("--model")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)

    def request(path, data=None):
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(args.base_url.rstrip("/") + path, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    assert request("/api/v1/game/version")["gameVersion"] == "0.6.0"
    process = None
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        for case in args.cases:
            output = args.output / case
            assert not output.exists() or not any(output.iterdir()), "Choose a fresh case output directory"
            lobby = request("/api/v1/lobbies", {"displayName": "M5 " + case, "maxPlayers": 8,
                "gameVersion": "0.6.0", "protocolVersion": "3"})
            (args.output / (case + "-lobby.json")).write_text(json.dumps(lobby, indent=2) + "\n")
            deadline = time.monotonic() + 50
            while lobby["status"] not in ("Ready", "Running"):
                assert time.monotonic() < deadline, "Lobby startup deadline"
                time.sleep(.5)
                lobby = request("/api/v1/lobbies/" + lobby["roomCode"])
            a, b = MODES[case]
            command = [sys.executable, str(ROOT / "scripts/launch-cooperative-agents.py"),
                "--room", lobby["roomCode"], "--output", str(output),
                "--coordinator-brain", a, "--technician-brain", b, "--exit-on-complete"]
            if args.model:
                command += ["--model", args.model]
            with (args.output / (case + "-supervisor.log")).open("w") as log:
                process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            print(json.dumps({"case": case, "room": lobby["roomCode"], "supervisor_pid": process.pid}), flush=True)
            result = process.wait(timeout=700)
            assert result == 0, "Independent agent objective failed; inspect safe local reports"
            with (args.output / (case + "-summary.stdout.json")).open("w") as stream:
                subprocess.run([sys.executable, str(ROOT / "scripts/summarize-cooperation.py"), str(output),
                    "--output", str(args.output / (case + "-summary.json")), "--require-success"],
                    cwd=ROOT, stdout=stream, check=True)
            launches = [json.loads((output / role / "launch.json").read_text())
                        for role in ("coordinator", "technician")]
            assert len({item["runtime_pid"] for item in launches}) == 2
            assert len({item["client_pid"] for item in launches}) == 2
            assert len({item["bridge_credential_sha256"] for item in launches}) == 2
            (args.output / (case + "-processes.json")).write_text(json.dumps({
                "runtime_count": 2, "unity_count": 2, "independent_ephemeral_credentials": True,
                "launches": launches}, indent=2) + "\n")
            print(json.dumps({"case": case, "passed": True, "summary": str(args.output / (case + "-summary.json"))}), flush=True)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
