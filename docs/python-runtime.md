# ACTOR Python Runtime

The Runtime is a local process that handles typed protocol exchange, extension
discovery, a deterministic latest-observation cache and command output. It has
no Unity/NGO knowledge, LLM, navigation system or memory service. Standard and
Agents Arena semantics live in separately registered Python plugin modules.

## Install and run

Python 3.12+ is required. Protobuf and gRPC versions are pinned in
`runtime/pyproject.toml`; generated sources and descriptor artifacts are checked
in. Generation is needed only after changing schemas.

```bash
python3 -m venv build/runtime-venv
build/runtime-venv/bin/python -m pip install -e 'runtime[dev]'
```

Set a fresh `ACTOR_BRIDGE_TOKEN` in the environment of both the Runtime and
Unity client. Do not commit or print it. A development launcher can use
`secrets.token_hex(32)` and pass the result directly in each process environment.
The token authenticates gRPC metadata named `x-actor-bridge-token`.

```bash
build/runtime-venv/bin/actor-runtime serve --port 50051
build/runtime-venv/bin/actor-runtime scripted agents-arena \
  --port 50051 --report build/actor-validation/runtime.json
```

Run either command, not both on the same port. `scripted` also starts the server,
waits for a negotiated Unity session, then executes the installed example. It
remains available after completion so the client can stay connected. Optional
`--exit-on-complete` stops it after the route, returning a nonzero exit status
if the example failed. `--connect-timeout` controls the initial wait (120 s by
default), `--command-timeout-ms` sets command TTL (250 ms), and `--debug` enables
gRPC debug diagnostics. `--measure-allocations` includes current/peak traced
Python memory in the report; it does not account for all native gRPC allocations.
`ACTOR_BRIDGE_PORT` supplies the default port.

The server always binds `127.0.0.1`. It rejects invalid tokens before creating a
session and limits gRPC messages to 1 MiB. A five-second missing-peer deadline
ends an unresponsive stream. A rejected manifest returns a structured Core
`ProtocolError`, including extension ID, required version and schema hash when
applicable. No observations or commands are exchanged through files. `--report`
is an optional diagnostic artifact for tests and developer review.

## Extension registration

`importlib.metadata.entry_points(group="actor.extensions")` discovers installed
factory functions that return `ExtensionPackage`. Each package contains its
manifest, descriptor-set bytes and optional generated message classes. Plugins
are normal Python packaging metadata; Core does not branch on game IDs.

The repository includes these registered plugins:

- `actor_standard`: Transform2D, Motion2D and Locomotion2D.
- `actor_agents_arena`: RoomMembership and RoomChanged; the route example is an
  `actor.examples` entry point, also outside Runtime Core.

The registry verifies exact SHA-256 fingerprints of generated descriptor
artifacts, loads all imports into an isolated descriptor pool, and compares
known generated message/file schemas against their installed descriptors.
Default Protobuf JSON names are normalized for that comparison because Python
generated descriptors can omit these derived names. This does not change the
artifact hash. Missing descriptors and conflicting schemas fail explicitly.
Unknown installed types are resolved through `message_factory.GetMessageClass`;
generated classes remain the preferred result for known schemas.
Capability type URLs are also checked against the declaring extension's own
descriptor set, including its explicit imports. A type known through some
other installed extension cannot be advertised under an unrelated package.

Required missing/version/hash mismatches reject negotiation. Unknown optional
extensions remain opaque and do not prevent connection. Their envelopes retain
type URL and bytes without attempting an incompatible decode. Type URLs are
deterministic: `type.googleapis.com/<fully-qualified-protobuf-message-name>`.

## Developer API

Policies receive a negotiated `RuntimeSession`:

```python
from actor_standard import LOCOMOTION, Locomotion2DCommand, Transform2DObservation

position = session.world.latest(Transform2DObservation)
session.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=1))
await session.world.wait_until(lambda world: world.revision > 10, timeout=2)
```

The session resolves capability metadata, checks the payload type, packs `Any`
and assigns command IDs without manual wire serialization. Commands can only
target the actor controlled by that connection. TTL is explicit; commands carry
`issued_at_unix_ms`, and the writer discards expired unsent values. Unity checks
expiration again on dispatch, while the input source independently zeros stale
movement on stream failure or missing updates.

The cache keys observations by entity and capability, returns typed copies, and
keeps at most 512 entries and 64 recent events. A new connection has a new cache.
If multiple capabilities use one message type for the same entity, pass
`capability_id` explicitly to `world.latest(...)`; ambiguous lookups fail clearly.
`world.latest_entry(...)` returns the typed value plus its sequence, revision
and monotonic `received_at` timestamp. Freshness belongs to each observation;
empty frames or updates to other capabilities do not refresh an old value.
Stream session IDs and monotonically increasing sequences reject cross-session
messages and duplicate/old messages. Older observation revisions are ignored.

Latest-delivery commands replace previous values for the same capability/actor;
there is no reliable locomotion backlog. Buffers hold at most 32 distinct latest
targets and 32 reliable controls. Pending results and result history are each
bounded at 64. Wire sequence numbers are assigned by the actual writer, so
coalescing does not create out-of-order output. Command results mean adapter
acceptance, not proof of server physics execution.

## Scripted reference example

The Agents Arena example requires Standard and game-extension capabilities. It
uses a short explicit waypoint route through the reference game's doorway,
receives both authoritative RoomMembership updates and RoomChanged events,
pauses in Engineering, returns to Main Hall and zeros movement. This is a
policy example with known geometry, not a generic pathfinding layer.

Commands refresh at 20 Hz, with lower intent near a waypoint. Missing/stale or
nonfinite Transform or Motion observations fail safely and send zero intent.
Arrival and the final stop require four distinct fresh samples, including
Motion confirming zero velocity; repeating one cached value is insufficient.
Session exit,
policy failure and normal completion clear movement through the game bridge.

## Verification

```bash
cd runtime
../build/runtime-venv/bin/python -m pytest -q
```

Tests cover installed and synthetic entry-point discovery, known/dynamic `Any`,
required and optional extension negotiation, duplicate/version/hash/schema
errors, bounded caches and command coalescing, ordering, TTL, structured malformed
messages, actual loopback gRPC exchange, authentication, cancellation/reconnect,
graceful shutdown and a deterministic scripted route. Real Unity multiplayer
proof is documented separately from this policy fixture.
Regression tests include twenty sequential reconnects without a waiting policy,
shutdown/error delivery when reliable buffers are full, per-observation
freshness, distinct arrival samples and foreign extension type rejection.

The optional report includes session counts, observations, commands/events,
command-result round-trip samples and Python CPU usage. Default logs describe
connections, capabilities, events and aggregate command counts; individual
transform frames are not logged.
