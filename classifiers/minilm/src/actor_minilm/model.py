"""Frozen binary BERT inference using the same renderer as training."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .contract import ClassifierInput
from .features import get_renderer

DEFAULT_RUN = Path(__file__).resolve().parent / "checkpoints/v7"


class Classifier:
    def __init__(self, run: Path = DEFAULT_RUN):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        run = Path(run)
        manifest = run / "manifest.json"
        if manifest.exists():
            for name, expected in json.loads(manifest.read_text())["files"].items():
                if hashlib.sha256((run / name).read_bytes()).hexdigest() != expected:
                    raise ValueError(f"Checkpoint file SHA-256 mismatch: {name}")
        self.report = json.loads((run / "report.json").read_text())
        if self.report["architecture"] != "bert_sequence_classifier":
            raise ValueError("This package supports BERT sequence-classification checkpoints only")
        self.model_sha = hashlib.sha256((run / "model/model.safetensors").read_bytes()).hexdigest()
        if self.model_sha != self.report["model_sha256"]:
            raise ValueError("Model SHA-256 does not match report.json")
        self.render = get_renderer(self.report["renderer"])
        self.max_length = self.report["max_sequence_length"]
        self.threshold = self.report["threshold"]
        if not 0 < self.threshold < 1:
            raise ValueError("Invalid review threshold")
        torch.set_num_threads(2)
        self.model = AutoModelForSequenceClassification.from_pretrained(run / "model", local_files_only=True).cpu().eval()
        self.tokenizer = AutoTokenizer.from_pretrained(run / "model", local_files_only=True)

    def predict(self, value: ClassifierInput):
        import torch

        text = self.render(value)
        encoded = self.tokenizer(text, return_tensors="pt", truncation=False)
        token_count = encoded["input_ids"].shape[1]
        if token_count > self.max_length:
            raise ValueError(f"Input has {token_count} tokens; maximum is {self.max_length}. Refusing silent truncation")
        device = next(self.model.parameters()).device
        encoded = {key: value.to(device) for key, value in encoded.items()}
        with torch.inference_mode():
            score = float(torch.softmax(self.model(**encoded).logits, dim=1)[0, 1])
        if not math.isfinite(score):
            raise ValueError("Non-finite model score")
        return {"should_request_brain_review": score >= self.threshold, "review_score": score,
                "threshold": self.threshold, "status": self.report["status"], "schema_version": value.schema_version,
                "token_count": token_count, "scores_are_calibrated": False, "model_sha256": self.model_sha}
