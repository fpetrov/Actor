"""Guardian clocks, transactions and fake IO only: no child/socket/OBS starts."""
import asyncio
import json
import signal
import socket
import struct
from types import SimpleNamespace

import pytest

from actor_broadcast.supervisor import (
    FramedSocket, GuardianHost, RetainedControllerGroup, SupervisorError,
    decode_packet, encode_packet, parse_request, unreaped_controller_exited,
)


RUN = "a" * 32
OTHER = "b" * 32


class Clock:
    def __init__(self): self.value = 100.0
    def __call__(self): return self.value
    async def sleep(self, seconds): self.value += seconds; await asyncio.sleep(0)


class Lease:
    def __init__(self, channel, log): self.channel, self.log, self.held = channel, log, False
    def acquire(self): self.held = True; self.log.append("lease_acquired"); return self
    def release(self): self.held = False; self.log.append("lease_released")


class Group:
    pid = 1234567
    def __init__(self, log): self.log, self.closed, self.fail = log, False, False
    async def terminate(self):
        self.log.append("group_cleanup")
        if self.fail: raise SupervisorError("fixture_group_failed")
        self.closed = True; return True


class Services:
    def __init__(self, log):
        self.log = log; self.run_clean = True; self.close_fail = False
        self.begin_fail = False; self.pending = None; self.mutating = False
    def bind_controller(self, group): self.group = group
    def bind_unsafe(self, callback): self.unsafe = callback
    async def begin_run(self, room, run):
        self.log.append("begin")
        if self.begin_fail: raise RuntimeError("DO_NOT_COPY_PRIVATE_ERROR")
    def validate_call(self, op, args):
        if args: raise SupervisorError("fixture_invalid_arguments")
    async def call(self, op, args):
        self.log.append(op)
        self.mutating = True  # Ownership/intent precedes the fake wire await.
        if self.pending is not None: await self.pending.wait()
        self.mutating = False
        return {"accepted": True}
    def status(self): return {"ready": True, "owned_stream": self.mutating}
    async def cleanup_run(self):
        assert not self.mutating, "cleanup must not precede the retained transaction"
        self.log.append("run_cleanup")
        return self.run_clean
    async def close(self):
        self.log.append("services_close")
        if self.close_fail: raise RuntimeError("PRIVATE")


def setup(**options):
    log = []; clock = Clock(); group = Group(log); services = Services(log)
    owner = GuardianHost("fedorr_codex", group, services, clock=clock,
        lease_factory=lambda channel: Lease(channel, log), **options)
    return owner, services, group, clock, log


def request(number, op, args=None, run=None):
    return {"v": 1, "id": number, "op": op, "run_id": run, "args": args or {}}


class Transport:
    def __init__(self): self.incoming = asyncio.Queue(); self.sent = []; self.closed = False
    async def send(self, value): self.sent.append(value)
    async def receive(self):
        item = await self.incoming.get()
        if item is EOFError: raise EOFError
        return item
    async def aclose(self): self.closed = True


async def settled(predicate):
    for _ in range(200):
        if predicate(): return
        await asyncio.sleep(0)
    pytest.fail("fake operation did not settle")


def test_unicode_frame_roundtrip_and_no_generic_process_rpc():
    value = request(1, "prepare_output", {"renderer_url": "http://127.0.0.1/голос"}, RUN)
    assert decode_packet(encode_packet(value)) == value
    assert parse_request(value) is value
    for op in ("shell", "spawn", "GetStreamServiceSettings", "execute", "delete_room"):
        with pytest.raises(SupervisorError): parse_request(request(2, op))


@pytest.mark.parametrize("raw", [
    b'{"v":1,"v":1}', b'{"number":NaN}', b'{"number":Infinity}',
    b'[]', b'"private"', b'\xff',
])
def test_decoder_rejects_duplicate_invalid_nonfinite_or_nonobject_json(raw):
    with pytest.raises(SupervisorError): decode_packet(struct.pack("!I", len(raw)) + raw)


@pytest.mark.parametrize("value", [
    {"text": "x" * 4097}, {"x": float("nan")}, {"x": float("inf")},
    {"x": 2**63}, {"x": "\x00"}, {"x": "\ud800"}, {"x": [0] * 256},
    {"x": [[[[[[[[[0]]]]]]]]]}, {"x": object()},
])
def test_encoder_bounds_before_any_io(value):
    with pytest.raises(SupervisorError): encode_packet(value)


