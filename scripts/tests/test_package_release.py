"""Archive guards protect a public release from accidental tree escape/dev builds."""
import importlib.util
import json
from pathlib import Path
import pytest

SPEC = importlib.util.spec_from_file_location("package_release", Path(__file__).parents[1] / "package-release.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_archive_refuses_external_symlink(tmp_path):
    tree = tmp_path / "product"
    tree.mkdir()
    (tree / "outside").symlink_to(tmp_path / "private")
    with pytest.raises(ValueError, match="External archive symlink"):
        module.validate_tree(tree)


def test_archive_refuses_accidental_credential_file(tmp_path):
    (tmp_path / "auth.json").write_text("{}")
    with pytest.raises(ValueError, match="Credential-like"):
        module.validate_tree(tmp_path)


def test_build_must_be_matching_release_not_development(tmp_path):
    path = tmp_path / "build-info.json"
    stamp = dict(schema="actor.unity-build.v1", game_version=module.GAME_VERSION,
                 development=False, options="None", target="StandaloneOSX")
    path.write_text(json.dumps(stamp))
    assert module.build_info(tmp_path, "StandaloneOSX")["development"] is False
    for patch in ({"development": True}, {"options": "Development"}, {"target": "StandaloneLinux64"}, {"game_version": "0.8.0"}):
        path.write_text(json.dumps(stamp | patch))
        with pytest.raises(ValueError):
            module.build_info(tmp_path, "StandaloneOSX")


def native(path, platform="macos"):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"\xcf\xfa\xed\xfe" + b"\0" * 32 if platform == "macos" else b"\x7fELF\x02\x01" + b"\0" * 12 + b"\x3e\x00"
    path.write_bytes(data); path.chmod(0o755)


def fake_release(tmp_path, monkeypatch):
    root = tmp_path / "source"; root.mkdir()
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "source_state", lambda: ("a" * 40, False))
    for folder, target in (("agents-arena-client", "StandaloneOSX"), ("agents-arena-server", "StandaloneLinux64")):
        directory = root / "build" / folder; directory.mkdir(parents=True)
        (directory / "build-info.json").write_text(json.dumps(dict(schema="actor.unity-build.v1", game_version=module.GAME_VERSION,
            development=False, options="None", target=target, source_commit="a"*40, source_dirty=False)))
    app = root / "build/agents-arena-client/AgentsArena.app"
    native(app / "Contents/MacOS/Agents Arena")
    framework = app / "Contents/Frameworks/UnityPlayer.framework"
    native(framework / "Versions/A/UnityPlayer")
    (framework / "Versions/Current").symlink_to("A", target_is_directory=True)
    (framework / "UnityPlayer").symlink_to("Versions/Current/UnityPlayer")
    server = root / "build/agents-arena-server"
    native(server / "AgentsArenaServer", "linux")
    backup = server / "AgentsArenaServer_BackUpThisFolder_ButDontShipItWithYourGame"
    backup.mkdir(); (backup / "private-symbols.txt").write_text("not for distribution")
    cp = root / "build/control-plane"; cp.mkdir()
    native(cp / "Actor.ControlPlane", "linux")
    (cp / "Actor.ControlPlane.dll").write_bytes(b"published application assembly")
    (cp / "appsettings.json").write_text(json.dumps({"Match": {"GameVersion": module.GAME_VERSION, "ProtocolVersion": "4"}}))
    (cp / "build-info.json").write_text(json.dumps(dict(schema="actor.control-plane-build.v1", game_version=module.GAME_VERSION,
        target="linux-x64", configuration="Release", source_commit="a"*40, source_dirty=False,
        executable_sha256=module.digest(cp / "Actor.ControlPlane"), assembly_sha256=module.digest(cp / "Actor.ControlPlane.dll"))))
    for name, value in (("docs/installation.md", "Verified installation instructions"),
            ("Agents Arena/Assets/Resources/UI/Fonts/OFL.txt", "Font license"),
            ("skills/actor-play/SKILL.md", "Project onboarding"), ("skills/actor-play/agents/openai.yaml", "interface: {}")):
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(value)
    runtime = root / "runtime"; runtime.mkdir()
    (runtime / "actor_runtime").mkdir(); (runtime / "actor_runtime/__init__.py").write_text('__version__ = "0.7.0"\n')
    (runtime / "pyproject.toml").write_text('''[project]
name = "actor-runtime"
version = "0.7.0"
dependencies = ["protobuf==7.36.2"]
[project.optional-dependencies]
codex = ["openai-codex==0.162.1"]
[project.scripts]
actorctl = "actor_runtime.control:main"
[tool.setuptools]
packages = ["actor_runtime"]
package-dir = {actor_runtime = "actor_runtime"}
''')
    make_wheel(root)
    return root


