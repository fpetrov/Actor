from pathlib import Path
from importlib.resources import as_file, files
import json
import shutil
import subprocess

import pytest


def test_real_audio_worklet_chunk_continuity_cancel_truncation_and_deadman():
    node = shutil.which("node")
    if node is None: pytest.skip("Node is optional for the production JS processor check")
    root = Path(__file__).resolve()
    # Test the actual imported package asset in both checkout and wheel runs.
    # as_file also supports a resource supplied by a non-filesystem loader.
    with as_file(files("actor_broadcast.resources").joinpath("audio-worklet.js")) as processor:
        run = subprocess.run([node, str(root.with_name("broadcast_worklet.cjs")), str(processor)],
            capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"passed": True, "checks": 6, "scope": "production_worklet_synthetic_host_no_device"}
