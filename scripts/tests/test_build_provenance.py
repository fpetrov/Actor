"""Actual isolated Git trees and synthetic publish bytes; no .NET/build launch."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location("build_provenance", Path(__file__).parents[1] / "build-provenance.py")
module = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(module)


def repository(tmp_path):
    root = tmp_path / "source"; root.mkdir()
    (root / ".gitignore").write_text("build/\n.env\n")
    (root / "code.py").write_text("original\n")
    def command(*args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false", *args], cwd=root, capture_output=True, check=True)
    command("init", "-q"); command("add", ".")
    command("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "baseline")
    return root


def product(root):
    output = root / "build/control-plane"; output.mkdir(parents=True)
    executable = output / "Actor.ControlPlane"; executable.write_bytes(b"published host"); executable.chmod(0o755)
    (output / "Actor.ControlPlane.dll").write_bytes(b"published application")
    (output / "appsettings.json").write_text(json.dumps({"Match": {"GameVersion": "0.9.0", "ProtocolVersion": "4"}}))
    return output


def test_ignored_build_outputs_and_credentials_do_not_change_source_identity(tmp_path):
    root = repository(tmp_path); before = module.capture(root)
    assert before["source_dirty"] is False
    product(root); (root / ".env").write_text("PRIVATE=not part of source snapshot")
    assert module.capture(root) == before
    assert "PRIVATE" not in json.dumps(before)


def test_untracked_production_art_scripts_and_changes_while_already_dirty_are_bound(tmp_path):
    root = repository(tmp_path); before = module.capture(root)
    source = root / "art"; source.mkdir(); (source / "original.svg").write_text("first authored asset")
    first = module.capture(root)
    assert first["source_dirty"] and first["source_fingerprint"] != before["source_fingerprint"]
    (source / "original.svg").write_text("second authored asset")
    second = module.capture(root)
    assert second["source_dirty"] and second["source_fingerprint"] != first["source_fingerprint"]


def test_same_git_status_does_not_hide_tracked_content_or_executable_mode_changes(tmp_path):
    root = repository(tmp_path); code = root / "code.py"
    code.write_text("changed once\n"); first = module.capture(root)
    code.write_text("changed twice\n"); second = module.capture(root)
    assert first["source_commit"] == second["source_commit"] and first["source_dirty"] == second["source_dirty"] is True
    assert first["source_fingerprint"] != second["source_fingerprint"]
    code.chmod(0o755)
    assert module.capture(root)["source_fingerprint"] != second["source_fingerprint"]


def test_source_symlink_binds_link_not_external_credential_contents(tmp_path):
    root = repository(tmp_path); outside = tmp_path / "private"; outside.write_text("secret one")
    (root / "source-link").symlink_to(outside); first = module.capture(root)
    outside.write_text("secret two")
    assert module.capture(root) == first
    (root / "source-link").unlink(); (root / "source-link").symlink_to("elsewhere")
    assert module.capture(root)["source_fingerprint"] != first["source_fingerprint"]


def test_cp_stamp_binds_exact_unchanged_source_host_assembly_and_compatibility(tmp_path):
    root = repository(tmp_path); previous = module.capture(root); output = product(root)
    stamp = module.control_stamp(root, previous, output)
    assert stamp["schema"] == "actor.control-plane-build.v1" and stamp["source_dirty"] is False
    assert stamp["source_fingerprint"] == previous["source_fingerprint"]
    assert stamp["executable_sha256"] == module.digest(output / "Actor.ControlPlane")
    assert stamp["assembly_sha256"] == module.digest(output / "Actor.ControlPlane.dll")
    assert stamp["target"] == "linux-x64" and stamp["configuration"] == "Release"
    (root / "code.py").write_text("changed during test/publish")
    with pytest.raises(ValueError, match="source_changed_during_build"):
        module.control_stamp(root, previous, output)


def test_old_admission_or_missing_app_cannot_produce_stamp(tmp_path):
    root = repository(tmp_path); previous = module.capture(root); output = product(root)
    config = output / "appsettings.json"
    config.write_text(json.dumps({"Match": {"GameVersion": "0.9.0", "ProtocolVersion": "3"}}))
    with pytest.raises(ValueError, match="compatibility"): module.control_stamp(root, previous, output)
    config.write_text(json.dumps({"Match": {"GameVersion": "0.9.0", "ProtocolVersion": "4"}}))
    (output / "Actor.ControlPlane.dll").unlink()
    with pytest.raises(ValueError, match="missing"): module.control_stamp(root, previous, output)


def test_source_mutation_during_product_hashing_prevents_stamp(tmp_path, monkeypatch):
    root = repository(tmp_path); previous = module.capture(root); output = product(root)
    original = module.digest
    def changed(path):
        result = original(path)
        if path.name == "Actor.ControlPlane.dll": (root / "code.py").write_text("changed during stamp")
        return result
    monkeypatch.setattr(module, "digest", changed)
    with pytest.raises(ValueError, match="source_changed_during_stamp"):
        module.control_stamp(root, previous, output)


def test_cli_failure_is_fixed_projection_and_does_not_emit_partial_stamp(tmp_path, monkeypatch, capsys):
    root = repository(tmp_path); previous = module.capture(root); output = product(root)
    source = tmp_path / "private-snapshot"; source.write_text(json.dumps(previous))
    (root / "code.py").write_text("PRIVATE SOURCE CHANGE")
    monkeypatch.setattr(module, "ROOT", root)
    target = output / "build-info.json"
    assert module.main(["stamp-control-plane", "--snapshot", str(source), "--output", str(target)]) == 1
    value = capsys.readouterr().out
    assert not target.exists() and "PRIVATE" not in value and str(root) not in value
    assert json.loads(value)["error_code"] == "build_provenance_rejected"