@pytest.mark.parametrize("field,value", [("v", True), ("id", True), ("id", 0),
    ("id", 2**53), ("args", []), ("run_id", "foreign"), ("op", [])])
def test_request_fields_have_strict_types(field, value):
    item = request(1, "hello"); item[field] = value
    with pytest.raises(SupervisorError): parse_request(item)


def test_framed_socket_requires_unnamed_inherited_unix_pair_without_opening_any_socket():
    class Fake:
        family = socket.AF_UNIX; type = socket.SOCK_STREAM
        def __init__(self): self.changes = []
        def getsockname(self): return ""
        def getpeername(self): return ""
        def setblocking(self, value): self.changes.append(("blocking", value))
        def set_inheritable(self, value): self.changes.append(("inheritable", value))
    fake = Fake(); channel = FramedSocket(fake)
    assert channel.socket is fake and fake.changes == [("blocking", False), ("inheritable", False)]
    fake.family = socket.AF_INET
    with pytest.raises(SupervisorError): FramedSocket(fake)
    for fd in (True, 0, 2, -1):
        with pytest.raises(SupervisorError): FramedSocket.from_inherited_fd(fd)


async def test_per_run_lease_repeated_preview_cleanup_and_global_forward_lifetime():
    host, services, group, clock, log = setup()
    await host.begin_run("ABC123", RUN)
    with pytest.raises(SupervisorError): await host.begin_run("ABC123", OTHER)
    assert log == ["lease_acquired", "begin"]
    assert await host.end_run() is True
    assert "services_close" not in log and not host.terminal
    await host.begin_run("DEF456", OTHER)
    assert await host.shutdown() is True
    assert log[-4:] == ["run_cleanup", "group_cleanup", "services_close", "lease_released"]
    assert host.closed and host.lease is None and group.closed


async def test_partial_prepare_failure_retains_lease_until_explicit_cleanup():
    host, services, group, clock, log = setup(); services.begin_fail = True
    with pytest.raises(RuntimeError): await host.begin_run("ABC123", RUN)
    assert host.run_id == RUN and host.lease.held
    assert await host.shutdown() is True
    assert log[-1] == "lease_released"


async def test_unverified_run_cleanup_kills_owned_group_but_keeps_lease_and_forward_for_retry():
    host, services, group, clock, log = setup(); await host.begin_run("ABC123", RUN)
    services.run_clean = False
    assert await host.shutdown("supervisor_controller_eof") is False
    assert group.closed and host.lease.held and host.cleanup_failures == ("run",)
    assert "services_close" not in log
    services.run_clean = True
    assert await host.retry_cleanup() is True
    assert host.closed and host.terminal
    with pytest.raises(SupervisorError): await host.begin_run("ABC123", OTHER)


async def test_group_failure_or_service_close_failure_cannot_release_lease():
    host, services, group, clock, log = setup(); await host.begin_run("ABC123", RUN)
    group.fail = True
    assert await host.shutdown() is False and host.lease.held
    assert "services_close" not in log
    group.fail = False; services.close_fail = True
    assert await host.retry_cleanup() is False and host.lease.held
    services.close_fail = False
    assert await host.retry_cleanup() is True and log[-1] == "lease_released"


@pytest.mark.parametrize("operation", ["start_stream", "observe", "prepare_output"])
async def test_pending_transaction_is_not_cancelled_or_cleaned_before_it_settles(operation):
    host, services, group, clock, log = setup(operation_timeout=.001)
    await host.begin_run("ABC123", RUN)
    services.pending = asyncio.Event()
    host._operation = asyncio.create_task(services.call(operation, {}))
    await settled(lambda: services.mutating)
    assert await host.shutdown("supervisor_controller_eof") is False
    assert group.closed and host.lease.held and "run_cleanup" not in log
    assert host.cleanup_failures == ("operation",) and not host._operation.cancelled()
    services.pending.set(); await host._operation
    assert await host.retry_cleanup() is True
    assert log.index(operation) < log.index("run_cleanup") < log.index("lease_released")


async def test_cleanup_wait_cancellation_keeps_the_sole_cleanup_owner():
    host, services, group, clock, log = setup(); await host.begin_run("ABC123", RUN)
    gate = asyncio.Event()
    original = services.cleanup_run
    async def delayed(): await gate.wait(); return await original()
    services.cleanup_run = delayed
    waiting = asyncio.create_task(host.shutdown()); await settled(lambda: host._cleanup_task is not None)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError): await waiting
    assert not host._cleanup_task.cancelled() and host.lease.held
    gate.set(); assert await host._cleanup_task is True


