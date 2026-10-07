# Agents Arena networking

Agents Arena uses Unity **6000.6.4f1**, Netcode for GameObjects **2.13.3**, and the Editor's built-in Unity Transport **6.6.0**. NGO 3.x requires a newer Unity release and adds Entities dependencies; this project keeps its current GameObject architecture.

**NGO is the networking implementation of the Agents Arena reference game, not part of the engine-independent ACTOR contract.** `shared/com.actor.contracts` contains admission claims and HMAC code with `noEngineReferences`; the same source is compiled by the Control Plane and imported into Unity as a local package. It references neither Unity nor NGO. There is no Agent Runtime or gRPC integration in this milestone.

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

`PlayerInputSource` implements the existing `IMovementInputSource`. The owner-only `NetworkCharacter` reads that source and sends normalized movement intent at **20 Hz**, using an unreliable ownership-restricted RPC. Sequence numbers reject late packets; non-finite input is rejected and input magnitude is clamped. The server discards excessive submissions and clears stale intent after **0.25 seconds**.

On the server, a network command adapter feeds the unchanged `ActorCharacter → CharacterMotor2D → Rigidbody2D` path. Motor acceleration, deceleration and collisions remain in physics FixedUpdate. `NetworkTransform` explicitly uses server authority, position interpolation and unreliable deltas. `NetworkRigidbody2D` makes client bodies kinematic; the client motor and facade command loop are disabled. Client transforms cannot become simulation state.

The server replicates authoritative velocity, facing, ActorId and PlayerName. `ICharacterMotionState` lets the existing animation view observe either the local motor or replicated motion. It selects the original directional clips and mirroring without transmitting animation frames. Only the owned character enables keyboard input and camera follow. Nameplates are a separate presentation component. `SetLocalInputSource` can later accept agent/replay commands without changing motor, physics, replication or animation logic.

The multiplayer scene is copied from the current Main Hall, preserving the user's collider/light changes. The original offline scene and character prefab remain available.

## Scene and connection lifecycle

The Lobby scene contains one persistent NetworkManager/session, menu view/controller and a separate `LobbyApiClient`. Clients start in the menu, save their player name locally, refresh rooms, create a room or join by its six-character RoomCode. The server loads MainHallMultiplayer additively through NGO scene management and reports readiness only after the scene loads. Clients synchronize that scene before play. Leaving shuts down the client and unloads gameplay while retaining the lobby; disconnect despawns the server-owned player object.

A dedicated build parses match/port/version arguments, reads its per-match secret from the environment, starts its transport, loads gameplay and sends outgoing local HTTP READY and heartbeat callbacks. Headless builds disable lobby UI, keyboard input, camera, lights and character presentation. `FinishMatch()` provides the MATCH_FINISHED callback for future game rules.

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

This is a single-VPS human multiplayer MVP. There is no local prediction/reconciliation, so control latency includes the remote round trip. HTTP without a domain/TLS is the current deployment mode. No accounts, durable match recovery, matchmaking, chat or agent brains are implemented.

Next: define engine-neutral Protobuf observations/actions, add a local gRPC Game Adapter bridge, then build ACTOR Runtime and an `AgentInputSource`. NGO remains inside this reference game; runtime/brain implementations should consume the neutral adapter contract.

References: [NGO changelog](https://docs.unity3d.com/Packages/com.unity.netcode.gameobjects@2.13/changelog/CHANGELOG.html), [Unity Dedicated Server builds](https://docs.unity.com/en-us/engine/6000.5/manual/platform-specific/dedicated-server/build), [ASP.NET Core OpenAPI](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/openapi/aspnetcore-openapi?view=aspnetcore-10.0).
