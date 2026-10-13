"""Actual launcher subprocess/signal regression; no Codex, Unity or Runtime internals mocked."""
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

import pytest


def executable(path: Path, source: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n" + source)
    path.chmod(0o755)


def eventually(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate(), "Owned fixture processes did not reach the expected lifecycle state"


def alive(pid: int):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(os.name != "posix", reason="Launcher SIGTERM semantics are tested on desktop Unix and Linux CI")
@pytest.mark.parametrize("launcher_name", ["launch-agent.py", "launch-scripted-agent.py"])
def test_sigterm_launcher_stops_owned_children_and_allows_provider_cleanup(tmp_path, launcher_name):
    repository = Path(__file__).resolve().parents[2]
    fixture = tmp_path / "fixture-repository"
    scripts = fixture / "scripts"
    scripts.mkdir(parents=True)
    launcher = scripts / launcher_name
    shutil.copyfile(repository / "scripts" / launcher_name, launcher)
    markers = fixture / "markers"
    markers.mkdir()
    provider = fixture / "fake-provider.py"
    provider.write_text('''import os, signal, time
from pathlib import Path
root = Path(os.environ["ACTOR_LAUNCH_TEST_DIR"])
def stop(signum, frame): raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
(root / "provider.pid").write_text(str(os.getpid()))
try:
    while True: time.sleep(0.02)
finally:
    (root / "provider-cleanup.marker").write_text("provider interrupted and closed")
''')
    runtime = fixture / "build/runtime-venv/bin/actor-runtime"
    executable(runtime, '''import os, signal, socket, subprocess, sys, time
from pathlib import Path
root = Path(os.environ["ACTOR_LAUNCH_TEST_DIR"])
def stop(signum, frame): raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
provider = subprocess.Popen([sys.executable, os.environ["ACTOR_LAUNCH_TEST_PROVIDER"]])
listener = socket.socket()
try:
    deadline = time.monotonic() + 3
    while not (root / "provider.pid").exists():
        if time.monotonic() >= deadline: raise RuntimeError("provider did not start")
        time.sleep(0.01)
    port = int(sys.argv[sys.argv.index("--port") + 1])
    listener.bind(("127.0.0.1", port))
    listener.listen(4)
    (root / "runtime.pid").write_text(str(os.getpid()))
    while True:
        connection, _ = listener.accept()
        connection.close()
finally:
    provider.terminate()
    provider.wait(timeout=3)
    listener.close()
    (root / "runtime-stopped.marker").write_text("runtime completed graceful provider cleanup")
''')
    client = fixture / "fake client executable"
    executable(client, '''import os, signal, time
from pathlib import Path
root = Path(os.environ["ACTOR_LAUNCH_TEST_DIR"])
def stop(signum, frame): raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
(root / "client.pid").write_text(str(os.getpid()))
try:
    while True: time.sleep(0.02)
finally:
    (root / "client-stopped.marker").write_text("client stopped")
''')
    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = port_probe.getsockname()[1]
    arguments = [sys.executable, str(launcher), "--room", "ABC123", "--port", str(port),
                 "--client", str(client), "--output", str(fixture / "reports")]
    if launcher_name == "launch-agent.py":
        arguments += ["--brain", "codex", "--objective", "Exercise graceful launcher interruption"]
    environment = dict(os.environ, ACTOR_LAUNCH_TEST_DIR=str(markers), ACTOR_LAUNCH_TEST_PROVIDER=str(provider))
    process = subprocess.Popen(arguments, cwd=tmp_path, env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    children = []
    try:
        def pid_files_ready():
            return all((markers / f"{name}.pid").exists() and (markers / f"{name}.pid").read_text().strip().isdigit()
                       for name in ("runtime", "client", "provider"))
        eventually(pid_files_ready)
        children = [int((markers / f"{name}.pid").read_text()) for name in ("runtime", "client", "provider")]
        assert all(alive(pid) for pid in children)
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=8)
        assert process.returncode == 0, output
        assert "Objective completed" not in output and "Scripted run completed" not in output
        assert (markers / "provider-cleanup.marker").read_text() == "provider interrupted and closed"
        assert (markers / "runtime-stopped.marker").exists()
        assert (markers / "client-stopped.marker").exists()
        eventually(lambda: all(not alive(pid) for pid in children))
    finally:
        # If this regression returns, clean only the fixture-owned process group.
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: pass
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait(timeout=5)