async def test_cancelled_server_starts_cleanup_before_cancellable_transport_drain():
    host, services, group, clock, log = setup(); transport = Transport()
    await host.begin_run("ABC123", RUN)
    server = asyncio.create_task(host.serve(transport))
    await asyncio.sleep(0); server.cancel()
    with pytest.raises(asyncio.CancelledError): await server
    assert host.terminal and host._cleanup_task is not None
    assert await host.shutdown() is True and group.closed and host.lease is None


async def test_pending_operation_timeout_is_terminal_and_never_frees_unknown_start_ownership():
    host, services, group, clock, log = setup(operation_timeout=.001)
    transport = host._transport = Transport(); host._hello = True
    await host.begin_run("ABC123", RUN); services.pending = asyncio.Event()
    await host._receive_request(request(1, "start_stream", {}, RUN))
    await asyncio.sleep(.005)
    assert host.terminal and host.reason == "supervisor_operation_timeout"
    assert await host.shutdown() is False and host.lease.held
    assert "run_cleanup" not in log
    services.pending.set(); await host._operation
    assert await host.retry_cleanup() is True


async def test_global_heartbeat_does_not_race_run_transition_and_cannot_revive_terminal():
    host, services, group, clock, log = setup()
    host._hello = True
    host.heartbeat(1, None); await host.begin_run("ABC123", RUN)
    clock.value += 1; host.heartbeat(2, None)
    for sequence, scope in ((2, None), (True, None), (3, RUN)):
        with pytest.raises(SupervisorError): host.heartbeat(sequence, scope)
    assert host.last_heartbeat == 101
    clock.value = 103
    assert host.check_deadline() is False and host.reason == "supervisor_heartbeat_lost"
    with pytest.raises(SupervisorError): host.heartbeat(100, None)
    assert await host.shutdown() is True


async def test_hello_peer_identity_and_replayed_request_are_not_authorised():
    host, services, group, clock, log = setup(); host._transport = Transport()
    with pytest.raises(SupervisorError): await host._receive_request(request(1, "hello", {"pid": 777}))
    assert not host._hello
    await host._receive_request(request(2, "hello", {"pid": group.pid}))
    with pytest.raises(SupervisorError): await host._receive_request(request(2, "heartbeat", {"sequence": 1}))
    assert host._heartbeat_sequence == 0
    await host.shutdown()


async def test_operation_errors_are_constant_codes_without_private_error_text():
    host, services, group, clock, log = setup(); transport = host._transport = Transport()
    await host._receive_request(request(1, "hello", {"pid": group.pid}))
    await host._receive_request(request(2, "begin_run", {"room_code": "ABC123"}, RUN))
    await settled(lambda: any(row.get("id") == 2 for row in transport.sent))
    await host._receive_request(request(3, "start_stream", {"SECRET": "not allowed"}, RUN))
    await settled(lambda: any(row.get("id") == 3 for row in transport.sent))
    reply = next(row for row in transport.sent if row.get("id") == 3)
    assert reply["ok"] is False and reply["error"] == "fixture_invalid_arguments"
    assert "SECRET" not in json.dumps(reply) and "start_stream" not in log
    await host.shutdown()


async def test_receiver_keeps_heartbeats_live_during_pending_start_and_eof_drains_owner():
    host, services, group, clock, log = setup(); transport = Transport()
    server = asyncio.create_task(host.serve(transport))
    await transport.incoming.put(request(1, "hello", {"pid": group.pid}))
    await settled(lambda: host._hello)
    await transport.incoming.put(request(2, "begin_run", {"room_code": "ABC123"}, RUN))
    await settled(lambda: any(row.get("id") == 2 for row in transport.sent))
    services.pending = asyncio.Event()
    await transport.incoming.put(request(3, "start_stream", {}, RUN))
    await settled(lambda: services.mutating)
    clock.value += 1
    await transport.incoming.put(request(4, "heartbeat", {"sequence": 1}))
    await settled(lambda: host._heartbeat_sequence == 1)
    await transport.incoming.put(request(5, "start_record", {}, RUN))
    await settled(lambda: any(row.get("id") == 5 for row in transport.sent))
    assert next(row for row in transport.sent if row.get("id") == 5)["error"] == "supervisor_busy"
    await transport.incoming.put(EOFError)
    services.pending.set()
    assert await server is True and host.reason == "supervisor_controller_eof"
    assert group.closed and transport.closed and log[-1] == "lease_released"


