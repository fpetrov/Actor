#!/usr/bin/env python3
"""Record bounded source/build identities without printing paths or contents."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "actor.source-snapshot.v1"


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", *arguments], cwd=root, stderr=subprocess.DEVNULL)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def inventory(root: Path) -> list[bytes]:
    return sorted(set(filter(None, git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0"))))


def capture(root: Path = ROOT) -> dict:
    commit = git(root, "rev-parse", "HEAD").decode("ascii").strip()
    status = git(root, "status", "--porcelain", "-z", "--untracked-files=all")
    paths = inventory(root)
    value = hashlib.sha256()
    for name in paths:
        relative = PurePosixPath(os.fsdecode(name))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid_source_inventory")
        path = root.joinpath(*relative.parts)
        value.update(len(name).to_bytes(8, "big") + name)
        try:
            before = path.lstat()
        except FileNotFoundError:
            value.update(b"deleted\0")
            continue
        value.update(str(before.st_mode & 0o777).encode() + b"\0")
        if stat.S_ISLNK(before.st_mode):
            target = os.fsencode(os.readlink(path))
            value.update(b"link\0" + len(target).to_bytes(8, "big") + target)
        elif stat.S_ISREG(before.st_mode):
            value.update(b"file\0" + bytes.fromhex(digest(path)))
        else:
            raise ValueError("unsupported_source_entry")
        after = path.lstat()
        if (before.st_size, before.st_mtime_ns, before.st_mode, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_mode, after.st_ino):
            raise ValueError("source_changed_during_snapshot")
    if (git(root, "rev-parse", "HEAD").decode("ascii").strip() != commit
            or git(root, "status", "--porcelain", "-z", "--untracked-files=all") != status or inventory(root) != paths):
        raise ValueError("source_changed_during_snapshot")
    if re.fullmatch(r"[a-f0-9]{40}", commit) is None:
        raise ValueError("invalid_source_commit")
    return {"schema": SCHEMA, "source_commit": commit, "source_dirty": bool(status), "source_fingerprint": value.hexdigest()}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".actor-stamp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False); stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def control_stamp(root: Path, previous: dict, product: Path) -> dict:
    current = capture(root)
    if previous != current or previous.get("schema") != SCHEMA:
        raise ValueError("source_changed_during_build")
    config = json.loads((product / "appsettings.json").read_text()).get("Match", {})
    if config.get("GameVersion") != "0.10.0" or config.get("ProtocolVersion") != "4":
        raise ValueError("published_compatibility_mismatch")
    executable, assembly = product / "Actor.ControlPlane", product / "Actor.ControlPlane.dll"
    if not executable.is_file() or not os.access(executable, os.X_OK) or not assembly.is_file():
        raise ValueError("published_product_missing")
    for path in (executable, assembly):
        if path.is_symlink():
            raise ValueError("published_product_link_rejected")
    result = {"schema": "actor.control-plane-build.v1", "game_version": "0.10.0", "target": "linux-x64",
        "configuration": "Release", "source_commit": current["source_commit"], "source_dirty": current["source_dirty"],
        "source_fingerprint": current["source_fingerprint"], "executable_sha256": digest(executable),
        "assembly_sha256": digest(assembly), "built_at_utc": datetime.now(timezone.utc).isoformat()}
    if capture(root) != current:
        raise ValueError("source_changed_during_stamp")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("snapshot", "stamp-control-plane"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.operation == "snapshot":
            result = capture(ROOT)
        else:
            if args.snapshot is None:
                raise ValueError("source_snapshot_required")
            previous = json.loads(args.snapshot.read_text())
            result = control_stamp(ROOT, previous, args.output.parent)
        atomic_json(args.output, result)
        print(json.dumps({"operation": args.operation, "recorded": True, "source_dirty": result["source_dirty"]}))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({"operation": args.operation, "recorded": False, "error_code": "build_provenance_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
