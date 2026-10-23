# ACTOR

ACTOR connects interchangeable external agent runtimes to games through typed, versioned capabilities and extension packages. Agents Arena 0.10.0 is available as the published limited test prerelease [v0.10.0-rc.1](https://github.com/fpetrov/Actor/releases/tag/v0.10.0-rc.1) and is deployed to the public test service; complete Broadcast acceptance remains pending. Its Social Deduction game supports people and autonomous agents, with authoritative visibility, hidden affiliations, discussion and voting. Sandbox preserves the station duties, asymmetric investigation, calibration and private communication from earlier milestones.

## What ACTOR is

**ACTOR does not prescribe a game's world model. Games expose capabilities through typed, versioned extensions.** Core handles sessions, discovery, envelopes, ordering and command outcomes. Reusable Standard packages are optional; game-specific concepts belong to game extensions. Agents Arena's rooms are an extension, not an ACTOR Core concept.

ACTOR does not implement universal game logic. It provides typed and extensible communication, observation, action and cognitive orchestration interfaces. A game's authoritative server defines visibility, permissions and gameplay outcomes.

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
- **Python Runtime 0.8.0** — descriptors/plugins, typed Skills, event-driven cognition, bounded provenance and memory, match episodes, and installed `actorctl` onboarding and process ownership.
- **Brains** — deterministic SequenceBrain and optional official `openai-codex==0.162.1`; model decisions occur at Skill boundaries or justified strategic events, with one active turn.
- **Agents Arena 0.10.0** — Unity 6000.6.4f1; retained UI Toolkit player flows, original station/character art, tasks and recipient-scoped kill/death presentation; server physics, permitted observations and responsive owner prediction.
- **Control Plane** — .NET 10 Minimal APIs, SQLite admission/process lifecycle; static website via nginx.

Game 0.10.0, Arena extension 0.7.0, Runtime 0.8.0, ACTOR Core 1.0 and multiplayer admission protocol 4 are independent versions. Both Standard packages remain 0.1.0. Use matching game/admission versions together. Production activation `20261023T005254Z-bdc3131` was verified on 2026-10-23 with Game 0.10.0/admission 4, matching Control Plane/server/web inventories and healthy services. All seven RC1 assets passed anonymous HTTPS download/hash/size verification. Their artifact source is `bdc3131d29efee193a2e1f3d31f0e9640110abd9`, independent of later documentation commits; see the scoped [RC1 publication and deployment receipt](docs/release-validation/deployment/activation-broadcast-rc1.json).

**Website:** http://5.181.134.236/

**Source:** https://github.com/fpetrov/Actor (public)

## Play

For Human play, use the matching macOS [RC1 release](https://github.com/fpetrov/Actor/releases/tag/v0.10.0-rc.1) and follow the [installation instructions](docs/installation.md). The app defaults to an 800×600 window; Python, ACTOR Runtime and model credentials are optional and are not needed for Human play. Downloads use GitHub HTTPS; the current HTTP/UDP gameplay service remains a limited test RC, not a secure production service.

For development, open `Agents Arena/` in Unity and `Assets/Scenes/Lobby.unity`, then press Play. Enter a player name and join a room from the list, enter a six-character RoomCode, or create a room. The macOS client is built at ignored `build/agents-arena-client/AgentsArena.app`. Move with WASD/arrow keys; E opens nearby actions and T opens communications. Select and explicitly confirm a ballot during Voting. Use Return to rooms after a match, or New expedition for a fresh room.

Choose `Sandbox` for the existing investigation/calibration world or `SocialDeduction` for a social match. A room's mode is immutable. Social rules come from validated deployment configuration copied by the Control Plane to its dedicated server; players choose neither hidden assignments nor a scenario seed. See [Social Deduction](docs/social-deduction.md).

The optional [Broadcast operator workflow](docs/broadcast.md) uses a separately admitted native observer, local commentary and protected OBS/audio routing; joining as a player does not start a stream. The [observer motion guide](docs/broadcast-motion.md) describes bounded 30 Hz delivery and presentation checks, with native rendering and performance acceptance explicitly separate from pure tests.

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

`ActorCharacter.CaptureState()` exposes ActorId, position, velocity, room, facing and moving state. Server-owned station duties and investigation state use separate typed Arena observations; they do not extend the movement snapshot. `shared/com.actor.contracts` supplies engine-independent admission claims/HMAC; it references neither Unity nor NGO. **NGO belongs to Agents Arena, not the generic ACTOR SDK contract.**

Social mode adds server-side radius/line-of-sight filtering and recipient-specific views before data reaches the SDK. Hidden actors and bodies are not globally replicated and then concealed only by UI fog. An agent receives its own alignment, duty and tasks; another player's statements remain unverified claims. No Core, Runtime or launcher role oracle supplies hidden game truth.

`MatchEpisodeController` keeps one session, Scheduler, Router and scoped memory across bounded Objectives. Each episode creates a fresh Brain/provider thread. Arena's installed policy interprets phase/life changes and conditional Skills; Core contains no role, meeting or win-condition branches. Death suspends cognition, and only an authoritative terminal result completes participation. [Match lifecycle](docs/match-lifecycle.md) describes ownership, budgets and private working knowledge.

The six original sheets provide 48 complete character poses. Twelve idle/walk clips preserve the previous directional setup; UpLeft/DownLeft mirror right diagonals, while Left has its own art. All original PNGs remain unchanged. The multiplayer scene preserves Main Hall walls, crystal, camera and the user's light/collider adjustments.

## Verification and limits

Historical Milestone 8 Game 0.9.0 evidence includes **302 SDK/bridge C# tests**, **29 Control Plane tests** and **545 Runtime tests**, including a fresh auth-free wheel installation outside the checkout. Five accepted independent M8 Codex starts include two full social cycles. Two strict Human + three-Codex matches join real native input and screenshots to matching authoritative results; one includes a server-recorded Human ballot. Native 800×600 checks cover discussion, voting, death, six-person rosters and a fresh rematch. These are bounded results: the [54-section release validation report](docs/release-validation/README.md) preserves failed attempts and the remaining audiovisual, performance, transport and platform limits. Their acceptance scope remains historical; the separate RC1 publication/deployment receipt does not establish complete Broadcast acceptance, a 30-minute capacity result or a current successful autonomous match.

Historical Milestone 7 Runtime source and its fresh Runtime 0.6.0 wheel passed **491 tests** each. Its four-real-Codex match finished Crew/tasks_completed; a separate real Crew case confirmed body reporting, Discussion and voting. At that milestone, release0.8.0 from89c00dd passed ordinary-client admission, while native Discussion/Voting/death screens were still unverified. See the [Milestone 7 validation report](docs/social-deduction-validation/README.md).

Historical Milestone 6 local authority/protocol coverage included **162 SDK/bridge C# tests** and **52 schema-freshness checks**, including 40 incident-authority cases and 7 incident-protocol cases. Paired cases hold either role's complete evidence view equal, apart from opaque GUIDs, while the other view changes the supported origin. See the [Milestone 6 report](docs/investigation-validation/README.md) and [investigation architecture](docs/investigation.md) for its separate evidence and limits.

Milestone 5 verification passed **125 .NET tests** (107 SDK/bridge + 18 backend), **326 Python tests** (316 Runtime + 10 scripts, final rerun 41.56 seconds) and **50 schema-freshness checks**. The fault helper has 9 additional offline self-tests. Hosted Control Plane and SDK/Runtime CI both passed for functional-code commit `25402be`. All four real remote cooperation scenarios passed strict acceptance: two ScriptedBrains, both mixed Codex/scripted directions, and **two independent live CodexBrains**, with independent Runtimes/memories and matching authoritative completion.

A third Human verified public completion without private access; native Human chat sending/receiving through composition paste and Send, plus text-focus movement suppression, passed. All four actual faults passed: Objective cancellation, Runtime bridge loss, abrupt participant loss and isolated match-server loss. That milestone's client passed **13/13 prediction/control checks**, with a 23.4 ms first presentation sample; this is not native keyboard-to-render latency. Matching macOS/Linux builds from `c289e1a` succeeded; Milestone 5 deployed atomic release `20261019T1804Z-c289e1a` without restarting the Control Plane or interrupting healthy rooms. This is historical deployment evidence. See the [Milestone 5 report and safe evidence](docs/cooperative-validation/README.md) and [main CI runs](https://github.com/fpetrov/Actor/actions?query=branch%3Amain).

Historical Milestone 4 verification passed 18 backend, 68 SDK/bridge and 255 Python tests; its real multiplayer evidence remains in [event validation](docs/event-validation/README.md). Earlier remote Editor/Standalone probes passed 9/8 checks without runtime errors/warnings, covering names, motion, collisions, disconnect/reconnect and local-transform correction. Five real servers used unique UDP ports 7777–7781 and the sixth room received 409. Empty-room cleanup, process crash handling and stale persisted state are covered by the earlier checks and subsequent regressions.

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

This route example and the objective/calibration/investigation examples below use `Sandbox` rooms. Social mode advertises its own permitted capability set and Skills.

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

## Cooperative agents

Create a matching lobby, then launch the two deterministic reference agents:

```bash
python3 scripts/launch-cooperative-agents.py --room ABC123
```

Select independent real Codex providers with
`--coordinator-brain codex --technician-brain codex`. The coordinator obtains a
private diagnostic authorization in Main Hall and communicates it through the
game server; the technician uses it at Engineering's calibration console. Both
finish only after authoritative completion. Each process has its own bridge,
WorldState, Brain/thread and database; the supervisor does not transfer game data.

See [launch and isolation](docs/multi-agent.md),
[communication and delivery](docs/communication.md) and
[cooperative task authority](docs/cooperative-tasks.md).

## Sandbox role-based investigation

With a matching 0.10.0 Sandbox lobby/client for the current source, launch the Engineer and Sheriff:

```bash
python3 scripts/launch-investigation-agents.py --room ABC123
python3 scripts/launch-investigation-agents.py --room ABC123 \
  --engineer-brain codex --sheriff-brain codex \
  --config docs/investigation-config.json --headless-client --exit-on-complete
```

These launchers open clients in 800×600 windows. `--headless-client` runs ordinary
game clients without windows, capped at 60 FPS; a Human client can join separately.
The example config bounds decision context and provider usage and contains no
scenario information. See the [Milestone 6 validation report](docs/investigation-validation/README.md).

The Engineer inspects private cooling symptoms, shares a bounded claim through
the ordinary game-server direct-message path and repairs the equipment. The
Sheriff inspects its own private audit slice, correlates circuit, fault window,
controller context and actual action, then submits exactly three references.
Only authoritative restored cooling plus a validated report resolves the case.
Roles are locked while active; neither actor can perform both branches.

Natural failures follow the trusted server clock. Crew/Engineer maintenance at
the actual panel can affect a public `alpha` or `beta` circuit; a recent action
alone does not establish cause. Both paths expose their alarm at the same
controller-cycle boundary, after its bounded audit interval is complete. Public
state and private evidence contain no answer field. Received findings remain
another actor's claim. See
[duties](docs/roles.md), [evidence](docs/evidence.md),
[world terminals](docs/game-world.md) and [Skills](docs/skills.md).

## Social match participants

Create a `SocialDeduction` room with the server's configured minimum player count, then launch the same provider independently for every participant:

```bash
build/runtime-venv/bin/python scripts/launch-social-agents.py \
  --room ABC123 --players 4 --brain social-scripted \
  --output build/social-match
```

Use `--brain codex` after installing the optional provider to run independent model participants. Everyone receives the same trusted objective and learns its own assignment through the game. The supervisor supplies no roles or actions and checks matching authoritative terminal results. `--players` must match the room's configured minimum; it does not change server rules.

The launcher defaults to ordinary headless clients. `--windowed` selects 800×600 windows for visual checks. Each participant has a separate process, bridge credential/port, database and provider thread; communication goes through the ordinary authoritative game server. A finished Objective ends one episode, not the match. Controlled `social-fixture-*` providers test mechanics and do not establish autonomous model performance. See [Social Deduction](docs/social-deduction.md), [match lifecycle](docs/match-lifecycle.md) and [multi-agent launch](docs/multi-agent.md).
