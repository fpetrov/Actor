# ACTOR

ACTOR will connect interchangeable autonomous agent brains to real-time games. The current milestone is a deployed, server-authoritative human multiplayer reference game.

- **Agents Arena** — Unity 6000.6.4f1 game, Lobby menu and Main Hall; NGO 2.13.3 / Unity Transport 6.6.0.
- **Actor.ControlPlane** — .NET 10 ASP.NET Core Minimal APIs, SQLite lobby metadata, signed admission and process/port lifecycle.
- **Static frontend** — HTML/CSS/vanilla JavaScript served exclusively by nginx.
- **Future Agent Runtime** — engine-neutral observations/actions, memory, local gRPC bridge and interchangeable brains; not implemented yet.

**Website:** http://5.181.134.236/

**Source:** https://github.com/fpetrov/Actor (private)

## Play

Open `Agents Arena/` in Unity and `Assets/Scenes/Lobby.unity`, then press Play. Enter a player name and join a room from the list, enter a six-character RoomCode, or create a room. The macOS client is built at ignored `build/agents-arena-client/AgentsArena.app`. Move with WASD/arrow keys or a gamepad; use Leave room to return to the lobby.

The website creates/lists rooms and copies their codes; gameplay runs in the Unity client. Central client URL/version configuration lives in `Assets/Settings/LobbyClient.asset`. The original offline `MainHall.unity` scene and character prefab remain available, including the user's subsequent scene edits.

## Build and deploy

Git is rooted in `Actor/`; there is no nested Unity repository. Install Git LFS before pulling binary art. Unity Assets, Packages and ProjectSettings are versioned. Build/cache output, SQLite runtime files, logs, environment files and private keys are ignored.

Requirements: Unity 6000.6.4f1 with matching Linux Mono/Dedicated Server modules, .NET 10 SDK, SSH and rsync. Close Unity before CLI builds of the same checkout.

```bash
scripts/build-server.sh
scripts/build-client.sh
scripts/publish-control-plane.sh
scripts/build-web.sh
scripts/deploy-vps.sh <host> <ssh-user> <private-key-path>
```

Backend source/test projects are in `src/` and `tests/`; `Actor.sln` and `global.json` select .NET 10. Scripts use the system SDK, `DOTNET` override, or the locally installed ignored `build/tools/dotnet/dotnet`. The VPS uses self-contained Linux x64 output and does not need .NET SDK/runtime or Unity Editor.

## Architecture

```text
nginx → Minimal API → LobbyManager → IGameServerAllocator → Unity Dedicated Server
                                                 Unity clients ↔ NGO + Transport
```

Client input sends only movement intent. Server `ActorCharacter → CharacterMotor2D → Rigidbody2D` owns acceleration, movement and collisions. Clients interpolate server transforms; animation observes compact replicated motion through `ICharacterMotionState`. Owner-only input and camera remain separate from simulation/presentation. A future agent or replay can replace the input source without rewriting those layers.

`ActorCharacter.CaptureState()` exposes ActorId, position, velocity, room, facing, moving, role and interaction state. Roles/interactions remain placeholders. `shared/com.actor.contracts` supplies engine-independent admission claims/HMAC; it references neither Unity nor NGO. **NGO belongs to Agents Arena, not the future ACTOR SDK contract.**

The six original sheets provide 48 complete character poses. Twelve idle/walk clips preserve the previous directional setup; UpLeft/DownLeft mirror right diagonals, while Left has its own art. All original PNGs remain unchanged. The multiplayer scene preserves Main Hall walls, crystal, camera and the user's light/collider adjustments.

## Verification and limits

18 backend tests passed. Both Unity builds succeeded. Actual remote Editor + Standalone probes passed 9 and 8 checks with no runtime errors/warnings; names, motion, collisions, disconnect/reconnect and local-transform correction were checked. Five real servers used unique UDP ports 7777–7781 and the sixth room received 409. Empty-room cleanup, process crash handling and stale persisted state are covered.

The limit and timeouts are configurable, with production MaxConcurrentGameServers=5. Control Plane serves API only on 127.0.0.1:5080; nginx exposes HTTP 80, and gameplay uses direct UDP. There is no prediction/reconciliation, TLS/domain, account system or durable recovery of active matches. Restarting/deploying the Control Plane ends active matches; metadata persists in SQLite.

See [networking](docs/networking.md), [deployment](docs/deployment.md), [remote validation evidence](docs/multiplayer-validation/README.md), and [previous playable milestone](docs/VALIDATION.md).

## Next milestone

Define engine-neutral Protobuf observation/action contracts, add a local gRPC Game Adapter bridge, then implement ACTOR Runtime and AgentInputSource before connecting Codex SDK or other brains. Keep the game-specific NGO transport behind the adapter boundary.
