#!/usr/bin/env python3
"""Package fixed release roots and verify their extracted bytes, links and modes.

This does not build, publish, install, sign, launch or authenticate anything.
"""
from __future__ import annotations

import argparse
import base64
import configparser
import csv
from email.parser import BytesParser
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
GAME_VERSION = "0.9.0"
RUNTIME_VERSION = "0.7.0"
SHA = re.compile(r"[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
PRIVATE_NAMES = {".env", "auth.json", "credentials.json", "codex_home", ".ssh", "auth", "sessions", "logs"}
SKILL_FILES = {"SKILL.md", "agents/openai.yaml"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return stream_digest(stream)


def stream_digest(stream) -> str:
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(block)
    return value.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def excluded(name: str) -> bool:
    return name in {".DS_Store", "__MACOSX"} or name.endswith("_BackUpThisFolder_ButDontShipItWithYourGame")


def entries(root: Path):
    """Do not follow directory links or include Unity's explicitly unshipped backup."""
    pending = [root]
    while pending:
        parent = pending.pop()
        for path in sorted(parent.iterdir()):
            if excluded(path.name):
                continue
            yield path
            if path.is_dir() and not path.is_symlink():
                pending.append(path)


def validate_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Product root must be a real directory")
    base = root.resolve(strict=True)
    for path in entries(root):
        mode = path.lstat().st_mode
        if path.name.lower() in PRIVATE_NAMES or path.name.lower().startswith(".env."):
            raise ValueError("Credential-like artifact inside product root")
        if path.is_symlink():
            try:
                target = path.resolve(strict=True)
            except (OSError, RuntimeError):
                raise ValueError("External archive symlink or dangling link") from None
            if os.path.isabs(os.readlink(path)) or not target.is_relative_to(base):
                raise ValueError("External archive symlink")
        elif not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)) or mode & 0o7000:
            raise ValueError("Unsupported or privileged product entry")


def native_executable(path: Path, target: str) -> None:
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("Missing executable permission on native executable")
    with path.open("rb") as stream:
        header = stream.read(20)
    if target == "macos":
        valid = header[:4] in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
    else:
        valid = len(header) == 20 and header[:6] == b"\x7fELF\x02\x01" and header[18:20] == b"\x3e\x00"
    if not valid:
        raise ValueError("Native executable platform mismatch")


def build_info(root: Path, target: str) -> dict:
    info = json.loads((root / "build-info.json").read_text())
    if info.get("schema") != "actor.unity-build.v1" or info.get("game_version") != GAME_VERSION:
        raise ValueError("Build provenance/version mismatch")
    if info.get("development") is not False or info.get("options") != "None" or info.get("target") != target:
        raise ValueError("Only a verified non-Development platform build can be packaged")
    return info


def control_info(root: Path) -> dict:
    info = json.loads((root / "build-info.json").read_text())
    if (info.get("schema") != "actor.control-plane-build.v1" or info.get("game_version") != GAME_VERSION
            or info.get("target") != "linux-x64" or info.get("configuration") != "Release"):
        raise ValueError("Control-plane build provenance/version mismatch")
    checksum = info.get("executable_sha256")
    if not isinstance(checksum, str) or SHA.fullmatch(checksum) is None or checksum != digest(root / "Actor.ControlPlane"):
        raise ValueError("Control-plane executable changed after publish")
    assembly = info.get("assembly_sha256")
    if not isinstance(assembly, str) or SHA.fullmatch(assembly) is None or assembly != digest(root / "Actor.ControlPlane.dll"):
        raise ValueError("Control-plane assembly changed after publish")
    configuration = json.loads((root / "appsettings.json").read_text()).get("Match", {})
    if configuration.get("GameVersion") != GAME_VERSION or configuration.get("ProtocolVersion") != "4":
        raise ValueError("Control-plane compatibility settings mismatch")
    return info


def source_state() -> tuple[str, bool]:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True).strip())
    if COMMIT.fullmatch(commit) is None:
        raise ValueError("Invalid Git commit identity")
    return commit, dirty


def validate_provenance(stamps: list[dict], commit: str, dirty: bool, allow_dirty: bool) -> bool:
    if any(not isinstance(info.get("source_commit"), str) or COMMIT.fullmatch(info["source_commit"]) is None
            or type(info.get("source_dirty")) is not bool for info in stamps):
        raise ValueError("Missing exact build source provenance")
    consistent = all(info["source_commit"] == commit and info["source_dirty"] is False for info in stamps)
    if not allow_dirty and (dirty or not consistent):
        raise ValueError("Built artifacts must prove this exact clean source commit")
    return consistent


