#!/usr/bin/env python3
"""Pinned official codegen; --check compares regenerated artifacts without editing them."""
from __future__ import annotations

import argparse
import base64
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from google.protobuf import descriptor_pb2, descriptor_pool

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "build/proto-tools"
PROTOC_VERSION = "36.2"
GRPC_TOOLS_VERSION = "2.84.0"
GRPC_TOOLS_SHA256 = "1bfc81e6a218d71a9d9b0569ec50b8e27697c5fa89a7bef926424434b0802d96"
PROTOC_ASSETS = {
    ("Darwin", "arm64"): ("osx-aarch_64", "9cd98a532c5c5e0c4161314de0225de27e4c8a323917b6ea7b1b714d3ae23466"),
    ("Darwin", "x86_64"): ("osx-x86_64", "228cc7add4616cc14ca5e80dee83209d44449a7aee95a914ae748fa374efb078"),
    ("Linux", "x86_64"): ("linux-x86_64", "121f6c7afe1d4d0e3ea6aab9432038599250134cbf4474cb1167d2c7decd4278"),
    ("Linux", "aarch64"): ("linux-aarch_64", "8b8f18bd2b30346efbc698dd5a73dd7c805f3ef8380f6dfc95c768f3f1852f6a"),
}
GROUPS = {
    "actor-core": ["actor/core/v1/actor.proto", "actor/core/v1/bridge.proto"],
    "actor-standard": ["actor/standard/v1/transform2d.proto", "actor/standard/v1/motion2d.proto", "actor/standard/v1/locomotion2d.proto"],
    "agents-arena": ["extensions/agents_arena/v1/rooms.proto"],
}
SDK_OUTPUT = Path("packages/com.actor.sdk/Runtime/Protocol/Generated")
GRPC_OUTPUT = Path("packages/com.actor.transport.grpc/Runtime/Generated")
ARENA_OUTPUT = Path("Agents Arena/Assets/Scripts/Actor/Generated")
PYTHON_OUTPUT = Path("generated/python")
MANAGED_OUTPUTS = [SDK_OUTPUT, GRPC_OUTPUT, ARENA_OUTPUT, PYTHON_OUTPUT, Path("generated/descriptors"), Path("generated/manifests")]


def run(args: list[str]) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def verified_archive(filename: str, url: str, digest: str, destination: Path) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / filename
    if not archive.exists():
        print(f"Downloading pinned tool {filename}", flush=True)
        temporary = archive.with_suffix(archive.suffix + ".download")
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        temporary.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise RuntimeError(f"Checksum mismatch: {archive}")
    if not destination.exists():
        with zipfile.ZipFile(archive) as bundle:
            # Only official pinned archives are extracted, after checksum verification.
            bundle.extractall(destination)


def tools() -> tuple[Path, Path, Path]:
    host = (platform.system(), platform.machine())
    if host not in PROTOC_ASSETS:
        raise RuntimeError(f"Codegen supports macOS/Linux x64/arm64, got {host}")
    asset, checksum = PROTOC_ASSETS[host]
    name = f"protoc-{PROTOC_VERSION}-{asset}.zip"
    directory = CACHE / f"protoc-{PROTOC_VERSION}-{asset}"
    verified_archive(name, f"https://github.com/protocolbuffers/protobuf/releases/download/v{PROTOC_VERSION}/{name}", checksum, directory)
    protoc = directory / "bin/protoc"
    protoc.chmod(0o755)
    actual = subprocess.check_output([str(protoc), "--version"], text=True).strip()
    if actual != f"libprotoc {PROTOC_VERSION}":
        raise RuntimeError(f"Unexpected protoc: {actual}")
    grpc = CACHE / f"grpc.tools-{GRPC_TOOLS_VERSION}"
    verified_archive(f"grpc.tools.{GRPC_TOOLS_VERSION}.nupkg",
        f"https://api.nuget.org/v3-flatcontainer/grpc.tools/{GRPC_TOOLS_VERSION}/grpc.tools.{GRPC_TOOLS_VERSION}.nupkg",
        GRPC_TOOLS_SHA256, grpc)
    target = "macosx_universal" if host[0] == "Darwin" else "linux_arm64" if host[1] == "aarch64" else "linux_x64"
    plugin = grpc / "tools" / target / "grpc_csharp_plugin"
    plugin.chmod(0o755)
    return protoc, directory / "include", plugin


