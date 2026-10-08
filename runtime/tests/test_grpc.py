import asyncio

import grpc
import pytest
from actor.core.v1 import actor_pb2 as core
from actor.core.v1 import bridge_pb2 as wire
from actor.core.v1 import bridge_pb2_grpc
from google.protobuf.any_pb2 import Any

from actor_runtime.server import RuntimeServer
from actor_standard import TRANSFORM, LOCOMOTION, Transform2DObservation, Locomotion2DCommand
from actor_agents_arena import ROOM_CHANGED, RoomChangedEvent
from conftest import message, envelope

TOKEN = "test-only-random-placeholder-token-32chars"


@pytest.fixture
async def server(registry):
    result = RuntimeServer(registry, TOKEN, port=0)
    await result.start()
    yield result
    await result.stop()


def open_call(channel, token=TOKEN):
    return bridge_pb2_grpc.ActorBridgeStub(channel).Connect(
        metadata=(("x-actor-bridge-token", token),))


async def handshake(call, manifest):
    await call.write(message(1, hello=core.GameHello(game_id=manifest.game_id,
                    game_version=manifest.game_version, protocol_version=manifest.protocol_version,
                    self_actor_id=manifest.self_actor_id)))
    await call.write(message(2, manifest=manifest))
    return await asyncio.wait_for(call.read(), timeout=3)


async def test_true_bidi_hello_manifest_observation_command_result_event(server, manifest):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{server.port}") as channel:
        call = open_call(channel)
        assert (await handshake(call, manifest)).hello.accepted
        session = await server.next_session(timeout=3)
        # Server sends a command before the client has sent any observation.
        command_id = session.command(LOCOMOTION, Locomotion2DCommand(desired_move_y=-1))
        command = await asyncio.wait_for(call.read(), timeout=3)
        assert command.command.command_id == command_id
        assert command.command.target_actor_id == manifest.self_actor_id
        await call.write(message(3, observation=core.ObservationFrame(revision=1,
                observations=[envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=2))])))
        await call.write(message(4, command_result=core.CommandResult(command_id=command_id,
                                                        status=core.COMMAND_STATUS_ACCEPTED)))
        packed = Any()
        packed.Pack(RoomChangedEvent(previous_room_id="main-hall", current_room_id="engineering"))
        await call.write(message(5, event=core.GameEventEnvelope(revision=2, capability_id=ROOM_CHANGED,
                        extension_id="agents-arena", extension_version="0.1.0",
                        entity_id="actor-1", payload=packed)))
        await session.world.wait_until(lambda world: len(world.events) == 1, timeout=3)
        assert session.world.latest(Transform2DObservation).x == 2
        assert session.world.events[-1].payload.current_room_id == "engineering"
        assert session.command_results[-1].status == core.COMMAND_STATUS_ACCEPTED
        assert session.command_result_rtt_ms
        call.cancel()


async def test_invalid_token_rejected_before_session(server):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{server.port}") as channel:
        call = open_call(channel, token="invalid")
        with pytest.raises(grpc.aio.AioRpcError) as failure:
            await call.read()
        assert failure.value.code() == grpc.StatusCode.UNAUTHENTICATED
        assert not server.sessions


async def test_required_missing_extension_sent_as_protocol_error(server, manifest):
    manifest.extensions[1].extension_id = "missing-extension"
    async with grpc.aio.insecure_channel(f"127.0.0.1:{server.port}") as channel:
        call = open_call(channel)
        failure = await handshake(call, manifest)
        assert failure.error.code == "RequiredExtensionMissing"
        assert failure.error.extension_id == "missing-extension"
        assert failure.error.required_version == "0.1.0"
        assert failure.error.schema_hash
        assert (await call.read()).disconnect.reason == "RequiredExtensionMissing"
        assert await call.read() is grpc.aio.EOF


async def test_cancellation_reconnect_graceful_half_close(server, manifest):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{server.port}") as channel:
        first = open_call(channel)
        await handshake(first, manifest)
        old = await server.next_session(timeout=3)
        first.cancel()
        await asyncio.wait_for(old.closed.wait(), timeout=3)
        second = open_call(channel)
        await handshake(second, manifest)
        new = await server.next_session(timeout=3)
        assert new is not old
        assert not new.world.observations
        await second.done_writing()
        assert await asyncio.wait_for(second.read(), timeout=3) is grpc.aio.EOF
        assert await second.code() == grpc.StatusCode.OK
        await asyncio.wait_for(new.closed.wait(), timeout=3)


async def test_shutdown_sends_disconnect_and_cleans_tasks(server, manifest):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{server.port}") as channel:
        call = open_call(channel)
        await handshake(call, manifest)
        session = await server.next_session(timeout=3)
        await server.stop()
        disconnect = await asyncio.wait_for(call.read(), timeout=3)
        assert disconnect.disconnect.reason == "Runtime shutdown"
        await asyncio.wait_for(session.closed.wait(), timeout=3)
        assert not server.sessions


async def test_handshake_timeout_is_structured(registry):
    server = RuntimeServer(registry, TOKEN, port=0, peer_timeout_seconds=0.05)
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{server.port}") as channel:
            call = open_call(channel)
            error = await asyncio.wait_for(call.read(), timeout=3)
            assert error.error.code == "PeerTimeout"
            assert (await call.read()).disconnect.reason == "PeerTimeout"
    finally:
        await server.stop()


async def test_twenty_reconnects_without_waiting_policy_do_not_retain_closed_sessions(server, manifest):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{server.port}") as channel:
        for _ in range(20):
            call = open_call(channel)
            assert (await handshake(call, manifest)).hello.accepted
            assert len(server.available) == 1
            await call.done_writing()
            assert await asyncio.wait_for(call.read(), timeout=3) is grpc.aio.EOF
            assert not server.available
        assert not server.sessions
        assert len(server.completed) == 16


async def test_shutdown_full_control_queue_still_terminates_server(server, registry):
    from actor_runtime.session import RuntimeSession
    from actor_runtime.errors import ActorError

    session = RuntimeSession(registry)
    server.sessions.add(session)
    for _ in range(session.mailbox.control_capacity):
        session.mailbox.put_control(wire.RuntimeToGame(heartbeat=core.Heartbeat()))
    session.mailbox.put_latest(("actor", "capability"), wire.RuntimeToGame(command=core.CommandEnvelope()))
    waiting = asyncio.create_task(server.next_session(timeout=3))
    await asyncio.sleep(0)
    await server.stop()
    assert session.closed.is_set() and session.mailbox.closed
    assert not session.mailbox.controls and not session.mailbox.commands
    assert not server.sessions and not server.available
    with pytest.raises(ActorError, match="RuntimeUnavailable"):
        await waiting
