#!/usr/bin/env python3
"""Verify an actual Runtime wheel outside this checkout, without Codex/Jev.

Builds from an isolated source copy, installs the wheel plus pinned test extras
in a fresh venv, then runs the copied Runtime suite outside the repository.
The default safe summary is build/milestone5-package.json. No model is invoked,
and neither provider SDK nor authentication is needed. First run needs network
access for ordinary package dependencies.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MODEL_ENV_KEYS = {"ACTOR_BRIDGE_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY", "TYPESAFE_API_KEY"}


class VerificationError(Exception):
    def __init__(self, stage, kind, returncode=None):
        self.stage, self.kind, self.returncode = stage, kind, returncode


def command(stage, arguments, *, cwd, env, timeout=300):
    print(f"Runtime package verification: {stage}", flush=True)
    # Capture child output for pass/fail only: pip configuration, model-like
    # fixture strings and failure tracebacks are never copied to public reports.
    process = subprocess.Popen(arguments, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, start_new_session=os.name != "nt")
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGINT)
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise VerificationError(stage, "deadline_exceeded") from None
    if process.returncode:
        raise VerificationError(stage, "process_failed", process.returncode)
    return output


PROBE = r'''
import importlib
import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path
import sys
from importlib import resources

prefix = Path(sys.prefix).resolve()
modules = [
    "actor_runtime.session", "actor_runtime.skills", "actor_runtime.privacy", "actor_runtime.inbox",
    "actor_standard", "actor_standard_communication", "actor_standard_communication.skills",
    "actor_agents_arena.calibration", "actor_agents_arena.cooperative_brain",
    "actor_agents_arena.investigation", "actor_agents_arena.investigation_brain", "actor_agents_arena.investigation_events",
    "actor.core.v1.bridge_pb2_grpc", "actor.standard.communication.v1.communication_pb2",
    "extensions.agents_arena.v1.calibration_pb2", "extensions.agents_arena.v1.incidents_pb2",
    "actor_codex.brain", "actor_jev.classifier",
]
for name in modules:
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(prefix), "Import escaped the installed wheel"
assert importlib.util.find_spec("openai_codex") is None
assert importlib.util.find_spec("typesafe_sdk") is None
from actor_runtime.extensions import ExtensionRegistry
from actor_runtime.skills import SkillRegistry
from actor_runtime.brains import BrainRegistry
from actor_runtime.classifiers import EventRuleRegistry
extensions = ExtensionRegistry.discover()
assert set(extensions.packages) == {"actor.standard", "actor.standard.communication", "agents-arena"}
skills = SkillRegistry.discover()
assert {"actor.wait", "actor.send-message", "actor.wait-for-message",
        "agents-arena.start-calibration", "agents-arena.inspect-diagnostic",
        "agents-arena.submit-calibration", "agents-arena.wait-for-calibration",
        "agents-arena.select-role", "agents-arena.wait-for-incident", "agents-arena.inspect-cooling",
        "agents-arena.inspect-security-records", "agents-arena.repair-cooling", "agents-arena.maintenance-override",
        "agents-arena.share-investigation-evidence", "agents-arena.submit-investigation-report",
        "agents-arena.wait-for-incident-resolved", "agents-arena.cancel-investigation"} <= set(skills.definitions)
brains = BrainRegistry.discover()
assert {"scripted", "codex", "calibration-coordinator", "calibration-technician", "investigation-engineer",
        "investigation-sheriff", "maintenance-operator", "investigation-claimant"} <= set(brains.factories)
rules = EventRuleRegistry.discover()
from actor_runtime.extensions import type_url
from actor_agents_arena import OWN_INVESTIGATION_VIEW, OwnInvestigationViewObservation
private_view_rule = rules.rules[(OWN_INVESTIGATION_VIEW, "agents-arena", type_url(OwnInvestigationViewObservation))]
assert private_view_rule.data_policy == "session_only"
assert private_view_rule.expiry_field == "expires_at_unix_ms"
for package, basename in [("actor.metadata", "standard.v1"),
    ("actor.communication.metadata", "communication.v1"),
    ("extensions.agents_arena.metadata", "agents_arena.v1")]:
    data = resources.files(package)
    assert data.joinpath(basename + ".pb").read_bytes()
    assert data.joinpath(basename + ".json").read_bytes()
print(json.dumps({
    "runtime_version": metadata.version("actor-runtime"),
    "imports_from_installed_wheel": True,
    "optional_sdk_modules_absent": True,
    "generated_resources_present": True,
    "extension_versions": {name: package.version for name, package in extensions.packages.items()},
    "extension_schema_hashes": {name: package.schema_hash for name, package in extensions.packages.items()},
    "discovered_skills": len(skills.definitions), "discovered_brains": len(brains.factories),
    "discovered_event_rules": len(rules.rules),
    "installed_distribution_versions": {dist.metadata["Name"]: dist.version for dist in metadata.distributions()},
}, sort_keys=True))
'''


def verify(args, report):
    ignored = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", ".pytest_cache", "dist", "build")
    with tempfile.TemporaryDirectory(prefix="actor-runtime-wheel-check-") as temporary:
        workspace = Path(temporary)
        staged, outside = workspace / "source", workspace / "outside-checkout"
        staged.mkdir()
        outside.mkdir()
        shutil.copytree(ROOT / "runtime", staged / "runtime", ignore=ignored)
        shutil.copytree(ROOT / "generated/python", staged / "generated/python", ignore=ignored)
        specification = tomllib.loads((staged / "runtime/pyproject.toml").read_text())
        environment = {key: value for key, value in os.environ.items()
                       if key not in MODEL_ENV_KEYS | {"PYTHONPATH", "PYTHONHOME"}}
        environment["PYTHONNOUSERSITE"] = "1"
        wheelhouse = workspace / "wheels"
        wheelhouse.mkdir()
        command("build isolated wheel", [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
            "--disable-pip-version-check", "--no-input", "--wheel-dir", str(wheelhouse), str(staged / "runtime")],
            cwd=outside, env=environment)
        wheels = list(wheelhouse.glob("actor_runtime-*.whl"))
        if len(wheels) != 1:
            raise VerificationError("inspect wheel", "expected_one_wheel")
        wheel = wheels[0]
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            required = {
                "actor/communication/metadata/communication.v1.json",
                "actor/communication/metadata/communication.v1.pb",
                "actor/standard/communication/v1/communication_pb2.py",
                "extensions/agents_arena/v1/calibration_pb2.py",
                "extensions/agents_arena/v1/incidents_pb2.py",
                "extensions/agents_arena/metadata/agents_arena.v1.pb",
                "actor_standard_communication/skills.py", "actor_runtime/privacy.py", "actor_runtime/inbox.py",
                "actor_agents_arena/investigation.py", "actor_agents_arena/investigation_brain.py",
                "actor_agents_arena/investigation_events.py",
            }
            if not required <= names or any(".egg-info/" in name for name in names):
                raise VerificationError("inspect wheel", "missing_or_invalid_package_files")
        report["wheel"] = {"version": specification["project"]["version"], "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                           "bytes": wheel.stat().st_size, "required_generated_files_present": True}
        venv = workspace / "venv"
        command("create fresh environment", [sys.executable, "-m", "venv", str(venv)], cwd=outside, env=environment)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        environment["PATH"] = str(python.parent) + os.pathsep + environment.get("PATH", "")
        dev = specification["project"]["optional-dependencies"]["dev"]
        command("install wheel and base/dev dependencies", [str(python), "-I", "-m", "pip", "install", "--no-input",
            "--disable-pip-version-check", "--only-binary=:all:", str(wheel), *dev], cwd=outside, env=environment)
        output = command("isolated import/resources/plugin probe", [str(python), "-I", "-c", PROBE], cwd=outside, env=environment)
        report["isolated_probe"] = json.loads(output.strip())
        if report["isolated_probe"]["runtime_version"] != specification["project"]["version"]:
            raise VerificationError("installed wheel identity", "runtime_version_mismatch")
        # Copy fixtures only. No runtime source package, generated Python tree,
        # editable installation or checkout path is available to test imports.
        shutil.copytree(staged / "runtime/tests", outside / "runtime/tests", ignore=ignored)
        (outside / "scripts").mkdir()
        for name in ("launch-agent.py", "launch-scripted-agent.py"):
            shutil.copyfile(ROOT / "scripts" / name, outside / "scripts" / name)
        (outside / "generated/descriptors").mkdir(parents=True)
        shutil.copyfile(ROOT / "generated/descriptors/actor-core.pb", outside / "generated/descriptors/actor-core.pb")
        result = outside / "pytest-results.xml"
        started = time.monotonic()
        failure = None
        try:
            command("complete Runtime tests against installed wheel", [str(python), "-I", "-m", "pytest", "runtime/tests", "-q",
                "-o", "asyncio_mode=auto", "--rootdir", str(outside), "--junitxml", str(result)],
                cwd=outside, env=environment, timeout=args.test_timeout)
        except VerificationError as error:
            failure = error
        if result.exists():
            document = ET.parse(result).getroot()
            suites = document
            suites = [suites] if suites.tag == "testsuite" else suites.findall("testsuite")
            totals = {name: sum(int(suite.get(name, 0)) for suite in suites)
                      for name in ("tests", "failures", "errors", "skipped")}
            totals["passed"] = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
            report["tests"] = {**totals, "duration_seconds": time.monotonic() - started,
                "source": "copied fixtures, installed wheel imports only", "model_authentication_used": False}
            # Names aid local diagnosis without recording assertions, fixture
            # values, captured output, or parameter IDs that might contain data.
            failures = [case for case in document.iter("testcase")
                        if case.find("failure") is not None or case.find("error") is not None]
            if failures:
                report["tests"]["failed_cases"] = [
                    {"module": case.get("classname", "").split("[")[0],
                     "test": case.get("name", "").split("[")[0]} for case in failures[:20]]
        if failure:
            raise failure
        if not report.get("tests", {}).get("passed"):
            raise VerificationError("test evidence", "missing_test_result")
        report["passed"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/milestone5-package.json")
    parser.add_argument("--test-timeout", type=float, default=180)
    args = parser.parse_args()
    if not 30 <= args.test_timeout <= 1800:
        parser.error("--test-timeout must be between 30 and 1800 seconds")
    report = {"scope": "Isolated real-wheel packaging and deterministic Runtime tests; no live multiplayer/model claim",
        "created_at": datetime.now(timezone.utc).isoformat(), "passed": False,
        "provider_sdk_extras_installed": False, "model_authentication_used": False}
    started = time.monotonic()
    try:
        verify(args, report)
    except VerificationError as error:
        report["failure"] = {"stage": error.stage, "kind": error.kind, "returncode": error.returncode}
    except Exception as error:
        report["failure"] = {"stage": "verification", "kind": type(error).__name__}
    report["duration_seconds"] = time.monotonic() - started
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": report["passed"], "tests": report.get("tests"),
                      "failure": report.get("failure"), "report": str(args.output.resolve())}, indent=2))
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