def canonical_descriptor(data: bytes) -> bytes:
    original = descriptor_pb2.FileDescriptorSet.FromString(data)
    canonical = descriptor_pb2.FileDescriptorSet()
    for source in sorted(original.file, key=lambda item: item.name):
        target = canonical.file.add()
        target.CopyFrom(source)
        target.ClearField("source_code_info")
    return canonical.SerializeToString(deterministic=True)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def extension_metadata(output: Path, group: str, source: str, resource: Path,
    csharp_directory: Path, namespace: str, helper: str) -> None:
    definition = json.loads((ROOT / source).read_text())
    data = (output / "generated/descriptors" / (group + ".pb")).read_bytes()
    fingerprint = hashlib.sha256(data).hexdigest()
    pool = descriptor_pool.DescriptorPool()
    remaining = list(descriptor_pb2.FileDescriptorSet.FromString(data).file)
    while remaining:
        progress = False
        for file in remaining[:]:
            try:
                pool.Add(file)
            except TypeError:
                continue
            remaining.remove(file)
            progress = True
        if not progress:
            raise RuntimeError("Descriptor dependency cycle or missing dependency")
    capabilities = []
    for source_capability in definition.pop("capabilities"):
        source_capability = source_capability.copy()
        message_name = source_capability.pop("message_type")
        pool.FindMessageTypeByName(message_name)  # Fail on stale metadata/type names.
        type_url = "type.googleapis.com/" + message_name
        is_command = source_capability["kind"] == "command"
        source_capability.update({"version": definition["extension_version"], "extension_id": definition["extension_id"],
            "input_type_url": type_url if is_command else "", "output_type_url": "" if is_command else type_url})
        capabilities.append(source_capability)
    definition.update({"schema_hash": fingerprint, "descriptor_set": resource.name + ".pb", "capabilities": capabilities})
    serialized = json.dumps(definition, indent=2, sort_keys=True) + "\n"
    write_text(output / "generated/manifests" / (group + ".json"), serialized)
    write_text(output / PYTHON_OUTPUT / resource.parent / (resource.name + ".json"), serialized)
    descriptor = output / PYTHON_OUTPUT / resource.parent / (resource.name + ".pb")
    descriptor.parent.mkdir(parents=True, exist_ok=True)
    descriptor.write_bytes(data)
    write_text(output / csharp_directory / (helper + ".g.cs"), f'''// <auto-generated> Generated by scripts/generate-proto.py; do not edit. </auto-generated>
namespace {namespace}
{{
    public static class {helper}
    {{
        public const string Id = "{definition['extension_id']}";
        public const string Version = "{definition['extension_version']}";
        public const string SchemaHash = "{fingerprint}";
        private const string DescriptorBase64 = "{base64.b64encode(data).decode('ascii')}";
        public static Actor.Sdk.ExtensionPackage Create(bool required = true) =>
            new Actor.Sdk.ExtensionPackage(Id, Version, SchemaHash, System.Convert.FromBase64String(DescriptorBase64), {definition['supported_protocol_major']}, required);
    }}
}}
''')


def generate(output: Path) -> None:
    protoc, include, plugin = tools()
    for directory in MANAGED_OUTPUTS:
        (output / directory).mkdir(parents=True, exist_ok=True)
    common = [str(protoc), "-I", str(ROOT / "proto"), "-I", str(include)]
    run(common + [f"--csharp_out={output / SDK_OUTPUT}", "--csharp_opt=file_extension=.g.cs"] + GROUPS["actor-core"] + GROUPS["actor-standard"])
    run(common + [f"--csharp_out={output / ARENA_OUTPUT}", "--csharp_opt=file_extension=.g.cs"] + GROUPS["agents-arena"])
    run(common + [f"--plugin=protoc-gen-grpc={plugin}", f"--grpc_out={output / GRPC_OUTPUT}", "--grpc_opt=no_server", "actor/core/v1/bridge.proto"])
    all_sources = [source for group in GROUPS.values() for source in group]
    run(common + [f"--python_out={output / PYTHON_OUTPUT}"] + all_sources)
    # grpcio-tools 1.82.2 embeds protoc 35.0; used only for its official gRPC Python plugin.
    run([sys.executable, "-m", "grpc_tools.protoc", "-I", str(ROOT / "proto"),
        f"--grpc_python_out={output / PYTHON_OUTPUT}", "actor/core/v1/bridge.proto"])
    for name, sources in GROUPS.items():
        descriptor = output / "generated/descriptors" / (name + ".pb")
        run(common + [f"--descriptor_set_out={descriptor}", "--include_imports"] + sources)
        descriptor.write_bytes(canonical_descriptor(descriptor.read_bytes()))
    extension_metadata(output, "actor-standard", "proto/actor/standard/extension.json", Path("actor/metadata/standard.v1"),
        SDK_OUTPUT, "Actor.Protocol.Standard.V1", "ActorStandardExtensionPackage")
    extension_metadata(output, "agents-arena", "proto/extensions/agents_arena/extension.json", Path("extensions/agents_arena/metadata/agents_arena.v1"),
        ARENA_OUTPUT, "AgentsArena.ActorProtocol.V1", "AgentsArenaExtensionPackage")
    # Resource names include .v1, so append rather than replace the suffix.
    for directory in (output / PYTHON_OUTPUT).rglob("*"):
        if directory.is_dir():
            write_text(directory / "__init__.py", "# Generated package marker.\n")


def output_files(base: Path) -> dict[Path, bytes]:
    result = {}
    for directory in MANAGED_OUTPUTS:
        for path in (base / directory).rglob("*"):
            if path.is_file() and path.suffix not in (".meta", ".pyc") and "__pycache__" not in path.parts:
                result[path.relative_to(base)] = path.read_bytes()
    return result


def main() -> None:
    for name, expected in {"protobuf": "7.36.2", "grpcio-tools": "1.82.2", "grpcio": "1.82.2"}.items():
        if version(name) != expected:
            raise RuntimeError(f"{name} must be {expected}; run scripts/generate-proto.sh")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="actor-codegen-", dir=CACHE) as temporary:
        output = Path(temporary)
        generate(output)
        expected = output_files(output)
        existing = output_files(ROOT)
        if arguments.check:
            mismatches = sorted(str(path) for path in set(expected) | set(existing) if expected.get(path) != existing.get(path))
            if mismatches:
                raise SystemExit("Generated protocol artifacts are stale:\n" + "\n".join(mismatches))
            print(f"Protocol freshness passed: {len(expected)} generated artifacts")
        else:
            for path in existing.keys() - expected.keys():
                (ROOT / path).unlink()
            for path, data in expected.items():
                destination = ROOT / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            print(f"Generated {len(expected)} protocol artifacts")


if __name__ == "__main__":
    main()
