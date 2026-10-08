"""Installed extension discovery, descriptor verification and typed Any decoding."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from importlib import metadata
import json
from typing import Iterable

from google.protobuf.any_pb2 import Any
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.message import DecodeError, Message

from .errors import ActorError

TYPE_PREFIX = "type.googleapis.com/"


def type_url(message_type: type[Message]) -> str:
    return TYPE_PREFIX + message_type.DESCRIPTOR.full_name


def reflected_schema(descriptor):
    result = descriptor_pb2.DescriptorProto()
    descriptor.CopyToProto(result)
    # Python generated bindings may omit default json_name while descriptor sets
    # explicitly contain it. Reflection exposes the same effective name in both.
    for field in result.field:
        field.json_name = descriptor.fields_by_name[field.name].json_name
    for i, nested in enumerate(descriptor.nested_types):
        result.nested_type[i].CopyFrom(reflected_schema(nested))
    return result


def reflected_file(descriptor):
    result = descriptor_pb2.FileDescriptorProto()
    descriptor.CopyToProto(result)
    result.ClearField("source_code_info")
    for i, message in enumerate(descriptor.message_types_by_name.values()):
        result.message_type[i].CopyFrom(reflected_schema(message))
    return result


def descriptor_message_names(descriptor_set):
    names = set()

    def visit(message, prefix):
        full_name = prefix + message.name
        names.add(full_name)
        for nested in message.nested_type:
            visit(nested, full_name + ".")

    for file in descriptor_set.file:
        prefix = file.package + "." if file.package else ""
        for message in file.message_type:
            visit(message, prefix)
    return frozenset(names)


@dataclass(frozen=True)
class ExtensionPackage:
    extension_id: str
    version: str
    protocol_major: int
    schema_hash: str
    descriptor_bytes: bytes
    message_types: tuple[type[Message], ...] = ()

    @classmethod
    def from_resources(cls, manifest_bytes: bytes, descriptor_bytes: bytes,
                       message_types: Iterable[type[Message]] = ()) -> ExtensionPackage:
        manifest = json.loads(manifest_bytes)
        return cls(manifest["extension_id"], manifest["extension_version"],
                   manifest["supported_protocol_major"], manifest["schema_hash"],
                   descriptor_bytes, tuple(message_types))


@dataclass(frozen=True)
class OpaquePayload:
    type_url: str
    value: bytes


@dataclass(frozen=True)
class NegotiatedExtensions:
    available: frozenset[str]
    opaque: frozenset[str]
    warnings: tuple[dict, ...] = ()


class ExtensionRegistry:
    def __init__(self):
        self.packages: dict[str, ExtensionPackage] = {}
        self.pool = descriptor_pool.DescriptorPool()
        self._files: dict[str, bytes] = {}
        self._types: dict[str, type[Message]] = {}
        self._package_symbols: dict[str, frozenset[str]] = {}

    @classmethod
    def discover(cls, entry_points=None) -> ExtensionRegistry:
        registry = cls()
        points = (metadata.entry_points(group="actor.extensions")
                  if entry_points is None else entry_points)
        for point in sorted(points, key=lambda item: item.name):
            try:
                registry.register(point.load()())
            except ActorError:
                raise
            except Exception as error:
                raise ActorError("ExtensionPackageInvalid", "Extension plugin could not load",
                                 {"plugin": point.name, "reason": str(error)}) from error
        return registry

    def register(self, package: ExtensionPackage):
        details = {"extension_id": package.extension_id, "required_version": package.version,
                   "schema_hash": package.schema_hash}
        if package.extension_id in self.packages:
            old = self.packages[package.extension_id]
            code = "DuplicateExtension" if old.version == package.version else "ExtensionVersionConflict"
            raise ActorError(code, "An extension ID must be registered exactly once", details)
        if not package.extension_id or not package.version or package.protocol_major != 1:
            raise ActorError("ExtensionPackageInvalid", "Invalid extension identity or protocol", details)
        if not package.descriptor_bytes:
            raise ActorError("DescriptorMissing", "Extension descriptor set is missing", details)
        actual = hashlib.sha256(package.descriptor_bytes).hexdigest()
        if actual != package.schema_hash:
            raise ActorError("SchemaHashMismatch", "Installed descriptor fingerprint differs", details)
        descriptor_set = descriptor_pb2.FileDescriptorSet()
        try:
            descriptor_set.ParseFromString(package.descriptor_bytes)
        except DecodeError as error:
            raise ActorError("DescriptorInvalid", "Descriptor set cannot be decoded", details) from error
        if not descriptor_set.file:
            raise ActorError("DescriptorMissing", "Descriptor set has no files", details)
        package_symbols = descriptor_message_names(descriptor_set)
        incoming = {}
        for file in descriptor_set.file:
            encoded = file.SerializeToString(deterministic=True)
            previous = incoming.get(file.name, self._files.get(file.name))
            if previous is not None and previous != encoded:
                raise ActorError("DescriptorConflict", "Different schemas share one file identity",
                                 {**details, "file": file.name})
            incoming[file.name] = encoded
        # Build a fresh pool transactionally: a failed plugin never poisons registration.
        files = {**self._files, **incoming}
        pool = descriptor_pool.DescriptorPool()
        pending = dict(files)
        while pending:
            progressed = False
            for name, encoded in tuple(pending.items()):
                file = descriptor_pb2.FileDescriptorProto.FromString(encoded)
                if all(dependency not in pending for dependency in file.dependency):
                    try:
                        pool.AddSerializedFile(encoded)
                    except (TypeError, ValueError) as error:
                        raise ActorError("DescriptorInvalid", "Descriptor imports or symbols are invalid",
                                         {**details, "file": name}) from error
                    del pending[name]
                    progressed = True
            if not progressed:
                raise ActorError("DescriptorInvalid", "Missing or cyclic descriptor dependencies", details)
        known = dict(self._types)
        for message_type in package.message_types:
            url = type_url(message_type)
            if message_type.DESCRIPTOR.full_name not in package_symbols:
                raise ActorError("GeneratedTypeMismatch", "Generated message is absent from its extension descriptor",
                                 {**details, "type_url": url})
            try:
                descriptor = pool.FindMessageTypeByName(message_type.DESCRIPTOR.full_name)
            except KeyError as error:
                raise ActorError("GeneratedTypeMismatch", "Generated message is missing from descriptors",
                                 {**details, "type_url": url}) from error
            generated = reflected_schema(message_type.DESCRIPTOR)
            declared = reflected_schema(descriptor)
            if generated != declared or reflected_file(message_type.DESCRIPTOR.file) != reflected_file(descriptor.file):
                raise ActorError("GeneratedTypeMismatch", "Generated binding differs from descriptor",
                                 {**details, "type_url": url})
            known[url] = message_type
        self.packages[package.extension_id] = package
        self._package_symbols[package.extension_id] = package_symbols
        self._files, self.pool, self._types = files, pool, known

    def package_has_type(self, extension_id: str, url: str) -> bool:
        return (url.startswith(TYPE_PREFIX) and
                url[len(TYPE_PREFIX):] in self._package_symbols.get(extension_id, ()))

    def negotiate(self, extensions) -> NegotiatedExtensions:
        available, opaque, warnings, seen = set(), set(), [], set()
        for requested in extensions:
            details = {"extension_id": requested.extension_id,
                       "required_version": requested.extension_version, "schema_hash": requested.schema_hash}
            if requested.extension_id in seen:
                raise ActorError("ManifestInvalid", "Duplicate extension declaration", details)
            seen.add(requested.extension_id)
            installed = self.packages.get(requested.extension_id)
            problem = None
            if installed is None:
                problem = ActorError("RequiredExtensionMissing", "Required extension is not installed", details)
            elif installed.protocol_major != requested.supported_protocol_major:
                problem = ActorError("ProtocolVersionMismatch", "Extension protocol version differs", details)
            elif installed.version != requested.extension_version:
                problem = ActorError("ExtensionVersionMismatch", "Installed extension version differs", details)
            elif installed.schema_hash != requested.schema_hash:
                problem = ActorError("SchemaHashMismatch", "Game and installed descriptor fingerprints differ", details)
            if problem:
                if requested.required:
                    raise problem
                opaque.add(requested.extension_id)
                warnings.append(problem.as_dict())
            else:
                available.add(requested.extension_id)
        return NegotiatedExtensions(frozenset(available), frozenset(opaque), tuple(warnings))

    def resolve_type(self, url: str) -> type[Message] | None:
        if not url.startswith(TYPE_PREFIX):
            return None
        if url in self._types:
            return self._types[url]
        name = url[len(TYPE_PREFIX):]
        try:
            descriptor = self.pool.FindMessageTypeByName(name)
        except KeyError:
            return None
        resolved = message_factory.GetMessageClass(descriptor)
        self._types[url] = resolved
        return resolved

    def decode(self, payload: Any, *, opaque: bool = False) -> Message | OpaquePayload:
        message_type = None if opaque else self.resolve_type(payload.type_url)
        if message_type is None:
            return OpaquePayload(payload.type_url, bytes(payload.value))
        message = message_type()
        try:
            if not payload.Unpack(message):
                raise ActorError("PayloadTypeMismatch", "Payload type could not be unpacked")
        except DecodeError as error:
            raise ActorError("PayloadInvalid", "Protobuf payload is malformed",
                             {"type_url": payload.type_url}) from error
        return message
