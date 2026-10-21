#!/usr/bin/env python3
"""Strict safe projection of an explicitly requested live owned-RPC fault probe."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import tempfile
import unittest


SCHEMA = "actor.social-protocol-fault-probe.v1"
CHECKS = {
    "wrong_match_rejected": "authoritative_outcome_verified",
    "expired_request_rejected": "authoritative_outcome_verified",
    "stale_phase_kill_rejected": "authoritative_outcome_verified",
    "self_target_kill_rejected": "authoritative_outcome_verified",
    "identical_request_replayed": "authoritative_outcome_verified",
    "cross_type_correlation_conflict_rejected": "authoritative_outcome_verified",
    "final_ballot_not_overwritten": "authoritative_final_ballot_preserved",
    "cancel_before_start_ack_stops_work": "ordinary_work_cancelled_before_start_ack",
}


def count(value):
    return value if type(value) is int and 0 <= value <= 1000000 else None


def project(value):
    rows = value.get("checks")
    valid_rows = (isinstance(rows, list) and len(rows) == len(CHECKS)
                  and all(isinstance(row, dict) and type(row.get("name")) is str for row in rows)
                  and {row["name"] for row in rows} == set(CHECKS))
    checks = []
    for name, outcome in CHECKS.items():
        row = next((row for row in rows if isinstance(row, dict) and row.get("name") == name), {}) if isinstance(rows, list) else {}
        checks.append({"name": name, "passed": row.get("completed") is True and row.get("passed") is True and row.get("outcome") == outcome})
    metrics = {key: count(value.get(key)) for key in ("requests_submitted", "matching_action_results", "probe_failures")}
    elapsed = value.get("duration_seconds")
    elapsed = elapsed if type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed <= 305 else None
    passed = (value.get("schema") == SCHEMA and value.get("completed") is True and value.get("passed") is True
              and valid_rows and all(row["passed"] for row in checks) and all(number is not None for number in metrics.values())
              and metrics["requests_submitted"] >= 9 and metrics["matching_action_results"] >= 10
              and metrics["probe_failures"] == 0 and elapsed is not None)
    return {"schema": "actor.social-protocol-fault-acceptance.v1", "passed": passed, "checks": checks,
            "metrics": {**metrics, "duration_seconds": elapsed},
            "scope": "Actual ordinary owned RPC and private authoritative outcomes. No game truth, IDs, poses or private payloads copied."}


class ProjectionTests(unittest.TestCase):
    @staticmethod
    def report():
        return {"schema": SCHEMA, "completed": True, "passed": True, "duration_seconds": 45,
                "requests_submitted": 10, "matching_action_results": 11, "probe_failures": 0,
                "checks": [{"name": name, "outcome": outcome, "completed": True, "passed": True} for name, outcome in CHECKS.items()]}

    def test_all_actual_cases_are_required(self):
        self.assertTrue(project(self.report())["passed"])
        for name in CHECKS:
            value = self.report(); next(row for row in value["checks"] if row["name"] == name)["completed"] = False
            self.assertFalse(project(value)["passed"], name)
        value = self.report(); value["checks"] = value["checks"][:-1]
        self.assertFalse(project(value)["passed"])
        value = self.report(); value["checks"][-1] = value["checks"][0]
        self.assertFalse(project(value)["passed"])

    def test_malformed_metrics_and_unverified_outcomes_fail(self):
        for bad in (True, None, "10", -1):
            value = self.report(); value["matching_action_results"] = bad
            self.assertFalse(project(value)["passed"])
        for bad in (float("nan"), float("inf"), -1, 306, True):
            value = self.report(); value["duration_seconds"] = bad
            self.assertFalse(project(value)["passed"])
        value = self.report(); value["checks"][0]["name"] = []
        self.assertFalse(project(value)["passed"])
        value = self.report(); value["checks"][-1]["outcome"] = "local_acceptance_only"
        self.assertFalse(project(value)["passed"])

    def test_private_unknown_fields_and_text_are_never_copied(self):
        private = "PRIVATE-CANARY"
        value = self.report(); value.update(token=private, role=private, path=private, actor_id=private, payload=private)
        value["checks"][0].update(text=private, evidence_id=private)
        self.assertNotIn(private, json.dumps(project(value)))
        value["checks"][0]["outcome"] = private
        self.assertFalse(project(value)["passed"])
        self.assertNotIn(private, json.dumps(project(value)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return 0 if unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProjectionTests)).wasSuccessful() else 1
    if not args.report:
        parser.error("--report is required")
    try:
        if args.report.stat().st_size > 1024 * 1024:
            raise ValueError("report exceeds bounds")
        value = json.loads(args.report.read_text())
        if not isinstance(value, dict):
            raise ValueError("report must be an object")
    except (OSError, ValueError):
        value = {}
    result = project(value)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": result["passed"], "checks_passed": sum(check["passed"] for check in result["checks"])}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