def retained_group(*, members_after_kill=(), invalid_members=None, wait_fail=False):
    clock = Clock(); process = SimpleNamespace(pid=1234567, returncode=None)
    signals = []; waits = []
    def members(pid):
        assert pid == process.pid
        if invalid_members is not None: return invalid_members
        return (process.pid, 777) if not signals or signals[-1][1] != signal.SIGKILL else (process.pid, *members_after_kill)
    def send(pid, sig): signals.append((pid, sig))
    async def wait(timeout):
        waits.append(timeout)
        if wait_fail: raise TimeoutError
        process.returncode = -9; return -9
    group = RetainedControllerGroup(process, group_members=members, verify_group=lambda pid: pid,
        send_signal=send, wait_child=wait, leader_exited=lambda pid: True,
        clock=clock, sleep=clock.sleep, term_grace=.1, drain_timeout=.05)
    return group, process, signals, waits, clock


async def test_group_uses_only_exact_retained_pgid_and_reaps_after_all_others_disappear():
    group, process, signals, waits, clock = retained_group()
    assert await group.terminate() is True
    assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    assert waits == [.05] and group.closed
    await group.terminate(); assert len(signals) == 2 and len(waits) == 1


async def test_live_group_member_prevents_reaping_or_success_and_retry_does_not_resignal():
    group, process, signals, waits, clock = retained_group(members_after_kill=(777,))
    with pytest.raises(SupervisorError, match="supervisor_group_not_drained"): await group.terminate()
    assert process.returncode is None and not waits and not group.closed
    group.group_members = lambda pid: (pid,)
    assert await group.terminate() is True and len(signals) == 2 and len(waits) == 1


@pytest.mark.parametrize("members", [(True,), (1,), (777, 777), "unknown", list(range(2, 260))])
async def test_unknown_group_membership_never_signals_a_process(members):
    group, process, signals, waits, clock = retained_group(invalid_members=members)
    with pytest.raises(SupervisorError): await group.terminate()
    assert not signals and not waits and not group.closed


async def test_accidental_reap_during_term_grace_prevents_later_group_signal():
    group, process, signals, waits, clock = retained_group()
    async def invalid_sleep(seconds): process.returncode = 0
    group.sleep = invalid_sleep
    with pytest.raises(SupervisorError, match="supervisor_group_reaped_early"): await group.terminate()
    assert signals == [(process.pid, signal.SIGTERM)] and not waits


async def test_failed_wait_retains_owner_without_resending_to_possibly_reused_group():
    group, process, signals, waits, clock = retained_group(wait_fail=True)
    with pytest.raises(TimeoutError): await group.terminate()
    assert not group.closed and process.returncode is None
    async def recovered(timeout): process.returncode = -9; return -9
    group.wait_child = recovered
    assert await group.terminate() is True and len(signals) == 2


@pytest.mark.parametrize("gap", [2, 3])
async def test_late_heartbeat_cannot_revive_expired_gap_before_timer_runs(gap):
    host, services, group, clock, log = setup(); host._hello = True
    clock.value += gap
    with pytest.raises(SupervisorError): host.heartbeat(1, None)
    assert host.terminal and host.reason == "supervisor_heartbeat_lost"
    assert host.last_heartbeat == 100 and host._heartbeat_sequence == 0
    await host.shutdown()


@pytest.mark.parametrize("gap", [10, 11])
async def test_late_hello_cannot_reset_startup_deadline_before_timer_runs(gap):
    host, services, group, clock, log = setup(); host._transport = Transport()
    clock.value += gap
    with pytest.raises(SupervisorError): await host._receive_request(request(1, "hello", {"pid": group.pid}))
    assert not host._hello and host.reason == "supervisor_handshake_timeout"
    assert host.last_heartbeat == 100
    await host.shutdown()


async def test_unsafe_storm_has_one_retained_notification_and_shutdown_drains_it():
    host, services, group, clock, log = setup(); await host.begin_run("ABC123", RUN)
    gate = asyncio.Event()
    class Stalled(Transport):
        async def send(self, value): await gate.wait(); self.sent.append(value)
    transport = host._transport = Stalled()
    for _ in range(1000): services.unsafe("obs_graph_changed")
    assert host.run_unsafe and len(host._replies) == 1
    await asyncio.sleep(0)
    assert await host.shutdown() is True
    assert transport.closed and not host._replies and not transport.sent
    services.unsafe("obs_graph_changed")
    assert not host._replies and host.terminal


