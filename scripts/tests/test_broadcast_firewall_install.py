"""Filesystem/command fakes only. Never invokes sudo, launchctl, lsof or PF."""
import importlib.util
import json
import os
from pathlib import Path
import plistlib
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location("broadcast_firewall_install", Path(__file__).parents[1] / "install-broadcast-firewall.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class Commands:
    def __init__(self, paths):
        self.paths = paths; self.calls = []; self.listening = False; self.loaded = False
        self.protected = False; self.proof = True; self.fail_install = False; self.foreign_job = False
        self.wrong_args = None

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        assert kwargs["env"] == {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"}
        assert 0 < kwargs["timeout"] <= 45
        out = err = ""; rc = 0
        if args[0] == "/usr/sbin/lsof":
            rc = 0 if self.listening else 1
            if self.listening: out = "p12345\nn*:4455\n"
        elif args[:2] == ["/bin/launchctl", "print"]:
            if self.loaded:
                out = "path = " + ("/foreign.plist" if self.foreign_job else str(self.paths.plist)) + "\nprogram = /usr/bin/python3\n"
                args = plistlib.loads(self.paths.plist.read_bytes())["ProgramArguments"] if self.paths.plist.exists() else ["foreign"]
                if self.wrong_args == "script": args[4] = "/foreign.py"
                elif self.wrong_args == "flags": args.remove("-I")
                out += "arguments = {\n" + "".join("\t" + arg + "\n" for arg in args) + "}\n"
            else: rc = 113; err = "Could not find service " + m.LABEL
        elif args[:2] == ["/bin/launchctl", "bootstrap"]:
            assert not self.loaded; self.loaded = True
        elif args[:2] == ["/bin/launchctl", "kickstart"]:
            assert self.loaded
        elif args[:2] == ["/bin/launchctl", "bootout"]:
            assert self.loaded; self.loaded = False
        elif args[:4] == [m.PYTHON, "-I", "-S", "-B"]:
            if args[4] == "-c":
                assert "FirewallAttestationClient" in args[5]
                rc = 0 if self.proof else 2
            else:
                assert Path(args[4]).name == "obs_firewall.py"
                operation = args[5]
                if operation == "install":
                    self.paths.state.write_text("owned PF recovery state")
                    self.paths.state.chmod(0o600)
                    if self.fail_install: rc = 2; out = json.dumps({"verified": False, "reason": "pf_disabled_explicit_enable_required"})
                    else: self.protected = True; out = json.dumps({"verified": True})
                elif operation == "check":
                    rc = 0 if self.protected else 2
                    out = json.dumps({"verified": self.protected})
                elif operation == "remove":
                    assert not self.listening
                    self.protected = False; self.paths.state.unlink()
                    out = json.dumps({"removed": True, "only_owned_anchor": True})
                else: raise AssertionError(args)
        else: raise AssertionError(args)
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "root"; root.mkdir(mode=0o700)
    p = m.Paths(root)
    for parent in (p.snapshots.parent, p.private.parent, p.run.parent, p.plist.parent):
        parent.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source"; source.mkdir()
    for name in m.MODULES: (source / name).write_text("# reviewed " + name + "\n")
    digest, files = m.source_snapshot(source)
    commands = Commands(p)
    installer = m.Installer(p, run=commands, uid=os.geteuid(), gid=os.getegid(), sleep=lambda _: None)
    return installer, commands, p, digest, files


def install(setup):
    i, c, p, digest, files = setup
    result = i.install(digest, files, expected_digest=digest)
    return i, c, p, digest, files, result


def test_default_and_help_are_unprivileged_read_only(monkeypatch, capsys):
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: pytest.fail("Plan must not spawn commands"))
    assert m.main([]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["executed"] is False and value["minimum_python"] == "3.9"
    assert "--execute" in value["install"] and value["source_digest"] in value["install"]
    with pytest.raises(SystemExit) as exit: m.main(["--help"])
    assert exit.value.code == 0


def test_reviewed_digest_mismatch_prevents_every_mutation(setup):
    i, c, p, digest, files = setup
    with pytest.raises(m.InstallError, match="digest_mismatch"):
        i.install(digest, files, expected_digest="0" * 64)
    assert not c.calls and not p.private.exists() and not p.snapshots.exists()


def test_install_is_exact_immutable_two_module_snapshot_with_fixed_readonly_job(setup):
    i, c, p, digest, files, result = install(setup)
    snapshot = p.snapshots / digest
    assert {x.name for x in snapshot.iterdir()} == set(m.MODULES)
    assert snapshot.stat().st_mode & 0o777 == 0o555
    for name in m.MODULES:
        assert (snapshot / name).read_bytes() == files[name]
        assert (snapshot / name).stat().st_mode & 0o777 == 0o444
    job = plistlib.loads(p.plist.read_bytes())
    assert job["ProgramArguments"] == [m.PYTHON, "-I", "-S", "-B", str(snapshot / m.MODULES[1]),
        "--state", str(p.state), "--socket", str(p.socket), "--port", "4455"]
    assert job["RunAtLoad"] is False and job["KeepAlive"] is False and job["UserName"] == "root"
    assert p.plist.stat().st_mode & 0o777 == 0o444 and p.private.stat().st_mode & 0o777 == 0o700
    assert p.run.stat().st_mode & 0o777 == 0o755 and result["attestor_started"] is False
    assert c.protected and not c.loaded
    before = len(c.calls)
    assert i.install(digest, files, expected_digest=digest)["idempotent"] is True
    assert len(c.calls) == before + 1 and c.calls[-1][5] == "check"
    assert not any("--allow-enable-pf" in command for command in c.calls)


def test_enable_pf_requires_explicit_passthrough(setup):
    i, c, p, digest, files = setup
    i.install(digest, files, expected_digest=digest, allow_enable_pf=True)
    assert [x for x in c.calls if x[:1] == [m.PYTHON]][0][-1] == "--allow-enable-pf"


@pytest.mark.parametrize("foreign", ["listener", "plist", "socket", "job", "state"])
def test_foreign_or_active_resources_are_not_overwritten(setup, foreign):
    i, c, p, digest, files = setup
    if foreign == "listener": c.listening = True
    elif foreign == "job": c.loaded = True; c.foreign_job = True
    elif foreign == "plist": p.plist.write_text("foreign")
    else:
        target = p.socket if foreign == "socket" else p.state
        target.parent.mkdir(parents=True, exist_ok=True); target.write_text("foreign")
    with pytest.raises(m.InstallError): i.install(digest, files, expected_digest=digest)
    assert not p.receipt.exists() and not c.protected
    assert not any("bootstrap" in x or "bootout" in x for x in c.calls)
    if foreign == "plist": assert p.plist.read_text() == "foreign"


def test_start_requires_fresh_root_peer_proof_and_stop_preserves_firewall(setup):
    i, c, p, digest, files, _ = install(setup)
    assert i.start() == {"attestor_started": True, "root_peer_fresh_proof": True}
    assert c.loaded and c.protected
    assert i.start()["root_peer_fresh_proof"] is True
    before = len(c.calls)
    assert i.stop()["firewall_protection_removed"] is False
    assert not c.loaded and c.protected and p.state.exists()
    assert not any(len(x) > 5 and x[5] == "remove" for x in c.calls[before:])
    assert i.start()["root_peer_fresh_proof"] is True  # ordinary stop/start


def test_missing_fresh_proof_never_claims_started_and_retains_recovery(setup):
    i, c, p, digest, files, _ = install(setup); c.proof = False
    with pytest.raises(m.InstallError, match="fresh_root_proof_missing"): i.start()
    assert c.protected and p.receipt.exists() and p.state.exists()
    assert json.loads(p.receipt.read_text())["stage"] == "installed"


def test_stale_socket_is_retained_for_review_not_blindly_unlinked(setup):
    i, c, p, digest, files, _ = install(setup)
    p.socket.write_text("unexpected existing resource")
    with pytest.raises(m.InstallError, match="stale_socket"): i.start()
    assert p.socket.read_text() == "unexpected existing resource" and c.protected
    assert not c.loaded


def test_uninstall_active_listener_keeps_protection_and_service(setup):
    i, c, p, digest, files, _ = install(setup); i.start(); c.listening = True
    before = len(c.calls)
    with pytest.raises(m.InstallError, match="listener_must_be_disabled"): i.uninstall()
    assert c.loaded and c.protected and p.receipt.exists()
    assert not any("bootout" in x or (len(x) > 5 and x[5] == "remove") for x in c.calls[before:])


@pytest.mark.parametrize("tamper", ["module", "extra", "plist"])
def test_modified_or_extra_files_block_uninstall_before_unprotect(setup, tamper):
    i, c, p, digest, files, _ = install(setup)
    if tamper == "extra":
        (p.snapshots / digest).chmod(0o700)
        (p.snapshots / digest / "unrelated.txt").write_text("keep")
        (p.snapshots / digest).chmod(0o555)
    else:
        target = p.snapshots / digest / m.MODULES[0] if tamper == "module" else p.plist
        target.chmod(0o600); target.write_text("changed"); target.chmod(0o444)
    with pytest.raises(m.InstallError): i.uninstall()
    assert c.protected and p.receipt.exists()
    assert not any(len(x) > 5 and x[5] == "remove" for x in c.calls)


def test_uninstall_removes_only_owned_resources_and_is_idempotent(setup):
    i, c, p, digest, files, _ = install(setup); i.start()
    unrelated = p.private / "operator-note.txt"; unrelated.write_text("keep")
    assert i.uninstall()["only_exact_owned_resources"] is True
    assert not c.loaded and not c.protected and not p.plist.exists() and not (p.snapshots / digest).exists()
    assert unrelated.read_text() == "keep" and not p.receipt.exists()
    assert i.uninstall()["already_absent"] is True


def test_partial_pf_failure_retains_journal_and_never_unprotects_implicitly(setup):
    i, c, p, digest, files = setup; c.fail_install = True
    with pytest.raises(m.InstallError, match="pf_disabled_explicit_enable_required"):
        i.install(digest, files, expected_digest=digest)
    receipt = json.loads(p.receipt.read_text())
    assert receipt["stage"] == "snapshot" and p.state.exists() and p.plist.exists()
    assert (p.snapshots / digest / m.MODULES[0]).read_bytes() == files[m.MODULES[0]]
    assert not any("bootout" in x or (len(x) > 5 and x[5] == "remove") for x in c.calls)
    c.fail_install = False
    assert i.install(digest, files, expected_digest=digest, allow_enable_pf=True)["resumed_verified_snapshot"] is True
    assert c.protected and json.loads(p.receipt.read_text())["stage"] == "installed"


@pytest.mark.parametrize("wrong", ["script", "flags"])
def test_same_plist_path_and_python_do_not_authorize_different_loaded_arguments(setup, wrong):
    i, c, p, digest, files, _ = install(setup)
    c.loaded = True; c.wrong_args = wrong
    before = len(c.calls)
    with pytest.raises(m.InstallError, match="definition_changed"): i.start()
    with pytest.raises(m.InstallError, match="definition_changed"): i.stop()
    assert c.loaded and c.protected
    assert not any("kickstart" in x or "bootout" in x for x in c.calls[before:])


def test_symlinked_source_or_root_directory_is_refused(setup, tmp_path):
    i, c, p, digest, files = setup
    source = tmp_path / "linked-source"; source.mkdir()
    target = tmp_path / "actual.py"; target.write_text("# actual")
    (source / m.MODULES[0]).symlink_to(target)
    with pytest.raises(OSError): m.source_snapshot(source)
    p.snapshots.parent.rename(p.snapshots.parent.with_name("real-support"))
    p.snapshots.parent.symlink_to(p.snapshots.parent.with_name("real-support"), target_is_directory=True)
    with pytest.raises(m.InstallError, match="directory_unsafe"):
        i.install(digest, files, expected_digest=digest)
    assert not c.protected


def test_writable_ancestor_is_refused_without_chmod_or_pf_mutation(setup):
    i, c, p, digest, files = setup
    ancestor = p.run.parent.parent
    ancestor.chmod(0o775)
    with pytest.raises(m.InstallError, match="root_directory_unsafe"):
        i.install(digest, files, expected_digest=digest)
    assert ancestor.stat().st_mode & 0o777 == 0o775
    assert not p.receipt.exists() and not p.snapshots.exists() and not c.protected
    assert not any(x[0] == m.PYTHON or "bootstrap" in x for x in c.calls)
