"""Opt-in macOS PF isolation. Never called implicitly by OBS/Broadcast startup.

Run install/remove as an explicitly authorized administrator. Main rules are
never loaded/flushed/replaced, and no existing states or other anchors are
deleted. A disabled PF requires explicit --allow-enable-pf; its own enable
reference is released with -X, never global -d. Rules prove a local policy,
not an external LAN reachability measurement.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess


class FirewallError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


@dataclass(frozen=True)
class FirewallPlan:
    owner: str
    port: int = 4455

    def __post_init__(self):
        if not re.fullmatch(r"[a-f0-9]{32}", self.owner): raise FirewallError("pf_owner_invalid")
        if type(self.port) is not int or not 1024 <= self.port <= 65535: raise FirewallError("pf_port_invalid")

    @property
    def anchor(self): return "com.apple/000.actor-broadcast-" + self.owner
    @property
    def label(self): return "actor-broadcast-" + self.owner
    @property
    def rules(self):
        return (f'pass in quick on lo0 proto tcp from any to any port {self.port} no state label "{self.label}-loopback"\n'
                f'block drop in quick on ! lo0 proto tcp from any to any port {self.port} label "{self.label}-deny"\n')


def pf_enabled(text):
    found = re.findall(r"^Status:\s+(Enabled|Disabled)\b", text, re.M)
    if len(found) != 1: raise FirewallError("pf_status_unrecognized")
    return found[0] == "Enabled"


def _lines(text): return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def validate_parent(main_rules, children, plan):
    # A pre-existing earlier pass quick could bypass a child anchor. Refuse
    # custom layouts rather than rewriting a user's firewall to suit us.
    lines = _lines(main_rules)
    while lines and lines[0].startswith("scrub-anchor "): lines.pop(0)
    if not lines or lines[0] != 'anchor "com.apple/*" all':
        raise FirewallError("pf_reachable_first_anchor_required")
    names = []
    for line in _lines(children):
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", line): raise FirewallError("pf_anchor_list_unrecognized")
        name = line.removeprefix("com.apple/")
        if name and name != plan.anchor.split("/")[1]: names.append(name)
    if any(name < plan.anchor.split("/")[1] for name in names):
        raise FirewallError("pf_earlier_child_anchor")


def validate_loaded(text, plan):
    # pfctl may print the explicit equality when normalizing a port.
    normalized = re.sub(r"\bport\s*=\s*", "port ", text)
    lines = _lines(normalized)
    expected = _lines(plan.rules)
    if lines != expected: raise FirewallError("pf_owned_rules_changed")


def validate_interfaces(text):
    # `-s rules` omits global `set skip` options. A skipped interface bypasses
    # every anchor, even a verified quick block. `-v -s Interfaces` emits one
    # interface/group name per line, with an optional literal " (skip)".
    # Refuse unknown output instead of changing somebody else's skip policy.
    if len(text.encode()) > 32768: raise FirewallError("pf_interfaces_unrecognized")
    lines = text.splitlines()
    if not lines or len(lines) > 256: raise FirewallError("pf_interfaces_unrecognized")
    names = set()
    for line in lines:
        found = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.:-]{0,63})( \(skip\))?", line)
        if found is None or found[1] in names: raise FirewallError("pf_interfaces_unrecognized")
        names.add(found[1])
        if found[2] is not None and found[1] != "lo0": raise FirewallError("pf_non_loopback_skip")
    if "lo0" not in names: raise FirewallError("pf_interfaces_unrecognized")


def parse_enable_token(text):
    found = re.findall(r"\bToken\s*:\s*([0-9]{1,20})\b", text)
    if len(found) != 1: raise FirewallError("pf_enable_token_unrecognized")
    return found[0]


def listener_present(port):
    """Metadata only, both address families; never return PID/name details."""
    result = subprocess.run(["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpn"],
        text=True, capture_output=True, timeout=3,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"})
    if len(result.stdout) + len(result.stderr) > 32768: raise FirewallError("pf_listener_check_unverified")
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip(): return False
    if result.returncode == 0 and re.search(r"^p[0-9]+$", result.stdout, re.M): return True
    raise FirewallError("pf_listener_check_unverified")


class PfInstaller:
    def __init__(self, *, run=subprocess.run, listener_check=listener_present):
        self._run = run; self.listener_check = listener_check

    def command(self, *args, input=None):
        result = self._run(["/sbin/pfctl", *args], input=input, text=True, capture_output=True,
                           timeout=5, env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"})
        if result.returncode != 0: raise FirewallError("pf_command_failed")
        if len(result.stdout) + len(result.stderr) > 131072: raise FirewallError("pf_output_bound")
        return result.stdout, result.stderr

    def inspect(self, state):
        plan = FirewallPlan(state["owner"], state["port"])
        if not pf_enabled(self.command("-s", "info")[0]): raise FirewallError("pf_disabled")
        validate_interfaces(self.command("-v", "-s", "Interfaces")[0])
        rules = self.command("-s", "rules")[0]
        validate_parent(rules, self.command("-a", "com.apple", "-s", "Anchors")[0], plan)
        if hashlib.sha256(rules.encode()).hexdigest() != state["main_sha256"]:
            raise FirewallError("pf_main_rules_changed")
        validate_loaded(self.command("-a", plan.anchor, "-s", "rules")[0], plan)
        return {"verified": True, "port": plan.port, "loopback_allowed": True,
                "non_loopback_ipv4_ipv6_denied": True, "main_rules_replaced": False,
                "existing_states_flushed": False, "external_lan_probe_performed": False}

    def install(self, path, *, port=4455, allow_enable_pf=False):
        path = _state_path(path)
        if path.exists():
            old = _read_state(path)
            if old["port"] != port: raise FirewallError("pf_existing_port_mismatch")
            return self.inspect(old)
        plan = FirewallPlan(secrets.token_hex(16), port)
        enabled = pf_enabled(self.command("-s", "info")[0])
        if not enabled and allow_enable_pf is not True: raise FirewallError("pf_disabled_explicit_enable_required")
        validate_interfaces(self.command("-v", "-s", "Interfaces")[0])
        main = self.command("-s", "rules")[0]
        validate_parent(main, self.command("-a", "com.apple", "-s", "Anchors")[0], plan)
        # Existing PF states can bypass new rules. Installation precedes
        # websocket enable; refuse any existing state referencing this port.
        states = self.command("-s", "states")[0]
        # BSD pfctl prints IPv4 ports as :PORT and IPv6 ports as [PORT].
        if re.search(rf":{port}(?![0-9])|\[{port}\]", states): raise FirewallError("pf_existing_port_state")
        if self.listener_check(port) is not False: raise FirewallError("pf_listener_still_active")
        token = None; load_attempted = False; persisted = False; state = None
        try:
            out, err = self.command("-E")
            token = parse_enable_token(out + "\n" + err)
            state = {"schema": "actor.obs-pf.v1", "owner": plan.owner, "port": port,
                     "enable_token": token, "main_sha256": hashlib.sha256(main.encode()).hexdigest()}
            # A recovery record exists BEFORE the first anchor mutation. An
            # audit/normalization failure cannot orphan an anonymous anchor.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(state, stream); stream.flush(); os.fsync(stream.fileno())
            persisted = True; load_attempted = True
            self.command("-a", plan.anchor, "-f", "-", input=plan.rules)
            return self.inspect(state)
        except BaseException:
            incomplete = False
            # A listener may have appeared after the install preflight. Like
            # explicit removal, rollback must never unprotect an active or
            # unverifiable listener. Retain the owned rules/reference and the
            # recovery state for an administrator to retry after disabling it.
            can_unprotect = True
            if token is not None or load_attempted:
                try: can_unprotect = self.listener_check(port) is False
                except BaseException: can_unprotect = False
            if not can_unprotect: incomplete = True
            try:
                if load_attempted and can_unprotect:
                    # A partial load/foreign edit is never blindly erased.
                    existing = self.command("-a", plan.anchor, "-s", "rules")[0]
                    if existing.strip():
                        validate_loaded(existing, plan)
                        self.command("-a", plan.anchor, "-F", "rules")
            except BaseException: incomplete = True
            finally:
                if token is not None and can_unprotect:
                    try:
                        self.command("-X", token)
                        if state is not None: state["enable_token"] = None
                    except BaseException: incomplete = True
                if persisted:
                    if incomplete:
                        # Root-private recovery keeps exact ownership, even
                        # when only the token or only the anchor remains.
                        fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
                        with os.fdopen(fd, "w") as stream:
                            json.dump(state, stream); stream.flush(); os.fsync(stream.fileno())
                    else: path.unlink()
            if incomplete: raise FirewallError("pf_install_cleanup_incomplete_state_retained") from None
            raise

    def remove(self, path):
        path = _state_path(path)
        if not path.exists(): return {"removed": True, "already_absent": True}
        state = _read_state(path); plan = FirewallPlan(state["owner"], state["port"])
        # Never expose a wildcard OBS listener by removing its protection.
        # The explicit administrator disables OBS websocket first.
        if self.listener_check(plan.port) is not False: raise FirewallError("pf_listener_still_active")
        existing = self.command("-a", plan.anchor, "-s", "rules")[0]
        if existing.strip():
            validate_loaded(existing, plan)
            self.command("-a", plan.anchor, "-F", "rules")
        if state["enable_token"] is not None: self.command("-X", state["enable_token"])
        path.unlink()
        return {"removed": True, "only_owned_anchor": True, "global_disable_used": False}


def _state_path(path):
    path = Path(path).absolute()
    if path.is_symlink() or not path.parent.is_dir() or path.parent.resolve() != path.parent:
        raise FirewallError("pf_state_path_unsafe")
    parent = path.parent.stat()
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
        raise FirewallError("pf_state_parent_unsafe")
    return path


def _read_state(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 4096:
            raise FirewallError("pf_state_unsafe")
        value = json.load(stream)
    if (set(value) != {"schema", "owner", "port", "enable_token", "main_sha256"}
            or value["schema"] != "actor.obs-pf.v1" or (value["enable_token"] is not None
                and (not isinstance(value["enable_token"], str) or not re.fullmatch(r"[0-9]{1,20}", value["enable_token"])))
            or not re.fullmatch(r"[a-f0-9]{64}", value["main_sha256"])):
        raise FirewallError("pf_state_invalid")
    FirewallPlan(value["owner"], value["port"])
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "check", "remove"))
    parser.add_argument("--state", required=True)
    parser.add_argument("--port", type=int, default=4455)
    parser.add_argument("--allow-enable-pf", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0: raise SystemExit("pf_administrator_required")
    installer = PfInstaller()
    try:
        if args.operation == "install": value = installer.install(args.state, port=args.port, allow_enable_pf=args.allow_enable_pf)
        elif args.operation == "check": value = installer.inspect(_read_state(_state_path(args.state)))
        else: value = installer.remove(args.state)
        print(json.dumps(value, sort_keys=True))
    except (FirewallError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"verified": False, "reason": getattr(error, "code", "pf_operation_failed")})); raise SystemExit(2)


if __name__ == "__main__": main()
