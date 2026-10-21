# Actual SocialDeduction fault acceptance

`run_faults.py` is an opt-in development fixture for an **already created isolated room**. It launches four ordinary headless Unity clients and four independent authenticated Python Runtimes. All four receive the same trusted match Objective, provider and options. It never assigns Alignment, supplies a victim, teleports, changes server state, forces a winner, or issues a game command. The fixture Brains perform ordinary typed Skills using only their own permitted context.

The supervisor chooses a numbered owned participant which is currently Alive. A fixed `--participant 1` can be supplied; otherwise it takes the first Alive numbered client in the requested public phase. It never selects by Alignment. The only injected fault is `SIGKILL` of that participant's **live child `Popen`**, after matching its PID to this invocation's `launch.json`. It cannot signal a PID supplied by a caller, another room, or a saved previous manifest.

No live case was run as part of writing this helper. The offline tests below exercise the auditor and acceptance rules, not fault acceptance in a game.

## Preconditions

- Build the ordinary game client and install Runtime 0.6.0 with the M7 fixture providers. No model SDK/account is used.
- Use the isolated test Control Plane, normally `http://127.0.0.1:5081` through the root-owned tunnel. The production API is not contacted by default.
- Create a **fresh SocialDeduction room for every run**, `MinPlayers=4`. Use `MaxPlayers=6` for `late-reconnect` so the fifth normal ticket does not depend on expiry of the disconnected client's admission reservation.
- For `client-loss`, `late-reconnect`, or an Exploration client fault, configure the ordinary public `KillCooldownMs` to **at least 45000**. NGO can need approximately 30 seconds to detect an abruptly lost client. A first kill at the default 30-second cooldown would race disconnect detection. The helper checks the authoritative public rule before injecting the fault and refuses an ambiguous setup.
- Use normal bounded discussion/voting and match durations. The default helper deadline is 240 seconds; increase it, up to 900, when public settings require it. It never extends server deadlines.
- Supply an empty output directory. Five local bridge ports beginning at `--base-port` must be free.

Every client runs `-batchmode -nographics -screen-fullscreen 0 -screen-width 800 -screen-height 600`; the ordinary game caps headless FPS at 60. No window/fullscreen, GUI, remote server command, or deployment is performed.

## Cases

```sh
build/runtime-venv/bin/python spikes/social-faults/run_faults.py \
  --room ROOM01 --case runtime-loss \
  --output build/social-faults/runtime-loss

build/runtime-venv/bin/python spikes/social-faults/run_faults.py \
  --room ROOM02 --case client-loss \
  --output build/social-faults/client-loss --base-port 18610

build/runtime-venv/bin/python spikes/social-faults/run_faults.py \
  --room ROOM03 --case late-reconnect \
  --output build/social-faults/late-reconnect --base-port 18620
```

These cases fault during the public Exploration phase. They use `social-fixture-parity`: Crew wait in Main Hall; the Impostor waits for its own cooldown, approaches a currently visible player, and requests real kills. After the fault, the expected result is inferred **only from the actual postgame summary**. If the forfeiter was the Impostor, the remaining Crew must win with `impostors_eliminated`. If the forfeiter was Crew, an ordinary subsequent kill must produce an Impostor `parity` victory. A different legitimate result is retained as a non-pass for this particular acceptance case, never rewritten.

`runtime-loss` kills only the Python Runtime and deliberately leaves its ordinary Unity client alive. It additionally requires an observed bridge `Error`, authoritative own `Disconnected`, disabled keyboard, at least four distinct reports spanning two seconds with speed ≤0.05, client still alive and measured mean FPS >30. That two-second confirmation is **not** a measurement of the first movement-stop latency.

`late-reconnect` first kills an assigned client, then starts a fifth ordinary client with the **same display name**, a fresh token/port/profile and a normal new lobby ticket. Its Runtime uses ordinary passive `serve`, without a Brain or commands. The new ActorId must differ. Its actual authenticated own state must be `Spectator`, Alignment `Unspecified`, `Assigned=false`, `CanMove=false`; four fresh pose+motion frames spanning at least two seconds must remain stationary. The ordinary client must also report Spectator, keyboard disabled and zero commands. This proves the actual passive spectator lifecycle; it does not claim a forbidden action was attempted. Pure authority tests cover rejection of such actions.

To exercise a fresh reconnect **during an actual meeting**, use a new room with
capacity six and a public Discussion timer of at least 60 seconds (90 seconds
is the normal default), plus `KillCooldownMs >= 45000`:

```sh
build/runtime-venv/bin/python spikes/social-faults/run_faults.py \
  --room ROOM06 --case late-reconnect --phase discussion \
  --output build/social-faults/discussion-reconnect --base-port 18650 --timeout 360
```

The Discussion variant uses the existing ordinary `social-fixture-vote`
participants. A real visible kill/body report must create Discussion. Before
faulting a publicly Alive numbered client, the helper requires **45 seconds
remaining** in the authoritative public phase window; the protocol does not
expose the configured total Discussion duration separately. Short windows fail
before injection rather than manufacturing a phase. The fifth Runtime/client
is started immediately after the owned client is killed. Its actual client
launch and first spectator samples must precede the 30-second disconnect
window. The original same-name identity is never reused.

