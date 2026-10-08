#!/usr/bin/env python3
"""Restore locked NuGet libraries and stage Unity-compatible assemblies/licenses."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "scripts/unity-grpc-deps/Dependencies.csproj"
dotnet = os.environ.get("DOTNET") or str(ROOT / "build/tools/dotnet/dotnet")
if not Path(dotnet).exists():
    dotnet = "dotnet"
env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_CLI_HOME=str(ROOT / "build/tools/dotnet-home"))
subprocess.run([dotnet, "restore", str(PROJECT), "--locked-mode"] if PROJECT.with_name("packages.lock.json").exists()
               else [dotnet, "restore", str(PROJECT)], check=True, env=env)
subprocess.run([dotnet, "build", str(PROJECT), "--no-restore", "-c", "Release"], check=True, env=env)

sdk = ROOT / "packages/com.actor.sdk/Runtime/Plugins"
grpc = ROOT / "packages/com.actor.transport.grpc/Runtime/Plugins"
sdk.mkdir(parents=True, exist_ok=True)
grpc.mkdir(parents=True, exist_ok=True)
selected = {
    "Google.Protobuf": sdk,
    "Grpc.Core.Api": grpc,
    "Grpc.Net.Client": grpc,
    "Grpc.Net.Common": grpc,
    "Microsoft.Extensions.DependencyInjection.Abstractions": grpc,
    "Microsoft.Extensions.Logging.Abstractions": grpc,
    "System.Diagnostics.DiagnosticSource": grpc,
    "System.IO.Pipelines": grpc,
    "System.Runtime.CompilerServices.Unsafe": grpc,
}
assets = json.loads((PROJECT.parent / "obj/project.assets.json").read_text())
nuget_root = Path(next(iter(assets["packageFolders"])))
target = next(iter(assets["targets"].values()))
licenses = ROOT / "packages/com.actor.transport.grpc/ThirdParty"
licenses.mkdir(parents=True, exist_ok=True)
metadata = []
for key, package in target.items():
    name, version = key.split("/")
    if name not in selected:
        continue
    files = [path for path in package.get("runtime", {}) if path.endswith("/" + name + ".dll")]
    if len(files) != 1:
        raise RuntimeError("Missing/ambiguous compatible assembly: " + key)
    source = nuget_root / name.lower() / version / files[0]
    destination = selected[name] / (name + ".dll")
    shutil.copyfile(source, destination)
    archive = nuget_root / name.lower() / version / (name.lower() + "." + version + ".nupkg")
    archive_hash = hashlib.sha512(archive.read_bytes()).digest()
    import base64
    expected = (archive.parent / (archive.name + ".sha512")).read_text().strip()
    if base64.b64encode(archive_hash).decode() != expected:
        raise RuntimeError("NuGet archive hash mismatch: " + key)
    with zipfile.ZipFile(archive) as contents:
        for filename in contents.namelist():
            if Path(filename).name.upper() in {"LICENSE.TXT", "THIRD-PARTY-NOTICES.TXT"}:
                (licenses / (name + "-" + Path(filename).name)).write_bytes(contents.read(filename))
    metadata.append({"package": name, "version": version, "asset": files[0],
                     "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                     "nuget_sha512": expected})

sources = {
    "Google.Protobuf-LICENSE.txt": "https://raw.githubusercontent.com/protocolbuffers/protobuf/v36.2/LICENSE",
    "Grpc-dotnet-LICENSE.txt": "https://raw.githubusercontent.com/grpc/grpc-dotnet/v2.84.0/LICENSE",
}
for name, url in sources.items():
    subprocess.run(["curl", "--fail", "--location", "--silent", url, "-o", str(licenses / name)], check=True)
sdk_licenses = ROOT / "packages/com.actor.sdk/ThirdParty"
sdk_licenses.mkdir(parents=True, exist_ok=True)
shutil.copyfile(licenses / "Google.Protobuf-LICENSE.txt", sdk_licenses / "Google.Protobuf-LICENSE.txt")
(licenses / "dependencies.json").write_text(json.dumps(metadata, indent=2) + "\n")
print("Staged", len(metadata), "pinned Unity libraries; framework-provided System.Memory/Buffers/Tasks are excluded.")
