# Add a typed game extension

ACTOR Core defines the exchange mechanism, rather than a universal ontology of games. A game supplies its own schemas and registers typed capabilities. This example adds fictitious `MyGame.Mana`; it is a tutorial and is not part of Agents Arena gameplay or the SDK's Standard capabilities.

## 1. Define the schema

Create `proto/extensions/my_game_mana/v1/mana.proto`:

```proto
syntax = "proto3";
package my_game.mana.v1;
option csharp_namespace = "MyGame.ActorProtocol.V1";

message ManaObservation {
  float current = 1;
  float maximum = 2;
}
```

## 2. Describe the local package

Create `proto/extensions/my_game_mana/extension.json`:

```json
{
  "extension_id": "my-game-mana",
  "extension_version": "0.1.0",
  "supported_protocol_major": 1,
  "generation": {
    "sources": ["extensions/my_game_mana/v1/mana.proto"],
    "csharp_output": "MyGame/Assets/Actor/Generated",
    "helper_namespace": "MyGame.ActorProtocol.V1",
    "helper_name": "MyGameManaExtensionPackage",
    "python_resource": "extensions/my_game_mana/metadata/my_game_mana.v1",
    "artifact_name": "my-game-mana"
  },
  "capabilities": [
    {
      "capability_id": "my-game.mana",
      "kind": "observation",
      "message_type": "my_game.mana.v1.ManaObservation",
      "description": "Current and maximum mana."
    }
  ]
}
```

Choose `csharp_output` for your game or separate extension UPM package. Keep game mechanics outside `com.actor.sdk`. Source paths are relative to `proto/`; C# output paths are relative to the repository; Python resources are relative to `generated/python/`. C# output must be a dedicated directory named `Generated`. Absolute paths, `..`, Windows drive paths and escaping symlinks are rejected. Artifact/resource names must be unique.

`generation` is local build metadata and is omitted from the generated Runtime manifest. The generator supplies descriptor bytes, schema hash and canonical type URLs. Package metadata JSON is never the Game ↔ Runtime IPC format.

## 3. Generate and check

```bash
scripts/generate-proto.sh
scripts/generate-proto.sh --check
```

The generator discovers every `proto/**/extension.json`; adding a game package does not require editing Core or the generator. For metadata stored elsewhere, use `--extension-manifest path/to/extension.json`; its source/output paths keep the same repository-relative meanings.

For this example it produces:

- C# message bindings and `MyGameManaExtensionPackage.g.cs` in the configured game directory.
- Python bindings under `generated/python/extensions/my_game_mana/v1/`.
- Descriptor/manifest artifacts under `generated/descriptors/my-game-mana.pb` and `generated/manifests/my-game-mana.json`.
- Installed-package resources `extensions/my_game_mana/metadata/my_game_mana.v1.{pb,json}` under the generated Python tree.

Generated sources are committed and freshness is checked. `scripts/generate-proto.sh --self-test` proves this workflow with an isolated fictitious Mana package under ignored `build/`, including typed Any roundtrip, descriptor/helper generation and six path-safety checks. It never installs Mana into the real game or modifies production generated outputs.

## 4. Register in Unity

Reference `com.actor.sdk` from the game, then register the generated message through an extension:

```csharp
using System;
using Actor.Sdk;
using MyGame.ActorProtocol.V1;

public sealed class ManaActorExtension : IActorExtension
{
    private readonly Func<(float Current, float Maximum)> capture;
    public ExtensionPackage Package { get; } =
        MyGameManaExtensionPackage.Create(required: true);

    public ManaActorExtension(Func<(float Current, float Maximum)> capture)
        => this.capture = capture;

    public void Register(IActorExtensionRegistrar registry)
    {
        registry.RegisterObservation<ManaObservation>(
            "my-game.mana", "Current and maximum mana.", _ =>
            {
                var state = capture();
                return new ManaObservation
                {
                    Current = state.Current,
                    Maximum = state.Maximum
                };
            });
    }
}
```

The game's `IActorGameAdapter.Register` then calls:

```csharp
registry.RegisterExtension(new ManaActorExtension(
    () => (manaComponent.Current, manaComponent.Maximum)));
```

SDK callbacks run on the game thread through `ActorBridgeClient.Pump`. The generated helper embeds the exact descriptor set and its fingerprint. Registration checks that the compiled message belongs to that package/schema; manifest construction, Any serialization, type URL and envelope identity are automatic. The game developer writes only its capture logic and capability identity.

Commands use `RegisterCommand<T>(id, description, handler, delivery)` and events use `RegisterEvent<T>(id, description)`, which returns a typed publisher. Choose `CommandDelivery.Latest` for replaceable intent and `Ordered` for actions whose ordering matters. Handler acceptance acknowledges intent; authoritative observations report actual execution.

## 5. Install a Python plugin

For an additional package at `runtime/extensions/my_game_mana/`, create `my_game_mana/__init__.py`:

```python
from importlib import resources

from actor_runtime.extensions import ExtensionPackage
from extensions.my_game_mana.v1.mana_pb2 import ManaObservation


def extension_package() -> ExtensionPackage:
    data = resources.files("extensions.my_game_mana.metadata")
    return ExtensionPackage.from_resources(
        data.joinpath("my_game_mana.v1.json").read_bytes(),
        data.joinpath("my_game_mana.v1.pb").read_bytes(),
        (ManaObservation,),
    )
```

Its `pyproject.toml` packages the generated child modules/resources and declares an entry point:

```toml
[build-system]
requires = ["setuptools==84.0.0"]
build-backend = "setuptools.build_meta"

[project]
name = "actor-my-game-mana"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["actor-runtime==0.6.0"]

[project.entry-points."actor.extensions"]
my-game-mana = "my_game_mana:extension_package"

[tool.setuptools]
package-dir = {my_game_mana = "my_game_mana", "extensions.my_game_mana" = "../../../generated/python/extensions/my_game_mana"}
packages = ["my_game_mana", "extensions.my_game_mana", "extensions.my_game_mana.v1", "extensions.my_game_mana.metadata"]

[tool.setuptools.package-data]
"extensions.my_game_mana.metadata" = ["*.pb", "*.json"]
```

Install into the same environment as ACTOR Runtime:

```bash
python -m pip install -e runtime
python -m pip install -e runtime/extensions/my_game_mana
actor-runtime serve
```

Runtime discovers `actor.extensions` entry points, verifies package identity/version/fingerprint, loads descriptors and maps generated message classes automatically. No `if game == "my-game"` branch is added to Runtime Core. Installed descriptors can also decode types dynamically when a generated class is absent. Unknown optional extensions can stay opaque; a missing required extension produces `RequiredExtensionMissing` with its ID, version and hash.

A policy in the game plugin reads a typed copy from the deterministic cache:

```python
mana = session.world.latest(ManaObservation, capability_id="my-game.mana")
if mana is not None:
    print(mana.current, mana.maximum)
```

The plugin is independent of NGO and Unity; it only consumes ACTOR protocol and typed game schemas.

## Version changes

Never reuse field numbers; reserve deleted field numbers and names. Prefer additive fields while preserving existing meaning. A breaking schema receives a new major proto package/type URL, such as `my_game.mana.v2`. Extension version, Core protocol version and game version are separate. The MVP verifies an exact required package version and descriptor fingerprint; a hash is package identity evidence rather than a semantic compatibility proof.
