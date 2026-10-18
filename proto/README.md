# ACTOR protocol sources

ACTOR defines an exchange mechanism for typed, versioned capabilities. It does not prescribe a game's world model. `actor/core/v1` contains only infrastructure; `actor/standard/v1` supplies three optional reusable 2D capabilities; `extensions/agents_arena/v1` owns room membership, room-change events and the server-validated StationSignal event.

Core protocol **1.0** is independent of both Agents Arena's game version and its multiplayer admission protocol. Proto packages ending in `v1` identify a schema major. Standard package **0.1.0** and Agents Arena package **0.2.0** are advertised separately. Required packages currently require an exact version and schema fingerprint match; optional unknown packages can remain opaque. Arena's additive StationSignal contract is documented in [Signal Console](../docs/station-signals.md).

## Generate and verify

```bash
scripts/generate-proto.sh
scripts/generate-proto.sh --check
scripts/generate-proto.sh --self-test
```

Generation supports macOS/Linux on x64/arm64 and bootstraps its ignored tools under `build/proto-tools/`. Python 3.12+ and network access are required on the first run. Downloads come from official Protobuf GitHub releases and NuGet, with SHA-256 checksums pinned in the script.

The toolchain pins official **protoc 36.2**, **grpc_csharp_plugin 2.84.0**, **grpcio-tools 1.82.2**, and **Python protobuf 7.36.2**. The latter grpcio-tools embeds protoc 35.0 and is used only for its official Python gRPC stub generator. Official protoc 36.2 generates all message bindings and descriptor sets. Unity's runtime uses **Google.Protobuf 3.36.2**. No message serialization or RPC stubs are handwritten.

Generated C#, Python, descriptor sets and metadata are version-controlled. Ordinary Unity users do not need protoc. `--check` regenerates into an ignored temporary directory and compares every managed artifact without altering committed outputs. `.meta` files and Python bytecode are excluded from the comparison.

## Outputs

- `packages/com.actor.sdk/Runtime/Protocol/Generated`: Core and Standard C# messages and the Standard extension package helper.
- `packages/com.actor.transport.grpc/Runtime/Generated`: official gRPC C# client, kept outside the transport-neutral SDK.
- `Agents Arena/Assets/Scripts/Actor/Generated`: game extension C# messages and package helper.
- `generated/python/actor`: Core/Standard Python messages and official Python gRPC service bindings.
- `generated/python/extensions/agents_arena`: game extension Python messages.
- `generated/descriptors`: one canonical `FileDescriptorSet` per schema package.
- `generated/manifests`: generated package metadata. Matching descriptor/metadata resources are copied into Python packages so installed plugins can load them through `importlib.resources`.

Source extension metadata resides beside its schemas in `extension.json`. Each discovered `proto/**/extension.json` declares its generation sources, C# output/helper and Python resource/artifact names. `--extension-manifest` adds a local metadata file outside that tree. Game packages require no Core or generator edits. The generation section is omitted from Runtime metadata; this JSON describes a local package, not the IPC format. Capability type names are checked against descriptor sets during generation. The SDK derives wire manifests and `Any` envelopes automatically from typed registrations.

Output paths must stay relative to the repository (Python resources relative to `generated/python`), without parent traversal or escaping symlinks. C# uses dedicated `Generated` folders; collisions/duplicate ownership fail rather than silently overwrite bindings. The isolated `--self-test` generates a fictitious Mana extension and verifies typed bindings/Any, descriptors/package helper and path guards. See [extension tutorial](../docs/extensions.md).

## Fingerprint and compatibility

Descriptors include imported schemas. The generator sorts files by their stable proto path, removes source locations, and serializes deterministically. `schema_hash` is the lowercase SHA-256 of the exact resulting descriptor artifact bytes. Comments/source locations do not alter the fingerprint. C# package helpers embed these same bytes; `ExtensionPackage` validates the fingerprint, preserves immutable descriptors, and refuses registrations whose compiled type/schema is absent or incompatible.

Canonical type URLs are `type.googleapis.com/<fully-qualified-proto-message-name>`. ACTOR validates the complete URL, capability identity and extension identity/version before unpacking a command. The prefix is an identifier and is never fetched over the network.

Never reuse field numbers. Reserve removed field numbers and names. Prefer additive fields, preserve existing semantics, and use a new major proto package/type URL for breaking changes. A schema fingerprint detects an exact-package mismatch; it is not a semantic compatibility proof. Package version, Core version, game version and multiplayer admission version remain distinct.

Commands contain a monotonic per-session ID, target actor, issue time and TTL. Both bridge peers run on the same host, so Unix milliseconds reject commands delayed in transport/game-thread buffers; ordering uses session/sequence/revision rather than wall-clock time. `Accepted` acknowledges adapter acceptance of intent, while authoritative game observations show execution.
