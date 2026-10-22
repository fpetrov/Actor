# Runtime Skills

Runtime 0.6.0 adds an optional synchronous `SkillDefinition.availability`
predicate over its read-only typed context. Catalog construction, validated
calls and the final check before lease acquisition all enforce it. Installed
participation policies may additionally restrict permitted Skills. These are
client safeguards; the game server independently checks permissions, phase epoch,
expiry, range and visibility. See [match lifecycle](match-lifecycle.md).

A Runtime Skill is a typed procedure over game capabilities. A Brain selects one Skill at a decision point; the Skill then executes independently for seconds without calling the Brain. It reads authoritative observations and issues typed commands through the existing RuntimeSession API.

Runtime Skills are distinct from game-native `CapabilityKind.Skill` and from Codex SDK `SkillInput`. No Core Protobuf changes are needed. Core/Standard remain engine-neutral; Agents Arena navigation and its coordinates live only in `actor_agents_arena.skills`.

```text
Brain decision → typed validation → foreground lease → Skill execution
                                                     ↓
                                                typed capabilities
                                                     ↓
                                        RuntimeSession → existing gRPC

Skill result ← cleanup + revoked command rights ← terminal condition
```

## Components and typing

- `ActorSkill`: synchronous read-only `preflight`, async `execute` and async `cleanup`.
- `SkillDefinition`: ID/version/description, input/result Pydantic models, factory, typed capability requirements, owned commands, execution and cleanup deadlines.
- `SkillRegistry`: discovers installed definitions, rejects duplicates/version conflicts/invalid schemas, creates implementations and builds a deterministic compatible catalog.
- `SkillContext`: restricted typed observation/event/command helpers, per-observation freshness, cancellation-aware waits and monotonic clock.
- `SkillScheduler`: one foreground Skill per session, command ownership, deadlines, cancellation, cleanup and structured telemetry.
- `SkillResult`: `succeeded`, `failed`, `cancelled` or `timed_out`, machine-readable reason, typed output serialized as JSON, duration and command count.

`SkillModel` uses Pydantic strict validation, forbids extra fields and nonfinite numbers. Models automatically generate input/result JSON Schema; a separate handwritten schema is unnecessary. Implementations receive a validated model, rather than an arbitrary arguments dict, and return their declared result model. Invalid values never reach implementation execution.

```python
registry = SkillRegistry.discover()
catalog = registry.catalog(session)
typed_input = registry.validate_call(session, "actor.wait", {"duration_seconds": 2})

scheduler = SkillScheduler(session, registry)
result = await scheduler.run("actor.wait", {"duration_seconds": 2})
document = result.model_dump(mode="json")
```

The catalog contains `id`, `version`, `description`, input/result schemas, required capabilities, owned command capabilities and timeout. Compatibility means that the negotiated session advertises the expected capability kinds/types through decoded extensions. Transient observation readiness does not remove definitions from the catalog. Before `SkillStarted`, Scheduler revalidates the call, checks required observations/freshness, ownership and game-specific preflight. An orchestration layer should wait for the initial observation frame before its first immediate decision.

## Capability boundary and ownership

Skills receive no raw gRPC transport or mutable WorldState. They declare typed `CapabilityRequirement` records. Context checks that every read/write belongs to that declaration and that every command is owned by the current lease. Reads return Protobuf copies, preserve sample sequence/revision and use the individual observation's monotonic receive time. New unrelated events/frames cannot refresh a cached Transform.

```python
entry = context.observation_entry("some-game.pose", PoseObservation)
pose = context.observation("some-game.pose", PoseObservation)
events = context.events("some-game.changed", ChangedEvent, after_sequence=cursor)
context.command("some-game.control", ControlCommand(...))
await context.wait_until(predicate, timeout_seconds=2)
await context.sleep(0.05)
```

Context has a monotonic `now()` and controlled `actor_id`. Installed Python Skills are trusted local code; the public context API is a capability/lifecycle boundary, rather than a Python sandbox.

MVP scheduling allows one foreground Skill per session, including across separate Scheduler instances. Its declared command set is reserved before execution and retained through cleanup. This prevents competing Skills from writing Locomotion2D. Later scheduling can allow nonconflicting background procedures without changing Skill definitions.

Termination revokes normal execution command rights before cleanup. A dedicated cleanup task briefly retains rights to the same owned commands; no stale/background task may refresh them afterward. A command-owning implementation must provide explicit cleanup or it cannot start. Cleanup does not transfer its rights to unrelated child tasks.

## Lifecycle and failure

