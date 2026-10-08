from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest
from google.protobuf.any_pb2 import Any
from google.protobuf import descriptor_pb2

from actor.core.v1 import actor_pb2 as core
from actor_runtime.errors import ActorError
from actor_runtime.extensions import ExtensionPackage, ExtensionRegistry, OpaquePayload, type_url
from actor_standard import extension_package as standard_package, Transform2DObservation
from actor_agents_arena import extension_package as arena_package, RoomMembershipObservation


@pytest.mark.parametrize("value", [Transform2DObservation(x=2, y=-3),
                                   RoomMembershipObservation(room_id="engineering")])
def test_known_any_roundtrip(registry, value):
    packed = Any()
    packed.Pack(value)
    result = registry.decode(packed)
    assert type(result) is type(value)
    assert result == value
    assert packed.type_url == type_url(type(value))


def test_actual_installed_entry_point_discovery(registry):
    assert set(registry.packages) == {"actor.standard", "agents-arena"}


def test_discovery_loads_plugin_factory_without_game_branch():
    point = SimpleNamespace(name="arbitrary-plugin", load=lambda: standard_package)
    assert set(ExtensionRegistry.discover([point]).packages) == {"actor.standard"}


@pytest.mark.parametrize("changed,code", [({}, "DuplicateExtension"),
                                         ({"version": "2.0.0"}, "ExtensionVersionConflict")])
def test_duplicate_id_is_explicit(registry, changed, code):
    with pytest.raises(ActorError, match=code):
        registry.register(replace(standard_package(), **changed))


@pytest.mark.parametrize("changed,code", [({"descriptor_bytes": b""}, "DescriptorMissing"),
                                         ({"schema_hash": "0" * 64}, "SchemaHashMismatch")])
def test_invalid_installed_package(registry, changed, code):
    with pytest.raises(ActorError, match=code):
        ExtensionRegistry().register(replace(standard_package(), **changed))


def test_generated_binding_must_match_descriptor():
    package = arena_package()
    with pytest.raises(ActorError, match="GeneratedTypeMismatch"):
        ExtensionRegistry().register(replace(package, message_types=(Transform2DObservation,)))


def test_generated_foreign_type_cannot_use_another_registered_package_descriptor():
    registry = ExtensionRegistry()
    registry.register(arena_package())
    with pytest.raises(ActorError, match="GeneratedTypeMismatch"):
        registry.register(replace(standard_package(), message_types=(RoomMembershipObservation,)))


def test_extension_can_use_explicitly_included_import_symbols(registry):
    package = mana_package()
    descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(package.descriptor_bytes)
    imported = descriptor_pb2.FileDescriptorSet.FromString(standard_package().descriptor_bytes)
    descriptor_set.file[0].dependency.append(imported.file[0].name)
    for file in imported.file:
        descriptor_set.file.add().CopyFrom(file)
    encoded = descriptor_set.SerializeToString(deterministic=True)
    registry.register(replace(package, descriptor_bytes=encoded, schema_hash=hashlib.sha256(encoded).hexdigest()))
    assert registry.package_has_type(package.extension_id, type_url(Transform2DObservation))
    assert not registry.package_has_type(package.extension_id, type_url(RoomMembershipObservation))


@pytest.mark.parametrize("field,value,code", [
    ("extension_id", "missing-package", "RequiredExtensionMissing"),
    ("extension_version", "99.0.0", "ExtensionVersionMismatch"),
    ("schema_hash", "0" * 64, "SchemaHashMismatch"),
    ("supported_protocol_major", 2, "ProtocolVersionMismatch"),
])
def test_required_negotiation_error(registry, manifest, field, value, code):
    item = manifest.extensions[0]
    setattr(item, field, value)
    with pytest.raises(ActorError, match=code) as failure:
        registry.negotiate(manifest.extensions)
    assert failure.value.details["required_version"]
    assert failure.value.details["schema_hash"]


def test_unknown_optional_extension_is_opaque(registry, manifest):
    manifest.extensions.add(extension_id="unknown-game-mechanic", extension_version="1.0.0",
                            supported_protocol_major=1, schema_hash="deadbeef", required=False)
    result = registry.negotiate(manifest.extensions)
    assert result.opaque == {"unknown-game-mechanic"}
    packed = Any(type_url="type.googleapis.com/my.game.NewObservation", value=b"\x08\x01")
    assert registry.decode(packed, opaque=True) == OpaquePayload(packed.type_url, packed.value)


def mana_package():
    file = descriptor_pb2.FileDescriptorProto(name="my_game/mana.proto", package="my.game.v1", syntax="proto3")
    message = file.message_type.add(name="ManaObservation")
    message.field.add(name="current", number=1, type=descriptor_pb2.FieldDescriptorProto.TYPE_FLOAT,
                      label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL)
    descriptor = descriptor_pb2.FileDescriptorSet(file=[file]).SerializeToString(deterministic=True)
    return ExtensionPackage("my-game.mana", "0.1.0", 1, hashlib.sha256(descriptor).hexdigest(), descriptor)


def test_descriptor_only_dynamic_type_without_generated_import(registry):
    registry.register(mana_package())
    cls = registry.resolve_type("type.googleapis.com/my.game.v1.ManaObservation")
    payload = Any()
    payload.Pack(cls(current=17))
    assert registry.decode(payload).current == 17


def test_descriptor_collision_does_not_poison_registry(registry):
    package = mana_package()
    registry.register(package)
    descriptor = descriptor_pb2.FileDescriptorSet.FromString(package.descriptor_bytes)
    descriptor.file[0].message_type[0].field[0].type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    changed = descriptor.SerializeToString(deterministic=True)
    with pytest.raises(ActorError, match="DescriptorConflict"):
        registry.register(ExtensionPackage("other-id", "0.1.0", 1, hashlib.sha256(changed).hexdigest(), changed))
    assert registry.resolve_type("type.googleapis.com/my.game.v1.ManaObservation")(current=3).current == 3


def test_missing_descriptor_import_is_structured():
    file = descriptor_pb2.FileDescriptorProto(name="broken.proto", dependency=["missing.proto"])
    encoded = descriptor_pb2.FileDescriptorSet(file=[file]).SerializeToString()
    with pytest.raises(ActorError, match="DescriptorInvalid"):
        ExtensionRegistry().register(ExtensionPackage("broken", "0.1.0", 1,
                                                     hashlib.sha256(encoded).hexdigest(), encoded))


def test_unknown_type_url_and_invalid_wire_payload(registry):
    assert registry.resolve_type("other-prefix/actor.standard.v1.Transform2DObservation") is None
    packed = Any(type_url=type_url(Transform2DObservation), value=b"\xff")
    with pytest.raises(ActorError, match="PayloadInvalid"):
        registry.decode(packed)
