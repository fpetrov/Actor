"""Contract integrity only; authoritative networking/privacy has separate tests."""

from dataclasses import replace
import hashlib
from importlib import resources
import json
from pathlib import Path

from google.protobuf.any_pb2 import Any
from google.protobuf.descriptor_pb2 import FileDescriptorSet
import pytest

from actor.core.v1 import actor_pb2 as core
from actor.standard.communication.v1 import communication_pb2 as communication
from actor_agents_arena import (
    extension_package as arena_package, DiagnosticResultEvent, SubmitCalibrationCommand,
    CalibrationTaskStateObservation, CalibrationProgressObservation, StartCalibrationCommand,
)
from actor_runtime.errors import ActorError
from actor_runtime.extensions import ExtensionPackage, ExtensionRegistry, type_url
from actor_runtime.session import RuntimeSession
from actor_standard import extension_package as motion_package
from conftest import envelope, message, negotiate


RESOURCE = resources.files("actor.communication.metadata")
COMMUNICATION_TYPES = (
    communication.SendMessageCommand, communication.MessageReceivedEvent,
    communication.MessageDeliveryResultEvent, communication.CommunicationPeer,
    communication.CommunicationPeersObservation,
)


def package(*, generated=True):
    return ExtensionPackage.from_resources(RESOURCE.joinpath("communication.v1.json").read_bytes(),
        RESOURCE.joinpath("communication.v1.pb").read_bytes(), COMMUNICATION_TYPES if generated else ())


def declaration(installed, *, required=True, **changes):
    result = core.ExtensionManifest(extension_id=installed.extension_id, extension_version=installed.version,
        supported_protocol_major=installed.protocol_major, schema_hash=installed.schema_hash, required=required)
    for key, value in changes.items():
        setattr(result, key, value)
    return result


@pytest.mark.parametrize("value", [
    communication.SendMessageCommand(recipient_actor_id="peer-b", channel_id="direct", text="Hello",
        correlation_id="correlation-1", task_id="task-1", sensitive=True, ttl_ms=60000, match_id="match-1"),
    communication.MessageReceivedEvent(message_id="server-message-1", sender_actor_id="peer-a",
        recipient_actor_id="peer-b", channel_id="direct", text="Hello", correlation_id="correlation-1",
        task_id="task-1", sensitive=True, server_unix_ms=1000, expires_at_unix_ms=61000,
        match_id="match-1", server_sequence=10),
    communication.MessageDeliveryResultEvent(correlation_id="correlation-1", message_id="server-message-1",
        accepted_by_server=True, stage=communication.MESSAGE_DELIVERY_STAGE_SERVER_ACCEPTED, match_id="match-1"),
    communication.CommunicationPeersObservation(match_id="match-1", server_revision=3,
        peers=[communication.CommunicationPeer(actor_id="peer-b", display_name="Partner")],
        allowed_channels=["direct", "station"]),
])
def test_generated_and_descriptor_only_communication_any_roundtrip(value):
    packed = Any()
    packed.Pack(value)
    for generated in (True, False):
        registry = ExtensionRegistry()
        registry.register(package(generated=generated))
        decoded = registry.decode(packed)
        assert decoded.SerializeToString(deterministic=True) == value.SerializeToString(deterministic=True)
        assert type_url(type(decoded)) == "type.googleapis.com/" + value.DESCRIPTOR.full_name
    assert packed.type_url.startswith("type.googleapis.com/actor.standard.communication.v1.")


def test_communication_is_independent_of_motion_core_and_arena():
    installed = package()
    descriptor = FileDescriptorSet.FromString(installed.descriptor_bytes)
    assert installed.extension_id == "actor.standard.communication" and installed.version == "0.1.0"
    assert {file.package for file in descriptor.file} == {"actor.standard.communication.v1"}
    assert hashlib.sha256(installed.descriptor_bytes).hexdigest() == installed.schema_hash
    registry = ExtensionRegistry()
    registry.register(installed)
    negotiated = registry.negotiate([declaration(installed)])
    assert negotiated.available == {installed.extension_id}
    assert not registry.package_has_type(installed.extension_id,
        "type.googleapis.com/actor.extensions.agents_arena.v1.DiagnosticResultEvent")
    assert not registry.package_has_type("actor.standard", type_url(communication.SendMessageCommand))


def test_communication_optional_missing_or_incompatible_does_not_require_game_upgrade():
    registry = ExtensionRegistry()
    installed = package()
    assert registry.negotiate([declaration(installed, required=False)]).opaque == {installed.extension_id}
    with pytest.raises(ActorError, match="RequiredExtensionMissing"):
        registry.negotiate([declaration(installed)])
    registry.register(installed)
    assert registry.negotiate([declaration(installed, required=False, extension_version="99.0.0")]).opaque == {
        installed.extension_id}
    with pytest.raises(ActorError, match="ExtensionVersionMismatch"):
        registry.negotiate([declaration(installed, extension_version="99.0.0")])


