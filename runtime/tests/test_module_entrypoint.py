"""Exercise the subprocess entry point used by actorctl join."""

import subprocess
import sys


def test_runtime_module_exposes_cli():
    result = subprocess.run(
        [sys.executable, "-m", "actor_runtime", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "actor-runtime" in result.stdout
    assert "--brain" in result.stdout
    assert "--config" in result.stdout
