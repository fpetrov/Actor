"""Print fixture diagnostics without exposing authentication configuration."""

import json
import os
from pathlib import Path

root = Path(__file__).resolve().parents[3]
path = root / "build/grpc-spike/python-status.json"
if not path.exists():
    print(json.dumps({"state": "not_started"}))
else:
    status = json.loads(path.read_text())
    try:
        os.kill(status["pid"], 0)
        status["process_running"] = True
    except ProcessLookupError:
        status["process_running"] = False
    except PermissionError:
        status["process_running"] = None
        status["process_check"] = "sandbox permission denied"
    print(json.dumps(status, indent=2))
