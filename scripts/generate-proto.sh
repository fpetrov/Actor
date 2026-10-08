#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
task_python="${PYTHON:-python3}"
"$task_python" -c 'import sys; assert sys.version_info >= (3, 12), "ACTOR code generation requires Python 3.12+. Set PYTHON to its executable."'
task_venv="$task_root/build/proto-tools/venv"
if [[ ! -x "$task_venv/bin/python" ]]; then
  "$task_python" -m venv "$task_venv"
fi
if ! "$task_venv/bin/python" -c 'from importlib.metadata import version; assert all(version(name) == expected for name, expected in {"grpcio":"1.82.2", "grpcio-tools":"1.82.2", "protobuf":"7.36.2", "setuptools":"84.0.0", "typing-extensions":"4.16.0"}.items())' 2>/dev/null; then
  "$task_venv/bin/python" -m pip install --disable-pip-version-check -r "$task_root/scripts/generate-proto-requirements.txt"
fi
exec "$task_venv/bin/python" "$task_root/scripts/generate-proto.py" "$@"