def make_wheel(root, *, changed=None, extra=None):
    import base64
    import csv
    import hashlib
    import io
    import zipfile
    payload, _ = module.package_payload(root)
    files = {name: source.read_bytes() for name, source in payload.items()}
    prefix = "actor_runtime-0.7.0.dist-info/"
    files.update({prefix + "METADATA": b'Name: actor-runtime\nVersion: 0.7.0\nRequires-Dist: protobuf==7.36.2\nRequires-Dist: openai-codex==0.162.1; extra == "codex"\n',
        prefix + "WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        prefix + "entry_points.txt": b"[console_scripts]\nactorctl = actor_runtime.control:main\n",
        prefix + "top_level.txt": b"actor_runtime\n"})
    if changed: files.update(changed)
    if extra: files.update(extra)
    record = io.StringIO(); writer = csv.writer(record, lineterminator="\n")
    for name, data in sorted(files.items()):
        checksum = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        writer.writerow((name, checksum, str(len(data))))
    writer.writerow((prefix + "RECORD", "", "")); files[prefix + "RECORD"] = record.getvalue().encode()
    directory = root / "build/runtime-dist"; directory.mkdir(exist_ok=True)
    path = directory / "actor_runtime-0.7.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items(): archive.writestr(name, data)
    return path


def test_product_root_dangling_links_and_special_files_rejected(tmp_path):
    tree = tmp_path / "tree"; tree.mkdir()
    link = tmp_path / "root-link"; link.symlink_to(tree, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"): module.validate_tree(link)
    (tree / "dangling").symlink_to("missing")
    with pytest.raises(ValueError, match="dangling"): module.validate_tree(tree)
    (tree / "dangling").unlink()
    import os
    os.mkfifo(tree / "fifo")
    with pytest.raises(ValueError, match="Unsupported"): module.validate_tree(tree)


def test_mac_binary_requires_actual_x_permission_and_native_header(tmp_path):
    path = tmp_path / "client"; native(path)
    module.native_executable(path, "macos")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="permission"): module.native_executable(path, "macos")
    path.chmod(0o755); path.write_bytes(b"#!/bin/sh\n")
    with pytest.raises(ValueError, match="platform mismatch"): module.native_executable(path, "macos")


def test_clean_exact_source_required_even_when_current_tree_clean():
    stamp = dict(source_commit="a"*40, source_dirty=False)
    assert module.validate_provenance([stamp]*3, "a"*40, False, False)
    for stamps, commit, dirty in (([stamp]*3, "a"*40, True), ([stamp, stamp | {"source_dirty":True}], "a"*40, False),
            ([stamp | {"source_commit":"b"*40}], "a"*40, False)):
        with pytest.raises(ValueError, match="exact clean"): module.validate_provenance(stamps, commit, dirty, False)
    assert not module.validate_provenance([stamp | {"source_dirty":True}], "a"*40, False, True)
    with pytest.raises(ValueError, match="provenance"): module.validate_provenance([{}], "a"*40, False, True)


def test_cp_stamp_requires_release_version_source_and_unchanged_executable(tmp_path, monkeypatch):
    root = fake_release(tmp_path, monkeypatch); cp = root / "build/control-plane"
    assert module.control_info(cp)["configuration"] == "Release"
    (cp / "Actor.ControlPlane").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed after publish"): module.control_info(cp)


def test_cp_host_hash_cannot_hide_changed_application_or_old_admission_config(tmp_path, monkeypatch):
    root = fake_release(tmp_path, monkeypatch); cp = root / "build/control-plane"
    app = cp / "Actor.ControlPlane.dll"; original = app.read_bytes(); app.write_bytes(b"changed code")
    with pytest.raises(ValueError, match="assembly changed"): module.control_info(cp)
    app.write_bytes(original)
    (cp / "appsettings.json").write_text(json.dumps({"Match": {"GameVersion": module.GAME_VERSION, "ProtocolVersion": "3"}}))
    with pytest.raises(ValueError, match="compatibility"): module.control_info(cp)


