# Agents Arena networking

Agents Arena uses Unity **6000.6.4f1**, Netcode for GameObjects **2.13.3**, and the Editor's built-in Unity Transport **6.6.0**. NGO 3.x requires a newer Unity release and adds Entities dependencies; this project keeps its current GameObject architecture.

**NGO is the networking implementation of the Agents Arena reference game, not part of the engine-independent ACTOR contract.** `shared/com.actor.contracts` contains admission claims and HMAC code with `noEngineReferences`; the same source is compiled by the Control Plane and imported into Unity as a local package. It references neither Unity nor NGO. The generic ACTOR SDK and Python Runtime now connect through a separate local gRPC adapter; NGO remains exclusively the reference game networking implementation.

## Control flow

```text
Browser / Unity lobby
          |
     nginx :80
          |
 /api/v1 Minimal APIs
          |
     LobbyManager
          |
 IGameServerAllocator
          |
 LocalProcessGameServerAllocator
          |
 Linux Unity Dedicated Server : UDP 7777–7781
          |
   NGO + Unity Transport
          |
  Unity human-controlled clients
```

The Control Plane orchestrates rooms and processes. Unity alone simulates game physics.

## Input, simulation and presentation

`PlayerInputSource` implements the existing `IMovementInputSource`. The owner-only `NetworkCharacter` reads it every rendered frame. Press, release and direction changes send immediately; unchanged intent refreshes at up to **30 Hz**, using an unreliable ownership-restricted RPC. Sequence numbers reject late packets; non-finite input is rejected and magnitude is clamped. The server discards excessive submissions (120/s ceiling) and clears stale intent after **0.25 seconds**.

The server runs `CharacterMotor2D → Rigidbody2D` in FixedUpdate, with the existing speed, acceleration, deceleration and colliders. `NetworkTransform` retains server authority and interpolation for replicated roots. `NetworkRigidbody2D` keeps client bodies kinematic; client motors remain disabled. No client position or velocity is accepted as simulation input.

The owned player's **Visual child** uses `OwnerMovementPrediction`, so input moves the sprite immediately without waiting for a round trip. Animation, nameplate and camera observe this presentation. The network root stays server controlled. Prediction shares the motor velocity calculation and sweeps the authored capsule against static room geometry, including wall sliding. Other players use normal NGO interpolation; player-to-player physics remains authoritative and can require correction.

At up to 30 Hz the server sends only the owner a post-physics snapshot: position, velocity, facing, monotonic snapshot ID, the actually simulated input sequence and its simulated duration. The client discards older snapshots, starts from the authoritative pose and replays the remaining input history. Small visual corrections decay over roughly 0.1 seconds and are swept against walls; corrections over 0.75 units snap. Replay retains at most the newest 0.5 seconds with 128 history entries. After 0.5 seconds without snapshots prediction stops, and recovery resets to authoritative state. This is scoped movement prediction, not rollback of all world physics.

The server also replicates velocity, facing, ActorId and PlayerName. `ICharacterMotionState` lets animation observe the motor, predicted owner motion or replicated remote motion. It selects the original clips/mirroring without transmitting animation frames. Only the owner enables keyboard input and camera follow. `SetLocalInputSource` can later accept agent/replay commands without changing physics or presentation. `ActorCharacter.CaptureState()` retains the server-replicated root position.

Movement snapshots were introduced in admission protocol2/game0.2.1. The current cooperative reference game uses **admission protocol3/game0.6.0**, independently of **ACTOR Core protocol1.0**. Old clients fail version validation before connecting to an incompatible game server.

The multiplayer scene is copied from the current Main Hall, preserving the user's collider/light changes. Its southern end cap opens into Engineering, assembled from existing map art and separate floor colliders. Server-written NetworkRoomMembership resolves physical capsule centers against stable RoomVolume2D IDs and replicates transitions. The original offline scene and character prefab remain available.

## Scene and connection lifecycle

The Lobby scene contains one persistent NetworkManager/session, menu view/controller and a separate `LobbyApiClient`. Clients start in the menu, save their player name locally, refresh rooms, create a room or join by its six-character RoomCode. The server loads MainHallMultiplayer additively through NGO scene management and reports readiness only after the scene loads. Clients synchronize that scene before play. Leaving shuts down the client and unloads gameplay while retaining the lobby; disconnect despawns the server-owned player object.

A dedicated build parses match/port/version arguments, reads its per-match secret from the environment, starts its transport, loads gameplay and sends outgoing local HTTP READY and heartbeat callbacks. Headless builds disable lobby UI, keyboard input, camera, lights and character presentation. `FinishMatch()` provides the MATCH_FINISHED callback for future game rules.

## Authoritative communication and cooperation

The player prefab adds a separate `NetworkCooperation` behaviour. The ordinary
owned reliable RPC path verifies approved connection identity and that
connection's exact player NetworkObject. Sender identity comes from its server
ActorId; commands supply no trusted sender identity. The joined match GUID
fences every communication/task request against delayed traffic from a previous
lobby. Client and server builds must use the same regenerated prefab.

`CooperativeSession` owns a server-only bounded `CooperativeAuthority`. Direct
message content is sent through **SendTo.Owner** on the selected recipient's
player object. The sender receives a separate delivery-result payload. A third
client's remote player components never receive the private bytes. Station chat
is explicitly routed to each permitted connected recipient; it is separate from
the existing server-generated station-signal broadcast. These are game visibility
rules, not a claim of encrypted UDP transport.

The reference policy bounds text to512 UTF-8 bytes, requests to2048 bytes,
pending client receipts to64, and replay records to512 with expiration. Ordered
messages get server UUIDs and sequence numbers. Per-owner ingress, authoritative
sender/global budgets and a separate acknowledgement budget limit reliable RPC
traffic. Correlation replay includes the Protobuf type identity and payload;
identical sends return the latest receipt without delivering the content twice.

