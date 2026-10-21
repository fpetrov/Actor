# Isolated lifecycle acceptance

`lifecycle.py` is an opt-in harness for a controlled Objective against one
ordinary built game client. It does not run during tests or on import. The Python
harness owns its authenticated loopback Runtime and exactly one `Popen` client;
only that held child is terminated. It never mutates a server, chooses an
alignment or reads another participant's context, database or live private role.
Use a fresh ordinary lobby and matching client/server version.

The existing `run_faults.py` owns the separate four-participant Runtime/client
loss and late-spectator cases. This helper does not replace those results.
Controlled lifecycle success is not autonomous model intelligence or a match win.

## Preparation

Install the current Runtime (`runtime[dev,codex]` only for the Codex case) and
check official sign-in separately with `actor-runtime codex status`. Keep this
harness's parent process alive until it writes `acceptance.json` and exits.
No credential is printed or written to the report.

For an active Social match, launch **three** other ordinary independent players
in a fresh Social room, then use `--ready-social` for this fourth participant.
That option runs the existing typed `agents-arena.ready-for-match` Skill and
waits for this actor's own assigned/alive state and public Exploration phase.
It does not choose or reveal a role. Do not start the four-player supervisor
and then add this helper as a fifth participant: a late join is a spectator.
Without `--ready-social`, a Sandbox or already-permitted ordinary session can
exercise the same generic cancellation/turn-cap mechanics.

For a one-client Sandbox probe, create a separate ordinary `Sandbox` room,
wait for its Control Plane status `Ready`, and omit `--ready-social` from the
commands below. No second participant, role selection, calibration or incident
is needed for the generic Wait procedure. The same owned Sandbox room can be
used sequentially while it remains available; each launch receives a new normal
admission ticket and its own bridge credential. Never pre-reserve tickets just
to probe the room, and wait for any externally coordinated staging lease before
creating it. These results must be labelled Sandbox lifecycle checks, not Social
match acceptance.

All helper clients are headless ordinary clients, with the usual owner input,
server physics, adapter and gRPC path. They are not `UNITY_SERVER` builds. Use
separate unused local ports and fresh output directories. `--api-url` must point
to the Control Plane that owns the room; examples use the local acceptance API.

## Explicit Objective cancellation with a live bridge

```bash
build/runtime-venv/bin/python spikes/social-faults/lifecycle.py \
  --case objective-cancel --room ABC123 --ready-social \
  --api-url http://127.0.0.1:5081 --port 18750 \
  --output build/social-lifecycle/cancel-wait
```

The deterministic Brain selects the production `actor.wait` Skill. Once its
foreground execution is active, the helper calls **that exact
`ObjectiveRunner.cancel()` instance** and awaits it. It then proves that a fresh
Scheduler can acquire the foreground for a zero-duration Wait, without issuing
a game command. The bridge and ordinary client stay alive for another 2.2
seconds: no new command, no active Skill/Runner, a closed Brain, no pending
cleanup, stationary client and disabled keyboard are required before owned
process cleanup. This is different from SIGTERM, which shuts down the Runtime
and bridge.

To cancel real movement instead, add `--skill navigate --target-room security`.
The ordinary Social navigation Skill must actually remain active and send at
least three commands before cancellation. An unavailable/finished route fails
the fixture; it is never silently replaced with Wait. Use fresh physical spawn
and normal match state, without arranging hidden actor positions.

## Actual official Codex turn-cap exhaustion

```bash
build/runtime-venv/bin/python spikes/social-faults/lifecycle.py \
  --case codex-turn-budget --room ABC123 --ready-social \
  --api-url http://127.0.0.1:5081 --port 18751 --timeout 240 \
  --output build/social-lifecycle/codex-turn-cap
```

This case constructs the real `CodexBrain(max_turns=1)` and the production
ObjectiveRunner with room for four decisions. Its controlled catalog contains
only `actor.wait`; the objective asks to wait one second and then finish.
Nothing supplies a game answer or private assignment. The provider must choose
and complete an actual Wait. A second decision attempt must raise the real
`ActorError(code='codex_turn_limit')` from CodexBrain, before a second adapter
call. A transparent observer records that code and rethrows it. There is no
fallback from `decision_limit`, timeout, a model's Finish or `decision_count`.

Acceptance requires official adapter `unique_model_turns > 0`, SDK 0.162.1,
read-only/ephemeral settings, one provider decision, two decision attempts,
singleflight, zero model tool calls, failed Objective cleanup and a still-live
bridge. The generic Objective result reason is `brain_failure`; the independently
observed exact provider denial distinguishes its cause. A model that finishes
without using Wait makes this case fail, rather than fabricating cap evidence.

CLI `--max-decisions 1` alone cannot prove this case: it also bounds the
Objective loop, so ordinary execution can end with `decision_limit` before the
provider's separate cap is reached. These are different limits.

## Server-loss observer; root performs the fault separately

