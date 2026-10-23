# Executable plans and classifier context

The Python Runtime accepts bounded plans alongside legacy single-Skill decisions.
It does not require changes to the Unity client or game protocol. The default
classifier remains selected by the user's configuration.

## Behavior

The Brain can return a `plan` with one to four sequential Skill calls, up to two
one-shot conditions, and an optional failure fallback. Every call is validated
before accepting the plan and again by the existing Scheduler at execution.
Conditions use `nonempty` on a declared repeated or string field of a negotiated,
decoded observation/event capability. There is no execution of arbitrary code or
natural-language predicates.

The Runtime executes successive steps without requesting another Brain decision.
A failed step discards the remaining sequence and runs the fallback at most once.
A matching condition may replace the remaining plan with its response. The old
foreground Skill is cancelled and fully cleaned up before that response starts.
After the sequence, fallback or response finishes, control returns to the Brain.

Critical lifecycle handling remains authoritative: phase changes, death and game
over invalidate old execution. By default a neural request for review also supersedes the plan.
Conditions cannot suppress mandatory critical reviews. Ordinary observation frames
still do not trigger classification; only the first fresh matching observation of
an active accepted condition is nominated for assessment. Already true conditions
are checked against fresh self observations when a plan is accepted.

## Background continuation

Enable bounded lookahead in the local cognitive configuration:

```json
{
  "classifier": {"provider": "minilm"},
  "brain": {"plan_ahead": true, "plan_ahead_max_age": 10}
}
```

While the last step of an accepted plan executes, the Runtime prepares at most
one next decision through the same Brain. The context identifies this as
`planning.mode = continuation`, names the accepted plan, and explicitly requires
its successful completion. The next decision must describe what comes after that
plan, without repeating its running steps or inventing their results. Preparation
does not reserve actuators or execute commands. Single-Skill decisions retain the
ordinary sequential behavior; a Brain can return a one-step plan to opt into lookahead.

The prepared decision is validated against the current Skill catalog after the
old foreground Skill has released its lease. Its age since preparation finished
must not exceed `plan_ahead_max_age` seconds. Actual Skills still perform their
freshness, availability and server-side action checks. A failed step, planned
reaction, critical transition, disconnect or cancellation discards the continuation.
Preparations count against the same decision/token budgets, including discarded
answers. Providers without nonterminal `interrupt_decision` support stay sequential.

With lookahead enabled, ordinary advisory reviews no longer interrupt the current
Skill or invalidate an in-flight Brain answer. New messages remain pending for the
next snapshot; accepting an older answer never acknowledges newer events. Critical
reviews have a separate execution revision and retain their existing interruption
and lifecycle policy. This reduces pauses without changing classifier weights or
the default rules provider. It is opt-in; omit `brain.plan_ahead` to keep the prior behavior.

Private lifecycle traces include `ContinuationStarted`, `ContinuationReady` and
`ContinuationDiscarded`. The Objective report identifies prepared/discarded decisions
and whether lookahead was enabled. Use `runtime/benchmarks/plan_ahead.py` to compare
inter-action waits with a controlled model delay. That probe measures scheduling,
not live-game strategy or visual quality.

Arena separates own work progress from role/life transitions. The first own-state
event, a change of alignment, duty, life, assignment or connection generation, and
any non-alive state still take the trusted critical route. Once those fields are
known, updates limited to readiness, movement gating, cooldowns, meeting allowance
or the own revision are ordinary classifier inputs. They remain in the agent's
permitted state; Skills still enforce current movement and action permissions.
The transition cache is bounded to 128 session/match/actor identities; an evicted
identity takes the conservative critical route again on its next event. Phase,
meeting, attack and emergency policies are unchanged.

## Example decision

Use actual task/room/capability IDs from the agent's permitted context. The values
below illustrate the format; they do not select a role or reveal another player.

```json
{
  "kind": "plan",
  "steps": [
    {"kind": "skill", "skill_id": "agents-arena.perform-task", "arguments": {"task_id": "task-1"}},
    {"kind": "skill", "skill_id": "agents-arena.navigate-social-room", "arguments": {"target_room_id": "engineering"}}
  ],
  "conditions": [
    {
      "capability_id": "agents-arena.social-visible-world",
      "field": "bodies",
      "when": "nonempty",
      "response": {"kind": "skill", "skill_id": "agents-arena.report-visible-body", "arguments": {}}
    }
  ],
  "fallback": {"kind": "skill", "skill_id": "actor.wait", "arguments": {"duration_seconds": 0.25}}
}
```

`report-visible-body` chooses a currently visible body at execution time and reuses
the existing report Skill's freshness, range, navigation and correlated server
result checks. A missing body causes failure, not a fabricated successful report.

## Classifier contract and recording

`ClassificationContext.current_plan` is an own-agent snapshot of the accepted plan:
ID, state, current step and arguments, remaining steps, unused conditions and
remaining fallback. It is captured synchronously before classification. A null
value means there is no executing plan; no plan is reconstructed from later actions.
Public diagnostics redact this future intent while activity is private.

The optional [MiniLM v7 package](../classifiers/minilm/README.md) projects these
structured fields into its classifier input. The exact input is captured before
inference when private recording is enabled. Missing plans remain unknown.
Critical rules retain their direct route and mandatory policy. Providers may use
`bind_router(router)` and `observe_rule_assessment(context, event, assessment)`;
no internal method patching is required.

Private recording separates input, provider assessment, post-policy Router events,
accepted Brain decisions, plan steps, Skills and command outcomes. See the
[recording contract](../classifiers/minilm/INTEGRATION_AND_DATA.md). A collector
failure is diagnostic and must not change a game decision.

## Verification

Install Runtime from this checkout; MiniLM is an optional separate dependency.

```sh
python -m pytest runtime/tests -q
python -m pytest classifiers/minilm/tests runtime/tests/test_minilm_integration.py -q
```

The neural integration test runs real v7 weights with a deterministic Brain and
typed task/body/report observations. It verifies an accepted conditional response
executes without an extra Brain turn, and checks the exact input retained for
future review. It is not a win-rate estimate or a new live game.

## Limits

Conditions are one-shot; the first chosen response ends the prior sequence.
There is no arbitrary branching language, retry loop or saved-plan persistence
across episodes. The example fallback is a bounded wait, not an implicit task
completion. Broader strategy and live quality still need an independent game run.
