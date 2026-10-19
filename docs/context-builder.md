# Decision context

`actor_runtime.snapshots.build_decision_context` selects a bounded view of the
existing session `WorldState`. It neither owns another cache nor changes the
observations/events it reads. Skills still validate their own authoritative
observations and freshness before acting.

The ObjectiveRunner captures context at one decision point after foreground
Skill cleanup. It supplies the decision reason, cognitive revision, explicit
trigger events from EventRouter and retrieved past evidence from MemoryStore.
Position updates alone do not create a decision or change cognitive revision.

## Authority

`DecisionContext.objective` is the user's instruction and `available_skills` is
installed Runtime metadata. `external_state()` and `external_json()` exclude
both. They include game metadata, actor identity, observations, recent events,
trigger events, previous SkillResult, current Skill identity, validation feedback
and memories. Codex receives this projection as `ExternalMessage`.

A game message or remembered message saying “ignore the objective” remains
external data. Memories include their evidence kind, source event, source
session and age; they are historical evidence, never newly observed world state.
Classifier assessment is context for reasoning, not authority to issue commands.

## Selection

The builder uses Protobuf reflection and canonical type URLs, including dynamic
extension types. It contains no room, signal or inventory types.

- Observations needed by the current/previous Skill's installed capability
  declarations come first. Other selection considers freshness, objective/Skill
  keyword overlap and the controlled actor, with deterministic tie breaking.
- Routed events prefer assessed urgency and relevance, then objective overlap
  and recency. Their extension identity, assessment and aggregation count remain
  visible. Without routing, the previous event cursor and latest-event ordering
  preserve the earlier API behavior.
- Installed optional event rules may provide a bounded `context_priority`
  function. Communication uses this to prefer expected sender, task, channel and
  correlation identifiers ahead of unrelated later messages. Skills explicitly
  select safe input metadata through `SkillDefinition.context_fields`; message
  text and authorization codes are excluded from that metadata. The previous
  working Skill result remains available to the authorized Brain separately.
- Trigger events have a separate collection. They survive the ordinary event
  count limit, decision cursor, historical ring eviction and duplicate inputs.
  A trigger is not repeated in recent events.
- Retrieved memories preserve MemoryStore ranking; the builder limits their
  number and payload and excludes expired entries.

Each payload retains capability/entity/type identity, sequence, revision and its
own receipt age. `stale=true` labels an older observation; unrelated fresh frames
cannot refresh it. Stale nonrequired data ranks below fresh data. Unknown optional
extensions expose `opaque=true`, identity and age without exporting binary bytes.

Session-only/sensitive event values stay on this external authority surface.
They never become developer instructions or the user's Objective. Explicit
event expiry produces `expired=true` with no payload value. An expired private
previous Skill result similarly retains its identity/status with an expired
output marker. Secret redaction remains active for public diagnostics after
expiry. Reports serialize safe projections; they do not serialize this raw
working context or the private provider conversation.

## Budgets

The centralized `ContextConfig` used by the cognitive CLI currently defaults to:

| Setting | Default |
|---|---:|
| `max_external_context_bytes` | 32,768 UTF-8 bytes |
| `max_observations` | 24 |
| `max_events` | 12 recent events |
| `max_memory_items` | 6 |
| `max_event_payload_bytes` | 4,096 |
| `stale_observation_seconds` | 3 seconds |

Direct callers retain the previous builder defaults: 64 observations, 32 recent
events, 8 KiB per payload and 64 KiB external JSON, plus 8 memory items. The
following optional arguments extend the existing signature:

```python
context = build_decision_context(
    session, objective, catalog,
    trigger_events=router.take_trigger_events(),
    routed_events=router.context_events(),
    memories=await memory.retrieve(objective=objective.text),
    decision_reason="critical_event",
    cognitive_revision=router.cognitive_revision,
    current_skill="my-game.inspect",
    max_external_bytes=32768,
    max_memories=6,
    max_event_payload_bytes=4096,
    stale_after_seconds=3,
)
```

`decision_reason` also describes `objective_started`, `skill_succeeded`,
`skill_failed` and other Runtime decision points. The trigger records carry the
actual event IDs and reflected payloads explaining an event-driven decision.

Large ordinary payloads become explicitly truncated metadata. A large trigger
retains a deterministic bounded preview of actual structured data, prioritizing
compact fields before long strings; `truncated=true` identifies the loss. Unknown
opaque payloads retain metadata only because Runtime cannot interpret them.

Under total byte pressure the builder removes less relevant recent events,
then low-ranked memories, then observation values/records in selection order.
The previous SkillResult retains its status/reason even when a large output
must be omitted. It never quietly removes the only trigger payload to make room
for unrelated observations. If mandatory metadata, trigger previews and the
previous result cannot fit, it raises an explicit budget error. A burst of many
individually bounded critical triggers can require this fail-safe outcome;
the Router's bounded aggregation policy is separate from snapshot packing.

`observations_omitted`, `events_omitted`, `memories_omitted` and each record's
`truncated` flag expose selection loss. `external_bytes` measures the exact UTF-8
JSON size. `estimated_tokens` is only `ceil(bytes / 4)`, a heuristic; SDK usage is
the source for measured provider tokens. A bounded snapshot does not bound the
accumulated provider conversation: ObjectiveRunner separately limits decisions,
replans, duration and recorded cumulative provider usage.

See [event routing](event-routing.md), [memory](memory.md), [Brains](brains.md)
and [Codex](codex.md) for the surrounding lifecycle.
