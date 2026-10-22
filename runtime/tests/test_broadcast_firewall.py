"""Fake pfctl only: never changes the host's firewall or enables PF."""
import json
import os
from types import SimpleNamespace

import pytest

from actor_broadcast.obs_firewall import (FirewallError, FirewallPlan, PfInstaller,
    parse_enable_token, pf_enabled, validate_loaded, validate_parent)


class FakePf:
    def __init__(self):
        self.enabled = True; self.main = 'scrub-anchor "com.apple/*" all\nanchor "com.apple/*" all\n'
        self.children = "250.ApplicationFirewall\n"; self.states = ""; self.rules = {}; self.calls = []
        self.interfaces = "lo0 (skip)\nen0\n"
    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs.get("input"))); args = command[1:]; out = ""; err = ""
        assert command[0] == "/sbin/pfctl" and kwargs["timeout"] == 5
        if args == ["-s", "info"]: out = "Status: " + ("Enabled" if self.enabled else "Disabled") + " for 1 day\n"
        elif args == ["-v", "-s", "Interfaces"]: out = self.interfaces
        elif args == ["-s", "rules"]: out = self.main
        elif args == ["-s", "states"]: out = self.states
        elif args == ["-a", "com.apple", "-s", "Anchors"]: out = self.children
        elif args == ["-E"]: self.enabled = True; err = "Token : 123456789\n"
        elif args == ["-X", "123456789"]: pass
        elif len(args) == 4 and args[0] == "-a" and args[2:] == ["-f", "-"]: self.rules[args[1]] = kwargs["input"]
        elif len(args) == 4 and args[0] == "-a" and args[2:] == ["-s", "rules"]: out = self.rules.get(args[1], "")
        elif len(args) == 4 and args[0] == "-a" and args[2:] == ["-F", "rules"]: self.rules.pop(args[1], None)
        else: raise AssertionError(command)
        return SimpleNamespace(returncode=0, stdout=out, stderr=err)


def test_install_inspect_idempotent_remove_only_owned_anchor_and_reference(tmp_path):
    os.chmod(tmp_path, 0o700); path = tmp_path / "owned.json"; fake = FakePf(); installer = PfInstaller(run=fake, listener_check=lambda _: False)
    proof = installer.install(path)
    assert proof["verified"] and proof["non_loopback_ipv4_ipv6_denied"]
    assert proof["external_lan_probe_performed"] is False
    state = json.loads(path.read_text()); plan = FirewallPlan(state["owner"], 4455)
    assert path.stat().st_mode & 0o777 == 0o600
    assert "inet " not in plan.rules and "inet6 " not in plan.rules  # both address families
    assert "on ! lo0" in plan.rules and "quick" in plan.rules
    assert installer.install(path)["verified"]
    assert len([c for c, _ in fake.calls if c[1:] == ["-E"]]) == 1
    assert installer.remove(path)["only_owned_anchor"] and not path.exists()
    assert not any("-d" in c or ("-f" in c and "-a" not in c) for c, _ in fake.calls)
    assert installer.remove(path)["already_absent"]


def test_disabled_pf_requires_explicit_permission_before_any_write(tmp_path):
    fake = FakePf(); fake.enabled = False; installer = PfInstaller(run=fake, listener_check=lambda _: False)
    with pytest.raises(FirewallError, match="explicit_enable"): installer.install(tmp_path / "state.json")
    assert len(fake.calls) == 1
    assert installer.install(tmp_path / "state.json", allow_enable_pf=True)["verified"]