Local bridge Accepted means the request was queued locally. ServerAccepted is
the authoritative game policy outcome. ClientReceived requires an actual owner
client acknowledgement (all selected recipients for station chat). None means
the recipient's Brain read the message. There is no durable offline messaging:
a disconnected recipient is rejected, and a pending acknowledgement times out
explicitly. Messages arriving before Runtime bridge acceptance are not promised
as a new-session replay; cooperative launch fixtures wait for both Runtime
sessions before starting their objectives.

Calibration has distinct Coordinator and Technician actors. Its challenge is a
server-generated128-bit value, valid for300seconds, held only in server memory
and a targeted sensitive diagnostic result for the Coordinator. Public progress
omits participant identities and authorization; private assignment state is
sent only to its participants. A recognized authorization transfer is restricted
to a direct message from Coordinator to Technician, labelled sensitive by the
server and bounded by task expiry. Declared sensitive messages also require the
direct channel. Successful submission requires the Technician's actual message
acknowledgement, correct role/code, current task/match, Engineering membership,
live terminal and physical range. Three attempts and a two-second attempt
cooldown limit guessing.

Participant NGO disconnect, explicit cancellation, expiry and the owned
Runtime-unavailable notification cancel a nonterminal task. Completed tasks
remain completed when a launcher later disconnects. Client caches clear on
despawn/lobby change; bridge events remain within their negotiated session.
Server evidence logs contain IDs, outcomes and counters, with no message text or
authorization. Human/Agent diagnostics expose safe counts/task progress only.
See [communication](communication.md), [cooperative tasks](cooperative-tasks.md)
and [multi-agent launch](multi-agent.md).

## Admission

Tickets use `base64url(UTF-8 JSON).base64url(HMAC-SHA256(encoded payload))` with a random **32-byte per-match key**. Claims are `lobbyId`, `playerName`, `gameVersion`, `protocolVersion`, `expiresAt` (Unix seconds) and `nonce` (GUID in N format). Default lifetime is **45 seconds**.

The join API validates room status/capacity, trimmed player names of 2–24 characters and exact game/protocol versions. Pending tickets reserve capacity until expiry or a heartbeat reports their connected nonces. The NGO approval callback verifies HMAC, expiration, lobby and versions, rejects replayed nonces and independently enforces the player limit. Player names replicate in FixedString128Bytes, including valid Unicode names. Actor IDs are independent of names/GameObjects.

Signing keys never enter command-line arguments, SQLite, public DTOs or logs. The same random key protects the match's internal callbacks; nginx rejects `/internal/`, and Kestrel listens only on loopback.

## APIs

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/lobbies` | Active room list (`{lobbies: [...]}`) |
| POST | `/api/v1/lobbies` | Create room; 201 and Starting metadata |
| GET | `/api/v1/lobbies/{roomCode}` | Room metadata |
| POST | `/api/v1/lobbies/{roomCode}/join` | Host, UDP port, short-lived join ticket |
| GET | `/api/v1/game/version` | Game/protocol versions and public capacity defaults |
| GET | `/api/openapi/v1.json` | Public OpenAPI contract |
| GET | `/health` | Configuration/storage/process health |

Internal POST paths are `/internal/game-servers/{lobbyId}/ready`, `/heartbeat`, `/finished`, authenticated with `X-Actor-Match-Secret`. Heartbeats report player count, server state and connected ticket nonces. Public DTOs exclude PIDs, signing keys and filesystem paths. Structured errors use `{error: {code, message}}`; the sixth active room receives 409 `server_capacity_reached`. Create and join are rate limited by IP (12 and 60 per minute).

## Match lifecycle and persistence

States: Starting → Ready → Running; an empty previously occupied room returns to Ready, then Stopping → Finished. Startup failure, missing heartbeat or process exit produces Failed. Configuration controls the five-server limit, UDP range, capacities and all timeouts. Port reservations and room creation are concurrency-safe. Ports release only after process termination.

SQLite persists room metadata, status, port, PID/start time and timestamps. Runtime Process objects and keys stay in memory. Startup checks persisted active processes and marks stale rooms Failed; a verified leftover process is reaped. Active matches are deliberately ended on Control Plane restart because signing keys are not persisted. systemd also kills the service's child process group. Reaped PID checks include process start time and match arguments to avoid terminating an unrelated reused PID.

Default timeouts: startup 45s, never-joined empty room 300s, empty grace after players leave 60s, heartbeat 20s. Full capacity is still healthy.

## Current limits and next seam

This is a single-VPS reference game. Owner movement is predicted locally; remote players and authoritative interactions still depend on network latency. HTTP without a domain/TLS is the current deployment mode. Accounts, durable match recovery and matchmaking remain outside scope. Communication is bounded/live and task assignments are match-scoped.

ACTOR provides engine-neutral typed capabilities, a local gRPC bridge, Runtime Skills, independent Brains, events and bounded context/memory. AgentsArenaGameAdapter exports permitted messages, private task state and coherent raw owner physics snapshots; AgentInputSource feeds the same normalized movement path. Game authority/visibility stays in the adapter/server boundary. See [ACTOR protocol](actor-protocol.md) and [milestone evidence](actor-validation/README.md).

References: [NGO changelog](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.13/changelog/CHANGELOG.html), [Unity Dedicated Server builds](https://docs.unity.com/en-us/engine/6000.5/manual/platform-specific/dedicated-server/build), [ASP.NET Core OpenAPI](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/openapi/aspnetcore-openapi?view=aspnetcore-10.0).
