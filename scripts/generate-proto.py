#!/usr/bin/env python3
"""Pinned official codegen; --check compares regenerated artifacts without editing them."""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
from importlib.metadata import version
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import re
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
CORE_SOURCES = ["actor/core/v1/actor.proto", "actor/core/v1/bridge.proto"]
SDK_OUTPUT = Path("packages/com.actor.sdk/Runtime/Protocol/Generated")
GRPC_OUTPUT = Path("packages/com.actor.transport.grpc/Runtime/Generated")
PYTHON_OUTPUT = Path("generated/python")


@dataclass(frozen=True)
class ExtensionGeneration:
    definition: dict
    sources: tuple[str, ...]
    csharp_output: Path
    helper_namespace: str
    helper_name: str
    python_resource: Path
    artifact_name: str


def safe_relative(value: str, root: Path, *, generated_directory: bool = False) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or PureWindowsPath(value).drive:
        raise ValueError(f"Expected a portable relative path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in value.split("/"):
        raise ValueError(f"Path must stay relative without traversal: {value!r}")
    result = Path(path)
    if not (root / result).resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes its output root through a symlink: {value!r}")
    if generated_directory and (len(path.parts) < 2 or path.name != "Generated"):
        raise ValueError("C# output must be a dedicated subdirectory named Generated")
    return result


def discover_extensions(root: Path, extra_manifests: list[Path] | None = None) -> list[ExtensionGeneration]:
    files = {path.resolve() for path in (root / "proto").rglob("extension.json")}
    files.update(path.resolve() for path in (extra_manifests or []))
    result = []
    ids, artifacts, resources, owned_sources = set(), {"actor-core"}, set(), set(CORE_SOURCES)
    for path in sorted(files):
        definition = json.loads(path.read_text())
        generation = definition.pop("generation", None)
        if not isinstance(generation, dict):
            raise ValueError(f"Extension manifest needs generation metadata: {path}")
        extension_id = definition["extension_id"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", extension_id):
            raise ValueError(f"Invalid extension ID: {extension_id}")
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?", definition["extension_version"]):
            raise ValueError("Extension version must be semantic version text")
        if type(definition["supported_protocol_major"]) is not int or not 1 <= definition["supported_protocol_major"] <= 0xffffffff:
            raise ValueError("supported_protocol_major must be a positive integer")
        artifact = generation["artifact_name"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", artifact):
            raise ValueError("Invalid generated artifact name")
        csharp = safe_relative(generation["csharp_output"], root, generated_directory=True)
        resource = safe_relative(generation["python_resource"], root / PYTHON_OUTPUT)
        helper_namespace, helper_name = generation["helper_namespace"], generation["helper_name"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", helper_namespace):
            raise ValueError("Invalid C# helper namespace")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", helper_name):
            raise ValueError("Invalid C# helper name")
        sources = generation["sources"]
        if not isinstance(sources, list) or not sources:
            raise ValueError("Extension generation needs at least one proto source")
        validated_sources = []
        for source in sources:
            relative = safe_relative(source, root / "proto")
            if relative.suffix != ".proto" or not (root / "proto" / relative).is_file():
                raise ValueError(f"Proto source is missing: {source}")
            if source in owned_sources:
                raise ValueError(f"Proto source has multiple generating owners; import it instead: {source}")
            owned_sources.add(source)
            validated_sources.append(source)
        if extension_id in ids or artifact in artifacts or resource in resources:
            raise ValueError("Duplicate extension ID, artifact name, or Python resource")
        ids.add(extension_id); artifacts.add(artifact); resources.add(resource)
        result.append(ExtensionGeneration(definition, tuple(validated_sources), csharp,
            helper_namespace, helper_name, resource, artifact))
    return result


def managed_outputs(extensions: list[ExtensionGeneration]) -> list[Path]:
    return sorted({SDK_OUTPUT, GRPC_OUTPUT, PYTHON_OUTPUT, Path("generated/descriptors"), Path("generated/manifests"),
        *(extension.csharp_output for extension in extensions)}, key=str)


def source_arguments(root: Path, sources: list[str] | tuple[str, ...]) -> list[str]:
    # Absolute filenames cannot be interpreted by protoc as options supplied through metadata.
    return [str((root / "proto" / source).resolve()) for source in sources]


def run(args: list[str], root: Path = ROOT) -> None:
    subprocess.run(args, cwd=root, check=True)


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


def extension_metadata(output: Path, extension: ExtensionGeneration) -> None:
    definition = json.loads(json.dumps(extension.definition))
    group, resource, csharp_directory = extension.artifact_name, extension.python_resource, extension.csharp_output
    namespace, helper = extension.helper_namespace, extension.helper_name
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


def generate(output: Path, extensions: list[ExtensionGeneration], root: Path = ROOT) -> None:
    protoc, include, plugin = tools()
    for directory in managed_outputs(extensions):
        safe_relative(str(directory), root)  # Includes fixed roots, which could themselves be symlinks.
        (output / directory).mkdir(parents=True, exist_ok=True)
    common = [str(protoc), "-I", str(root / "proto"), "-I", str(include)]
    run(common + [f"--csharp_out={output / SDK_OUTPUT}", "--csharp_opt=file_extension=.g.cs"] + source_arguments(root, CORE_SOURCES), root)
    for extension in extensions:
        with tempfile.TemporaryDirectory(prefix="package-bindings-", dir=output) as temporary:
            bindings = Path(temporary)
            run(common + [f"--csharp_out={bindings}", "--csharp_opt=file_extension=.g.cs"] + source_arguments(root, extension.sources), root)
            for source in bindings.iterdir():
                destination = output / extension.csharp_output / source.name
                if destination.exists():
                    raise ValueError(f"Generated C# filename collision: {destination.relative_to(output)}")
                shutil.copyfile(source, destination)
    run(common + [f"--plugin=protoc-gen-grpc={plugin}", f"--grpc_out={output / GRPC_OUTPUT}", "--grpc_opt=no_server"] + source_arguments(root, ["actor/core/v1/bridge.proto"]), root)
    all_sources = CORE_SOURCES + [source for extension in extensions for source in extension.sources]
    run(common + [f"--python_out={output / PYTHON_OUTPUT}"] + source_arguments(root, all_sources), root)
    # grpcio-tools 1.82.2 embeds protoc 35.0; used only for its official gRPC Python plugin.
    run([sys.executable, "-m", "grpc_tools.protoc", "-I", str(root / "proto"),
        f"--grpc_python_out={output / PYTHON_OUTPUT}"] + source_arguments(root, ["actor/core/v1/bridge.proto"]), root)
    groups = [("actor-core", CORE_SOURCES)] + [(extension.artifact_name, list(extension.sources)) for extension in extensions]
    for name, sources in groups:
        descriptor = output / "generated/descriptors" / (name + ".pb")
        run(common + [f"--descriptor_set_out={descriptor}", "--include_imports"] + source_arguments(root, sources), root)
        descriptor.write_bytes(canonical_descriptor(descriptor.read_bytes()))
    for extension in extensions:
        helper = output / extension.csharp_output / (extension.helper_name + ".g.cs")
        if helper.exists():
            raise ValueError(f"Generated helper filename collision: {helper.relative_to(output)}")
        extension_metadata(output, extension)
    # Resource names include .v1, so append rather than replace the suffix.
    for directory in (output / PYTHON_OUTPUT).rglob("*"):
        if directory.is_dir():
            write_text(directory / "__init__.py", "# Generated package marker.\n")


def output_files(base: Path, extensions: list[ExtensionGeneration]) -> dict[Path, bytes]:
    result = {}
    for directory in managed_outputs(extensions):
        for path in (base / directory).rglob("*"):
            if path.is_file() and path.suffix not in (".meta", ".pyc") and "__pycache__" not in path.parts:
                result[path.relative_to(base)] = path.read_bytes()
    return result


def self_test() -> None:
    """Prove a new game package works through metadata alone, outside production outputs."""
    from importlib import import_module
    from google.protobuf.any_pb2 import Any

    with tempfile.TemporaryDirectory(prefix="extension-selftest-", dir=CACHE) as temporary:
        base = Path(temporary)
        workspace, output = base / "fixture-repo", base / "output"
        shutil.copytree(ROOT / "proto", workspace / "proto")
        package_dir = workspace / "proto/extensions/my_game_mana"
        write_text(package_dir / "v1/mana.proto", '''syntax = "proto3";
package my_game.mana.v1;
option csharp_namespace = "MyGame.ActorProtocol.V1";
message ManaObservation { float current = 1; float maximum = 2; }
''')
        manifest = {
            "extension_id": "my-game-mana", "extension_version": "0.1.0", "supported_protocol_major": 1,
            "generation": {
                "sources": ["extensions/my_game_mana/v1/mana.proto"],
                "csharp_output": "build/my-game/Generated",
                "helper_namespace": "MyGame.ActorProtocol.V1", "helper_name": "MyGameManaExtensionPackage",
                "python_resource": "extensions/my_game_mana/metadata/my_game_mana.v1", "artifact_name": "my-game-mana",
            },
            "capabilities": [{"capability_id": "my-game.mana", "kind": "observation",
                "message_type": "my_game.mana.v1.ManaObservation", "description": "Current and maximum mana."}],
        }
        manifest_path = package_dir / "extension.json"
        write_text(manifest_path, json.dumps(manifest))
        baseline = discover_extensions(ROOT)
        extensions = discover_extensions(workspace)
        assert len(extensions) == len(baseline) + 1
        generate(output, extensions, workspace)
        metadata = json.loads((output / "generated/manifests/my-game-mana.json").read_text())
        descriptor = (output / "generated/descriptors/my-game-mana.pb").read_bytes()
        assert metadata["schema_hash"] == hashlib.sha256(descriptor).hexdigest()
        assert "generation" not in metadata
        assert metadata["capabilities"][0]["output_type_url"] == "type.googleapis.com/my_game.mana.v1.ManaObservation"
        assert (output / "build/my-game/Generated/Mana.g.cs").is_file()
        helper = (output / "build/my-game/Generated/MyGameManaExtensionPackage.g.cs").read_text()
        assert metadata["schema_hash"] in helper and "DescriptorBase64" in helper
        sys.path.insert(0, str(output / PYTHON_OUTPUT))
        try:
            message = import_module("extensions.my_game_mana.v1.mana_pb2").ManaObservation
            value = message(current=7, maximum=10)
            packed = Any(); packed.Pack(value)
            decoded = message(); assert packed.Unpack(decoded) and decoded == value
        finally:
            sys.path.pop(0)
        # An explicitly supplied manifest need not reside under the discovery tree.
        extra = workspace / "build/local-extension.json"
        extra.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.rename(extra)
        assert len(discover_extensions(workspace)) == len(baseline)
        assert len(discover_extensions(workspace, [extra])) == len(baseline) + 1
        rejected = 0
        for invalid in ("../outside/Generated", "/tmp/outside/Generated", "C:\\outside\\Generated", ".", "build/not-dedicated"):
            try:
                safe_relative(invalid, workspace, generated_directory=True)
            except ValueError:
                rejected += 1
        assert rejected == 5
        outside = base / "outside"
        outside.mkdir()
        (workspace / "escape-link").symlink_to(outside, target_is_directory=True)
        try:
            safe_relative("escape-link/Generated", workspace, generated_directory=True)
        except ValueError:
            rejected += 1
        assert rejected == 6
        write_text(CACHE / "extension-selftest.json", json.dumps({"passed": True, "path_guards": rejected,
            "automatic_discovery": True, "explicit_manifest": True, "generated_any_roundtrip": True,
            "typed_csharp_helper": True, "schema_hash": metadata["schema_hash"]}, indent=2) + "\n")
        print("Extension generation self-test passed: automatic Mana package, typed bindings/Any, descriptors/helper, 6 path guards")


def main() -> None:
    for name, expected in {"protobuf": "7.36.2", "grpcio-tools": "1.82.2", "grpcio": "1.82.2"}.items():
        if version(name) != expected:
            raise RuntimeError(f"{name} must be {expected}; run scripts/generate-proto.sh")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--extension-manifest", action="append", type=Path, default=[],
        help="Also generate a local extension manifest outside auto-discovered proto/**/extension.json")
    parser.add_argument("--self-test", action="store_true", help="Generate an isolated fictitious Mana package and check path guards")
    arguments = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    if arguments.self_test:
        self_test()
        return
    extensions = discover_extensions(ROOT, arguments.extension_manifest)
    with tempfile.TemporaryDirectory(prefix="actor-codegen-", dir=CACHE) as temporary:
        output = Path(temporary)
        generate(output, extensions)
        expected = output_files(output, extensions)
        existing = output_files(ROOT, extensions)
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
                if not destination.resolve().is_relative_to(ROOT.resolve()):
                    raise ValueError(f"Generated file escapes repository through a symlink: {path}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            print(f"Generated {len(expected)} protocol artifacts")


if __name__ == "__main__":
    main()