@pytest.mark.parametrize("main,children,states", [
    ('pass in quick all\nanchor "com.apple/*" all\n', "", ""),
    ('anchor "elsewhere/*" all\n', "", ""),
    ('anchor "com.apple/*" all\n', "000.actor-aaa\n", ""),
    ('anchor "com.apple/*" all\n', "", "all tcp 10.0.0.1:4455 <- 10.0.0.2:60000 ESTABLISHED\n"),
    ('anchor "com.apple/*" all\n', "", "all tcp fe80::2[4455] <- fe80::3[60000] ESTABLISHED\n"),
])
def test_existing_policy_bypass_or_port_state_refused_without_mutation(tmp_path, main, children, states):
    fake = FakePf(); fake.main = main; fake.children = children; fake.states = states
    with pytest.raises(FirewallError): PfInstaller(run=fake, listener_check=lambda _: False).install(tmp_path / "state.json")
    assert not any("-E" in c or "-f" in c or "-F" in c for c, _ in fake.calls)


def test_changed_owned_anchor_never_silently_removed(tmp_path):
    fake = FakePf(); installer = PfInstaller(run=fake, listener_check=lambda _: False); path = tmp_path / "state.json"; installer.install(path)
    anchor = next(iter(fake.rules)); fake.rules[anchor] += "pass in all\n"
    with pytest.raises(FirewallError, match="changed"): installer.remove(path)
    assert path.exists() and "pass in all" in fake.rules[anchor]
    assert not any("-X" in c for c, _ in fake.calls)


def test_state_symlink_and_unsafe_parent_rejected_before_pf_commands(tmp_path):
    fake = FakePf(); installer = PfInstaller(run=fake, listener_check=lambda _: False)
    target = tmp_path / "target"; target.write_text("do not touch")
    link = tmp_path / "link"; link.symlink_to(target)
    with pytest.raises(FirewallError, match="unsafe"): installer.install(link)
    os.chmod(tmp_path, 0o777)
    with pytest.raises(FirewallError, match="unsafe"): installer.install(tmp_path / "state")
    assert not fake.calls and target.read_text() == "do not touch"


def test_strict_status_token_and_loaded_semantics():
    plan = FirewallPlan("a" * 32)
    validate_parent('anchor "com.apple/*" all\n', "250.ApplicationFirewall\n", plan)
    validate_loaded(plan.rules.replace("port 4455", "port = 4455"), plan)
    for bad in (plan.rules.replace("! lo0", "en0"), plan.rules.replace("quick ", ""), plan.rules + "pass all\n"):
        with pytest.raises(FirewallError): validate_loaded(bad, plan)
    with pytest.raises(FirewallError): pf_enabled("unrecognized")
    with pytest.raises(FirewallError): parse_enable_token("Token: 1\nToken: 2")


def test_failed_normalization_retains_recoverable_owner_but_releases_own_pf_token(tmp_path):
    class Changed(FakePf):
        def __call__(self, command, **kwargs):
            result = super().__call__(command, **kwargs)
            if "-f" in command:
                assert (tmp_path / "state.json").exists()  # before mutation
                self.rules[command[2]] += "pass in all\n"
            return result
    fake = Changed(); installer = PfInstaller(run=fake, listener_check=lambda _: False); path = tmp_path / "state.json"
    with pytest.raises(FirewallError, match="cleanup_incomplete_state_retained"): installer.install(path)
    assert path.exists() and json.loads(path.read_text())["enable_token"] is None
    assert any(c[1:] == ["-X", "123456789"] for c, _ in fake.calls)
    assert not any("-F" in c for c, _ in fake.calls)
    # An administrator may inspect/recover the exact named anchor, without
    # losing ownership or releasing the same PF token twice.
    anchor = next(iter(fake.rules)); fake.rules[anchor] = ""
    assert installer.remove(path)["removed"]
    assert sum(c[1:] == ["-X", "123456789"] for c, _ in fake.calls) == 1


def test_remove_active_listener_preserves_protection_and_enable_reference(tmp_path):
    fake = FakePf(); installer = PfInstaller(run=fake, listener_check=lambda _: False)
    path = tmp_path / "state.json"; installer.install(path); before = len(fake.calls)
    installer.listener_check = lambda port: port == 4455
    with pytest.raises(FirewallError, match="listener_still_active"): installer.remove(path)
    assert path.exists() and fake.rules and len(fake.calls) == before
    assert not any("-X" in command for command, _ in fake.calls)
    assert not any("-F" in c for c, _ in fake.calls)
    installer.listener_check = lambda _: False
    assert installer.remove(path)["removed"]
    assert sum(c[1:] == ["-X", "123456789"] for c, _ in fake.calls) == 1