Meeting reconnect proof is frozen while both actual wire and Unity reports
are still in the same Discussion phase/revision. It requires at least four
fresh current-match/current-own pose+motion frames spanning two seconds,
continuous sample gaps of at most 1.5 seconds, a fresh Unity report, an
independent accepted Runtime session and timestamps inside that phase's
deadline. A later Voting/GameOver spectator value cannot backfill this proof.
All remaining assigned participants must still reach their normal matching
authoritative terminal result, and all ten owned children must stop. Public
output contains phase/counts/booleans only; the frozen own identities and
timestamps remain ignored local evidence. Default Exploration behavior is
unchanged.

For Impostor-loss coverage, keep `--participant 1` fixed **before launch** and
repeat `--case runtime-loss` in separately created rooms with a short valid
public kill cooldown (for example 5000 ms). Classify the selected participant
only from `selected_postgame_alignment` after each full run. A bounded batch
must retain every attempt, failure and cleanup result; stop when a completed
postgame attempt identifies the forfeiter as Impostor, or report that the
chosen attempt limit was exhausted. Never inspect live Alignment to pick a
participant or relabel a Crew-loss run as Impostor loss. A short cooldown is
not allowed for the Exploration **client-loss** case, which still requires
45000 ms to separate NGO disconnect detection from normal kills.

To compare phases, run **two fresh rooms** and retain both reports:

```sh
build/runtime-venv/bin/python spikes/social-faults/run_faults.py \
  --room ROOM04 --case exploration-vs-meeting --phase exploration --component runtime \
  --output build/social-faults/exploration --base-port 18630

build/runtime-venv/bin/python spikes/social-faults/run_faults.py \
  --room ROOM05 --case exploration-vs-meeting --phase discussion --component runtime \
  --output build/social-faults/discussion --base-port 18640
```

The Discussion case uses `social-fixture-vote` for all four: an ordinary visible kill and a surviving Crew member's real body report create the meeting. The supervisor waits for Discussion before faulting an Alive participant. Neither the meeting nor a body is fabricated. Each run explicitly reports only its actual phase coverage; a single PASS cannot claim both phases or other fault cases. `--component client` also supports a meeting client fault; allow at least the ordinary NGO detection timeout in the discussion duration.

## Evidence and boundaries

The Runtime wrapper uses the existing generic `server_factory` seam to observe actual authenticated `GameToRuntime` envelopes before normal acceptance, then forwards them unchanged. It checks canonical types and self recipients, captures its own state, and retains bounded body victim IDs only when legitimately visible. No audit value is fed back into a Brain. Postgame data is accepted only after an authoritative terminal transition; three surviving Runtimes, Unity reports, actor/session identities and complete summaries must agree.

The forfeit must appear once in the complete postgame replay, with final life `Disconnected`. The forfeiter must never be a kill target or a body victim observed by any of these permitted clients. **This is not an omniscient enumeration of server bodies.** The report names that limitation; it combines final life, complete replay and all observed views rather than treating a local zero body count as global proof. Late reconnect additionally proves that the old postgame record remains Disconnected while the new identity is a spectator.

All children are owned by this invocation. Unlike the interactive single-agent launcher, Runtime and client lifetimes are independent so a Runtime fault does not automatically close Unity. The invocation freezes private evidence before cleanup, stops Runtime/provider loops and then clients, and records zero remaining owned children as a required assertion. Partial startup, failed evidence, deadlines and interruption follow the same cleanup path.

`launch.json`, `agent*/wire-private.json`, `client.json`, `runtime.json`, SQLite databases, logs and `evidence-private.json` are **ignored local evidence**. They can contain legitimate private state and must not be copied into public artifacts. Only `acceptance.json` is the intended public projection: bounded metadata, counters, postgame classification and explicit passed/failed/not-executed checks. It omits chat, Objectives, authorization codes, credentials, positions and live Alignment.

Recompute that projection from the same frozen run without launching processes:

```sh
build/runtime-venv/bin/python spikes/social-faults/run_faults.py \
  --summarize-only --output build/social-faults/runtime-loss
```

There is deliberately **no server-loss or SSH mutation option**. A separate root-owned server-loss test would first verify the exact isolated lobby ID, its allocation port and stored process identity, guard against touching any other match, and signal only that match's server. Its own live evidence is required; this helper never claims it executed server loss.

## Offline verification

```sh
build/runtime-venv/bin/python spikes/social-faults/test_faults.py
```

These auth-free tests use synthetic typed envelopes and mocked owned children. They cover protobuf JSON integers, role-dependent legitimate outcomes, recipient/type validation, truthful no-body scope, terminal/session/match agreement, distinct fresh stationary spectator samples, no live-role selection, phase coverage, PID guards, cleanup ordering and public projection privacy. They launch no Runtime, Unity client, network listener, model or server.