def test_runtime_wheel_required_exact_version_and_committed_source_bytes(tmp_path, monkeypatch):
    root = fake_release(tmp_path, monkeypatch)
    path = module.runtime_wheel(root); path.unlink()
    with pytest.raises(ValueError, match="Matching Runtime wheel"): module.runtime_wheel(root)
    make_wheel(root, changed={"actor_runtime/__init__.py":b"stale source"})
    with pytest.raises(ValueError, match="differs from current source"): module.runtime_wheel(root)
    make_wheel(root, extra={"openai_codex/auth.json":b"do not bundle SDK credentials"})
    with pytest.raises(ValueError): module.runtime_wheel(root)
    make_wheel(root, extra={"actor_runtime-0.7.0.dist-info/auth.json":b"secret"})
    with pytest.raises(ValueError, match="Unexpected Runtime wheel metadata"): module.runtime_wheel(root)


def test_runtime_metadata_cannot_change_cli_or_pinned_optional_dependency(tmp_path, monkeypatch):
    root = fake_release(tmp_path, monkeypatch); prefix = "actor_runtime-0.7.0.dist-info/"
    make_wheel(root, changed={prefix+"entry_points.txt":b"[console_scripts]\nactorctl = foreign:main\n"})
    with pytest.raises(ValueError, match="entry points"): module.runtime_wheel(root)
    make_wheel(root, changed={prefix+"METADATA":b"Name: actor-runtime\nVersion: 0.7.0\n"})
    with pytest.raises(ValueError, match="dependency"): module.runtime_wheel(root)


def test_tar_extraction_preserves_native_mode_and_relative_symlink(tmp_path):
    tree = tmp_path / "linux-server"; tree.mkdir()
    native(tree / "AgentsArenaServer", "linux"); (tree / "alias").symlink_to("AgentsArenaServer")
    archive = tmp_path / "server.tar.gz"; module.write_tar(tree, archive)
    assert module.verify_archive(archive, tree)["extracted_integrity"]
    repeat = tmp_path / "repeat.tar.gz"; module.write_tar(tree, repeat)
    assert module.digest(archive) == module.digest(repeat)


def test_archive_rejects_extra_traversal_entry_before_extraction(tmp_path, monkeypatch):
    import zipfile
    tree = tmp_path / "actor-play"; tree.mkdir(); (tree / "SKILL.md").write_text("safe")
    archive = tmp_path / "skill.zip"; module.write_zip(tree, archive)
    with zipfile.ZipFile(archive, "a") as value: value.writestr("../outside", b"bad")
    with pytest.raises(ValueError, match="Unexpected"): module.verify_archive(archive, tree)
    assert not (tmp_path / "outside").exists()


def test_staging_omits_unshipped_unity_backups_and_preserves_internal_links(tmp_path):
    source = tmp_path / "source"; source.mkdir(); (source / "data").write_text("game")
    (source / "alias").symlink_to("data")
    backup = source / "Server_BackUpThisFolder_ButDontShipItWithYourGame"; backup.mkdir(); (backup / "symbols").write_text("debug")
    staged = tmp_path / "product"; module.stage_tree(source, staged)
    assert (staged / "alias").is_symlink() and (staged / "alias").read_text() == "game"
    assert not (staged / backup.name).exists()


def test_staging_compares_prevalidated_bytes_not_a_newer_ignored_build(tmp_path):
    source = tmp_path / "source"; source.mkdir(); (source / "payload").write_bytes(b"validated build")
    expected = module.relative_snapshot(source)
    (source / "payload").write_bytes(b"new build during packaging")
    with pytest.raises(ValueError, match="input changed"):
        module.stage_tree(source, tmp_path / "staged", expected)


@pytest.mark.parametrize("platform", ["macos", "linux-server", "linux-control-plane"])
def test_staged_stamp_must_equal_exact_prevalidated_provenance(tmp_path, monkeypatch, platform):
    fake_release(tmp_path, monkeypatch); output = tmp_path / "not-published"
    original_copy, original_stage = module.shutil.copy2, module.stage_tree
    def changed_copy(source, destination, *args, **kwargs):
        result = original_copy(source, destination, *args, **kwargs)
        if platform == "macos" and Path(destination).name == "build-info.json":
            value = json.loads(Path(destination).read_text()); value["source_commit"] = "b"*40
            Path(destination).write_text(json.dumps(value))
        return result
    def changed_stage(source, destination, *args, **kwargs):
        original_stage(source, destination, *args, **kwargs)
        if destination.name == platform:
            value = json.loads((destination / "build-info.json").read_text()); value["source_commit"] = "b"*40
            (destination / "build-info.json").write_text(json.dumps(value))
    monkeypatch.setattr(module.shutil, "copy2", changed_copy); monkeypatch.setattr(module, "stage_tree", changed_stage)
    with pytest.raises(ValueError, match="stamp changed"):
        module.main(["--output", str(output)])
    assert not output.exists()


