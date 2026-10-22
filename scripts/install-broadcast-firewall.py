#!/usr/bin/python3
"""Reviewable macOS PF helper setup. Default: plan only, no privileged calls.

Only install/start/stop/uninstall --execute may change owned resources. Install
requires the displayed source digest and an absent OBS websocket listener.
Stopping the check-only launchd job leaves PF protection installed. Disable OBS
websocket before uninstall; only the owned anchor/reference are then removed.
No automatic boot installation, global PF reload, state flush or sudo policy.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
MODULES = ("obs_firewall.py", "obs_firewall_attestor.py")
LABEL = "com.actor.broadcast-firewall-attestor"
PORT = 4455
PYTHON = "/usr/bin/python3"


class InstallError(RuntimeError):
    pass


class Paths:
    def __init__(self, root=Path("/")):
        self.root = Path(root).absolute()
        self.snapshots = self.root / "Library/Application Support/ACTOR-Broadcast-Firewall"
        self.private = self.root / "private/var/db/actor-broadcast-firewall"
        self.run = self.root / "private/var/db/actor-broadcast-firewall-run"
        self.plist = self.root / ("Library/LaunchDaemons/" + LABEL + ".plist")
        self.receipt = self.private / "install.json"
        self.state = self.private / "pf-state.json"
        self.socket = self.run / "control.sock"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_snapshot(source=ROOT / "runtime/actor_broadcast"):
    files = {}
    for name in MODULES:
        fd = os.open(str(source / name), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 1 <= before.st_size <= 131072:
                raise InstallError("source_file_unsafe")
            data = stream.read(131073)
            after = os.fstat(stream.fileno())
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise InstallError("source_changed_during_read")
        files[name] = data
    digest = sha(b"".join(name.encode() + b"\0" + hashlib.sha256(files[name]).digest() for name in MODULES))
    return digest, files


def launchd_bytes(paths, digest):
    snapshot = paths.snapshots / digest
    return plistlib.dumps({
        "Label": LABEL, "UserName": "root", "GroupName": "wheel",
        "ProgramArguments": [PYTHON, "-I", "-S", "-B", str(snapshot / MODULES[1]),
            "--state", str(paths.state), "--socket", str(paths.socket), "--port", str(PORT)],
        "WorkingDirectory": str(snapshot), "Umask": 0o077,
        "RunAtLoad": False, "KeepAlive": False, "ProcessType": "Background", "ExitTimeOut": 10,
    }, sort_keys=True)


def plan(paths, digest):
    script = str(Path(__file__).resolve())
    base = ["sudo", PYTHON, "-I", "-S", "-B", script]
    return {"schema": "actor.obs-pf-install-plan.v1", "executed": False,
        "source_digest": digest, "snapshot": str(paths.snapshots / digest),
        "system_python": PYTHON, "minimum_python": "3.9", "port": PORT,
        "state": str(paths.state), "socket": str(paths.socket), "launchd_label": LABEL,
        "install": base + ["install", "--execute", "--expected-digest", digest],
        "start": base + ["start", "--execute"], "stop_preserving_pf": base + ["stop", "--execute"],
        "uninstall_after_obs_websocket_disabled": base + ["uninstall", "--execute"],
        "disabled_pf_requires_explicit_flag": "--allow-enable-pf",
        "no_global_pf_changes": True, "no_external_lan_probe": True,
        "reboot_recovery": "No automatic reinstall. Preserve recovery state; inspect before retry."}


class Installer:
    def __init__(self, paths=None, *, run=subprocess.run, uid=0, gid=0, sleep=time.sleep):
        self.paths = paths or Paths()
        self.run = run
        self.uid = uid
        self.gid = gid
        self.sleep = sleep

    def command(self, args, *, allowed=(0,), timeout=8):
        result = self.run(args, capture_output=True, text=True, timeout=timeout,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"})
        if len(result.stdout) + len(result.stderr) > 131072 or result.returncode not in allowed:
            raise InstallError("owned_command_failed")
        return result

    def directory(self, path, *, mode=None):
        path = Path(path)
        if self.paths.root not in (path, *path.parents) or path.absolute() != path:
            raise InstallError("path_outside_owned_roots")
        for parent in reversed((path, *path.parents)):
            if parent != self.paths.root and self.paths.root not in parent.parents:
                continue
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != self.uid or info.st_mode & 0o022:
                raise InstallError("root_directory_unsafe")
        if mode is not None and (stat.S_IMODE(path.stat().st_mode) != mode or path.stat().st_gid != self.gid):
            raise InstallError("owned_directory_mode_changed")

    def ensure_directory(self, path, mode, receipt):
        if path.exists() or path.is_symlink():
            self.directory(path, mode=mode)
            return
        self.directory(path.parent)
        path.mkdir(mode=mode)
        os.chown(str(path), self.uid, self.gid)
        os.chmod(str(path), mode)
        receipt["created_directories"].append(str(path))
        self.write_receipt(receipt)

    def file_bytes(self, path, *, mode):
        self.directory(path.parent)
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid or info.st_gid != self.gid or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != mode or info.st_size > 131072):
                raise InstallError("owned_file_unsafe")
            return stream.read(131073)

    def create_file(self, path, data, mode):
        self.directory(path.parent)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(fd, "wb") as stream:
            os.fchown(stream.fileno(), self.uid, self.gid)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(str(path), mode)

    def write_receipt(self, value):
        # Private owned directory; never overwrite a symlink/foreign receipt.
        if self.paths.receipt.exists() or self.paths.receipt.is_symlink():
            self.file_bytes(self.paths.receipt, mode=0o600)
        temp = self.paths.private / ".install-next.json"
        self.create_file(temp, json.dumps(value, sort_keys=True).encode(), 0o600)
        os.replace(str(temp), str(self.paths.receipt))

    def read_receipt(self):
        value = json.loads(self.file_bytes(self.paths.receipt, mode=0o600))
        if (set(value) != {"schema", "digest", "files", "plist_sha256", "stage", "created_directories"}
                or value["schema"] != "actor.obs-pf-install.v1"
                or not isinstance(value["digest"], str) or not re.fullmatch("[a-f0-9]{64}", value["digest"])
                or not isinstance(value["files"], dict) or set(value["files"]) != set(MODULES)
                or any(not isinstance(x, str) or not re.fullmatch("[a-f0-9]{64}", x) for x in value["files"].values())
                or value["plist_sha256"] != sha(launchd_bytes(self.paths, value["digest"]))
                or value["stage"] not in ("preparing", "snapshot", "pf-installed", "installed", "started", "stopped", "pf-removed")
                or not isinstance(value["created_directories"], list)
                or len(value["created_directories"]) > 4):
            raise InstallError("install_receipt_invalid")
        allowed = {str(p) for p in (self.paths.private, self.paths.run, self.paths.snapshots, self.paths.snapshots / value["digest"])}
        if len(set(value["created_directories"])) != len(value["created_directories"]) or not set(value["created_directories"]) <= allowed:
            raise InstallError("install_receipt_directories_invalid")
        return value

    def absent_listener(self):
        r = self.command(["/usr/sbin/lsof", "-nP", "-iTCP:" + str(PORT), "-sTCP:LISTEN", "-Fpn"], allowed=(0, 1), timeout=4)
        if r.returncode != 1 or r.stdout.strip() or r.stderr.strip():
            raise InstallError("obs_listener_must_be_disabled")

    def job(self, *, expected=False):
        r = self.command(["/bin/launchctl", "print", "system/" + LABEL], allowed=(0, 3, 113))
        if r.returncode:
            if "Could not find service" not in r.stderr + r.stdout:
                raise InstallError("launchd_status_unknown")
            return False
        if not expected:
            raise InstallError("existing_launchd_job_not_owned")
        # launchctl has no plist/JSON print API. Refuse unfamiliar or replaced
        # definitions instead of stopping a job merely because its label fits.
        text = r.stdout
        def scalar(key):
            values = re.findall(r"(?m)^[ \t]*" + key + r" = (.+)$", text)
            if len(values) != 1: raise InstallError("launchd_definition_changed")
            value = values[0]
            return json.loads(value) if value.startswith('"') else value
        blocks = re.findall(r"(?ms)^[ \t]*arguments = \{\n(.*?)^[ \t]*\}", text)
        if len(blocks) != 1: raise InstallError("launchd_arguments_unknown")
        actual = []
        for line in blocks[0].splitlines():
            value = line.strip()
            if not value: raise InstallError("launchd_arguments_unknown")
            actual.append(json.loads(value) if value.startswith('"') else value)
        receipt = self.read_receipt()
        expected_args = plistlib.loads(launchd_bytes(self.paths, receipt["digest"]))["ProgramArguments"]
        if scalar("path") != str(self.paths.plist) or scalar("program") != PYTHON or actual != expected_args:
            raise InstallError("launchd_definition_changed")
        return True

    def verify_snapshot(self, receipt):
        path = self.paths.snapshots / receipt["digest"]
        self.directory(path, mode=0o555)
        if {p.name for p in path.iterdir()} != set(MODULES):
            raise InstallError("snapshot_members_changed")
        for name in MODULES:
            if sha(self.file_bytes(path / name, mode=0o444)) != receipt["files"][name]:
                raise InstallError("snapshot_bytes_changed")
        if self.file_bytes(self.paths.plist, mode=0o444) != launchd_bytes(self.paths, receipt["digest"]):
            raise InstallError("launchd_plist_changed")
        self.directory(self.paths.private, mode=0o700)
        self.directory(self.paths.run, mode=0o755)
        return path

    def helper(self, snapshot, operation, *, allow_enable_pf=False):
        args = [PYTHON, "-I", "-S", "-B", str(snapshot / MODULES[0]), operation,
            "--state", str(self.paths.state), "--port", str(PORT)]
        if allow_enable_pf: args.append("--allow-enable-pf")
        r = self.command(args, allowed=(0, 2), timeout=45)
        try: value = json.loads(r.stdout)
        except (ValueError, TypeError): raise InstallError("pf_helper_proof_invalid")
        if not isinstance(value, dict): raise InstallError("pf_helper_proof_invalid")
        if r.returncode:
            reason = value.get("reason")
            if isinstance(reason, str) and re.fullmatch(r"pf_[a-z0-9_]{1,96}", reason):
                raise InstallError(reason)
            raise InstallError("pf_helper_operation_failed")
        if value.get("removed" if operation == "remove" else "verified") is not True:
            raise InstallError("pf_helper_proof_invalid")
        return value

    def install(self, digest, files, *, expected_digest, allow_enable_pf=False):
        if expected_digest != digest: raise InstallError("reviewed_source_digest_mismatch")
        if self.paths.receipt.exists() or self.paths.receipt.is_symlink():
            receipt = self.read_receipt()
            if receipt["digest"] != digest: raise InstallError("installed_snapshot_differs")
            snapshot = self.verify_snapshot(receipt)
            if receipt["stage"] in ("preparing", "snapshot"):
                self.absent_listener()
                self.job(expected=True)
                self.helper(snapshot, "install", allow_enable_pf=allow_enable_pf)
                receipt["stage"] = "installed"; self.write_receipt(receipt)
                return {"installed": True, "resumed_verified_snapshot": True, "attestor_started": False}
            if receipt["stage"] == "pf-removed": raise InstallError("partial_uninstall_requires_administrator_review")
            self.helper(snapshot, "check")
            return {"installed": True, "idempotent": True, "stage": receipt["stage"]}
        self.absent_listener(); self.job()
        if self.paths.plist.exists() or self.paths.plist.is_symlink() or self.paths.socket.exists() or self.paths.socket.is_symlink():
            raise InstallError("existing_plist_or_socket_not_owned")
        if self.paths.state.exists() or self.paths.state.is_symlink(): raise InstallError("pf_state_without_install_receipt")
        self.directory(self.paths.private.parent)
        created = []
        if not self.paths.private.exists():
            self.paths.private.mkdir(mode=0o700)
            os.chown(str(self.paths.private), self.uid, self.gid); os.chmod(str(self.paths.private), 0o700)
            created.append(str(self.paths.private))
        self.directory(self.paths.private, mode=0o700)
        receipt = {"schema": "actor.obs-pf-install.v1", "digest": digest,
            "files": {name: sha(files[name]) for name in MODULES}, "plist_sha256": sha(launchd_bytes(self.paths, digest)),
            "stage": "preparing", "created_directories": created}
        self.write_receipt(receipt)
        # Every later failure leaves root-private recovery evidence. Do not
        # automatically undo protection if a listener appeared during setup.
        self.ensure_directory(self.paths.snapshots, 0o755, receipt)
        snapshot = self.paths.snapshots / digest
        if snapshot.exists() or snapshot.is_symlink(): raise InstallError("snapshot_already_exists_without_receipt")
        self.ensure_directory(snapshot, 0o700, receipt)
        for name in MODULES: self.create_file(snapshot / name, files[name], 0o444)
        os.chmod(str(snapshot), 0o555)
        self.ensure_directory(self.paths.run, 0o755, receipt)
        self.directory(self.paths.plist.parent)
        self.create_file(self.paths.plist, launchd_bytes(self.paths, digest), 0o444)
        receipt["stage"] = "snapshot"; self.write_receipt(receipt)
        self.verify_snapshot(receipt)
        self.absent_listener()
        self.helper(snapshot, "install", allow_enable_pf=allow_enable_pf)
        receipt["stage"] = "installed"; self.write_receipt(receipt)
        return {"installed": True, "attestor_started": False, "protection_checked": True, "source_digest": digest}

    def check(self):
        receipt = self.read_receipt(); snapshot = self.verify_snapshot(receipt)
        self.helper(snapshot, "check")
        return {"protection_checked": True, "stage": receipt["stage"]}

    def start(self):
        receipt = self.read_receipt(); snapshot = self.verify_snapshot(receipt)
        self.helper(snapshot, "check")
        if not self.job(expected=True):
            if self.paths.socket.exists() or self.paths.socket.is_symlink():
                raise InstallError("stale_socket_requires_administrator_review")
            self.command(["/bin/launchctl", "bootstrap", "system", str(self.paths.plist)])
        self.job(expected=True)
        self.command(["/bin/launchctl", "kickstart", "system/" + LABEL])
        code = ("import asyncio,runpy,sys; "
            "m=runpy.run_path(sys.argv[1],run_name='_actor_pf_readonly_probe'); "
            "ok=asyncio.run(m['FirewallAttestationClient'](sys.argv[2]).refresh()); sys.exit(0 if ok else 2)")
        for attempt in range(10):
            r = self.command([PYTHON, "-I", "-S", "-B", "-c", code,
                str(snapshot / MODULES[1]), str(self.paths.socket)], allowed=(0, 2), timeout=4)
            if r.returncode == 0:
                receipt["stage"] = "started"; self.write_receipt(receipt)
                return {"attestor_started": True, "root_peer_fresh_proof": True}
            self.sleep(.2)
        raise InstallError("attestor_fresh_root_proof_missing")

    def stop(self):
        receipt = self.read_receipt(); self.verify_snapshot(receipt)
        if self.job(expected=True):
            self.command(["/bin/launchctl", "bootout", "system/" + LABEL], timeout=15)
        for attempt in range(20):
            if not self.job(expected=True) and not (self.paths.socket.exists() or self.paths.socket.is_symlink()):
                receipt["stage"] = "stopped"; self.write_receipt(receipt)
                return {"attestor_stopped": True, "firewall_protection_removed": False}
            self.sleep(.2)
        raise InstallError("attestor_stop_incomplete_recovery_retained")

    def uninstall(self):
        if not (self.paths.receipt.exists() or self.paths.receipt.is_symlink()):
            self.job()
            if any(p.exists() or p.is_symlink() for p in (self.paths.state, self.paths.plist, self.paths.socket)):
                raise InstallError("resources_without_install_receipt")
            if self.paths.snapshots.exists():
                self.directory(self.paths.snapshots, mode=0o755)
                if any(self.paths.snapshots.iterdir()): raise InstallError("snapshots_without_install_receipt")
            return {"uninstalled": True, "already_absent": True}
        receipt = self.read_receipt(); snapshot = self.verify_snapshot(receipt)
        self.absent_listener()
        self.stop()
        self.absent_listener()
        self.helper(snapshot, "remove")
        receipt["stage"] = "pf-removed"; self.write_receipt(receipt)
        # Revalidate every exact resource before deletion. Extra or modified
        # files are retained for review; no recursive delete or global cleanup.
        self.verify_snapshot(receipt)
        self.paths.plist.unlink()
        os.chmod(str(snapshot), 0o700)
        for name in MODULES: (snapshot / name).unlink()
        snapshot.rmdir()
        self.paths.receipt.unlink()
        for path in (self.paths.run, self.paths.snapshots, self.paths.private):
            if str(path) in receipt["created_directories"] and not any(path.iterdir()): path.rmdir()
        return {"uninstalled": True, "only_exact_owned_resources": True, "global_pf_changes": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", nargs="?", default="plan", choices=("plan", "install", "start", "stop", "check", "uninstall"))
    parser.add_argument("--execute", action="store_true", help="Explicitly perform the selected administrator operation")
    parser.add_argument("--expected-digest", help="Required source digest from the reviewed plan for install")
    parser.add_argument("--allow-enable-pf", action="store_true", help="Explicitly allow install to enable initially disabled PF")
    args = parser.parse_args(argv)
    try:
        digest, files = source_snapshot()
        if not args.execute or args.operation == "plan": result = plan(Paths(), digest)
        else:
            if sys.platform != "darwin" or os.geteuid() != 0: raise InstallError("macos_administrator_required")
            if sys.version_info < (3, 9): raise InstallError("system_python39_required")
            installer = Installer()
            if args.operation == "install": result = installer.install(digest, files, expected_digest=args.expected_digest, allow_enable_pf=args.allow_enable_pf)
            else: result = getattr(installer, args.operation)()
        print(json.dumps(result, sort_keys=True))
        return 0
    except (InstallError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"completed": False, "reason": str(error) if isinstance(error, InstallError) else "administrator_operation_incomplete",
            "recovery": "Inspect owned root-private install.json/pf-state.json; no automatic unprotect or foreign-resource cleanup."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