def public_stamp(info: dict) -> dict:
    return {key: info[key] for key in ("schema", "game_version", "unity", "target", "options", "development",
        "configuration", "source_commit", "source_dirty", "source_fingerprint", "built_at_utc", "total_bytes", "duration_seconds", "executable_sha256", "assembly_sha256") if key in info}


def package_payload(root: Path) -> tuple[dict[str, Path], dict]:
    specification = tomllib.loads((root / "runtime/pyproject.toml").read_text())
    if specification["project"]["version"] != RUNTIME_VERSION:
        raise ValueError("Runtime source version mismatch")
    settings = specification["tool"]["setuptools"]
    mappings = settings.get("package-dir", {})
    packages = settings["packages"]
    payload = {}
    for package in packages:
        parents = [key for key in mappings if package == key or package.startswith(key + ".")]
        key = max(parents, key=len) if parents else ""
        directory = root / "runtime" / mappings.get(key, ".")
        suffix = package[len(key):].lstrip(".") if key else package
        directory = directory.joinpath(*suffix.split(".")) if suffix else directory
        directory = directory.resolve(strict=True)
        if not directory.is_relative_to(root.resolve()):
            raise ValueError("Runtime package escaped source tree")
        paths = list(directory.glob("*.py"))
        for pattern in settings.get("package-data", {}).get(package, []):
            paths.extend(directory.glob(pattern))
        for path in paths:
            if path.is_file():
                payload[package.replace(".", "/") + "/" + path.relative_to(directory).as_posix()] = path
    return payload, specification


def runtime_wheel(root: Path) -> Path:
    expected = root / "build/runtime-dist" / f"actor_runtime-{RUNTIME_VERSION}-py3-none-any.whl"
    if not expected.is_file() or expected.is_symlink():
        raise ValueError("Matching Runtime wheel is required in build/runtime-dist")
    if expected.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Oversized Runtime wheel")
    payload, specification = package_payload(root)
    with zipfile.ZipFile(expected) as archive:
        infos = archive.infolist(); names = [item.filename for item in infos]
        if len(names) != len(set(names)) or any("\\" in name or PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts for name in names):
            raise ValueError("Unsafe or duplicate Runtime wheel member")
        metadata_root = f"actor_runtime-{RUNTIME_VERSION}.dist-info/"
        allowed_metadata = {metadata_root + name for name in ("METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD")}
        if not set(names) <= set(payload) | allowed_metadata or not allowed_metadata <= set(names):
            raise ValueError("Unexpected Runtime wheel metadata")
        if set(name for name in names if not name.startswith(metadata_root)) != set(payload):
            raise ValueError("Runtime wheel contains missing or unexpected package files")
        if any(stat.S_ISLNK(item.external_attr >> 16) for item in infos):
            raise ValueError("Runtime wheel must not contain symlinks")
        if any(item.file_size > 2 * 1024 * 1024 for item in infos if item.filename.startswith(metadata_root)):
            raise ValueError("Oversized Runtime wheel metadata")
        for name, source in payload.items():
            item = archive.getinfo(name)
            if item.file_size != source.stat().st_size or hashlib.sha256(archive.read(name)).hexdigest() != digest(source):
                raise ValueError("Runtime wheel differs from current source payload")
        metadata = BytesParser().parsebytes(archive.read(metadata_root + "METADATA"))
        if metadata.get("Name") != "actor-runtime" or metadata.get("Version") != RUNTIME_VERSION:
            raise ValueError("Runtime wheel metadata mismatch")
        if metadata.get("Requires-Python") != specification["project"].get("requires-python"):
            raise ValueError("Runtime wheel Python requirement mismatch")
        requirements = lambda values: {re.sub(r"\s+", "", value).replace("'", '"') for value in values}
        optional = specification["project"].get("optional-dependencies", {})
        declared = list(specification["project"].get("dependencies", [])) + [
            dependency + '; extra == "' + extra + '"' for extra, values in optional.items() for dependency in values]
        if requirements(metadata.get_all("Requires-Dist", [])) != requirements(declared):
            raise ValueError("Runtime wheel dependency metadata mismatch")
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(archive.read(metadata_root + "entry_points.txt").decode())
        expected_entries = {"console_scripts": specification["project"]["scripts"], **specification["project"].get("entry-points", {})}
        if set(parser.sections()) != set(expected_entries) or any(dict(parser[section]) != values for section, values in expected_entries.items()):
            raise ValueError("Runtime wheel entry points mismatch")
        records = list(csv.reader(io.StringIO(archive.read(metadata_root + "RECORD").decode())))
        if any(len(row) != 3 for row in records) or len(records) != len(names) or {row[0] for row in records} != set(names):
            raise ValueError("Runtime wheel RECORD inventory mismatch")
        for name, checksum, size in records:
            if name == metadata_root + "RECORD":
                if checksum or size:
                    raise ValueError("Invalid self-referential wheel RECORD")
                continue
            data = archive.read(name)
            wanted = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
            if checksum != wanted or size != str(len(data)):
                raise ValueError("Runtime wheel RECORD checksum mismatch")
    return expected