def test_failed_inspection_rolls_back_known_rules_and_always_releases_reference(tmp_path):
    class ChangedMain(FakePf):
        def __call__(self, command, **kwargs):
            result = super().__call__(command, **kwargs)
            if "-f" in command: self.main += "block out all\n"
            return result
    fake = ChangedMain(); path = tmp_path / "state.json"
    with pytest.raises(FirewallError, match="main_rules_changed"): PfInstaller(run=fake, listener_check=lambda _: False).install(path)
    assert not path.exists() and not fake.rules
    assert any(c[1:] == ["-X", "123456789"] for c, _ in fake.calls)


@pytest.mark.parametrize("interfaces,reason", [
    ("lo0 (skip)\nen0 (skip)\n", "non_loopback_skip"),
    ("lo0\nutun9 (skip)\n", "non_loopback_skip"),
    ("lo0\nen0\n  unknown flags\n", "interfaces_unrecognized"),
    ("lo0\nen0\nen0\n", "interfaces_unrecognized"),
    ("", "interfaces_unrecognized"),
])
def test_skipped_nonloopback_or_unknown_interfaces_refused_before_mutation(tmp_path, interfaces, reason):
    fake = FakePf(); fake.interfaces = interfaces
    with pytest.raises(FirewallError, match=reason):
        PfInstaller(run=fake, listener_check=lambda _: False).install(tmp_path / "state.json")
    assert not any("-E" in c or "-f" in c or "-F" in c for c, _ in fake.calls)


def test_interface_skip_drift_invalidates_otherwise_unchanged_attestation(tmp_path):
    fake = FakePf(); installer = PfInstaller(run=fake, listener_check=lambda _: False)
    path = tmp_path / "state.json"; installer.install(path)
    state = json.loads(path.read_text()); main = fake.main; rules = dict(fake.rules)
    fake.interfaces = "lo0 (skip)\nen0 (skip)\n"
    with pytest.raises(FirewallError, match="non_loopback_skip"): installer.inspect(state)
    assert fake.main == main and fake.rules == rules


def test_install_active_listener_refused_before_enable_or_anchor_mutation(tmp_path):
    fake = FakePf()
    with pytest.raises(FirewallError, match="listener_still_active"):
        PfInstaller(run=fake, listener_check=lambda _: True).install(tmp_path / "state.json")
    assert not fake.rules and not (tmp_path / "state.json").exists()
    assert not any("-E" in c or "-f" in c or "-F" in c or "-X" in c for c, _ in fake.calls)


@pytest.mark.parametrize("unknown", [False, True])
def test_failed_install_retains_protection_when_listener_appeared_or_cannot_be_verified(tmp_path, unknown):
    class ChangedMain(FakePf):
        def __call__(self, command, **kwargs):
            result = super().__call__(command, **kwargs)
            if "-f" in command: self.main += "block out all\n"
            return result
    fake = ChangedMain(); checks = []
    def listener_check(_):
        checks.append(1)
        if len(checks) == 1: return False  # before any PF write
        if unknown: raise FirewallError("pf_listener_check_unverified")
        return True
    installer = PfInstaller(run=fake, listener_check=listener_check); path = tmp_path / "state.json"
    with pytest.raises(FirewallError, match="cleanup_incomplete_state_retained"): installer.install(path)
    assert fake.rules and json.loads(path.read_text())["enable_token"] == "123456789"
    assert not any("-F" in c or "-X" in c for c, _ in fake.calls)
    installer.listener_check = lambda _: False
    assert installer.remove(path)["removed"] and not path.exists()
    assert sum(c[1:] == ["-X", "123456789"] for c, _ in fake.calls) == 1
