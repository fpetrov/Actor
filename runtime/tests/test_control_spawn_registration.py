"""Real actorctl join/cleanup with fake children, sockets and signal delivery."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest

from actor_runtime import control


class ClosingLog:
    def __init__(self, file, after_close):
        self.file, self.after_close = file, after_close

    def __enter__(self):
        return self.file.__enter__()

    def __exit__(self, *exception):
        result = self.file.__exit__(*exception)
        self.after_close()
        return result


@pytest.mark.parametrize("boundary", ["popen_return", "log_close", "identity_lookup"])
@pytest.mark.parametrize("child_index", [1, 2])
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_join_registers_each_created_child_before_interrupt_and_cleans_in_order(
        tmp_path, monkeypatch, boundary, child_index, signum):
    children, commands, cleanup = [], [], []
    previous = {signal.SIGINT: lambda *_: None, signal.SIGTERM: lambda *_: None}
    handlers = dict(previous)
    fired = False

    def set_handler(number, handler):
        old = handlers[number]
        handlers[number] = handler
        return old

    def deliver():
        nonlocal fired
        if not fired:
            fired = True
            handlers[signum](signum, None)

    class Child:
        def __init__(self, index):
            self.pid = 9000000 + index
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            # Existing repeated-signal protection must cover both children.
            assert handlers[signal.SIGINT] == handlers[signal.SIGTERM] == signal.SIG_IGN
            cleanup.append(("terminate", self.pid))
            self.returncode = -15

        def wait(self, timeout=None):
            assert self.returncode is not None
            cleanup.append(("wait", self.pid))
            return self.returncode

    def spawn(command, **kwargs):
        child = Child(len(children))
        children.append(child)
        commands.append((command, kwargs))
        if boundary == "popen_return" and len(children) == child_index:
            deliver()  # Child exists; the caller has not received its handle yet.
        return child

    original_open = Path.open

    def open_file(path, *args, **kwargs):
        file = original_open(path, *args, **kwargs)
        if boundary == "log_close" and path.name in ("runtime.log", "client-stdout.log"):
            return ClosingLog(file, lambda: deliver() if len(children) == child_index else None)
        return file

    def identity(pid):
        if (boundary == "identity_lookup" and len(children) == child_index
                and pid == children[-1].pid):
            deliver()
        return {"pid": pid, "started": "fixture", "command_sha256": "fixture"}

    profile = SimpleNamespace(default_brain="codex", match_policy="fixture-policy",
        client_arguments=lambda **options: ["--auto-room", options["room"], "--control-mode", "actor"])
    monkeypatch.setattr(control, "profiles", lambda: {"fixture-game": profile})
    monkeypatch.setattr(control.metadata, "entry_points", lambda **_: [object()])
    monkeypatch.setattr(control, "installed", lambda _: "fixture-installed")
    monkeypatch.setattr(control, "resolve_client", lambda *_, **__: (tmp_path / "client", {"integrity": "fixture"}))
    monkeypatch.setattr(control, "process_identity", identity)
    monkeypatch.setattr(control.subprocess, "Popen", spawn)
    monkeypatch.setattr(control.signal, "signal", set_handler)
    monkeypatch.setattr(Path, "open", open_file)
    probe = SimpleNamespace(bind=lambda address: None, getsockname=lambda: ("127.0.0.1", 52000))
    monkeypatch.setattr(control.socket, "socket", lambda: nullcontext(probe))
    monkeypatch.setattr(control.socket, "create_connection", lambda *_, **__: nullcontext())
    output = tmp_path / "run"
    goal = "Pursue your own side using only your permitted observations."
    args = SimpleNamespace(game="fixture-game", brain="codex", room="ABC123", port=0,
        client=None, client_manifest=None, client_sha256=None, api_url=None, config=None,
        objective=goal, player_name="Agent Probe", output=output, operation="match",
        match_policy=None, model="gpt-6-luna", memory="sqlite", headless=True, keep_client_open=False)

    assert control.join(args) == 130
    assert fired and len(children) == child_index  # No later spawn after interruption.
    assert cleanup == [operation for child in children for operation in
        (("terminate", child.pid), ("wait", child.pid))]
    assert all(child.poll() == -15 for child in children)
    assert handlers == previous  # Restore the caller's exact handlers after cleanup.
    state = json.loads((output / "launch.json").read_text())
    assert state["state"] == "interrupted" and state["reason"] == "user_stop"
    assert state["cleanup_complete"] is True and not state.get("cleanup_errors")
    runtime = commands[0][0]
    for option, value in (("--brain", "codex"), ("--objective", goal), ("--model", "gpt-6-luna"),
                          ("--match-policy", "fixture-policy"), ("--memory", "sqlite")):
        assert runtime[runtime.index(option) + 1] == value
    assert "ACTOR_BRIDGE_TOKEN" not in json.dumps(state) and goal not in json.dumps(state)
    assert all(len(options["env"]["ACTOR_BRIDGE_TOKEN"]) == 64 for _, options in commands)
    assert len({options["env"]["ACTOR_BRIDGE_TOKEN"] for _, options in commands}) == 1
    assert state["processes"]["launcher"]["pid"] == os.getpid()