async def test_stalled_operation_replies_are_bounded_before_another_mutation():
    host, services, group, clock, log = setup(); host._hello = True
    await host.begin_run("ABC123", RUN)
    gate = asyncio.Event()
    class Stalled(Transport):
        async def send(self, value): await gate.wait()
    host._transport = Stalled()
    for index in range(1, 5):
        await host._receive_request(request(index, "audit_output", run=RUN))
        await settled(lambda: host._operation.done())
    assert len(host._replies) == 4
    with pytest.raises(SupervisorError, match="supervisor_reply_backpressure"):
        await host._receive_request(request(5, "start_stream", run=RUN))
    assert "start_stream" not in log and host.terminal
    assert await host.shutdown() is True and not host._replies


async def test_send_deadline_includes_waiting_for_the_send_lock():
    class Fake:
        family = socket.AF_UNIX; type = socket.SOCK_STREAM
        def getsockname(self): return ""
        def getpeername(self): return ""
        def setblocking(self, value): pass
        def set_inheritable(self, value): pass
    framed = FramedSocket(Fake(), timeout=.001)
    await framed._send_lock.acquire()
    try:
        with pytest.raises(TimeoutError): await framed.send({"v": 1})
    finally: framed._send_lock.release()


async def test_leader_must_have_actually_exited_before_membership_can_prove_drain():
    group, process, signals, waits, clock = retained_group()
    group.leader_exited = lambda pid: False
    with pytest.raises(SupervisorError, match="supervisor_group_not_drained"): await group.terminate()
    assert not waits and process.returncode is None
    # A fork racing the signal dispatch is observed only after the leader has
    # genuinely stopped executing, not inferred from SIGKILL delivery.
    group.leader_exited = lambda pid: True
    group.group_members = lambda pid: (pid, 888)
    with pytest.raises(SupervisorError, match="supervisor_group_not_drained"): await group.terminate()
    assert not waits and len(signals) == 2
    group.group_members = lambda pid: (pid,)
    assert await group.terminate() is True and len(signals) == 2


async def test_already_exited_unreaped_sole_leader_needs_no_group_signal_on_darwin():
    group, process, signals, waits, clock = retained_group()
    group.group_members = lambda pid: (pid,)
    def deny_signal(*args): raise PermissionError(1, "fixture signal would fail")
    group.send_signal = deny_signal
    assert await group.terminate() is True
    assert process.returncode == -9 and not signals and waits == [.05]


@pytest.mark.parametrize("empty_members", [False, True])
async def test_term_exits_sole_leader_and_skips_unnecessary_zombie_group_kill(empty_members):
    group, process, signals, waits, clock = retained_group()
    group.leader_exited = lambda pid: bool(signals)
    group.group_members = lambda pid: () if signals and empty_members else (pid,)
    def send(pid, sig):
        assert sig == signal.SIGTERM, "KILL on an exited sole leader would fail on Darwin"
        signals.append((pid, sig))
    async def wait(timeout):
        waits.append(timeout); process.returncode = -15; return -15
    group.send_signal, group.wait_child = send, wait
    assert await group.terminate() is True
    assert signals == [(process.pid, signal.SIGTERM)] and waits == [.05]
    assert group.closed and process.returncode == -15


async def test_post_term_exit_probe_precedes_membership_and_final_fork_still_gets_kill():
    group, process, signals, waits, clock = retained_group()
    state = {"child":False}; order=[]
    def exited(pid):
        order.append("exit")
        if signals: state["child"] = signals[-1][1] != signal.SIGKILL
        return bool(signals)
    def members(pid):
        order.append("members")
        return (pid,777) if state["child"] else (pid,)
    def send(pid,sig):
        order.append(sig); signals.append((pid,sig))
        if sig == signal.SIGKILL: state["child"] = False
    group.leader_exited, group.group_members, group.send_signal = exited,members,send
    assert await group.terminate() is True
    assert order[:6] == ["exit","members",signal.SIGTERM,"exit","members",signal.SIGKILL]
    assert signals == [(process.pid,signal.SIGTERM),(process.pid,signal.SIGKILL)]
    assert waits == [.05] and group.closed


