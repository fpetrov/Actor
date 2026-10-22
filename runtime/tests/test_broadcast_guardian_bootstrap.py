"""Inherited guardian bootstrap with fake descriptors/transport, no OS calls."""
import asyncio
import socket
import stat
import struct
from types import SimpleNamespace

import pytest

from actor_broadcast import guardian_client as module
from actor_broadcast import supervisor


@pytest.fixture
def bootstrap(monkeypatch):
    state=SimpleNamespace(peer_uid=501,peer_pid=99,peer_error=None,peer_checks=0,hello_valid=True,
        transports=[],socket_calls=0,starts=0,context_during_hello=None)
    class Connection:
        family=socket.AF_UNIX;type=socket.SOCK_STREAM
        name="";peername=""
        def __init__(self):self.closed=False;self.inheritable=True;self.blocking=True
        def getsockname(self):return self.name
        def getpeername(self):return self.peername
        def getsockopt(self,*args):return struct.pack("3i",state.peer_pid,state.peer_uid,20)
        def set_inheritable(self,value):self.inheritable=value
        def setblocking(self,value):self.blocking=value
        def close(self):
            self.closed=True
            for transport in state.transports:transport.queue.put_nowait(None)
    state.connection=Connection()
    def connection(*,fileno):
        assert fileno==77;state.socket_calls+=1;return state.connection
    def peer_uid(value):
        assert value is state.connection;state.peer_checks+=1
        if state.peer_error:raise state.peer_error
        return state.peer_uid
    class Transport:
        def __init__(self,connection):
            assert connection is state.connection and not connection.inheritable and not connection.blocking
            self.queue=asyncio.Queue();self.closed=False;state.transports.append(self)
        async def send(self,request):
            if request["op"]=="hello":
                assert module._context is None  # Verification is not installed before actual hello ACK.
                assert request["args"]=={"pid":123} and request["run_id"]is None
                state.starts+=1
                if state.context_during_hello is not None:module._context=state.context_during_hello
                value={"controller_pid":123,"supervised":state.hello_valid}
            else:
                assert request["op"] in {"heartbeat","shutdown"};value=True
            await self.queue.put({"v":1,"id":request["id"],"ok":True,"value":value,"output_status":{}})
        async def receive(self):
            value=await self.queue.get()
            if value is None:raise EOFError
            return value
        async def aclose(self):self.closed=True;state.connection.close()
    monkeypatch.setattr(module,"_context",None)
    socket_api=SimpleNamespace(socket=connection,AF_UNIX=socket.AF_UNIX,AF_INET=socket.AF_INET,
        SOCK_STREAM=socket.SOCK_STREAM,SOCK_DGRAM=socket.SOCK_DGRAM,SOL_SOCKET=socket.SOL_SOCKET)
    if hasattr(socket,"SO_PEERCRED"):socket_api.SO_PEERCRED=socket.SO_PEERCRED
    monkeypatch.setattr(module,"socket",socket_api)
    monkeypatch.setattr(module,"peer_uid",peer_uid)
    monkeypatch.setattr(supervisor,"FramedSocket",Transport)
    monkeypatch.setattr(module,"os",SimpleNamespace(fstat=lambda fd:SimpleNamespace(st_mode=stat.S_IFSOCK|0o600),
        getpid=lambda:123,getppid=lambda:99,geteuid=lambda:501,getpgrp=lambda:123,getsid=lambda pid:123))
    return state


async def test_darwin_without_python_socket_peer_apis_uses_vetted_uid_helper_and_real_hello(bootstrap,monkeypatch):
    monkeypatch.delattr(module.socket,"SO_PEERCRED",raising=False)
    assert not hasattr(bootstrap.connection,"getpeereid")
    client=await module.GuardianClient.from_inherited_fd(77)
    assert bootstrap.peer_checks==1 and bootstrap.starts==1
    assert module._context is client and module.inherited_group_options()=={"start_new_session":False}
    await client.aclose()
    assert bootstrap.transports[0].closed and bootstrap.connection.closed


async def test_linux_still_requires_exact_parent_pid_in_addition_to_same_uid(bootstrap,monkeypatch):
    monkeypatch.setattr(module.socket,"SO_PEERCRED",17,raising=False)
    bootstrap.peer_pid=100
    with pytest.raises(module.GuardianError,match="guardian_peer_invalid"):
        await module.GuardianClient.from_inherited_fd(77)
    assert bootstrap.peer_checks==1 and bootstrap.starts==0 and not bootstrap.transports
    assert module._context is None and bootstrap.connection.closed


