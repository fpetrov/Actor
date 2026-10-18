# Local episodic memory

`actor_runtime.memory.MemoryStore` retains past evidence in bounded SQLite.
It is independent of Brain providers and is not another WorldState. Latest
observations stay in WorldState; ordinary Transform/Motion frames are not
automatically written to memory. EventRouter remembers selected observed events;
ObjectiveRunner remembers SkillResults and objective outcomes.

## Scope and lifetime

Default memory is an in-memory SQLite database, isolated to the current session.
Its synthetic profile is `session:<session_id>`; a network ActorId is never used
as a persistent agent identity. Closing Runtime discards this database.

Persistence requires **both** an explicit local database path and a stable
profile. Entries and retrieval are separated by profile and game ID. A new
network actor/session can retrieve the same explicit profile's prior game
evidence, with the original source session/actor still recorded. A different
profile or game cannot retrieve it. Profiles are local identifiers, not an
authentication system; users with access to the SQLite file can inspect it.

```bash
actor-runtime agent --brain codex --classifier rules \
  --agent-profile alice --memory-database ./local/alice.sqlite3 \
  --objective "Recall the most recent important signal and report what happened."

actor-runtime memory stats --agent-profile alice --game-id agents-arena \
  --memory-database ./local/alice.sqlite3
actor-runtime memory clear --agent-profile alice --game-id agents-arena \
  --memory-database ./local/alice.sqlite3
```

Use the actual negotiated game ID. `clear` affects only that game/profile, not
other agents sharing the file. It does not remove the database file. The file
is created with owner-only permissions; existing file permissions are unchanged.
No memory content is sent to a model unless selected into an explicitly chosen
Brain provider's external context.

## Evidence and deduplication

`MemoryEntry` records ID, profile/game/session/actor, evidence kind,
`source_event_id`, capability/type and extension identity, bounded summary and
structured content, importance, tags, creation time and expiry. Evidence kinds
are `observed_event`, `skill_result`, `objective_result` and explicitly recorded
`observed_fact`. Records describe what the agent received or did, not hidden
server state or inferred beliefs.

Event idempotency uses a trusted installed rule's `hints.semantic_id` plus the
capability when available. This prevents repeated classification or replay over
a new bridge session from recording a game-native unique signal twice. Rules
must provide an identity unique within the game, such as a server-issued UUID;
a match-local counter alone is unsuitable for persistent deduplication. The
original wire event ID/session/sequence/revision remain evidence in the content.
Without such a rule, the fallback uses wire session/sequence identity; Core
cannot infer whether distinct unknown messages describe the same logical event.
Duplicate writes do not extend the original TTL or change its observed content.

Skill-result identity includes the session, objective and structured result;
objective-result identity includes session and objective ID. This lets final
cleanup retry persistence without duplicating records.
Only an objective outcome supports reconciliation: if terminal cancellation wins
while its provisional result is being persisted, Runner updates that same record
to the final result. Original creation time and TTL stay unchanged. Observed
events and Skill evidence remain immutable on replay.

Opaque optional event types retain identity/opaque metadata and never store
their binary bytes. Oversized content becomes an explicit omission record with
`content_truncated=true`. Common credential/hidden-reasoning object keys are
excluded defensively; callers must supply only observed evidence and public
results. The store is not a general secret scanner for arbitrary prose. ACTOR
does not pass API credentials, SDK items or hidden reasoning to this API.

## Bounds and retrieval

`MemoryConfig` defaults to 1,000 entries, 4,096 bytes per structured content,
seven-day TTL and at most 6 retrieved entries. Direct MemoryStore construction
defaults to 8 retrieved entries. Summary/identity/tag lengths are also bounded.
Expiry uses wall-clock timestamps so it survives restarts; Skills use their
existing monotonic deadlines independently.

Every write/read prunes expired entries. A new write evicts the least important,
oldest existing evidence as needed. The row bound is **global to the file**,
including all games and profiles, preventing scope changes from growing disk
usage indefinitely. Sharing a file therefore also shares its storage budget.
Use one consistent bound for a shared file, or separate files for independent
budgets.

New databases use incremental auto-vacuum, secure deletion and a rollback
journal. SQLite's page-count limit caps the main file at
`max(256 KiB, 2 * max_entries * (max_payload_bytes + 8192) + 65536)` rounded up to
whole pages. Incremental vacuum reclaims free pages; repeated churn cannot grow
the main file past this bound. SQLite's temporary rollback journal can add a
bounded transaction copy. Existing oversized or incompatible files fail
explicitly rather than silently increasing the configured budget.

Retrieval first filters game/profile and optional capability/type/tag filters.
It ranks objective/current-Skill keyword overlap, importance and recency, with
deterministic tie breaking, then applies the configured retrieval limit. This is
simple lexical scoring, not embeddings or semantic RAG. Retrieved entries retain
their evidence identity and age in ContextBuilder, separate from current state.

## Async API and failure policy

```python
memory = MemoryStore.from_config(config.memory, session)
try:
    await memory.remember_event(routed_event, tags=("observed_signal",))
    await memory.remember_skill_result(skill_result, objective_id=objective.id)
    await memory.remember_objective_result(objective_result)
    past = await memory.retrieve(objective=objective.text, current_skill="my-game.inspect")
    stats = await memory.stats()
finally:
    await memory.close()
```

All SQLite open/check/write/prune/retrieval operations run on one dedicated
worker thread per store. At most 32 operations can be outstanding; overload
returns `memory_busy` rather than growing an unbounded executor queue. Cancelling
an awaiting caller does not pretend an already queued transaction was undone;
it completes in order before close. `close()` is idempotent and drains previously
submitted operations. CLI owns shared store lifetime after Router/Runner cleanup.
The synchronous gRPC ingestion path never awaits SQLite: EventRouter's async
worker is the persistence caller.

Missing database directories are created for the explicit configured path.
Corrupt, foreign, unsupported, inaccessible or oversized databases produce
`ActorError(code="memory_unavailable")` and an explicit degraded state.
**They are never deleted or replaced.** Stats exposes the state and a diagnostic
error code without SQL/content/secrets. Disabled memory returns no entries.
Operations after close fail with `memory_closed`.

Router treats a failed required event write as degraded; Runner reports
`memory_failure` when required persistence cannot complete. This makes a lost
critical history visible. Repair/backup or selecting another file is an explicit
operator action; there is no automatic destructive corruption recovery.

Tests exercise persistence/replay, profile/game isolation, TTL, pruning, lexical
retrieval, corrupt/foreign files, physical file bounds, bounded worker pressure
and cancellation while the disk worker is occupied. No provider or live model
is required.