async def test_live_group_permission_failure_after_term_never_reaps_or_reports_success():
    group, process, signals, waits, clock = retained_group()
    group.leader_exited = lambda pid: False
    group.group_members = lambda pid: (pid,)
    def send(pid,sig):
        signals.append((pid,sig))
        if sig == signal.SIGKILL: raise PermissionError(1,"fixture live group denied")
    group.send_signal = send
    with pytest.raises(PermissionError): await group.terminate()
    assert signals == [(process.pid,signal.SIGTERM),(process.pid,signal.SIGKILL)]
    assert not group.closed and not group._killed and not waits and process.returncode is None


@pytest.mark.parametrize("unknown", ["exit", "members"])
async def test_unknown_post_term_proof_retains_owner_without_kill_or_reap(unknown):
    group, process, signals, waits, clock = retained_group()
    group.leader_exited = lambda pid: None if signals and unknown == "exit" else bool(signals)
    group.group_members = lambda pid: "unknown" if signals and unknown == "members" else (pid,)
    with pytest.raises(SupervisorError): await group.terminate()
    assert signals == [(process.pid,signal.SIGTERM)]
    assert not group.closed and not group._killed and not waits and process.returncode is None


@pytest.mark.parametrize("exited,members", [(True,(1234567,777)), (False,(1234567,))])
async def test_permission_failure_is_never_accepted_for_descendants_or_live_leader(exited,members):
    group, process, signals, waits, clock = retained_group()
    group.group_members = lambda pid: members; group.leader_exited = lambda pid: exited
    def deny_signal(*args): raise PermissionError(1, "fixture denied")
    group.send_signal = deny_signal
    with pytest.raises(PermissionError): await group.terminate()
    assert not group.closed and not group._killed and not waits and process.returncode is None


async def test_exit_proof_precedes_members_snapshot_so_a_final_fork_cannot_escape_signals():
    group, process, signals, waits, clock = retained_group()
    state = {"probed":False,"child":False}; order=[]
    def exited(pid):
        order.append("exit_probe")
        if not state["probed"]:
            # Simulate the leader's final fork immediately before observed exit.
            state["probed"] = True; state["child"] = True
        return True
    def members(pid):
        order.append("members")
        return (pid,777) if state["child"] else (pid,)
    def send(pid,sig):
        signals.append((pid,sig))
        if sig == signal.SIGKILL: state["child"] = False
    group.leader_exited,group.group_members,group.send_signal = exited,members,send
    assert await group.terminate() is True
    assert order[:2] == ["exit_probe","members"]
    assert signals == [(process.pid,signal.SIGTERM),(process.pid,signal.SIGKILL)]
    assert waits == [.05] and group.closed


def test_non_reaping_exit_probe_uses_exact_child_and_waitnowait_without_os_call(monkeypatch):
    import actor_broadcast.supervisor as module
    calls = []
    names = {"P_PID": 1, "WEXITED": 4, "WNOHANG": 1, "WNOWAIT": 32,
        "CLD_EXITED": 1, "CLD_KILLED": 2, "CLD_DUMPED": 3}
    for key, value in names.items(): monkeypatch.setattr(module.os, key, value, raising=False)
    def fake(kind, pid, flags):
        calls.append((kind, pid, flags)); return SimpleNamespace(si_pid=pid, si_code=2)
    monkeypatch.setattr(module.os, "waitid", fake, raising=False)
    assert unreaped_controller_exited(1234567) is True
    assert calls == [(1, 1234567, 37)]
    monkeypatch.setattr(module.os, "waitid", lambda *args: None)
    assert unreaped_controller_exited(1234567) is False
    monkeypatch.setattr(module.os, "waitid", lambda *args: SimpleNamespace(si_pid=777, si_code=2))
    with pytest.raises(SupervisorError): unreaped_controller_exited(1234567)


