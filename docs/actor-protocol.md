# ACTOR protocol and adapter boundary

ACTOR Core does not prescribe a game's world model. It defines a typed, versioned extension mechanism through which a game declares observations, events, commands and supported capabilities. Rooms, health, weapons, inventories and game rules belong to optional capability packages or game extensions.

```text
Python ACTOR Runtime
  ExtensionRegistry → WorldState → installed scripted policy
                ↕ one loopback HTTP/2 gRPC stream
ACTOR SDK: protocol / registry / main-thread Pump / bounded mailboxes
                ↕ IActorGameAdapter
Agents Arena adapter → AgentInputSource → existing NGO input
                ↕ Unity Transport to VPS
Dedicated Server: motor / physics / room membership
```

## Three schema layers

| Layer | Sources / namespace | Responsibility |
| --- | --- | --- |
| Core | `proto/actor/core/v1`, `actor.core.v1` | Session, identity, manifest, Any envelopes, ordering, heartbeat, command outcome, errors |
| Standard | `proto/actor/standard/v1`, `actor.standard.v1` | Optional reusable Transform2D, Motion2D and Locomotion2D |
| Agents Arena | `proto/extensions/agents_arena/v1`, `actor.extensions.agents_arena.v1` | RoomMembership and RoomChanged |

`com.actor.sdk` contains Core/Standard generated types, extension registration and generic bridge lifecycle. It has no Unity engine references, NGO dependency or room mechanics. `com.actor.transport.grpc` provides the concrete native HTTP/2 implementation. The Unity reference game's adapter binds these generic interfaces to its existing network player and input abstraction.

## Strong typing and extensions

Messages are authored in `.proto` and generated for C# and Python by pinned official tools. Envelopes carry a capability ID, extension ID/version, entity identity and `google.protobuf.Any`. A payload's canonical identity is `type.googleapis.com/<protobuf full name>`. The schema's `v1` package gives breaking versions a distinct identity.

Game developers register a generated message type through a generic SDK method. The SDK derives the descriptor, type URL, Any payload and manifest entry. It rejects a registration whose compiled descriptor does not match the declared extension package. Runtime plugins provide metadata, descriptor bytes and optionally known generated Python classes. Descriptor-only installed types can also be decoded dynamically through an isolated Protobuf descriptor pool.

An extension package includes ID/version, supported Core major, a SHA-256 fingerprint, canonical descriptor set and capability metadata. Its fingerprint covers deterministic descriptor bytes, with imported schemas included, source comments removed and files sorted. Generated helpers and package resources carry the exact same descriptor artifact. Required packages must match the exact version/hash in this MVP. Missing or incompatible packages produce structured errors containing the package identity and expected schema hash. Optional unknown/incompatible packages are retained as opaque data or skipped without breaking the connection.

Capability definitions include kind, description, version, extension identity and input/output type URLs. Infrastructure kinds include Observation, Command, Event and future Skill; no skill execution is implemented. Ordered/latest command delivery describes queue semantics, not a game mechanic. Only registered observations, events and commands appear in a game's manifest.

## Session and stream

Python listens on `127.0.0.1:<configured port>`; Unity is the gRPC client. A random per-launch credential goes to both processes through `ACTOR_BRIDGE_TOKEN` and authenticates metadata `x-actor-bridge-token`. It is never included in protocol payloads, logs or committed configuration. The endpoint validator accepts only loopback HTTP addresses. Public nginx/VPS ports do not expose the bridge.

One `ActorBridge.Connect` RPC contains two independently sending streams. Every outer message has a fresh session UUID and increasing sequence number. Game → Runtime begins with Hello then CapabilityManifest. Runtime validates protocol, identity, extension packages and capabilities before sending an accepted RuntimeHello. Commands arriving before acceptance are rejected. Reconnect creates a new session and clears old queues/cache; old tasks cannot update a new session.

ObservationFrame and GameEventEnvelope revisions order captures and changes in the bridge session. They are not advertised as authoritative server simulation ticks. Room membership has a separate server-maintained revision inside the reference game. CommandResult Accepted means the adapter accepted intent; actual physical execution is visible in subsequent observations.

## Rates and backpressure

- Game observations default to **15 Hz**, configurable independently of rendering FPS.
- Scripted locomotion defaults to **20 Hz**; each value replaces older pending intent.
- Existing multiplayer commands refresh at up to **30 Hz**, with immediate send on changes. Server physics remains **50 Hz**.
- One pending observation frame is kept. Events/results/control use a bounded priority queue (128 by default).
- Generic latest commands coalesce by the advertised capability; overwritten pending commands return CommandSuperseded. Ordered commands remain bounded.
- Outgoing sequence numbers are assigned when the writer actually sends a message. A priority event supersedes an older unsent observation revision.
- Wire messages are limited to **1 MiB**. Overflow, invalid ordering or a fatal protocol error closes the session and clears agent intent.

Unity's host calls `ActorBridgeClient.Pump()` on the creating game thread. It is the only point that captures game observations or dispatches typed game handlers. Background tasks exchange Protobuf messages and update bounded mailboxes; they never access GameObject, Transform or Rigidbody2D. Native channel/handler teardown is scheduled outside the game-frame lock.

Commands carry issue time and TTL as well as sequence/command IDs. Both peers share one host clock; expiration is checked again on game-thread dispatch. This avoids resurrecting a command after it waited in a queue. Independent monotonic input timeout stops an agent after **0.25 s** without a fresh accepted movement command. Runtime policy additionally checks freshness per observation type. The reference adapter stops accepting commands if its authoritative game-server motion sample is stale.

## Reference game observations

Standard Transform2D/Motion2D come from a coherent raw owner post-physics server snapshot. They do not mix an interpolated root position with predicted velocity. Owner prediction continues to drive local sprites, animation, nameplates and camera for responsive controls.

`NetworkRoomMembership` computes physical room membership on the dedicated server from stable RoomVolume2D identities. Its replicated state updates `ActorCharacter.RoomId` and produces game-specific RoomChanged events. External Runtime commands cannot set room membership. Main Hall and Engineering share one networked gameplay scene; the new art and colliders do not alter Core/Standard protocol schemas.

## Versioning and generated sources

Core protocol **1.0**, extension package **0.1.0**, game **0.3.0** and multiplayer admission protocol **3** are independent versions. Never reuse Protobuf field numbers. Reserve removed numbers/names; prefer additive changes. A breaking schema changes its package/type identity (for example, `v2`). Exact schema hash validation is a package integrity/compatibility check, not a claim that arbitrary schema changes are semantically compatible.

Generated C#/Python files, descriptors and metadata are version controlled. Unity users do not need protoc. `scripts/generate-proto.sh --check` regenerates in a temporary output tree and compares artifacts; CI fails on stale generated sources. [Tooling and schema rules](../proto/README.md), [Unity SDK API](../packages/com.actor.sdk/README.md), [Python Runtime](python-runtime.md), [custom extensions](extensions.md), [actual gRPC spike](grpc-spike/README.md).

## Current scope

No Codex/LLM, persistent memory, classifier, skill execution, navigation system, package auto-download or additional IPC transport is implemented. The scripted example belongs to the Agents Arena extension and uses simple known waypoints. Future brains and skill/policy layers can sit above the same typed capability protocol without teaching Core a universal game ontology.