Every execution path calls cleanup: success, structured failure, exception, cancellation, timeout and session disconnect. `cancel()` waits for termination/cleanup; directly cancelling the task awaiting `run()` also produces a controlled cancelled result after cleanup. A cancellation requested before execution starts cannot leak the foreground reservation.

Execution and cleanup waits are bounded. Cooperative Skills finish promptly; command rights are revoked even when an erroneous implementation ignores cancellation. Such an implementation is reported through cleanup diagnostics and remains a plugin bug rather than being given continued command access.

Reasons include `unknown_skill`, `invalid_arguments`, `required_capability_missing`, `required_observation_missing`, `stale_world_state`, `command_ownership_conflict`, `session_disconnected`, `route_failed`, `cancelled`, `timeout`, `skill_exception` and `cleanup_failed`. Callers use these fields and never infer a result from exception prose.

Event-driven soft preemption uses the same cancellation/cleanup path:

```python
await scheduler.cancel(reason="preempted_by_event", details={
    "trigger_event_ids": [event_id], "decision_reason": "critical_event",
})
```

The result is `cancelled` with reason `preempted_by_event`; the Objective stays
Running and receives a new decision point after cleanup. Ordinary `cancel()`
keeps reason `cancelled`. The first cancellation request owns its bounded JSON
diagnostics. This is separate from terminal `ObjectiveRunner.cancel()`, which
ends the whole episode. EventRouter decides whether an assessed event merits
interruption; a review-only event can wait until this Skill naturally completes.
Wait exits its monotonic sleep promptly. Navigation revokes command rights and
issues zero intent before another Skill can acquire the same command lease.

Runtime diagnostics emit `SkillStarted`, `SkillSucceeded`, `SkillFailed`, `SkillCancelled` and `SkillTimedOut`; these are Runtime lifecycle records, not new game wire events. `scheduler.report()` includes bounded result/event history, active Skill and aggregate execution/failure information. Command count measures typed intents issued; session transport telemetry separately counts messages actually written after coalescing.

## Included Skills

`actor.wait` accepts `duration_seconds` from zero through 300. It uses a monotonic deadline and cancellation-aware sleep, owns no commands and contains no game assumptions.

The optional Communication package adds `actor.send-message` and
`actor.wait-for-message`. Sending awaits an authoritative correlated receipt;
waiting uses a bounded deduplicated inbox with sender/task/correlation filters,
including messages that arrived before the Skill started. Neither Skill calls
the Brain while waiting. See [communication](communication.md).

The Arena package also supplies Start/WaitAssignment/InspectDiagnostic/
SubmitCalibration/WaitCalibration procedures. Their typed schemas enter the
dynamic catalog through ordinary plugin discovery. They validate private
assignment and role, approach the installed terminals through the existing
movement controller and await typed authoritative outcomes. Task state remains
entirely server-owned. See [cooperative tasks](cooperative-tasks.md).

Arena 0.4.0 adds the following game-specific investigation procedures through
the same `actor.skills` plugin and dynamic catalog:

| Skill ID suffix (`agents-arena.`) | Procedure |
| --- | --- |
| `select-role` | Request Crew/Engineer/Sheriff and await the correlated server result |
| `wait-for-incident` | Await a public active case without model polling |
| `maintenance-override` | Crew/Engineer approaches the real panel and adjusts public `alpha`/`beta` |
| `inspect-cooling` | Engineer approaches Cooling Control and obtains its private diagnostic |
| `inspect-security-records` | Sheriff approaches Security Records and obtains its own bounded audit slice |
| `share-investigation-evidence` | Engineer sends a compact diagnostic claim to the assigned Sheriff through Standard direct communication |
| `repair-cooling` | Engineer approaches the panel, starts the server-timed repair and awaits completion |
| `submit-investigation-report` | Sheriff approaches its terminal and submits a hypothesis plus exactly three references |
| `wait-for-incident-resolved` | Require restored cooling and validated report for the same incident |
| `cancel-investigation` | Request bounded authoritative release of unfinished own-role work |

Terminal procedures reuse the existing movement controller and own Locomotion2D
while approaching. They validate fresh role, match, incident, permitted own
evidence and private expiry; server checks remain the authority. Cleanup issues
zero movement and cancels an unfinished repair where possible. Objective
finalization uses the same scheduler and typed cancellation command, preserving
an already resolved case.

