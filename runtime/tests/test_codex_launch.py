"""Real stand-in child processes only: never starts Codex/auth/model/network."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from actor_codex import launch
from actor_runtime.errors import ActorError


def bundle(monkeypatch, tmp_path, *, support=True):
    root = tmp_path / "official bundle with spaces"; root.mkdir()
    executable = root / "codex stand-in"
    executable.write_text("#!" + sys.executable + "\n" + """import json, os, sys
print(json.dumps({'environment': dict(os.environ), 'arguments': sys.argv[1:]}))
""")
    executable.chmod(0o700)
    path = root / "codex-path"; path.mkdir()
    monkeypatch.setattr(launch, "version", lambda name: launch.PINNED_SDK_VERSION)
    module = SimpleNamespace(bundled_codex_path=lambda: executable, bundled_path_dir=lambda: path if support else None)
    monkeypatch.setattr(launch.importlib, "import_module", lambda name: module if name == "codex_cli_bin" else pytest.fail("Only public bundle API allowed"))
    return executable, path


@pytest.mark.parametrize("support", [True, False])
def test_actual_final_child_drops_inherited_secrets_preserves_parent_and_exact_argv(tmp_path, monkeypatch, support):
    executable, path = bundle(monkeypatch, tmp_path, support=support)
    forbidden = {"ACTOR_BRIDGE_TOKEN", "ACTOR_OBSERVER_ADMISSION", "OBS_WEBSOCKET_PASSWORD", "TWITCH_ACCESS_TOKEN",
        "OPENAI_API_KEY", "SSH_AUTH_SOCK", "SSH_ASKPASS", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "UNFAMILIAR_FUTURE_CREDENTIAL", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT"}
    for index, key in enumerate(sorted(forbidden)): monkeypatch.setenv(key, "sentinel-private-" + str(index))
    for key in ("HOME", "CODEX_HOME", "XDG_CONFIG_HOME", "SSL_CERT_FILE"):
        monkeypatch.setenv(key, str(tmp_path / (key + " path with spaces")))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    parent = dict(os.environ)
    config = SimpleNamespace(codex_bin=None, launch_args_override=None,
        config_overrides=("features.shell_tool=false", 'mcp_servers={}', 'instructions="bounded words with spaces"'))
    args = launch.codex_launch_args(config)
    assert args[:4] == (sys.executable, "-I", "-S", "-c")
    assert str(executable) == args[5] and all(parent[key] not in repr(args) for key in forbidden)
    # Model the official SDK's actual merge behaviour rather than asserting
    # that the caller's filtered config.env could replace inherited variables.
    merged = parent.copy(); merged.update(launch.filtered_environment())
    result = subprocess.run(args, env=merged, cwd=tmp_path, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    child = json.loads(result.stdout)
    assert forbidden.isdisjoint(child["environment"])
    assert child["environment"]["HOME"] == parent["HOME"] and child["environment"]["CODEX_HOME"] == parent["CODEX_HOME"]
    assert child["environment"]["PATH"] == (str(path) + os.pathsep if support else "") + parent["PATH"]
    assert child["environment"]["LANG"] == "ru_RU.UTF-8"
    expected = []
    for value in config.config_overrides: expected.extend(("--config", value))
    assert child["arguments"] == expected + ["app-server", "--listen", "stdio://"]
    assert dict(os.environ) == parent and config.launch_args_override is None


@pytest.mark.parametrize("mutation,code", [
    ({"codex_bin":"/untrusted/codex"}, "codex_launch_override_forbidden"),
    ({"launch_args_override":("/untrusted/codex",)}, "codex_launch_override_forbidden"),
    ({"config_overrides":("bad\0value",)}, "codex_launch_config_invalid"),
])
def test_foreign_or_malformed_launch_refused_before_bundle_resolution(monkeypatch, mutation, code):
    monkeypatch.setattr(launch, "version", lambda _: pytest.fail("Must reject before inspecting a bundle"))
    config = SimpleNamespace(codex_bin=None, launch_args_override=None, config_overrides=())
    for name,value in mutation.items(): setattr(config,name,value)
    with pytest.raises(ActorError) as error: launch.codex_launch_args(config)
    assert error.value.code == code


def test_dependency_version_mismatch_refuses_even_available_binary(tmp_path, monkeypatch):
    bundle(monkeypatch, tmp_path)
    monkeypatch.setattr(launch, "version", lambda name: "0.163.0" if name == "openai-codex-cli-bin" else launch.PINNED_SDK_VERSION)
    with pytest.raises(ActorError) as error: launch.codex_launch_args(SimpleNamespace(config_overrides=()))
    assert error.value.code == "codex_version_mismatch"
