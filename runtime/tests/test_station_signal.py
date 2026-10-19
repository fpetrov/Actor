"""Game extension binding/descriptor and existing reliable duplex event path."""
import asyncio
import grpc
import pytest
from google.protobuf.any_pb2 import Any

from actor.core.v1 import actor_pb2 as core, bridge_pb2_grpc
from actor_agents_arena import (
    STATION_SIGNAL, StationSignalEvent, ROUTINE_NOTICE, URGENT_RECALL, STATION_WIDE,
    extension_package,
)
from actor_runtime.extensions import ExtensionRegistry, type_url
from actor_runtime.server import RuntimeServer
from conftest import message


@pytest.mark.parametrize("kind", [ROUTINE_NOTICE, URGENT_RECALL])
def test_station_signal_generated_and_descriptor_only_decoding(kind):
    signal = StationSignalEvent(signal_id="c" * 32, signal_kind=kind, source_actor_id="human-actor",
        target_room_id="main-hall", message="Server-authored signal", server_unix_ms=1790000000000,
        audience=STATION_WIDE, server_revision=9)
    package = extension_package()
    from conftest import extension_version
    assert package.version == extension_version("agents-arena")
    packed = Any(); packed.Pack(signal)
    assert packed.type_url == type_url(StationSignalEvent)
    registered = ExtensionRegistry(); registered.register(package)
    assert registered.decode(packed) == signal
    from dataclasses import replace
    dynamic = ExtensionRegistry(); dynamic.register(replace(package, message_types=()))
    decoded = dynamic.decode(packed)
    assert decoded.signal_id == signal.signal_id
    assert decoded.signal_kind == kind
    assert decoded.audience == STATION_WIDE
    assert decoded.server_revision == 9


async def test_station_signal_crosses_normal_typed_duplex_stream(manifest, registry):
    token = "test-only-station-signal-loopback-token"
    server = RuntimeServer(registry, token, port=0)
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{server.port}") as channel:
            call = bridge_pb2_grpc.ActorBridgeStub(channel).Connect(metadata=(("x-actor-bridge-token", token),))
            await call.write(message(1, hello=core.GameHello(game_id=manifest.game_id,
                game_version=manifest.game_version, self_actor_id=manifest.self_actor_id,
                protocol_version=manifest.protocol_version)))
            await call.write(message(2, manifest=manifest))
            reply = await asyncio.wait_for(call.read(), timeout=1)
            assert reply.hello.accepted
            session = await server.next_session(timeout=1)
            signal = StationSignalEvent(signal_id="b" * 32, signal_kind=URGENT_RECALL,
                source_actor_id="human-actor", target_room_id="main-hall", message="Server-authored recall",
                server_unix_ms=1790000000000, audience=STATION_WIDE, server_revision=3)
            packed = Any(); packed.Pack(signal)
            await call.write(message(3, event=core.GameEventEnvelope(capability_id=STATION_SIGNAL,
                extension_id="agents-arena", extension_version=extension_package().version,
                entity_id=signal.source_actor_id, revision=3, payload=packed)))
            await session.world.wait_until(lambda world: bool(world.events), timeout=1)
            entry = session.world.events[-1]
            assert entry.payload == signal
            assert entry.entity_id == "human-actor"
            assert entry.capability_id == STATION_SIGNAL
            call.cancel()
    finally:
        await server.stop()
