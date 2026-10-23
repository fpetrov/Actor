"""Evaluate labelled inputs without treating model predictions as labels."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .contract import ClassifierInput
from .features import render_canonical_v1
from .model import Classifier, DEFAULT_RUN


def read_rows(path):
    rows = []
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if type(row.get("label")) is not int or row["label"] not in (0, 1):
            raise ValueError(f"Line {number}: label must be integer 0 or 1")
        value = ClassifierInput.model_validate(row["input"])
        rows.append({**row, "input": value})
    if not rows:
        raise ValueError("Dataset is empty")
    return rows


def feature_hash(value):
    return hashlib.sha256(render_canonical_v1(value).encode()).hexdigest()


def metrics(labels, scores, threshold):
    counts = Counter((truth, int(score >= threshold)) for truth, score in zip(labels, scores, strict=True))
    tn, fp, fn, tp = (counts[k] for k in [(0, 0), (0, 1), (1, 0), (1, 1)])
    f1 = lambda a, b, c: 2 * a / (2 * a + b + c) if 2 * a + b + c else 0.
    return {"rows": len(labels), "confusion_matrix_true_rows_predicted_columns": [[tn, fp], [fn, tp]],
            "accuracy": (tn + tp) / len(labels), "macro_f1": (f1(tn, fp, fn) + f1(tp, fp, fn)) / 2,
            "review_precision": tp / (tp + fp) if tp + fp else 0.,
            "review_recall": tp / (tp + fn) if tp + fn else 0.,
            "missed_reviews": fn, "unnecessary_reviews": fp}


def score_rows(classifier, rows):
    import torch

    classifier.model.eval()
    scores = []
    with torch.inference_mode():
        for start in range(0, len(rows), 32):
            texts = [classifier.render(r["input"]) for r in rows[start:start + 32]]
            if any(len(classifier.tokenizer(t, truncation=False)["input_ids"]) > classifier.max_length for t in texts):
                raise ValueError("Dataset contains input over the model token limit")
            encoded = classifier.tokenizer(texts, padding=True, return_tensors="pt", truncation=False)
            device = next(classifier.model.parameters()).device
            encoded = {k: v.to(device) for k, v in encoded.items()}
            scores.extend(torch.softmax(classifier.model(**encoded).logits, dim=1)[:, 1].cpu().tolist())
    return metrics([r["label"] for r in rows], scores, classifier.threshold)


def main():
    parser = argparse.ArgumentParser(description="Evaluate a JSONL dataset; independence requires a separately designed split")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    args = parser.parse_args()
    rows = read_rows(args.dataset)
    model = Classifier(args.run)
    result = {"model_sha256": model.model_sha, "threshold": model.threshold,
              "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
              "independent_quality_claim": False, "metrics": score_rows(model, rows)}
    sets = sorted({r.get("check_set", "all") for r in rows})
    if len(sets) > 1:
        result["by_check_set"] = {name: score_rows(model, [r for r in rows if r.get("check_set", "all") == name]) for name in sets}
    print(json.dumps(result, indent=2))
