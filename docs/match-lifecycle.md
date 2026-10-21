# Match participation and bounded episodes

Runtime 0.6.0 adds a generic `MatchEpisodeController`. It coordinates existing
`ObjectiveRunner` episodes; it does not choose actions or interpret Alignment,
death, voting, rooms or victory conditions. Those meanings belong to the
installed Arena policy. ACTOR Core wire protocol remains 1.0.

```bash
actor-runtime match --brain codex --match-policy agents-arena \
  --port 50051 --report ./build/social/runtime.json --exit-on-complete
```

`social-scripted` selects the deterministic reference provider. The common
`--objective`, `--config`, `--model`, `--classifier`, `--memory`,
`--memory-database` and `--agent-profile` options remain available. Each
participant requires its own Runtime, bridge token, WorldState, memory/profile
and model resources. A supervisor supplies the same trusted objective to all
participants; own Alignment arrives only through a targeted private observation.
The direct CLI example assumes a client configured with the same fresh
`ACTOR_BRIDGE_TOKEN`; the ordinary multi-agent launcher configures both processes
without printing the credential.

## Resource ownership

A participation holds exclusive cognition on one Session and coordinates its
Scheduler and EventRouter. RuntimeServer owns the transport/session lifetime;
the caller owns final MemoryStore closure.
Every bounded episode receives a **fresh Brain instance and a fresh model
thread**, then closes it completely before another episode starts. The Router
keeps its bounded ingress, deduplication and evidence acknowledgements across
episodes. The shared Scheduler preserves one foreground lease and revokes
commands before any transition. CLI closes the shared MemoryStore last.

The existing standalone `actor-runtime agent` path still runs one Objective
with the original resource ownership. In a participation, ObjectiveRunner uses
an externally owned Router and defers extension finalizers until participation
termination. Ordinary Skill cleanup still happens after every execution.

`FinishObjective(success)` finishes that bounded episode. It cannot declare a
winning side, finish a game, revive an actor or manufacture authoritative state.
The coordinator can start another episode after a finite cooldown. Only the
installed policy's interpretation of received authoritative terminal state
produces `ParticipationResult.authoritative_terminal=true`.

## Policy and interruption

Factories in entry-point group `actor.match_policies` supply policies:

```python
def evaluate(session) -> EpisodeDirective: ...
def observe(session, working_knowledge) -> None: ...  # optional
def skill_available(session, definition) -> bool: ...  # optional
```

`EpisodeDirective` carries a bounded `scope_id`, `episode_key`, reason and one
of `waiting`, `active`, `suspended`, `terminal`. The generic coordinator never
branches on game-specific enum values. Policy code is installed developer code,
not player prose, model output or a new transport.

Arena keys episodes by authoritative phase epoch, own life/assignment and newly
discovered bodies. Ordinary visible movement does not restart cognition. A
targeted transition can precede its periodic observation: it invalidates old
execution immediately, while a new phase episode waits for a coherent fresh
observation/catalog. Own death/ejection suspends cognition and actions; the
client remains connected and passively waits for the authoritative team result.
No further Brain call starts after terminal match state is received.

The existing EventRouter still handles strategic interruptions inside an
episode. Typed phase/life/meeting/emergency/witness events use installed rules;
ordinary communication stays untrusted, bounded and noncritical. A body first
appearing in the permitted complete visibility bundle changes the Arena episode
key and records a private historical observation. Observation frames themselves
are never submitted to a classifier.

## Limits and faults

`CognitiveConfig.participation` defaults to 1,800 seconds, 32 episodes, 160
decisions, 1,500,000 measured tokens and a two-second episode restart cooldown.
Per-episode Brain/Objective/replan limits still apply. A remaining global
decision budget also bounds the next Objective's loop. Providers are never
reused to evade a model-thread limit.

Cancel, disconnect, deadline, routing degradation or exhausted participation
budget drains the active episode and Skill, then runs installed finalizers once
and closes routing. These outcomes are distinct from authoritative GameOver.
Team outcome and personal life/survival remain separate fields supplied by the
Arena policy; a dead Crew participant may belong to the winning side.

The controller's `cancel()` releases its cognitive ownership; it does not call
RuntimeServer.stop() or close the game transport itself. The caller decides
when to close those resources. CLI `--exit-on-complete` closes the Runtime after
the participation result. A finalized controller is not reused for a new run.

## Working knowledge and diagnostics

Private bounded `WorkingKnowledge` spans episodes. Records explicitly identify
historical observations, player claims or unverified hypotheses, with source
event/actor/capability, sequence/revision, observation time and expiry. A lost
visible actor's last-seen record is never refreshed by an empty bundle. It is
history, not a current tracked position. Current visible state stays in the
normal latest observation cache.

`actor.remember-hypothesis` optionally records a concise belief and confidence
with references to retained evidence. It does not verify a claim or create an
authoritative fact. Private contents never enter automatic SQLite evidence or
public reports. Persistent public memory is additionally scoped to the current
participation, so another match's history cannot be retrieved as current evidence.

Realtime match reports expose aggregate counters only. Private action identifiers
are also suppressed in logs, Scheduler diagnostics and automatic stored results;
an action named `kill-visible-actor` would itself disclose private activity.
Postgame safe reports can include completed episode metadata and the legitimate
authoritative summary. A typed final summary becomes public evidence eligible
for bounded durable memory only after a verified GameOver boundary; premature
summary delivery is rejected. No model conversation or hidden reasoning is recorded.

Automated Runtime tests exercise fresh providers, phase/death/terminal races,
passive suspension, global bounds, one-time finalizers, availability rechecks,
private working knowledge, scope isolation and typed correlated action outcomes.
They are local fixture tests, separate from live Unity/NGO/model acceptance.

Separate actual Sandbox probes cover the lower Objective lifecycle: an explicit
ObjectiveRunner cancellation left the ordinary client/bridge connected and
released the foreground lease; a real official Codex turn completed Wait before
the next decision received `codex_turn_limit`. Their bounded public projections
are [cancellation](social-deduction-validation/sandbox-objective-cancel.json) and
[provider cap](social-deduction-validation/sandbox-codex-turn-budget.json).
These controlled generic procedures do not establish Social match participation,
phase handling or an autonomous win. The first server-loss observer attempt
[timed out](social-deduction-validation/sandbox-server-loss-attempt01.json) and
is explicitly not accepted.