async def test_linux_valid_parent_peer_installs_context_after_hello(bootstrap,monkeypatch):
    monkeypatch.setattr(module.socket,"SO_PEERCRED",17,raising=False)
    client=await module.GuardianClient.from_inherited_fd(77)
    assert bootstrap.peer_checks==1 and bootstrap.starts==1 and module._context is client
    await client.aclose()


@pytest.mark.parametrize("change,code",[("uid","guardian_peer_invalid"),("uid_bool","guardian_peer_invalid"),
    ("peer_failure","guardian_peer_unverified"),("family","guardian_private_socket_required"),
    ("type","guardian_private_socket_required"),("local_name","guardian_private_socket_required"),
    ("peer_name","guardian_private_socket_required"),("group","guardian_isolated_controller_required"),
    ("session","guardian_isolated_controller_required"),("context","guardian_context_exists")])
async def test_metadata_peer_and_isolation_refusals_never_start_or_install_context(bootstrap,monkeypatch,change,code):
    monkeypatch.delattr(module.socket,"SO_PEERCRED",raising=False)
    existing=None
    if change=="uid":bootstrap.peer_uid=502
    if change=="uid_bool":bootstrap.peer_uid=True
    if change=="peer_failure":bootstrap.peer_error=OSError("private details never become the code")
    if change=="family":bootstrap.connection.family=socket.AF_INET
    if change=="type":bootstrap.connection.type=socket.SOCK_DGRAM
    if change=="local_name":bootstrap.connection.name="named-socket"
    if change=="peer_name":bootstrap.connection.peername="named-peer"
    if change=="group":monkeypatch.setattr(module.os,"getpgrp",lambda:88)
    if change=="session":monkeypatch.setattr(module.os,"getsid",lambda pid:88)
    if change=="context":existing=object();monkeypatch.setattr(module,"_context",existing)
    with pytest.raises(module.GuardianError,match=code):await module.GuardianClient.from_inherited_fd(77)
    assert bootstrap.starts==0 and not bootstrap.transports and bootstrap.connection.closed
    assert module._context is existing


@pytest.mark.parametrize("fd",[True,2,-1,"77"])
async def test_invalid_descriptor_never_constructs_a_socket_or_bootstrap_context(bootstrap,fd):
    with pytest.raises(module.GuardianError,match="guardian_private_socket_required"):
        await module.GuardianClient.from_inherited_fd(fd)
    assert bootstrap.socket_calls==0 and module._context is None


async def test_non_socket_metadata_refuses_before_adopting_descriptor(bootstrap,monkeypatch):
    monkeypatch.setattr(module.os,"fstat",lambda fd:SimpleNamespace(st_mode=stat.S_IFREG|0o600))
    with pytest.raises(module.GuardianError,match="guardian_private_socket_required"):
        await module.GuardianClient.from_inherited_fd(77)
    assert bootstrap.socket_calls==0 and module._context is None


async def test_verified_peer_but_invalid_actual_hello_never_installs_spawn_context(bootstrap,monkeypatch):
    monkeypatch.delattr(module.socket,"SO_PEERCRED",raising=False)
    bootstrap.hello_valid=False
    with pytest.raises(module.GuardianError,match="guardian_handshake_invalid"):
        await module.GuardianClient.from_inherited_fd(77)
    await asyncio.sleep(0)  # Closing the fake descriptor delivers EOF to its real receiver task.
    assert module._context is None and bootstrap.connection.closed and bootstrap.starts==1
    assert bootstrap.transports[0].closed


async def test_context_installed_during_hello_is_not_overwritten_and_losing_client_is_drained(bootstrap,monkeypatch):
    monkeypatch.delattr(module.socket,"SO_PEERCRED",raising=False)
    bootstrap.context_during_hello=object()
    with pytest.raises(module.GuardianError,match="guardian_context_exists"):
        await module.GuardianClient.from_inherited_fd(77)
    assert module._context is bootstrap.context_during_hello
    assert bootstrap.transports[0].closed and bootstrap.connection.closed