@pytest.mark.parametrize("pid,code,status,expected", [
    (1234567, 1, 0, True), (1234567, 2, 0, True), (1234567, 3, 0, True),
    (0, 0, 0, False), (777, 2, 0, None),
    (1234567, 4, 0, False), (1234567, 5, 0, False), (1234567, 6, 0, False),
    (1234567, 0, 0, None), (1234567, 7, 0, None), (1234567, -1, 0, None),
    (0, 5, 0, None), (0, 0, -1, None),
])
def test_darwin_libc_nonreaping_probe_exact_abi_with_fake_library(monkeypatch, pid, code, status, expected):
    import ctypes
    import actor_broadcast.supervisor as module
    calls = []
    monkeypatch.delattr(module.os, "waitid", raising=False)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    def fake(kind, requested, pointer, flags):
        info = pointer._obj
        assert ctypes.sizeof(info) == 104 and type(info).pid.offset == 12
        calls.append((kind, requested, flags))
        info.pid, info.code, info.signo = pid, code, signal.SIGCHLD if pid else 0
        return status
    def load(path, **options):
        assert path == "/usr/lib/libSystem.B.dylib" and options == {"use_errno": True}
        return SimpleNamespace(waitid=fake)
    monkeypatch.setattr(ctypes, "CDLL", load)
    if expected is None:
        with pytest.raises(SupervisorError): unreaped_controller_exited(1234567)
    else: assert unreaped_controller_exited(1234567) is expected
    assert calls == [(1, 1234567, 37)]


@pytest.mark.parametrize("pid,signo,code,si_error,result,errno", [
    (777,signal.SIGCHLD,5,0,0,0), (1234567,signal.SIGTERM,5,0,0,0),
    (1234567,signal.SIGCHLD,5,5,0,0), (1234567,signal.SIGCHLD,5,0,-1,3),
    (0,signal.SIGCHLD,0,0,0,0),
])
def test_darwin_nonterminal_reports_preserve_identity_signal_and_error_guards(monkeypatch,pid,signo,code,si_error,result,errno):
    import ctypes
    import actor_broadcast.supervisor as module
    def fake(kind,requested,pointer,flags):
        assert (kind,requested,flags)==(1,1234567,37)
        info=pointer._obj
        info.pid,info.signo,info.code,info.error=pid,signo,code,si_error
        ctypes.set_errno(errno)
        return result
    monkeypatch.setattr(ctypes,"CDLL",lambda *a,**k:SimpleNamespace(waitid=fake))
    with pytest.raises(SupervisorError,match="supervisor_exit_probe_failed"):
        module._darwin_unreaped_exit(1234567)


async def test_darwin_stopped_leader_real_libc_predicate_allows_term_then_kill_before_reap(monkeypatch):
    import ctypes
    import actor_broadcast.supervisor as module
    process=SimpleNamespace(pid=1234567,returncode=None)
    clock=Clock();signals=[];queries=[];waits=[];state={"code":5}
    def fake(kind,pid,pointer,flags):
        assert (kind,pid,flags)==(1,process.pid,37)
        info=pointer._obj
        assert ctypes.sizeof(info)==104 and type(info).pid.offset==12
        info.pid,info.signo,info.code=pid,signal.SIGCHLD,state["code"]
        info.status=signal.SIGSTOP if state["code"]==5 else signal.SIGKILL
        queries.append(state["code"]);return 0
    monkeypatch.setattr(ctypes,"CDLL",lambda *a,**k:SimpleNamespace(waitid=fake))
    def send(pid,number):
        assert pid==process.pid and process.returncode is None
        signals.append(number)
        if number==signal.SIGKILL:state["code"]=2
    async def wait(timeout):
        assert signals==[signal.SIGTERM,signal.SIGKILL]and state["code"]==2
        waits.append(timeout);process.returncode=-signal.SIGKILL;return process.returncode
    group=RetainedControllerGroup(process,group_members=lambda pid:(pid,),verify_group=lambda pid:pid,
        send_signal=send,leader_exited=module._darwin_unreaped_exit,wait_child=wait,
        clock=clock,sleep=clock.sleep)
    assert await group.terminate()is True and group.closed
    assert queries[:2]==[5,5]and queries[-1]==2 and len(waits)==1
    assert signals==[signal.SIGTERM,signal.SIGKILL]


@pytest.mark.parametrize("code",[4,5,6])
def test_non_darwin_waitid_path_still_rejects_unexpected_nonterminal_result(monkeypatch,code):
    import actor_broadcast.supervisor as module
    monkeypatch.setattr(module.os,"waitid",lambda *a:SimpleNamespace(si_pid=1234567,si_code=code),raising=False)
    for name,value in {"P_PID":1,"WEXITED":4,"WNOHANG":1,"WNOWAIT":32,"CLD_EXITED":1,"CLD_KILLED":2,"CLD_DUMPED":3}.items():
        monkeypatch.setattr(module.os,name,value,raising=False)
    with pytest.raises(SupervisorError):unreaped_controller_exited(1234567)
