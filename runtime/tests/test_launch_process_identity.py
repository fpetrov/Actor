"""Stable process metadata across caller terminals; all ps execution is fake."""
import hashlib
import os
from types import SimpleNamespace

import pytest

from actor_runtime import launching


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process identity query")
STARTED = "Thu Oct 22 15:05:10 2026"
COMMAND = "/usr/bin/python " + "/long-owned-fixture/" * 20 + "agent.py --mode actor --tail original"


def fake_ps(monkeypatch):
    state = {"started": STARTED, "command": COMMAND}
    calls = []

    def run(command, **options):
        calls.append((command, options))
        assert command[0] == "/bin/ps" and command[2:] == ["-p", command[3], "-o", "lstart=", "-o", "command="]
        assert options["capture_output"] is True and options["text"] is True
        assert options["timeout"] == 2 and options["check"] is False
        if command[3] != "42":
            return SimpleNamespace(returncode=1, stdout="")
        environment = options.get("env", os.environ)
        started = state["started"] if environment.get("LC_ALL") == "C" else "jeu. oct. 22 15:05:10 2026"
        text = started + " " + state["command"]
        # Root's independent Linux probe established this terminal-width
        # dependency. The fake deliberately retains it unless -ww is used.
        if "-ww" not in command:
            text = text[:int(environment.get("COLUMNS", "80"))]
        return SimpleNamespace(returncode=0, stdout=text + "\n")

    monkeypatch.setattr(launching.subprocess, "run", run)
    return state, calls


@pytest.mark.parametrize("columns", ["40", "80", "120", "240"])
@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8", "ru_RU.UTF-8"])
def test_full_identity_stays_equal_across_distinct_caller_widths_locales_and_ps_environments(
        monkeypatch, columns, locale):
    _, calls = fake_ps(monkeypatch)
    monkeypatch.setenv("COLUMNS", "40")
    monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
    first = launching.process_identity(42)
    monkeypatch.setenv("COLUMNS", columns)
    monkeypatch.setenv("LC_ALL", locale)
    monkeypatch.setenv("PS_PERSONALITY", "fixture-personality")
    monkeypatch.setenv("ACTOR_BRIDGE_TOKEN", "fixture-secret")
    assert launching.owned_process(first)
    assert first == {"pid": 42, "started": STARTED,
        "command_sha256": hashlib.sha256(COMMAND.encode()).hexdigest()}
    assert len(calls) == 2
    assert all(options["env"] == {"PATH": os.defpath, "LC_ALL": "C"} for _, options in calls)
    assert all(command[1] == "-ww" for command, _ in calls)
    assert "fixture-secret" not in str(first) + str(calls)


@pytest.mark.parametrize("change", ["pid", "started", "command_tail"])
def test_stable_format_does_not_weaken_actual_pid_start_or_full_command_mismatch(monkeypatch, change):
    state, _ = fake_ps(monkeypatch)
    identity = launching.process_identity(42)
    if change == "pid":
        identity = dict(identity, pid=43)
    elif change == "started":
        state["started"] = "Thu Oct 22 15:05:11 2026"
    else:
        # A difference beyond every formerly tested terminal width must count.
        state["command"] = COMMAND.removesuffix("original") + "replacement"
        assert state["command"][:240] == COMMAND[:240]
    assert not launching.owned_process(identity)


@pytest.mark.parametrize("failure", ["nonzero", "empty", "no_command", "oserror", "timeout", "decode"])
def test_failed_or_malformed_metadata_query_fails_closed_without_retry(monkeypatch, failure):
    calls = []

    def run(command, **options):
        calls.append(command)
        if failure == "oserror":
            raise OSError("fixture")
        if failure == "timeout":
            raise launching.subprocess.TimeoutExpired(command, 2)
        if failure == "decode":
            raise UnicodeDecodeError("ascii", b"\xff", 0, 1, "fixture")
        return SimpleNamespace(returncode=1 if failure == "nonzero" else 0,
            stdout="" if failure == "empty" else STARTED if failure == "no_command" else STARTED + " " + COMMAND)

    monkeypatch.setattr(launching.subprocess, "run", run)
    assert not launching.owned_process({"pid": 42, "started": STARTED, "command_sha256": "fixture"})
    assert len(calls) == 1


@pytest.mark.parametrize("pid", [None, True, False, 0, 1, -1, "42", 42.0])
def test_invalid_pid_never_queries_ps(monkeypatch, pid):
    monkeypatch.setattr(launching.subprocess, "run", lambda *_, **__: pytest.fail("Invalid PID queried ps"))
    assert launching.process_identity(pid) is None
