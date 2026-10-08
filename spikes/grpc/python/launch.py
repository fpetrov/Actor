"""Generate an ignored fixture token, then exec the foreground fixture server."""

import argparse
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=50061)
parser.add_argument("--tick-seconds", type=float, default=0.1)
args = parser.parse_args()
work = root / "build/grpc-spike"
work.mkdir(parents=True, exist_ok=True)
token_path = work / "token"
if token_path.exists():
    token = token_path.read_text().strip()
    if len(token) < 32:
        raise ValueError("Existing fixture token is invalid")
else:
    token = secrets.token_hex(32)
    fd = os.open(token_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(token + "\n")
token_path.chmod(0o600)
environment = os.environ.copy()
environment["ACTOR_SPIKE_TOKEN"] = token
python = str(work / "venv/bin/python")
os.chdir(root)
os.execve(python, [python, str(root / "spikes/grpc/python/server.py"),
                  "--port", str(args.port), "--tick-seconds", str(args.tick_seconds),
                  "--status", str(work / "python-status.json")], environment)
