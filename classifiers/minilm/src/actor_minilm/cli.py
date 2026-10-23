from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .contract import ClassifierInput
from .model import Classifier, DEFAULT_RUN
from .projection import project


def main():
    parser = argparse.ArgumentParser(description="Predict from a canonical input or one captured Runtime row; no game actions")
    parser.add_argument("input", type=Path)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--runtime-row", action="store_true", help="Input is one JSON capture object, not a JSONL file")
    args = parser.parse_args()
    raw = json.loads(args.input.read_text())
    value = project(raw) if args.runtime_row else ClassifierInput.model_validate(raw)
    model = Classifier(args.run)
    print(json.dumps({**model.predict(value), "model_input": value.model_dump(),
                      "rendered_input_sha256": hashlib.sha256(model.render(value).encode()).hexdigest()}, indent=2))
