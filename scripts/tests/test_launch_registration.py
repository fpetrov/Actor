"""Signals at actual launcher spawn/log boundaries; no processes or sockets run."""
from contextlib import ExitStack, nullcontext
import importlib.util
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]


def launcher(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Child:
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.waited = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        if self.returncode is None:
            raise AssertionError("Cleanup must stop the owned child before waiting")
        self.waited = True
        return self.returncode


class ClosingLog:
    def __init__(self, file, on_close):
        self.file, self.on_close = file, on_close

    def __enter__(self):
        return self.file.__enter__()

    def __exit__(self, *exception):
        result = self.file.__exit__(*exception)
        self.on_close()
        return result


class RegisteredLaunchTests(unittest.TestCase):
    def exercise(self, script, boundary, child_index, signum):
        module = launcher(script)
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            root = Path(folder)
            (root / "scripts").mkdir()
            runtime = root / "build/runtime-venv/bin/actor-runtime"
            runtime.parent.mkdir(parents=True)
            runtime.touch()
            client = root / "ordinary-client"
            client.touch()
            output = root / "output"
            stack.enter_context(patch.object(module, "__file__", str(root / "scripts" / script)))
            handlers, children, commands, groups = {}, [], [], []
            fired = False

            def deliver():
                nonlocal fired
                if not fired:
                    fired = True
                    handlers[signum](signum, None)

            def spawn(command, **kwargs):
                child = Child(9000000 + len(children))
                children.append(child)
                commands.append((command, kwargs))
                # Inject at the Popen return boundary, after the child exists.
                if boundary == "popen" and len(children) == child_index:
                    deliver()
                return child

            original_open = Path.open

            def open_file(path, *args, **kwargs):
                file = original_open(path, *args, **kwargs)
                if boundary == "log_exit" and path.name in ("runtime.log", "client-stdout.log", "launcher.log"):
                    return ClosingLog(file, lambda: deliver() if len(children) == child_index else None)
                return file

            stack.enter_context(patch.object(module.signal, "signal", side_effect=lambda key, value: handlers.__setitem__(key, value)))
            stack.enter_context(patch.object(module.subprocess, "Popen", side_effect=spawn))
            stack.enter_context(patch.object(Path, "open", open_file))
            stack.enter_context(patch("builtins.print"))
            if script == "launch-agent.py":
                probe = type("Probe", (), {"bind": lambda self, address: None})()
                stack.enter_context(patch.object(module.socket, "socket", return_value=nullcontext(probe)))
                stack.enter_context(patch.object(module.socket, "create_connection", return_value=nullcontext()))
                stack.enter_context(patch("sys.argv", [script, "--room", "ABC123", "--brain", "codex",
                    "--objective", "Use only your own permitted observations.", "--client", str(client),
                    "--output", str(output)]))
                self.assertIsNone(module.main())
            else:
                stack.enter_context(patch.object(module.os, "killpg", side_effect=lambda pid, sig: groups.append((pid, sig))))
                self.assertEqual(module.main(["--room", "ABC123", "--brain", "codex", "--output", str(output)]), 130)
                result = json.loads((output / "supervisor.json").read_text())
                self.assertEqual(result["status"], "interrupted")
                self.assertTrue(result["owned_process_cleanup_complete"])
                self.assertFalse(result["passed"])
                if os.name == "posix":
                    self.assertEqual(groups, [(children[0].pid, signal.SIGKILL)])
                    self.assertTrue(commands[0][1]["start_new_session"])
            self.assertTrue(fired)
            self.assertEqual(len(children), child_index, "No later child may start after interruption")
            self.assertTrue(all(child.terminated and child.waited for child in children))

    def test_single_agent_runtime_and_client_registered_before_signal_or_log_close(self):
        for boundary in ("popen", "log_exit"):
            for child_index in (1, 2):
                for signum in (signal.SIGINT, signal.SIGTERM):
                    with self.subTest(boundary=boundary, child=child_index, signal=signum):
                        self.exercise("launch-agent.py", boundary, child_index, signum)

    def test_social_supervisor_group_registered_before_signal_or_log_close(self):
        for boundary in ("popen", "log_exit"):
            for signum in (signal.SIGINT, signal.SIGTERM):
                with self.subTest(boundary=boundary, signal=signum):
                    self.exercise("launch-social-agents.py", boundary, 1, signum)


if __name__ == "__main__":
    unittest.main()