def stage_tree(source: Path, destination: Path, expected: dict | None = None) -> None:
    shutil.copytree(source, destination, symlinks=True, ignore=lambda _directory, names: [name for name in names if excluded(name)])
    validate_tree(destination)
    if expected is not None and relative_snapshot(destination) != expected:
        raise ValueError("Build input changed while staging")


def snapshot(root: Path) -> dict:
    validate_tree(root)
    result = {}
    for path in [root, *entries(root)]:
        name = root.name if path == root else root.name + "/" + path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        record = {"execute": mode & 0o111}
        if path.is_symlink():
            record.update(kind="link", target=os.readlink(path))
        elif path.is_dir():
            record.update(kind="dir")
        else:
            record.update(kind="file", bytes=path.stat().st_size, sha256=digest(path))
        result[name] = record
    return result


def relative_snapshot(root: Path) -> dict:
    return {name[len(root.name):].lstrip("/"): record for name, record in snapshot(root).items()}


def write_zip(tree: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, record in sorted(snapshot(tree).items()):
            path = tree.parent / name
            info = zipfile.ZipInfo(name + ("/" if record["kind"] == "dir" else ""), date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3; info.compress_type = zipfile.ZIP_DEFLATED
            mode = stat.S_IMODE(path.lstat().st_mode) & 0o777
            kind = {"dir": stat.S_IFDIR, "file": stat.S_IFREG, "link": stat.S_IFLNK}[record["kind"]]
            info.external_attr = (kind | mode) << 16 | (0x10 if record["kind"] == "dir" else 0)
            if record["kind"] == "file":
                with path.open("rb") as source, archive.open(info, "w") as destination:
                    shutil.copyfileobj(source, destination)
            else:
                archive.writestr(info, b"" if record["kind"] == "dir" else record["target"].encode())


def write_tar(tree: Path, output: Path) -> None:
    def stable(info):
        info.uid = info.gid = info.mtime = 0
        info.uname = info.gname = ""; info.pax_headers = {}
        return info
    with output.open("wb") as stream, gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", dereference=False, format=tarfile.PAX_FORMAT) as archive:
            archive.add(tree, arcname=tree.name, filter=stable)


def verify_archive(path: Path, tree: Path, *, macos: bool = False) -> dict:
    """Inspect exact membership before actual extraction; compare bytes and X bits."""
    expected = snapshot(tree)
    actual = {}
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                name = item.filename.rstrip("/"); mode = item.external_attr >> 16
                if name in actual or name not in expected:
                    raise ValueError("Unexpected or duplicate archive member")
                if item.file_size > max(expected[name].get("bytes", 0), len(expected[name].get("target", "").encode())):
                    raise ValueError("Archive member size mismatch")
                record = {"execute": mode & 0o111}
                if item.is_dir(): record.update(kind="dir")
                elif stat.S_ISLNK(mode): record.update(kind="link", target=archive.read(item).decode())
                elif stat.S_ISREG(mode):
                    with archive.open(item) as stream:
                        record.update(kind="file", bytes=item.file_size, sha256=stream_digest(stream))
                else: raise ValueError("Unsupported archive member")
                actual[name] = record
    else:
        with tarfile.open(path) as archive:
            for item in archive:
                name = item.name.rstrip("/")
                if name in actual or name not in expected:
                    raise ValueError("Unexpected or duplicate archive member")
                if item.size > expected[name].get("bytes", 0):
                    raise ValueError("Archive member size mismatch")
                record = {"execute": item.mode & 0o111}
                if item.isdir(): record.update(kind="dir")
                elif item.issym(): record.update(kind="link", target=item.linkname)
                elif item.isfile():
                    with archive.extractfile(item) as stream:
                        record.update(kind="file", bytes=item.size, sha256=stream_digest(stream))
                else: raise ValueError("Unsupported archive member")
                actual[name] = record
    if actual != expected:
        raise ValueError("Archive differs from staged product")
    with tempfile.TemporaryDirectory(prefix="actor-extract-check-") as directory:
        extract = Path(directory)
        if path.suffix == ".zip":
            if macos:
                if sys.platform != "darwin" or not Path("/usr/bin/ditto").is_file():
                    raise ValueError("macOS extraction verification requires macOS ditto")
                subprocess.run(["/usr/bin/ditto", "-x", "-k", str(path), str(extract)], check=True, capture_output=True)
            else:
                # Skill zip has only regular authored files; no executable/link claims.
                with zipfile.ZipFile(path) as archive:
                    archive.extractall(extract)
        else:
            subprocess.run(["tar", "-xzf", str(path), "-C", str(extract)], check=True, capture_output=True)
        if snapshot(extract / tree.name) != expected:
            raise ValueError("Extracted product bytes, symlinks or executable modes changed")
        if macos:
            native_executable(extract / tree.name / "AgentsArena.app/Contents/MacOS/Agents Arena", "macos")
            validate_client_manifest(extract / tree.name)
    return {"member_integrity": True, "extracted_integrity": True, "executable_modes_and_symlinks": True}


def validate_client_manifest(directory: Path) -> None:
    info = json.loads((directory / "client-manifest.json").read_text())
    if (set(info) != {"schema", "game_id", "game_version", "client"} or info.get("schema") != "actor.client.v1"
            or info.get("game_id") != "agents-arena" or info.get("game_version") != GAME_VERSION):
        raise ValueError("Client install manifest mismatch")
    client = info["client"]
    relative = Path(client["executable"])
    executable = (directory / relative).resolve(strict=True)
    if (set(client) != {"executable", "sha256"} or relative.is_absolute() or ".." in relative.parts
            or not executable.is_relative_to(directory.resolve()) or client.get("sha256") != digest(executable)):
        raise ValueError("Client install manifest must bind a relative contained executable")
    native_executable(executable, "macos")


def verify_checksums(directory: Path) -> None:
    lines = (directory / "SHA256SUMS").read_text().splitlines()
    expected = {path.name: digest(path) for path in directory.iterdir() if path.is_file() and path.name != "SHA256SUMS"}
    actual = {}
    for line in lines:
        if len(line) < 67 or line[64:66] != "  " or SHA.fullmatch(line[:64]) is None:
            raise ValueError("Invalid release checksum index")
        name = line[66:]
        if name in actual or "/" in name or "\\" in name:
            raise ValueError("Unsafe release checksum entry")
        actual[name] = line[:64]
    if actual != expected:
        raise ValueError("Release checksums do not match exact artifact inventory")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/releases" / GAME_VERSION)
    parser.add_argument("--allow-dirty", action="store_true", help="Local staging only; always produces a nonpublishable manifest")
    args = parser.parse_args(argv)
    commit, dirty = source_state()
    output = args.output.resolve()
    if output.exists():
        parser.error("Output already exists; preserve prior attempts and choose a fresh directory")
    client, server, control = (ROOT / "build" / name for name in ("agents-arena-client", "agents-arena-server", "control-plane"))
    if any(output.is_relative_to(tree.resolve()) for tree in (client, server, control, ROOT / "skills/actor-play", ROOT / "build/runtime-dist")):
        raise ValueError("Release output must not mutate a packaged input tree")
    app = client / "AgentsArena.app"; executable = app / "Contents/MacOS/Agents Arena"
    for tree in (app, server, control): validate_tree(tree)
    client_stamp = build_info(client, "StandaloneOSX"); server_stamp = build_info(server, "StandaloneLinux64")
    cp_stamp = control_info(control)
    consistent = validate_provenance([client_stamp, server_stamp, cp_stamp], commit, dirty, args.allow_dirty)
    native_executable(executable, "macos"); native_executable(server / "AgentsArenaServer", "linux")
    native_executable(control / "Actor.ControlPlane", "linux")
    inventories = {"macos": relative_snapshot(app), "linux-server": relative_snapshot(server),
        "linux-control-plane": relative_snapshot(control)}
    wheel = runtime_wheel(ROOT)
    wheel_checksum = digest(wheel)
    skill = ROOT / "skills/actor-play"; validate_tree(skill)
    if {path.relative_to(skill).as_posix() for path in entries(skill) if path.is_file()} != SKILL_FILES or any(path.is_symlink() for path in entries(skill)):
        raise ValueError("Skill package must contain only its two authored onboarding files")
    manifest = {"schema": "actor.release.v1", "game_id": "agents-arena", "game_version": GAME_VERSION,
        "admission_protocol_version": "4", "actor_core_version": "1.0.0", "arena_extension_version": "0.6.0",
        "runtime_version": RUNTIME_VERSION, "source_commit": commit, "source_dirty": dirty,
        "build_source_consistent": consistent, "publishable_source": not args.allow_dirty and not dirty and consistent,
        "packaging_mode": "local-staging" if args.allow_dirty else "release-candidate", "release_status": "limited-test-rc",
        "gameplay_transport": "HTTP control plane / UDP game transport", "client_signing": "ad-hoc; not Apple notarized",
        "windows": "not built; Windows Unity module unavailable on this host", "artifacts": [],
        "builds": {"macos": public_stamp(client_stamp), "linux_server": public_stamp(server_stamp), "linux_control_plane": public_stamp(cp_stamp)}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="actor-release-") as directory, tempfile.TemporaryDirectory(prefix=".actor-package-", dir=output.parent) as pending:
        stage, candidate = Path(directory), Path(pending)
        client_stage = stage / "AgentsArena-macOS"; client_stage.mkdir()
        stage_tree(app, client_stage / app.name, inventories["macos"])
        shutil.copy2(client / "build-info.json", client_stage / "build-info.json")
        if build_info(client_stage, "StandaloneOSX") != client_stamp:
            raise ValueError("Client build stamp changed while staging")
        write_json(client_stage / "client-manifest.json", {"schema": "actor.client.v1", "game_id": "agents-arena", "game_version": GAME_VERSION,
            "client": {"executable": "AgentsArena.app/Contents/MacOS/Agents Arena", "sha256": digest(executable)}})
        validate_client_manifest(client_stage)
        shutil.copy2(ROOT / "docs/installation.md", client_stage / "INSTALL.md")
        licenses = client_stage / "Licenses"; licenses.mkdir()
        shutil.copy2(ROOT / "Agents Arena/Assets/Resources/UI/Fonts/OFL.txt", licenses / "NotoSans-OFL.txt")
        if (ROOT / "LICENSE").exists(): shutil.copy2(ROOT / "LICENSE", licenses / "ACTOR-LICENSE")
        products = [(f"AgentsArena-{GAME_VERSION}-macOS.zip", "macos", client_stage)]
        for name, source in (("linux-server", server), ("linux-control-plane", control)):
            destination = stage / name; stage_tree(source, destination, inventories[name])
            if name == "linux-control-plane":
                if control_info(destination) != cp_stamp:
                    raise ValueError("Control-plane build stamp changed while staging")
            elif build_info(destination, "StandaloneLinux64") != server_stamp:
                raise ValueError("Server build stamp changed while staging")
            products.append((f"AgentsArena-{GAME_VERSION}-{name}.tar.gz", "linux-x64", destination))
        skill_stage = stage / "actor-play"; stage_tree(skill, skill_stage)
        products.append((f"ACTOR-actor-play-{RUNTIME_VERSION}.zip", "codex-skill", skill_stage))
        for name, platform, tree in products:
            archive = candidate / name
            (write_zip if archive.suffix == ".zip" else write_tar)(tree, archive)
            verification = verify_archive(archive, tree, macos=platform == "macos")
            manifest["artifacts"].append({"file": name, "platform": platform, "sha256": digest(archive), "bytes": archive.stat().st_size,
                "root": tree.name, "verification": verification,
                **({"client_manifest": tree.name + "/client-manifest.json"} if platform == "macos" else {})})
        destination = candidate / wheel.name; shutil.copy2(wheel, destination)
        if digest(destination) != wheel_checksum:
            raise ValueError("Runtime wheel changed during packaging")
        manifest["artifacts"].append({"file": destination.name, "platform": "python", "sha256": digest(destination), "bytes": destination.stat().st_size,
            "verification": {"version_and_entry_points": True, "package_bytes_match_source": True}})
        write_json(candidate / "release-manifest.json", manifest)
        (candidate / "SHA256SUMS").write_text("".join(digest(path) + "  " + path.name + "\n" for path in sorted(candidate.iterdir()) if path.is_file()))
        verify_checksums(candidate)
        if source_state() != (commit, dirty) or output.exists():
            raise ValueError("Source or output changed during packaging; no release was published")
        candidate.rename(output)
    print(json.dumps({"version": GAME_VERSION, "source_dirty": dirty, "publishable_source": manifest["publishable_source"],
        "artifacts": len(manifest["artifacts"]), "extraction_verified": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
