# ACTOR protocol and adapter boundary

ACTOR Core does not prescribe a game's world model. It defines a typed, versioned extension mechanism through which a game declares observations, events, commands and supported capabilities. Rooms, health, weapons, inventories and game rules belong to optional capability packages or game extensions.

```text
User Objective → Brain → Runtime Skill Engine
                           ↓
Python ACTOR Runtime: ExtensionRegistry / WorldState / RuntimeSession
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
| Standard motion | `proto/actor/standard/v1`, `actor.standard.v1` | Optional reusable Transform2D, Motion2D and Locomotion2D |
| Standard communication | `proto/actor/standard/communication/v1`, `actor.standard.communication.v1` | Independently optional typed messages, server receipts and authorized peer roster |
| Agents Arena | `proto/extensions/agents_arena/v1`, `actor.extensions.agents_arena.v1` | RoomMembership, RoomChanged, StationSignal and cooperative calibration |

`com.actor.sdk` contains Core/Standard generated types, extension registration and generic bridge lifecycle. It has no Unity engine references, NGO dependency or room mechanics. `com.actor.transport.grpc` provides the concrete native HTTP/2 implementation. The Unity reference game's adapter binds these generic interfaces to its existing network player and input abstraction.

## Strong typing and extensions

Messages are authored in `.proto` and generated for C# and Python by pinned official tools. Envelopes carry a capability ID, extension ID/version, entity identity and `google.protobuf.Any`. A payload's canonical identity is `type.googleapis.com/<protobuf full name>`. The schema's `v1` package gives breaking versions a distinct identity.

Game developers register a generated message type through a generic SDK method. The SDK derives the descriptor, type URL, Any payload and manifest entry. It rejects a registration whose compiled descriptor does not match the declared extension package. Runtime plugins provide metadata, descriptor bytes and optionally known generated Python classes. Descriptor-only installed types can also be decoded dynamically through an isolated Protobuf descriptor pool.

An extension package includes ID/version, supported Core major, a SHA-256 fingerprint, canonical descriptor set and capability metadata. Its fingerprint covers deterministic descriptor bytes, with imported schemas included, source comments removed and files sorted. Generated helpers and package resources carry the exact same descriptor artifact. Required packages must match the exact version/hash in this MVP. Missing or incompatible packages produce structured errors containing the package identity and expected schema hash. Optional unknown/incompatible packages are retained as opaque data or skipped without breaking the connection.

Capability definitions include kind, description, version, extension identity and input/output type URLs. Infrastructure kinds include Observation, Command, Event and future game-native Skill. Runtime-side Skills compose existing capabilities locally and do not use the reserved wire Skill kind or add manifest entries. See [Skills](skills.md) and [Brains](brains.md). Ordered/latest command delivery describes queue semantics, not a game mechanic. Only registered observations, events and commands appear in a game's manifest.

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

## Optional communication contract

`actor.standard.communication` is its own extension package **0.1.0**. It does
not change Core 1.0 or the existing `actor.standard` motion package 0.1.0/hash.
A game advertises these capabilities only when it implements their server-side
policy:

| Capability | Kind | Generated payload |
|---|---|---|
| `actor.standard.communication.send-message` | Ordered command | `SendMessageCommand` |
| `actor.standard.communication.message-received` | Event | `MessageReceivedEvent` |
| `actor.standard.communication.delivery-result` | Event | `MessageDeliveryResultEvent` |
| `actor.standard.communication.peers` | Observation | `CommunicationPeersObservation` |

The command includes recipient, channel, text, correlation/task IDs, sensitivity,
message TTL and match instance identity. It deliberately has no sender or server
message ID. The game derives the sender from its authenticated connection/owned
actor and allocates the unique message ID and monotonically increasing server
sequence. Match identity rejects messages queued for a previous lobby. A
match-scoped ActorId is not a stable memory profile.

The reference game's channels are `direct` and `station`. Direct messages have
an explicit recipient; station sends use an empty command recipient. The server
filters recipients and each receiving event identifies its actual recipient.
The reference server limits text to 512 UTF-8 bytes and actor/correlation/task/
match/message identifiers to 64 characters. Its channel allowlist contains only
the two names above. Message TTL is 1,000–300,000 ms, with a 60,000 ms Skill
default. Games validate bounds before allocating
reliable RPC work. Protobuf does not enforce length, visibility, rate or permission.

Four outcomes are separate:

1. Core `CommandResult.Accepted`: the local adapter accepted the typed request.
2. `MessageDeliveryResultEvent.accepted_by_server` with stage `SERVER_ACCEPTED`:
   the authoritative server accepted and routed it.
3. Stage `CLIENT_RECEIVED`: actual receiving-client acknowledgement reached
   the server. Station-wide receipt requires all intended recipients to acknowledge.
4. A Runtime/Skill/Brain consumed the message: local cognitive state, never
   asserted by the delivery stage.

Incoming content stays untrusted external game data. `sensitive` and expiry
support session-only handling; private text and diagnostic authorization must
not enter durable memory or public reports by default. Targeted delivery must
already happen on the game network: Python filtering cannot repair a global
broadcast leak. This is gameplay visibility, not transport encryption.

C# registration uses the existing typed registry and optional generated helper:

```csharp
public ExtensionPackage Package => ActorCommunicationExtensionPackage.Create(required: false);

