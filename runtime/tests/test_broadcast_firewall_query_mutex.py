"""Client query ownership with fake socket IO; no service/PF/native processes."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
from types import SimpleNamespace as Obj

import pytest

from actor_broadcast import obs_firewall_attestor as module
from actor_broadcast.ownership import ChannelLease


@pytest.fixture
def tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp("pf-query").resolve()


class Exchange:
    def __init__(self):
        self.active = self.peak = self.requests = 0
        self.entered = asyncio.Event(); self.release = asyncio.Event()
        self.writers = []; self.mutation = None

    async def open(self, *args, **kwargs):
        self.active += 1; self.peak = max(self.peak, self.active)
        owner = self
        class Writer:
            query = None; closed = False
            def get_extra_info(self, name): return object()
            def write(self, raw): self.query = json.loads(raw); owner.requests += 1; owner.entered.set()
            async def drain(self): pass
            def close(self):
                if not self.closed: self.closed = True; owner.active -= 1
        writer = Writer(); self.writers.append(writer)
        class Reader:
            async def readuntil(self, separator):
                await owner.release.wait()
                value = {"schema": "actor.obs-pf-attestation.v2", "challenge": writer.query["challenge"],
                    "port": 4455, "clock_domain": module.CLOCK_DOMAIN, "verified": True,
                    "checked_at_monotonic_ns": module.attestation_clock_ns(), "loopback_allowed": True,
                    "non_loopback_ipv4_ipv6_denied": True}
                if owner.mutation: owner.mutation(value)
                return json.dumps(value).encode() + b"\n"
        return Reader(), writer


def setup(monkeypatch, tmp_path, *, timeout=.2):
    server = Exchange(); leases = []
    def factory(path):
        lease = ChannelLease("pf_query", directory=tmp_path / "private-locks")
        leases.append(lease); return lease
    monkeypatch.setattr(module, "_client_query_lease", factory)
    monkeypatch.setattr(module.asyncio, "open_unix_connection", server.open)
    def client():
        return module.FirewallAttestationClient("/canonical/service.sock", timeout=timeout,
            verify_filesystem=lambda _: None, get_peer_uid=lambda _: 0)
    return server, leases, client


async def test_two_independent_clients_share_actual_persistent_flock_without_overlapping_exchange(monkeypatch,tmp_path):
    server,leases,client=setup(monkeypatch,tmp_path)
    first,second=client(),client()
    a=asyncio.create_task(first.refresh());await asyncio.wait_for(server.entered.wait(),.5)
    b=asyncio.create_task(second.refresh());await asyncio.sleep(.025)
    assert server.requests==1 and leases[0].held and not leases[1].held
    assert first._query_lease is leases[0] and second._query_lease is leases[1]
    inode=(tmp_path/"private-locks"/leases[0].name).stat().st_ino
    server.release.set()
    assert await asyncio.gather(a,b)==[True,True]
    assert server.peak==1 and all(not lease.held for lease in leases)
    assert first.verified and second.verified
    assert first._query_lease is second._query_lease is None
    assert (tmp_path/"private-locks"/leases[0].name).stat().st_ino==inode


async def test_lock_wait_consumes_same_query_deadline_and_never_sends_nonce(monkeypatch,tmp_path):
    server,leases,client=setup(monkeypatch,tmp_path,timeout=.1)
    held=ChannelLease("pf_query",directory=tmp_path/"private-locks").acquire()
    try:
        value=client();value._verified_at=module.attestation_clock_ns()
        assert not await value.refresh()
        assert server.requests==0 and not value.verified and value._query_lease is None
        assert all(not lease.held for lease in leases)
    finally:held.release()


@pytest.mark.parametrize("cancel_while_held",[False,True])
async def test_cancel_waiter_or_owner_releases_exact_fd_but_not_other_clients_lease(monkeypatch,tmp_path,cancel_while_held):
    server,leases,client=setup(monkeypatch,tmp_path)
    other=None
    if not cancel_while_held:other=ChannelLease("pf_query",directory=tmp_path/"private-locks").acquire()
    value=client();task=asyncio.create_task(value.refresh())
    if cancel_while_held:await asyncio.wait_for(server.entered.wait(),.5)
    else:await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert not value.verified and value._query_lease is None and all(not lease.held for lease in leases)
    if other:assert other.held;other.release()
    # Cancellation cannot strand the persistent inode or disable later queries.
    server.release.set();assert await client().refresh()


@pytest.mark.parametrize("fault",["invalid_proof","wrong_peer","json"])
async def test_proof_or_peer_failure_releases_mutex_and_fails_closed(monkeypatch,tmp_path,fault):
    server,leases,client=setup(monkeypatch,tmp_path);server.release.set()
    if fault=="invalid_proof":server.mutation=lambda row:row.update(challenge="0"*64)
    if fault=="json":server.mutation=lambda row:row.update(extra="not-allowed")
    value=client()
    if fault=="wrong_peer":value.get_peer_uid=lambda _:501
    assert not await value.refresh() and not value.verified
    assert all(not lease.held for lease in leases)and value._query_lease is None


async def test_fresh_prior_proof_is_retained_while_waiting_but_not_extended(monkeypatch,tmp_path):
    server,leases,client=setup(monkeypatch,tmp_path)
    value=client();timestamp=module.attestation_clock_ns();value._verified_at=timestamp
    held=ChannelLease("pf_query",directory=tmp_path/"private-locks").acquire()
    task=asyncio.create_task(value.refresh());await asyncio.sleep(.01)
    assert value.verified and value._verified_at==timestamp and server.requests==0
    held.release();server.release.set();assert await task
    assert value._verified_at>=timestamp


async def test_synchronous_validation_overrun_cannot_evade_total_timeout(monkeypatch,tmp_path):
    server,leases,client=setup(monkeypatch,tmp_path,timeout=.1);server.release.set()
    value=client();loop=asyncio.get_running_loop();clock=loop.time;offset=[0]
    def late_validation(row):offset[0]=1
    server.mutation=late_validation
    monkeypatch.setattr(loop,"time",lambda:clock()+offset[0])
    assert not await value.refresh() and not value.verified
    assert all(not lease.held for lease in leases)


def fake_account(monkeypatch, *, uid=501, bad=None):
    monkeypatch.setattr(module.os,"geteuid",lambda:uid)
    monkeypatch.setattr(pwd,"getpwuid",lambda value:Obj(pw_dir="/accounts/private-user"))
    monkeypatch.setattr(Path,"resolve",lambda path,*a,**k:path)
    def metadata(path):
        mode=stat.S_IFDIR|0o755;owner=uid if str(path).startswith("/accounts/private-user")else 0
        if bad=="writable"and str(path)=="/accounts":mode|=0o020
        if bad=="symlink"and str(path)=="/accounts/private-user/.actor":mode=stat.S_IFLNK|0o777
        if bad=="foreign"and str(path)=="/accounts/private-user":owner=999
        return Obj(st_mode=mode,st_uid=owner)
    monkeypatch.setattr(Path,"lstat",metadata)


def test_namespace_uses_pwd_effective_uid_and_full_canonical_service_hash_not_environment(monkeypatch):
    fake_account(monkeypatch)
    monkeypatch.setenv("HOME","/foreign/env-home")
    a=module._client_query_lease("/canonical/service.sock")
    b=module._client_query_lease("/canonical/other.sock")
    assert a.directory==Path("/accounts/private-user/.actor/broadcast/pf-attestor-query-locks/501")/hashlib.sha256(b"/canonical/service.sock").hexdigest()
    assert a.directory!=b.directory
    fake_account(monkeypatch,uid=502)
    assert module._client_query_lease("/canonical/service.sock").directory!=a.directory


@pytest.mark.parametrize("bad",["writable","symlink","foreign","home_alias","service_alias"])
def test_unsafe_namespace_or_service_alias_refuses_before_creation(monkeypatch,bad):
    fake_account(monkeypatch,bad=bad)
    if bad.endswith("alias"):
        original=Path.resolve
        monkeypatch.setattr(Path,"resolve",lambda path,*a,**k:Path("/other")if str(path)==("/canonical"if bad=="service_alias"else"/accounts/private-user")else original(path))
    with pytest.raises(module.AttestationError,match="pf_attestor_query_lock_unsafe"):
        module._client_query_lease("/canonical/service.sock")


@pytest.mark.parametrize("unsafe",["symlink","hardlink","permissions"])
async def test_inherited_safe_lease_guards_reject_bad_lock_inode_without_request(monkeypatch,tmp_path,unsafe):
    server,leases,client=setup(monkeypatch,tmp_path)
    original=ChannelLease("pf_query",directory=tmp_path/"private-locks").acquire();original.release()
    lock=tmp_path/"private-locks"/original.name
    if unsafe=="symlink":lock.unlink();lock.symlink_to(tmp_path/"other")
    if unsafe=="hardlink":os.link(lock,tmp_path/"alias")
    if unsafe=="permissions":lock.chmod(0o644)
    assert not await client().refresh() and server.requests==0