def test_client_install_manifest_uses_relative_checked_actual_executable(tmp_path):
    native(tmp_path / "AgentsArena.app/Contents/MacOS/Agents Arena")
    executable = tmp_path / "AgentsArena.app/Contents/MacOS/Agents Arena"
    info = dict(schema="actor.client.v1", game_id="agents-arena", game_version=module.GAME_VERSION,
        client=dict(executable="AgentsArena.app/Contents/MacOS/Agents Arena", sha256=module.digest(executable)))
    path = tmp_path / "client-manifest.json"; path.write_text(json.dumps(info)); module.validate_client_manifest(tmp_path)
    info["client"]["executable"] = str(executable); path.write_text(json.dumps(info))
    with pytest.raises(ValueError, match="relative"): module.validate_client_manifest(tmp_path)
    info["client"]["executable"] = "AgentsArena.app/Contents/MacOS/Agents Arena"
    info["client"]["sha256"] = "0"*64; path.write_text(json.dumps(info))
    with pytest.raises(ValueError): module.validate_client_manifest(tmp_path)


def test_checksum_inventory_rejects_changed_or_unlisted_files(tmp_path):
    path = tmp_path / "artifact"; path.write_bytes(b"verified")
    (tmp_path / "SHA256SUMS").write_text(module.digest(path)+"  artifact\n")
    module.verify_checksums(tmp_path)
    (tmp_path / "unlisted").write_text("extra")
    with pytest.raises(ValueError, match="inventory"): module.verify_checksums(tmp_path)
    (tmp_path / "unlisted").unlink(); path.write_text("tampered")
    with pytest.raises(ValueError): module.verify_checksums(tmp_path)


def test_output_inside_input_product_rejected_without_mutating_it(tmp_path, monkeypatch):
    root = fake_release(tmp_path, monkeypatch)
    output = root / "build/agents-arena-client/AgentsArena.app/nested-release"
    with pytest.raises(ValueError, match="must not mutate"):
        module.main(["--output", str(output)])
    assert not output.exists()


@pytest.mark.skipif(module.sys.platform != "darwin", reason="Actual macOS ditto mode/link extraction check")
def test_complete_synthetic_release_extracted_manifest_and_all_five_artifacts(tmp_path, monkeypatch):
    root = fake_release(tmp_path, monkeypatch); output = tmp_path / "release"
    assert module.main(["--output", str(output)]) == 0
    info = json.loads((output / "release-manifest.json").read_text())
    assert info["publishable_source"] is True and info["source_dirty"] is False
    assert len(info["artifacts"]) == 5
    client = next(row for row in info["artifacts"] if row["platform"] == "macos")
    assert client["client_manifest"] == "AgentsArena-macOS/client-manifest.json"
    assert all(row["verification"] for row in info["artifacts"])
    module.verify_checksums(output)
    with pytest.raises(SystemExit): module.main(["--output", str(output)])


@pytest.mark.skipif(module.sys.platform != "darwin", reason="Actual macOS ditto mode/link extraction check")
def test_dirty_override_never_looks_publishable_and_failed_package_is_not_exposed(tmp_path, monkeypatch):
    root = fake_release(tmp_path, monkeypatch); output = tmp_path / "local"
    for path in root.glob("build/*/build-info.json"):
        stamp = json.loads(path.read_text()); stamp["source_commit"] = "b"*40; path.write_text(json.dumps(stamp))
    assert module.main(["--output", str(output), "--allow-dirty"]) == 0
    info = json.loads((output / "release-manifest.json").read_text())
    assert info["publishable_source"] is False and info["packaging_mode"] == "local-staging"
    assert info["build_source_consistent"] is False
    output2 = tmp_path / "not-published"
    source = iter([("a"*40, False), ("c"*40, False)])
    monkeypatch.setattr(module, "source_state", lambda: next(source))
    with pytest.raises(ValueError, match="Source or output changed"):
        module.main(["--output", str(output2), "--allow-dirty"])
    assert not output2.exists()


@pytest.mark.skipif(module.sys.platform != "darwin", reason="Actual macOS ditto extraction check")
def test_verified_wheel_swap_during_copy_never_publishes_output(tmp_path, monkeypatch):
    fake_release(tmp_path, monkeypatch); output = tmp_path / "swapped"
    original = module.shutil.copy2
    def changed_copy(source, destination, *args, **kwargs):
        result = original(source, destination, *args, **kwargs)
        if Path(source).suffix == ".whl": Path(destination).write_bytes(b"changed after validation")
        return result
    monkeypatch.setattr(module.shutil, "copy2", changed_copy)
    with pytest.raises(ValueError, match="wheel changed during packaging"):
        module.main(["--output", str(output)])
    assert not output.exists()
