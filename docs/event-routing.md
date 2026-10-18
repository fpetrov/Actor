# Event-driven cognition

ACTOR0.3 extends the existing RuntimeSession, WorldState, ObjectiveRunner and
SkillScheduler. It adds no second receive loop, scheduler or game-state cache.
An Objective still runs through typed Skills; the event pipeline introduces
justified decision points while those procedures execute.

```text
authoritative game event → gRPC → RuntimeSession.accept
                                    │
                           EventRouter.ingest
                           bounded priority queues
                                    │
                         WorldState historical ring

installed event policy → assessment → Runtime routing policy
                                         │
                              strategic revision changes
                                         │
                      Skill cleanup / discard stale Brain answer
                                         │
                              ContextBuilder + Memory
                                         │
                                      Brain
                                         │
                                  typed SkillCall
```

Observations update WorldState independently. Transform, velocity and other
frame updates do **not** invoke the classifier. Only negotiated event envelopes
enter EventRouter. No state-change predicate is enabled implicitly.

## Installed semantics and policy

`actor.event_rules` entry points return `EventRule` objects. A rule binds the
capability ID, extension ID and canonical Protobuf type URL. Its small synchronous
`hints(event)` supplies trusted priority, possible criticality, semantic identity,
aggregation identity and tags; `assess(context,event)` supplies a strict
`EventAssessment`. Game payload prose cannot grant itself priority.

An assessment has independent relevance, urgency, buffer, remember, interrupt
and review dimensions, plus confidence/provider/reason. A provider only advises;
Runtime owns cancellation, persistence, cooldown, epoch changes and Brain calls.
Review without interrupt is deferred through a running Skill, but can invalidate
an active Brain decision. Critical uncertain assessments request conservative
review. Session disconnect and user cancellation remain deterministic lifecycle
conditions outside classifier authority.

The Arena plugin ignores routine station notices, buffers room transitions and
requests immediate review for authoritative urgent recalls. It reads the typed
kind/audience/source/revision, never keywords from `message`. Unknown or incomplete
signal metadata is conservative. Explicit trusted configuration tag
`station-observe-only` remembers training recalls without interrupting; absent
that declaration, an urgent recall requests review even when its relationship to
an arbitrary natural-language objective is uncertain. The Brain interprets the
actual objective. Rules do not pretend to understand every natural-language
opt-out. New games register their own semantics without Core game-ID branches.

```python
EventRule(
    capability_id="my-game.resource-unavailable",
    extension_id="my-game",
    type_url=type_url(ResourceUnavailable),
    hints=lambda event: EventHints(priority=50, potentially_critical=True,
                                  semantic_id=event.entry.payload.change_id),
    assess=my_resource_policy,
)
```

## Bounds, delivery and acknowledgement

Session constructs ingress immediately after manifest negotiation. `accept`
feeds it **before** WorldState's64-event history, including before the Objective
starts. The gRPC path does no network classification or SQLite access. Critical
installed rules can invalidate a strategic revision synchronously even while a
slow advisory classifier is awaiting a response. A separate async worker handles
classification and persistence.

Defaults:128 queued events,128 pending records,4096 deduplication keys and16KiB
per raw event. Routine aggregation uses a2-second window scoped by extension,
capability and source entity. Logical game IDs deduplicate semantic replays;
session/sequence identifies the wire event. Critical dedup keys are protected
from routine floods. New RuntimeSession means new working state; old-session
messages are rejected by the existing stream protocol.

Routine items may be aggregated or explicitly evicted, with counters. Triggers
and unassessed potentially critical items are protected. If the bounded protected
set cannot accept another critical event, ingress stops in an explicit degraded
state, wakes the Runner and safely fails the Objective after cleanup. Overflow
is never silently reported as success. Critical evidence pending at shutdown is
flushed to MemoryStore with a bounded deadline; persistence failure is explicit.

Acknowledgements distinguish received, assessed, remembered, context-included and
handled. Merely enqueuing does not mark an event handled. Only an accepted answer
at the same cognitive revision acknowledges included events. A newer event
cannot be acknowledged by an older answer. Audit history and all queues are
bounded. Unknown optional event types retain their metadata and wait for the
next natural context instead of waking the Brain on every unknown message.

## Soft preemption

`RequestReplan` preserves the Objective. A running Skill receives Scheduler
cancellation with `reason=preempted_by_event` and trigger IDs. Execution command
rights are revoked, cleanup zeros movement and the foreground lease is released
before a replacement Skill can start. Wait stops its timer; navigation stops
through the existing locomotion command. A structured cancelled SkillResult
reaches the next decision context.

Strategic event revisions, rather than observation revisions, invalidate Brain
answers. The Runner keeps one active decision, drains provider interruption and
discards an old answer before starting another. Event bursts coalesce into the
new snapshot. Critical events bypass the ordinary2-second decision cooldown;
at most8 replans and16 decisions per Objective are allowed by default.
Terminal Objective cancellation and disconnect keep their previous semantics.

See [Context Builder](context-builder.md), [memory](memory.md), [classifiers](classifiers.md),
[Brain lifecycle](brains.md), [Signal Console](station-signals.md) and
[actual validation](event-validation/README.md).