def test_manifest_metadata_has_four_typed_capabilities_and_ordered_message_delivery():
    manifest = json.loads(RESOURCE.joinpath("communication.v1.json").read_bytes())
    capabilities = {item["capability_id"]: item for item in manifest["capabilities"]}
    assert set(capabilities) == {"actor.standard.communication." + name for name in (
        "send-message", "message-received", "delivery-result", "peers")}
    command = capabilities["actor.standard.communication.send-message"]
    assert command["command_delivery"] == "ordered" and command["kind"] == "command"
    assert command["input_type_url"] == type_url(communication.SendMessageCommand)
    assert command["version"] == "0.1.0" and not command["output_type_url"]
    assert capabilities["actor.standard.communication.peers"]["kind"] == "observation"
    assert "generation" not in manifest


def test_motion_fingerprint_and_core_message_structure_remain_unchanged():
    assert motion_package().version == "0.1.0"
    assert motion_package().schema_hash == "5188081ff451868991d0ba8cda56dc18d0a422794251c43216586996f7d9fb21"
    core_artifact = Path(__file__).resolve().parents[2] / "generated/descriptors/actor-core.pb"
    assert hashlib.sha256(core_artifact.read_bytes()).hexdigest() == (
        "b4cfdef2baa9f58012310387dbae175f2d29061606d60043df20d809b313954f")
    assert set(core.CommandEnvelope.DESCRIPTOR.fields_by_name) == {
        "command_id", "capability_id", "extension_id", "extension_version", "target_actor_id", "ttl_ms",
        "payload", "issued_at_unix_ms"}
    assert "actor.standard.communication" not in core.DESCRIPTOR.serialized_pb.decode("latin1")


def test_server_identity_and_game_outcome_are_not_client_claim_fields():
    assert "sender_actor_id" not in communication.SendMessageCommand.DESCRIPTOR.fields_by_name
    assert "message_id" not in communication.SendMessageCommand.DESCRIPTOR.fields_by_name
    assert "coordinator_actor_id" not in StartCalibrationCommand.DESCRIPTOR.fields_by_name
    assert "completed" not in SubmitCalibrationCommand.DESCRIPTOR.fields_by_name
    assert communication.MESSAGE_DELIVERY_STAGE_SERVER_ACCEPTED != communication.MESSAGE_DELIVERY_STAGE_CLIENT_RECEIVED


def test_calibration_public_state_never_contains_diagnostic_secret_fields():
    for message in (CalibrationTaskStateObservation, CalibrationProgressObservation):
        fields = set(message.DESCRIPTOR.fields_by_name)
        assert not {"authorization_code", "authorization_challenge", "sensitive"} & fields
    assert "authorization_code" in DiagnosticResultEvent.DESCRIPTOR.fields_by_name
    private = DiagnosticResultEvent(correlation_id="inspect-1", task_id="task-1", accepted=True,
        authorization_code="synthetic-test-only", sensitive=True, expires_at_unix_ms=60000, match_id="match-1")
    packed = Any()
    packed.Pack(private)
    registry = ExtensionRegistry()
    registry.register(arena_package())
    assert registry.decode(packed) == private
    assert arena_package().version == "0.3.0"
    with pytest.raises(ActorError, match="ExtensionVersionMismatch"):
        registry.negotiate([declaration(arena_package(), extension_version="0.2.0")])


def test_foreign_generated_types_cannot_be_advertised_under_communication_hash():
    with pytest.raises(ActorError, match="GeneratedTypeMismatch"):
        ExtensionRegistry().register(replace(package(), message_types=(DiagnosticResultEvent,)))


def test_fixture_advertises_communication_optionally_and_existing_game_packages_required(session, manifest):
    flags = {extension.extension_id: extension.required for extension in manifest.extensions}
    assert flags == {"actor.standard": True, "agents-arena": True, "actor.standard.communication": False}
    assert "actor.standard.communication" in session.negotiated.available


async def test_game_without_communication_negotiates_normally_even_when_plugin_is_installed(registry, manifest):
    extension_id = "actor.standard.communication"
    capabilities = [item for item in manifest.capabilities if item.extension_id != extension_id]
    extensions = [item for item in manifest.extensions if item.extension_id != extension_id]
    manifest.ClearField("extensions")
    manifest.ClearField("capabilities")
    manifest.extensions.extend(extensions)
    manifest.capabilities.extend(capabilities)
    session = RuntimeSession(registry)
    try:
        await negotiate(session, manifest)
        assert session.ready.is_set()
        assert extension_id not in session.negotiated.available
        assert not any(item.startswith(extension_id) for item in session.capabilities)
    finally:
        session.close()


async def test_optional_communication_unsupported_runtime_keeps_opaque_metadata_without_breaking_game(manifest):
    registry = ExtensionRegistry()
    registry.register(motion_package())
    registry.register(arena_package())
    session = RuntimeSession(registry)
    try:
        await negotiate(session, manifest)
        assert session.ready.is_set() and session.negotiated.opaque == {"actor.standard.communication"}
        observed = envelope("actor.standard.communication.peers", "actor.standard.communication",
                            communication.CommunicationPeersObservation(match_id="match-1"))
        await session.accept(message(3, observation=core.ObservationFrame(revision=1, observations=[observed])))
        assert session.world.latest(communication.CommunicationPeersObservation) is None
        with pytest.raises(ActorError, match="ExtensionUnavailable"):
            session.command("actor.standard.communication.send-message", communication.SendMessageCommand())
    finally:
        session.close()
