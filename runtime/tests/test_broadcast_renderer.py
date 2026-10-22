"""Packaged renderer safety, without browsers, hardware, network or OBS calls."""
import ast
from importlib.resources import as_file, files
import inspect
import json
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


def test_actual_renderer_safe_watchdog_audio_readiness_and_unchanged_ack_contract():
    node = shutil.which("node")
    if node is None: pytest.skip("Node is optional for the production renderer host check")
    resources = files("actor_broadcast.resources")
    with as_file(resources.joinpath("renderer.js")) as renderer, as_file(resources.joinpath("overlay.html")) as overlay:
        run = subprocess.run([node, str(Path(__file__).with_name("broadcast_renderer.cjs")),
                              str(renderer), str(overlay)], capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"passed": True, "checks": 8,
        "scope": "production_renderer_synthetic_host_no_browser_device"}


def test_watchdog_browser_source_creation_order_prerequisite_is_voice_after_video():
    # Actual production creation order, not a fake graph's prefilled list.
    # This does NOT replace an actual OBS index/full-canvas/blend-mode audit.
    from actor_broadcast.obs import ObsController
    tree = ast.parse(textwrap.dedent(inspect.getsource(ObsController.prepare)))
    creations = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "_create_input" and call.args and isinstance(call.args[0], ast.Dict)):
            continue
        fields = dict(zip([key.value for key in call.args[0].keys], call.args[0].values))
        name = fields.get("inputName")
        if isinstance(name, ast.Attribute): creations.append((call.lineno, name.attr))
    assert [name for _, name in sorted(creations)] == ["VIDEO", "VOICE"]
