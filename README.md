# ACTOR

ACTOR connects interchangeable external agent runtimes to games through typed, versioned capabilities and extension packages. Milestone4 adds event-driven replanning, relevant bounded contexts and local episodic memory to the working typed Skill/Brain loop and deployed multiplayer reference game.

## What ACTOR is

**ACTOR does not prescribe a game's world model. Games expose capabilities through typed, versioned extensions.** Core handles sessions, discovery, envelopes, ordering and command outcomes. Reusable Standard packages are optional; game-specific concepts belong to game extensions. Agents Arena's rooms are an extension, not an ACTOR Core concept.

```text
                         USER OBJECTIVE
                               │
                               ▼
                        Brain / Codex
                               ▲
                               │
                         Context Builder
                               ▲
                   ┌───────────┴──────────┐
                   │                      │
               Event Router           SQLite Memory
                   ▲                      ▲
                   └──── WorldState ──────┘
                               ▲
                        local gRPC bridge
                               ▲
                    Unity SDK / Game Adapter
                               ↕
                    NGO / Dedicated Server

Action pipeline: Brain → typed Skill → cleanup/ownership → Game
```

- **ACTOR SDK** — local UPM `packages/com.actor.sdk`, engine-independent Core/Standard protocol and typed registration.
- **gRPC transport** — local UPM `com.actor.transport.grpc`, Grpc.Net.Client + native YetAnotherHttpHandler.
- **Python Runtime 0.3.0** — descriptors/plugins, state cache, typed Skills, event routing, bounded context, memory and ObjectiveRunner.
- **Brains** — deterministic SequenceBrain and optional official `openai-codex==0.162.1`; model decisions occur at Skill boundaries or justified strategic events, with one active turn.
- **Agents Arena 0.5.0** — Unity 6000.6.4f1, a common atlas for Main Hall ↔ Engineering, tested passages, authoritative station-signal console, server physics/rooms and responsive owner prediction.
- **Control Plane** — .NET 10 Minimal APIs, SQLite admission/process lifecycle; static website via nginx.

**Website:** http://5.181.134.236/

**Source:** https://github.com/fpetrov/Actor (public)

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

Client input sends only movement intent. Server `ActorCharacter → CharacterMotor2D → Rigidbody2D` owns acceleration, movement and collisions. Other players interpolate server transforms; the owned player predicts its presentation and reconciles server snapshots. Animation observes motion through `ICharacterMotionState`. Owner-only input and camera remain separate from simulation/presentation. Human keyboard and ACTOR AgentInputSource share that input path without duplicating movement logic.

`ActorCharacter.CaptureState()` exposes ActorId, position, velocity, room, facing, moving, role and interaction state. Roles/interactions remain placeholders. `shared/com.actor.contracts` supplies engine-independent admission claims/HMAC; it references neither Unity nor NGO. **NGO belongs to Agents Arena, not the generic ACTOR SDK contract.**

The six original sheets provide 48 complete character poses. Twelve idle/walk clips preserve the previous directional setup; UpLeft/DownLeft mirror right diagonals, while Left has its own art. All original PNGs remain unchanged. The multiplayer scene preserves Main Hall walls, crystal, camera and the user's light/collider adjustments.

## Verification and limits

Current verification passed18 backend,68 SDK/bridge and255 Python tests. Milestone4 results and real multiplayer evidence are in [event validation](docs/event-validation/README.md). Generated schema freshness and custom extension self-test passed. Both Unity builds succeeded. Actual remote Editor + Standalone probes passed 9 and 8 checks with no runtime errors/warnings; names, motion, collisions, disconnect/reconnect and local-transform correction were checked. Five real servers used unique UDP ports 7777–7781 and the sixth room received 409. Empty-room cleanup, process crash handling and stale persisted state are covered.

The limit and timeouts are configurable, with production MaxConcurrentGameServers=5. Control Plane serves API only on 127.0.0.1:5080; nginx exposes HTTP 80, and gameplay uses direct UDP. There is no TLS/domain, account system or durable recovery of active matches. Restarting/deploying the Control Plane ends active matches; metadata persists in SQLite.

The typed scripted bot and the new Brain→Skill loop completed MainHall→Engineering→MainHall through real VPS lobbies. Codex chose Navigate→Wait→Navigate→Finish using the dynamic catalog, with no model calls during Skills. See [cognitive validation](docs/cognitive-validation/README.md), [previous ACTOR validation](docs/actor-validation/README.md), [networking](docs/networking.md), [deployment](docs/deployment.md), [previous remote evidence](docs/multiplayer-validation/README.md) and [playable milestone](docs/VALIDATION.md).

## Run the scripted player

Install Python 3.12+ dependencies once, create a room on the website/Unity lobby, and launch the example:

```bash
python3 -m venv build/runtime-venv
build/runtime-venv/bin/python -m pip install -e 'runtime[dev]'
python3 scripts/launch-scripted-agent.py --room ROOMCODE
```

The helper generates an ephemeral bridge credential, starts the local Runtime and launches an ACTOR-controlled Unity client. A separate Human client can join the same room and observe it. Client UI switches Human ↔ ACTOR Runtime; an absent Runtime produces a clear status and stationary agent input. Runtime configuration is local and the bridge is not deployed to the VPS.

Developers: [protocol/architecture](docs/actor-protocol.md), [extension tutorial](docs/extensions.md), [Python CLI and plugins](docs/python-runtime.md), [Unity SDK](packages/com.actor.sdk/README.md), [gRPC compatibility spike](docs/grpc-spike/README.md). Generated artifacts are committed; validate with `scripts/generate-proto.sh --check`.

## Run a Brain-driven objective

Install the optional provider once, create a room, then select the Brain:

```bash
build/runtime-venv/bin/python -m pip install -e 'runtime[dev,codex]'
build/runtime-venv/bin/actor-runtime codex status
python3 scripts/launch-agent.py --room ABC123 --brain codex \
  --objective "Go to Engineering, wait there for 2 seconds, then return to Main Hall and finish."
```

Use `--brain scripted` for the fixed deterministic demo through the same Skill Engine. The old scripted player remains available. **Codex does not drive frames or send Unity commands directly.** It chooses a typed Skill; Runtime validates, executes and cleans it up before the next decision. Existing ChatGPT/Codex sign-in is reused; `actor-runtime codex login` provides official sign-in if needed.

Developer guides: [Skills](docs/skills.md), [Brains/Objectives](docs/brains.md), [Codex provider and authority boundary](docs/codex.md).

## Event-driven objective

```bash
python3 scripts/launch-agent.py --room ABC123 --brain codex --classifier rules \
  --memory sqlite --agent-profile alice --memory-database build/alice.sqlite3 \
  --objective "Go to Engineering and wait there for 30 seconds. Ignore routine station notifications, but if an urgent recall signal is received, return immediately to Main Hall and finish."
```

A Human player near the Main Hall Signal Console presses E, then chooses Routine
Notice or Urgent Recall. The server validates the interaction. Routine notices
leave the Skill running; an urgent recall safely preempts it and asks Codex to
replan. Objective cancellation remains a separate terminal operation. Without an
explicit database/profile, memory is session-local. Optional Jev is enabled only
with `--classifier jev` and the `runtime[jev]` extra.

Developer guides: [events/plugins](docs/event-routing.md),
[Context Builder](docs/context-builder.md), [memory](docs/memory.md),
[classifiers](docs/classifiers.md), [Signal Console](docs/station-signals.md).

## Next milestone

Add useful game-native interaction capabilities and typed Skills, building on
bounded event-driven cognition. Social memory, beliefs and richer task systems
remain separate layers; game semantics stay in their extensions.
