# ACTOR SDK milestone validation — 8 October 2026

Game **0.3.0**, admission protocol **3**, ACTOR Core **1.0**, Standard/Agents Arena extensions **0.1.0**. Reference release originally tested: `20261008T010800Z-cec0286`; the subsequent client GUI status fix is included in the final release. [SDK/Runtime Linux CI passed](https://github.com/fpetrov/Actor/actions/runs/37713572503).

## Real end-to-end result

The Python scripted example completed in **16.15s**. It received the game identity, stable controlled ActorId, CapabilityManifest, installed descriptor packages and generated types; sent typed Locomotion2DCommand intents; reached authoritative Engineering and received MainHall→Engineering RoomChanged; paused; returned; received Engineering→MainHall and stopped. No Codex, LLM or direct Runtime-to-Dedicated-Server control was involved.

Client A ran ACTOR mode through local gRPC and the existing NGO input path to VPS. Human Client B joined the same remote room, retained real keyboard control, walked using native WASD to view the corridor/Engineering and observed the bot's replicated roots and both server room revisions. **Observer10/10 passed**, remote displacement5.706 units, peak speed2.6 units/s, no gameplay errors/warnings.

[Runtime report](runtime.json), [Agent client](client.json), [Human client](human-client.json), [frozen Human observer](observer.json). Screenshots: [initial](observer-initial.png), [Engineering](observer-engineering.png), [return](observer-return.png), [complete](observer-complete.png).

## Local performance sanity

- Runtime command/CommandResult round trip: median **5.44ms**, p95 **9.42ms** (includes Unity frame scheduling and handler dispatch).
- Scripted policy sent **310** commands during the16.15s route, with latest-value replacements instead of stale command backlog.
- Observations configured15Hz and commands20Hz. Runtime snapshot contains accumulated live observation counts after route completion; these counts include idle observation traffic.
- Agent/Human average frame rates were **118.5/119.0FPS** on this machine; maximum SDK Pump time **2.113ms**. They are measurements, not guarantees across hardware.
- Runtime used **1.72% of one CPU core** over the reported session interval (including idle time). Traced Python memory: `{'current': 276805, 'peak': 344639}`. Native grpc allocations are outside tracemalloc.
- Managed client heap counters include game/scene startup and diagnostics; they are not claimed as isolated bridge allocations. Queues/observation history are explicitly bounded.

## Human movement regression

**13/13 real VPS checks passed** after SDK/Engineering integration. First predicted movement **22.6ms**, first replicated root movement **340.0ms**, release to locally stopped velocity **96.5ms**, idle discrepancy **0.0048units**, max speed2.6. Walls, sliding, rapid reversal, normalized speed and correction of a client teleport remained correct. [Movement report](human-movement.json), [client performance](human-movement-client.json), [screenshot](human-movement.png).

## Safety and lifecycle

The real Unity [control safety fixture](control-safety.json) passed **8/8**: Hello/manifest, strongly typed Standard+Room observations, unknown capability rejection, malformed Protobuf rejection, NaN rejection, valid accepted movement, authoritative displacement and a fresh stopped Motion observation after0.6s without commands. Movement expires after0.25s independently of policy/network timing. [Client after fixture/shutdown](control-safety-client.json) remained stationary with keyboard disabled and an explicit RuntimeUnavailable status, no errors/warnings.

Native UI checks confirmed ACTOR ignores a held keyboard key ([report](mode-agent-keys.json)); switching to Human enables keyboard, closes bridge and moves normally ([report](mode-human.json)); switching back establishes a different accepted session and disables keyboard ([report](mode-reconnected.json)). An asynchronous GUI Layout/Repaint control-count defect found during shutdown was fixed and the real shutdown/mode tests repeated without errors.

## Automated verification

- **18/18** Control Plane tests.
- **50/50** pure C# SDK/bridge tests: typedAny, manifests/descriptors/fingerprints, command TTL/types/ownedActor, buffering, lifecycle, sequence/session, reconnect/cancel, thread affinity and cleanup.
- **59/59** Python tests, including real loopbackgrpc, plugin discovery, dynamic descriptor-only Mana, opaque optional extensions, required missing/version/hash failures, descriptor ownership, freshness, bounded buffers and20 reconnects.
- **35/35** generated artifacts fresh; fictitious Mana package generation/self-test and path guards passed. [Self-test](extension-selftest.json).
- **10/10** Engineering geometry checks passed: physical doorway bothways, wall/reactor blocking, room volumes/boundary and network membership. [Geometry](geometry.json).
- Both full Mono client and Linux Dedicated Server builds succeeded. True gRPC Editor/standalone compatibility was established separately [before implementation](../grpc-spike/README.md).

## Scope and limits

All original PNGs, offline MainHall scene/character and user-authored collider/light changes are preserved. Owner prediction and existing30Hz input/50Hz physics remain. Core/SDK contain no room/game rules; Arena mechanics and waypoint policy remain game-specific. Local bridge tokens are ephemeral and absent from evidence.

macOS Editor/desktop Mono were exercised. IL2CPP and Windows/Linux clientgRPC were not tested here; installed desktop Unity modules use Mono. This is movement prediction plus authoritative physics, not rollback of all world interactions. No public registry/auto-download, additional transports, LLM, skills, memory or navigation system was added.
