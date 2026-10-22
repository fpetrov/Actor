"""Guardian hello identity seal over fake IO; no process/socket/account calls."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from actor_broadcast import cli
from actor_broadcast.coordinator import CoordinatorError
from actor_broadcast.guardian_client import GuardianClient
from actor_broadcast.supervisor import GuardianHost
from test_broadcast_guardian_adapters import Group, Lease, services, transport_pair


PID = 321
ACK = {"v":1,"id":1,"ok":True,"value":{"controller_pid":PID,"supervised":True},"output_status":{}}


def identity_fixture(monkeypatch):
    def forbidden(*_args, **_kwargs): pytest.fail("Never poll, wait or signal a metadata-only seal")
    process = NS(pid=PID,returncode=None,poll=forbidden,wait=forbidden,terminate=forbidden,kill=forbidden)
    group = NS(process=process,pid=PID,closed=False,leader_exited=lambda pid:False)
    identity = {"pid":PID,"started":"Fri Oct 23 01:09:08 2026","command_sha256":"b"*64}
    calls = []
    def read(pid):
        assert pid == PID; calls.append(pid); return dict(identity)
    monkeypatch.setattr(cli,"process_identity",read)
    monkeypatch.setattr(cli.os,"getpgid",lambda pid:pid)
    monkeypatch.setattr(cli.os,"getsid",lambda pid:pid)
    return process, group, identity, calls


def test_identity_is_current_reserved_child_not_initial_framework_stub(monkeypatch):
    process, group, current, calls = identity_fixture(monkeypatch)
    assert cli._controller_identity(process,group) == current
    assert calls == [PID] and process.returncode is None


@pytest.mark.parametrize("change", ["other_handle","other_pid","closed","reaped","exited","unknown_exit","wrong_pgid","wrong_sid"])
def test_identity_seal_refuses_lost_reservation_before_metadata(monkeypatch,change):
    process, group, _, calls = identity_fixture(monkeypatch)
    if change == "other_handle": group.process = NS(pid=PID,returncode=None)
    elif change == "other_pid": group.pid = PID+1
    elif change == "closed": group.closed = True
    elif change == "reaped": process.returncode = 0
    elif change == "exited": group.leader_exited = lambda _:True
    elif change == "unknown_exit": group.leader_exited = lambda _:None
    elif change == "wrong_pgid": monkeypatch.setattr(cli.os,"getpgid",lambda _:PID+1)
    elif change == "wrong_sid": monkeypatch.setattr(cli.os,"getsid",lambda _:PID+1)
    with pytest.raises(CoordinatorError,match="^broadcast_guardian_identity_unverified$"):
        cli._controller_identity(process,group)
    assert calls == []


@pytest.mark.parametrize("value", [None, {}, {"pid":PID,"started":"x","command_sha256":"x"},
    {"pid":PID+1,"started":"x","command_sha256":"b"*64},
    {"pid":PID,"started":"private\nmessage","command_sha256":"b"*64},
    {"pid":PID,"started":"x","command_sha256":"b"*64,"extra":"private"}])
def test_unknown_or_wrong_identity_metadata_fails_closed(monkeypatch,value):
    process, group, _, _ = identity_fixture(monkeypatch)
    monkeypatch.setattr(cli,"process_identity",lambda _:value)
    with pytest.raises(CoordinatorError,match="^broadcast_guardian_identity_unverified$"):
        cli._controller_identity(process,group)


def test_exit_during_metadata_does_not_seal_zombie_or_release_pid(monkeypatch):
    process, group, identity, _ = identity_fixture(monkeypatch)
    def read(_): group.leader_exited=lambda _:True; return identity
    monkeypatch.setattr(cli,"process_identity",read)
    with pytest.raises(CoordinatorError,match="^broadcast_guardian_identity_unverified$"):
        cli._controller_identity(process,group)
    assert process.returncode is None and not group.closed


async def test_transparent_ack_gate_waits_for_seal_and_cannot_rebind():
    entered, release = asyncio.Event(), asyncio.Event(); sent = []; calls = []
    async def send(value): sent.append(value)
    async def seal():
        calls.append("seal"); entered.set(); await asyncio.wait_for(release.wait(),1)
    transport = cli._IdentitySealedTransport(NS(send=send),PID,seal)
    sending = asyncio.create_task(transport.send(ACK))
    try:
        await asyncio.wait_for(entered.wait(),.5)
        assert sent == [] and not transport.sealed
        with pytest.raises(CoordinatorError,match="reseal_forbidden"): await transport.send(ACK)
        release.set(); await asyncio.wait_for(sending,.5)
        assert sent == [ACK] and sent[0] is ACK and transport.sealed
        with pytest.raises(CoordinatorError,match="reseal_forbidden"): await transport.send(ACK)
        assert calls == ["seal"] and sent == [ACK]
    finally:
        release.set(); await asyncio.gather(sending,return_exceptions=True)


@pytest.mark.parametrize("message", [
    {"v":1,"id":1,"ok":False,"error":"supervisor_handshake_rejected"},
    {**ACK,"value":{"controller_pid":PID+1,"supervised":True}},
    {**ACK,"value":{"controller_pid":PID,"supervised":1}},
    {**ACK,"v":True}, {**ACK,"id":True}, {**ACK,"value":True},
])
async def test_rejected_or_malformed_reply_never_claims_authenticated_identity(message):
    sent = []
    async def send(value): sent.append(value)
    async def seal(): pytest.fail("No valid accepted hello")
    transport = cli._IdentitySealedTransport(NS(send=send),PID,seal)
    await transport.send(message)
    assert sent == [message] and not transport.sealed


async def test_real_host_client_hello_seals_after_auth_before_child_bootstrap(tmp_path,monkeypatch):
    process, group, stable, calls = identity_fixture(monkeypatch)
    class OwnedGroup(Group):
        def __init__(self): self.pid=PID;self.closed=False
        async def terminate(self): self.closed=True;return True
    retained = OwnedGroup()
    host = GuardianHost("fedorr_codex",retained,services(tmp_path),lease_factory=Lease)
    parent, child = transport_pair(); published = {"controller":None}; events=[]
    async def seal():
        assert host._hello and host.run_id is None  # Actual host accepted the exact inherited request.
        published["controller"] = cli._controller_identity(process,group)
        events.append("sealed")
    wrapped = cli._IdentitySealedTransport(parent,PID,seal)
    owner = asyncio.create_task(host.serve(wrapped)); client = GuardianClient(child,controller_pid=PID)
    try:
        assert published["controller"] is None and calls == []
        await asyncio.wait_for(client.start(),1)
        assert published["controller"] == stable and events == ["sealed"]
        assert wrapped.sealed and host.lease is None
        # This is the child's ordinary post-hello self-capture boundary, now
        # equal to the parent seal instead of an earlier framework-stub hash.
        child_self = cli.process_identity(PID)
        assert child_self == published["controller"]
        stable["command_sha256"] = "c"*64
        await asyncio.wait_for(client._request("status",{},run_id=None),1)
        assert published["controller"]["command_sha256"] == "b"*64 and events == ["sealed"]
        await asyncio.wait_for(client.aclose(),1)
        assert await asyncio.wait_for(owner,1) and retained.closed
    finally:
        await client.aclose()
        if not owner.done(): host.fail("supervisor_test_cleanup")
        await asyncio.wait_for(owner,1)