```bash
build/runtime-venv/bin/python spikes/social-faults/lifecycle.py \
  --case server-loss-observer --room ABC123 --ready-social \
  --api-url http://127.0.0.1:5081 --port 18752 \
  --match-id EXACT_OWN_MATCH_ID --timeout 240 \
  --output build/social-lifecycle/server-loss
```

Wait for the safe `ready.json`/stdout readiness record. Only then may the root
operator terminate the isolated dedicated server through a deployment-specific
exact identity guard. `spikes/event-routing/runtime_faults.py` shows the original
procedure, but hardcodes the production journal service/log directory and must
not be used unchanged for a staging server. Fresh lobby metadata
must match the expected room/match/port, journal allocation PID, `/proc` actor
UID and executable, exclusive UDP owner and matching READY log. Its `remote`
function supports `inspect` and guarded `terminate` (SIGTERM). Never select a
server by process-name/port wildcard or terminate a Control Plane service.
Use the service/log identity for the actual deployment and keep the separate
guard output as part of acceptance evidence.

The readiness file carries `schema="actor.social-lifecycle-acceptance.v1"`,
`case="server-loss-observer"`, `ready`, `room_code`, `match_id`, own actor/session
IDs and `created_at_unix_ms`. The observer refreshes this diagnostic snapshot at
1 Hz while its task/session/client and authoritative own motion remain live;
expiry or cleanup clears `ready`. A guard must check exact room/match identity
and a snapshot age of at most 30 seconds, independently of copied-file mtime.

The observer sends no SSH command. It requires previously fresh own motion to
expire, then the ordinary bridge/session to disconnect and the Objective to
finish with `session_disconnected`. Active navigation may safely finish earlier
with a structured stale-state/command rejection and `brain_reported_failure`;
the observer still waits passively for the actual session disconnect before
accepting that result. It checks a stationary still-running client,
cleanup/provider drain and stable command count. The existing NGO disconnect
window can be 30 seconds, so terminal cleanup has a 45-second bound after motion
staleness. Movement intent expiration is much faster; this is not an instant
transport-loss claim. `--skill navigate` can observe the same boundary while a
normal move is active.

The helper's PASS means **observed connection-loss cleanup**, and explicitly
requires the separate guarded server-fault record. A disconnected client alone
cannot prove that the server process crashed. The helper does not substitute a
synthetic GameOver for transport loss.

## Participation budgets through the ordinary launcher

No production code is needed. Put one or more limits in a local cognitive JSON
file, for example:

```json
{
  "participation": {
    "duration_seconds": 45,
    "max_episodes": 32,
    "max_decisions": 160,
    "max_tokens": 1500000
  }
}
```

Then launch a fresh four-player group through the existing normal path:

```bash
build/runtime-venv/bin/python scripts/launch-social-agents.py \
  --room ABC123 --players 4 --brain social-privacy-fixture \
  --api-url http://127.0.0.1:5081 \
  --config build/social-lifecycle/participation-timeout.json \
  --timeout 180 --output build/social-lifecycle/participation-timeout
```

The current controlled privacy provider's first attack is delayed 100 seconds;
a 45-second participation duration therefore permits a deliberate time-budget
case without treating a normal quick game end as exhaustion. The expected
Runtime result is **failed**, `authoritative_terminal=false`, reason
`participation_timeout`. The multiplayer supervisor is expected to fail match
acceptance and clean its owned children; this does not mean the safety check
failed. Confirm the specific Runtime reason and no stale commands, not the
supervisor exit code alone.

Other independent config cases are `max_decisions: 1` →
`participation_decision_limit`, `max_episodes: 1` →
`participation_episode_limit`, and `max_tokens: 1000` →
`participation_usage_limit` when using a real Codex provider. Change only the
limit being exercised and give unrelated limits enough room. A measured token
limit is checked at telemetry boundaries: one in-flight SDK turn can overshoot
it; it is not a guaranteed billing/pre-generation hard cap. The strict trigger
reason and real SDK counters are necessary to claim that case executed.

Source privacy intentionally withholds full private episode reports until
GameOver. For a nonterminal budget stop, public CLI evidence is the participation
reason/counts, empty cleanup errors, stopped owned Runtime/SDK processes and
client/bridge command/velocity observations. Do not expose raw private contexts
just to obtain additional telemetry. A budget-stop report is not a winning match
or postgame-role disclosure.

## Scope and offline verification

The helper disables event routing for its single controlled Objective to isolate
API cancellation/provider denial from unrelated phase noise; game authority
checks are unchanged. It does not replace event-routing or full match-controller
acceptance. It deliberately runs no extension Objective finalizers because its
controlled procedure owns only Wait/navigation and no extension task. Normal
Skill cleanup and command revocation still run through the production Scheduler.

```bash
build/runtime-venv/bin/python -m pytest spikes/social-faults/test_lifecycle.py -q
```

Offline checks cover rejection of shutdown as API cancellation, missing/forged
cap evidence, unowned PID refusal, bounded drain, prompt cancellation of a waiting
provider, actual ObjectiveRunner cancellation with foreground reacquisition and
real CodexBrain cap logic using an explicitly fake offline adapter. They make no
live SDK, server-loss, Unity or model-quality claim.
