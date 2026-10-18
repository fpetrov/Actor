import json
from importlib import resources
from functools import cache

import pytest
from google.protobuf.any_pb2 import Any

from actor.core.v1 import actor_pb2 as core
from actor.core.v1 import bridge_pb2 as wire
from actor_runtime.extensions import ExtensionRegistry
from actor_runtime.session import RuntimeSession


@pytest.fixture
def registry():
    return ExtensionRegistry.discover()


@pytest.fixture
def manifest():
    result = core.CapabilityManifest(game_id="test-game", game_version="0.1.0", self_actor_id="actor-1",
                                    protocol_version=core.ProtocolVersion(major=1, minor=0))
    for resource, name in (("actor.metadata", "standard.v1.json"),
                           ("extensions.agents_arena.metadata", "agents_arena.v1.json")):
        document = json.loads(resources.files(resource).joinpath(name).read_bytes())
        result.extensions.add(extension_id=document["extension_id"],
                              extension_version=document["extension_version"],
                              supported_protocol_major=document["supported_protocol_major"],
                              schema_hash=document["schema_hash"], required=True)
        for specification in document["capabilities"]:
            item = dict(specification)
            item["kind"] = {"observation": 1, "command": 2, "event": 3}[item["kind"]]
            item["command_delivery"] = {"latest": 2, "ordered": 1}.get(item.get("command_delivery"), 0)
            result.capabilities.add(**item)
    return result


def message(sequence, **body):
    return wire.GameToRuntime(session_id="session-1", sequence=sequence, **body)


async def negotiate(session, manifest):
    await session.accept(message(1, hello=core.GameHello(
        game_id=manifest.game_id, game_version=manifest.game_version,
        self_actor_id=manifest.self_actor_id, protocol_version=manifest.protocol_version)))
    await session.accept(message(2, manifest=manifest))


@pytest.fixture
async def session(registry, manifest):
    result = RuntimeSession(registry)
    await negotiate(result, manifest)
    yield result
    result.close()


@cache
def extension_version(extension):
    resource, name = {
        "actor.standard": ("actor.metadata", "standard.v1.json"),
        "agents-arena": ("extensions.agents_arena.metadata", "agents_arena.v1.json"),
    }[extension]
    return json.loads(resources.files(resource).joinpath(name).read_bytes())["extension_version"]


def envelope(capability, extension, payload, *, entity="actor-1"):
    packed = Any()
    packed.Pack(payload)
    return core.ObservationEnvelope(capability_id=capability, extension_id=extension,
                                    extension_version=extension_version(extension), entity_id=entity, payload=packed)
