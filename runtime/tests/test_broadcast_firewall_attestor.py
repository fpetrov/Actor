"""Rootless Unix-socket fakes; no sudo/PF/OBS/listener modifications."""
import asyncio
import json
import os
from pathlib import Path
import time
import tempfile

import pytest

from actor_broadcast.obs_firewall_attestor import (AttestationError, FirewallAttestationClient,
    PfAttestor, peer_uid, verify_path)


@pytest.fixture
def tmp_path():
    # macOS sockaddr_un has a 104-byte path limit; pytest's default long
    # per-test folder is unsuitable even though the service path is short.
    directory = "/private/tmp" if Path("/private/tmp").is_dir() else tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="abpf-", dir=directory) as value:
        yield Path(value)


def proof():
    return {"verified": True, "port": 4455, "loopback_allowed": True,
            "non_loopback_ipv4_ipv6_denied": True}


def test_real_default_trust_policy_rejects_user_owned_socket_directory(tmp_path):
    if os.getuid() == 0: pytest.skip("This negative specifically exercises a nonroot directory")
    with pytest.raises(AttestationError, match="directory_unsafe"):
        verify_path(tmp_path / "service.sock", require_socket=False)


async def test_actual_unix_roundtrip_challenged_fresh_check_and_stale_invalidation(tmp_path):
    path = tmp_path / "service.sock"; calls = []
    def inspect(): calls.append(1); return proof()
    service = PfAttestor(tmp_path / "never-read", path, inspector=inspect,
        verify_filesystem=lambda *a, **k: None, administrator_uid=os.geteuid())
    await service.start()
    client = FirewallAttestationClient(path, max_age_seconds=.1,
        verify_filesystem=lambda _: None, administrator_uid=os.geteuid())
    try:
        assert await client.refresh() is True and client.verified and len(calls) == 1
        assert await client.refresh() is True and len(calls) == 2  # actual fresh inspect
        await asyncio.sleep(.11); assert not client.verified
    finally: await service.close()
    assert not path.exists() and not await client.refresh() and not client.verified


async def test_unprivileged_impostor_rejected_before_sending_nonce(tmp_path):
    path = tmp_path / "service.sock"; received = []
    async def handler(reader, writer):
        received.append(await reader.read()); writer.close()
    server = await asyncio.start_unix_server(handler, path=path)
    client = FirewallAttestationClient(path, verify_filesystem=lambda _: None, get_peer_uid=lambda _: 501)
    try:
        assert not await client.refresh() and client.last_error == "pf_attestor_peer_unverified"
        await asyncio.sleep(.01); assert received == [b""]
    finally: server.close(); await server.wait_closed()


@pytest.mark.parametrize("mutation", [
    lambda x: x.update(challenge="0" * 64),
    lambda x: x.update(port=4456),
    lambda x: x.update(verified=1),
    lambda x: x.update(checked_at_monotonic_ns=time.monotonic_ns() - 10_000_000_000),
    lambda x: x.update(non_loopback_ipv4_ipv6_denied=False),
])
async def test_replay_wrong_port_stale_or_weakened_proof_not_attestation(tmp_path, mutation):
    path = tmp_path / "service.sock"
    async def handler(reader, writer):
        query = json.loads(await reader.readline())
        result = {"schema": "actor.obs-pf-attestation.v1", "challenge": query["challenge"], "port": 4455,
                  "verified": True, "checked_at_monotonic_ns": time.monotonic_ns(), "loopback_allowed": True,
                  "non_loopback_ipv4_ipv6_denied": True}
        mutation(result); writer.write(json.dumps(result).encode() + b"\n"); await writer.drain(); writer.close()
    server = await asyncio.start_unix_server(handler, path=path)
    client = FirewallAttestationClient(path, verify_filesystem=lambda _: None, administrator_uid=os.geteuid())
    try: assert not await client.refresh() and not client.verified
    finally: server.close(); await server.wait_closed()


async def test_attestor_accepts_no_path_command_or_privileged_mutation_over_socket(tmp_path):
    path = tmp_path / "service.sock"; calls = []
    service = PfAttestor(tmp_path / "not-read", path, inspector=lambda: calls.append(1) or proof(),
        verify_filesystem=lambda *a, **k: None, administrator_uid=os.geteuid())
    await service.start()
    try:
        for request in ({"command": "disable"}, {"schema": "actor.obs-pf-query.v1", "challenge": "a" * 64, "port": 4455, "state": "/etc/pf.conf"}):
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(json.dumps(request).encode() + b"\n"); await writer.drain()
            result = json.loads(await reader.readline()); writer.close(); await writer.wait_closed()
            assert result["verified"] is False
        assert calls == []
    finally: await service.close()


async def test_repeated_timeouts_do_not_spawn_multiple_privileged_checks(tmp_path):
    import threading
    path = tmp_path / "service.sock"; entered = threading.Event(); release = threading.Event(); count = []
    def inspect(): count.append(1); entered.set(); release.wait(3); return proof()
    service = PfAttestor(tmp_path / "not-read", path, inspector=inspect,
        verify_filesystem=lambda *a, **k: None, administrator_uid=os.geteuid())
    await service.start()
    clients = [FirewallAttestationClient(path, timeout=.1, verify_filesystem=lambda _: None,
                 administrator_uid=os.geteuid()) for _ in range(3)]
    try:
        assert not await clients[0].refresh()
        assert entered.is_set()
        assert await asyncio.gather(clients[1].refresh(), clients[2].refresh()) == [False, False]
        assert len(count) == 1
    finally:
        release.set(); await service.close()
        if service._check_task: await service._check_task


async def test_fresh_previous_attestation_survives_poll_and_explicit_failure_revokes(tmp_path):
    import threading
    path = tmp_path / "service.sock"; entered = threading.Event(); release = threading.Event(); calls = []
    def inspect():
        calls.append(1)
        if len(calls) == 2: entered.set(); release.wait(1)
        return proof() if len(calls) < 3 else {"verified": False}
    service = PfAttestor(tmp_path / "not-read", path, inspector=inspect,
        verify_filesystem=lambda *a, **k: None, administrator_uid=os.geteuid())
    await service.start()
    client = FirewallAttestationClient(path, verify_filesystem=lambda _: None, administrator_uid=os.geteuid())
    try:
        assert await client.refresh()
        task = asyncio.create_task(client.refresh())
        for _ in range(50):
            if entered.is_set(): break
            await asyncio.sleep(.001)
        assert entered.is_set() and client.verified
        release.set(); assert await task and client.verified
        assert not await client.refresh() and not client.verified
    finally: release.set(); await service.close()