public void Register(IActorExtensionRegistrar scope)
{
    scope.RegisterCommand<SendMessageCommand>(
        "actor.standard.communication.send-message", "Send through the game server",
        (_, command) => SendThroughAuthorizedGameServer(command), CommandDelivery.Ordered);
    scope.RegisterEvent<MessageReceivedEvent>(
        "actor.standard.communication.message-received", "Authorized incoming message");
}
```

These types are in `Actor.Protocol.Standard.Communication.V1`; generated files
are under SDK `Runtime/Protocol/Communication/Generated`. Python bindings are
`actor.standard.communication.v1.communication_pb2`; descriptor and manifest
resources are `actor.communication.metadata/communication.v1.{pb,json}`. No
manual `Any` envelope or type URL is needed. Plugins use the same generic
extension registry as third-party packages. Games without these capabilities
work without communication Skills; unknown optional packages remain opaque.

## Game-specific calibration contract

Arena package **0.3.0** adds `calibration.proto` without changing existing room
or StationSignal field numbers. `CalibrationTaskStateObservation` contains the
controlled actor's private assignment and authoritative state, empty when
unassigned. `CalibrationProgressObservation` is separate public progress with
no participant assignment or authorization code. Assignment and task-changed
events go only to assigned participants.

`StartCalibrationCommand`, `InspectDiagnosticCommand`,
`SubmitCalibrationCommand` and `CancelCalibrationCommand` carry an idempotency
correlation and match ID. The server derives the caller and checks distinct
participants, role, terminal proximity, state, expiry, attempts and authorization.
`CalibrationResultEvent` gives the requester a typed outcome with action/status/
reason; local command acceptance alone never establishes completion.

Only `DiagnosticResultEvent`, targeted to the requesting Coordinator, contains
the short-lived code. Public progress and task state schemas have no code field.
Forwarding it to the Technician requires an explicit sensitive direct message
through the game server. Schema separation is one guard; recipient filtering
and lifecycle tests establish actual visibility.

> ACTOR does not implement universal game logic. It provides typed and extensible
> communication, observation, action and cognitive orchestration interfaces. A
> game's authoritative server defines visibility, permissions and gameplay outcomes.

## Versioning and generated sources

Core protocol **1.0**, Standard motion **0.1.0**, Standard communication **0.1.0**, Arena package **0.3.0**, game **0.6.0** and multiplayer admission protocol **3** are independent versions. Never reuse Protobuf field numbers. Reserve removed numbers/names; prefer additive changes. A breaking schema changes its package/type identity (for example, `v2`). Exact schema hash validation is a package integrity/compatibility check, not a claim that arbitrary schema changes are semantically compatible.

Generated C#/Python files, descriptors and metadata are version controlled. Unity users do not need protoc. `scripts/generate-proto.sh --check` regenerates in a temporary output tree and compares artifacts; CI fails on stale generated sources. [Tooling and schema rules](../proto/README.md), [Unity SDK API](../packages/com.actor.sdk/README.md), [Python Runtime](python-runtime.md), [custom extensions](extensions.md), [actual gRPC spike](grpc-spike/README.md).

`python scripts/verify-runtime-package.py` builds an actual wheel from a fixed
source/test snapshot, creates a fresh environment outside the checkout, installs
base and test dependencies, and checks resources, entry points and the complete
Runtime suite. Codex/Jev extras and model authentication are absent. Its bounded
summary is `build/milestone5-package.json`; this verifies packaging and local
tests, not live multiplayer or model behavior.

## Current scope

Runtime 0.4.0 composes independently scoped sessions, communication Skills and game-specific cooperative actions above event routing, bounded context/memory and unchanged Core. Arena owns calibration permissions and outcomes; Standard communication owns typed addressing/delivery interfaces. Optional Jev remains a provider-neutral classifier. Generic navigation and package auto-download remain future layers.
