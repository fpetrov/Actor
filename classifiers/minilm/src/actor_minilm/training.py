"""Portable continuation of a local checkpoint; the selected checkpoint is never overwritten."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time

from .evaluation import read_rows, feature_hash, score_rows
from .model import Classifier, DEFAULT_RUN


def validate_split(train, validation):
    groups = [{r.get("group") for r in rows} for rows in (train, validation)]
    if any(not isinstance(g, str) or not g for group in groups for g in group):
        raise ValueError("Every row needs a nonempty group; use the same group for all actors/events of a match")
    if groups[0] & groups[1]:
        raise ValueError("Training and validation groups overlap")
    hashes = [{feature_hash(r["input"]) for r in rows} for rows in (train, validation)]
    if hashes[0] & hashes[1]:
        raise ValueError("Identical rendered inputs cross training and validation")
    labels = {}
    for row in train + validation:
        key = feature_hash(row["input"])
        if key in labels and labels[key] != row["label"]:
            raise ValueError("Conflicting labels for the same model input")
        labels[key] = row["label"]
        weight = row.get("weight", 1)
        if type(weight) is not int or not 1 <= weight <= 16:
            raise ValueError("Row weight must be an integer from 1 to 16")


def main():
    parser = argparse.ArgumentParser(description="Continue a local MiniLM checkpoint using reviewed, grouped JSONL data")
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--initial", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=5e-6)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output already exists; never overwrite a model run")
    if (not 1 <= args.epochs <= 8 or not 1e-6 <= args.learning_rate <= 5e-5
            or not 1 <= args.batch_size <= 64):
        raise ValueError("Use 1–8 epochs, learning rate 1e-6–5e-5 and batch size 1–64")
    train, validation = read_rows(args.train), read_rows(args.validation)
    validate_split(train, validation)
    import random
    import torch

    if args.device == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA requested but unavailable")
    torch.manual_seed(args.seed)
    classifier = Classifier(args.initial)
    classifier.model.to(args.device)
    texts = [classifier.render(r["input"]) for r in train]
    ids = [classifier.tokenizer(t, truncation=False)["input_ids"] for t in texts]
    if any(len(value) > classifier.max_length for value in ids):
        raise ValueError("Training example exceeds token budget; shorten the upstream input explicitly")
    totals = {label: sum(r.get("weight", 1) for r in train if r["label"] == label) for label in (0, 1)}
    if not all(totals.values()):
        raise ValueError("Training needs both classes")
    before = score_rows(classifier, validation)

    def fingerprint(module):
        digest = hashlib.sha256()
        for parameter in module.parameters():
            digest.update(parameter.detach().cpu().contiguous().numpy().tobytes())
        return digest.hexdigest()

    encoder_before = fingerprint(classifier.model.bert)
    head_before = fingerprint(classifier.model.classifier)
    optimizer = torch.optim.AdamW(classifier.model.parameters(), lr=args.learning_rate, weight_decay=.01)
    steps = args.epochs * math.ceil(len(train) / args.batch_size)
    step = 0
    started = time.perf_counter()
    for epoch in range(args.epochs):
        order = list(range(len(train)))
        random.Random(args.seed + epoch).shuffle(order)
        order = [i for start in range(0, len(order), 64)
                 for i in sorted(order[start:start + 64], key=lambda i: len(ids[i]))]
        classifier.model.train()
        for start in range(0, len(order), args.batch_size):
            batch = order[start:start + args.batch_size]
            encoded = classifier.tokenizer([texts[i] for i in batch], padding=True,
                return_tensors="pt", truncation=False)
            encoded = {k: v.to(args.device) for k, v in encoded.items()}
            labels = torch.tensor([train[i]["label"] for i in batch], device=args.device)
            weights = torch.tensor([train[i].get("weight", 1) / totals[train[i]["label"]] for i in batch],
                dtype=torch.float32, device=args.device)
            logits = classifier.model(**encoded).logits
            losses = torch.nn.functional.cross_entropy(logits, labels, reduction="none")
            loss = (losses * weights).sum() / weights.sum()
            if not torch.isfinite(loss):
                raise RuntimeError("Non-finite training loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(classifier.model.parameters(), 1.)
            step += 1
            factor = min(1., step / max(1, steps * .1)) * max(0., (steps - step) / (steps * .9))
            for group in optimizer.param_groups:
                group["lr"] = args.learning_rate * factor
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
    elapsed = time.perf_counter() - started
    encoder_changed = fingerprint(classifier.model.bert) != encoder_before
    head_changed = fingerprint(classifier.model.classifier) != head_before
    if not encoder_changed or not head_changed:
        raise RuntimeError("Training did not update both encoder and classification head")
    after = score_rows(classifier, validation)
    args.output.mkdir(parents=True, mode=0o700)
    classifier.model.save_pretrained(args.output / "model")
    classifier.tokenizer.save_pretrained(args.output / "model")
    metadata = {key: classifier.report[key] for key in ("architecture", "schema_version",
                "renderer", "base_revision", "threshold", "max_sequence_length")}
    report = {**metadata, "status": "development_only", "scores_are_calibrated": False,
              "development_guard_passed": after["review_recall"] >= .95 and after["macro_f1"] >= before["macro_f1"] - .02,
              "encoder_weights_changed": encoder_changed, "head_weights_changed": head_changed,
              "parent_model_sha256": classifier.model_sha,
              "model_sha256": hashlib.sha256((args.output / "model/model.safetensors").read_bytes()).hexdigest(),
              "training_seconds": elapsed, "device": args.device, "training_rows": len(train),
              "epochs": args.epochs, "batch_size": args.batch_size, "learning_rate": args.learning_rate,
              "seed": args.seed, "training_group_count": len({r["group"] for r in train}),
              "validation_group_count": len({r["group"] for r in validation}),
              "dataset_sha256": {"train": hashlib.sha256(args.train.read_bytes()).hexdigest(),
                                 "validation": hashlib.sha256(args.validation.read_bytes()).hexdigest()},
              "before": before, "validation": after,
              "limitations": ["Continuation from the selected checkpoint, not exact replay without historical data.",
                              "Group and exact-feature checks cannot detect every semantic duplicate.",
                              "Human review and a new live holdout are required before promotion."]}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    restored = Classifier(args.output)
    reference = classifier.predict(validation[0]["input"])["review_score"]
    actual = restored.predict(validation[0]["input"])["review_score"]
    if abs(reference - actual) > 1e-5:
        raise RuntimeError("Saved checkpoint differs after reloading")
    print(json.dumps(report, indent=2))