Inspection and sharing results use `session_only` policy. Working evidence
reaches its authorized Brain; public diagnostics and SQLite retain safe metadata.
A diagnostic claim received through communication remains untrusted player
content. Sharing success establishes the correlated server receipt, while report
authority separately requires actual receiving-client ACK. Neither stage proves
that a Brain understood the claim. The Sheriff must correlate circuit, covered
time window, controller context and observed action; merely naming a hypothesis
or citing every record cannot complete the task. See [investigation](investigation.md)
and [evidence](evidence.md).

`SkillDefinition.result_policy` is `public`, `session_only` or `sensitive`.
Authorized working results still reach the Brain; public diagnostics and SQLite
use safe projections. Private output/details are withheld, known secrets are
redacted recursively and free-form Objective outcome prose is withheld after
private data was handled. `context_fields` explicitly opts safe input fields into
relevance metadata; message text and authorization are never included there.

`agents-arena.navigate-to-room` accepts `target_room_id` of `engineering` or `main-hall`. It requires decoded Transform2D, Motion2D, Locomotion2D, RoomMembership and RoomChanged capabilities, and owns Locomotion2D. Its small predefined reference-game graph routes through:

```text
Main Hall anchor (0, -1.4)
       ↕
Main Hall doorway (0, -4.7)
       ↕
Engineering doorway (0, -6)
       ↕
Engineering anchor (0, -7)
```

Navigation refreshes movement intent at 20 Hz, slows near a waypoint and checks fresh coherent Transform/Motion samples. It finishes only after authoritative RoomMembership confirms the destination and four distinct new Transform/Motion samples confirm stopping. A cached sample cannot be counted repeatedly, even if RoomMembership keeps updating. A transition to a different room must also have the expected typed RoomChanged event. Calling the Skill for the actor's current room safely confirms a stable stop without inventing a transition.

Navigation cleanup always issues zero Locomotion2D when the session remains writable. If the stream is already gone, Unity bridge disconnect cleanup and the existing AgentInputSource/server stale-intent timeouts provide the additional stop layers. Position, room membership and physics are never written directly by a Skill.

## Add a plugin

Use ordinary Python entry points in group `actor.skills`. A factory returns an iterable of `SkillDefinition`; Core has no game-ID branches.

```toml
[project.entry-points."actor.skills"]
my-game = "my_game.skills:skill_package"
```

A synthetic typed procedure needs only an implementation and definition:

```python
from pydantic import Field
from actor_runtime.skills import ActorSkill, SkillDefinition, SkillModel

class IncrementInput(SkillModel):
    amount: int = Field(ge=1, le=10)

class IncrementOutput(SkillModel):
    count: int

class Increment(ActorSkill):
    async def execute(self, context, arguments: IncrementInput):
        return IncrementOutput(count=arguments.amount)

def skill_package():
    return (SkillDefinition(
        skill_id="test.increment-counter",
        version="1.0.0",
        description="Return a validated synthetic counter increment.",
        input_model=IncrementInput,
        result_model=IncrementOutput,
        factory=Increment,
    ),)
```

For a real command procedure, add typed capability requirements and its owned command IDs, then implement cleanup to restore the game's safe intent. Keep route data and game semantics in that game's plugin.

## Verification

```bash
build/runtime-venv/bin/python -m pytest \
  runtime/tests/test_skills.py runtime/tests/test_navigation_skill.py \
  runtime/tests/test_preemption.py -q
```

Tests cover synthetic plugin discovery without Core edits, deterministic catalog and compatibility, duplicate/version/schema rejection, strict input bounds/types/finite values, success/failure/exception/cancellation/deadline/disconnect cleanup, shared ownership and revoked rights. Navigation tests use the actual typed RuntimeSession interfaces, authoritative room events and closed-loop fixture physics; real remote Unity multiplayer evidence is recorded separately.

## M8 social procedures

Arena adds `agents-arena.discuss`, `agents-arena.await-discussion-update` and `agents-arena.follow-visible-actor`; optional generic `actor.remember-contradiction` complements `actor.remember-hypothesis`. Discussion batches attributed replies without model polling or claim verification. Following uses fresh current sight and bounded dated last sight, never hidden positions or automatic attacks. See [social reasoning](social-reasoning.md) for authority/privacy boundaries.

`perform-task` and `fake-task` now consume the own targeted task-work observation for the same maintenance/calibration/sensor mechanics exposed to Human players. Their command lease includes typed task steps and exact owned-work cancellation; a step receipt is not final completion. New Social actions carry the own authenticated `connection_generation`. The new game capabilities register through the Arena manifest and ordinary Skill definitions; Core1.0's wire descriptor remains unchanged.
