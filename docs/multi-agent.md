# Independent agents in one lobby

Each agent has its own Python process, RuntimeSession, WorldState, EventRouter,
SkillScheduler, bridge port/token, output directory and SQLite database. A
standalone objective has one ObjectiveRunner/Brain; match participation creates
bounded Objective episodes with a fresh Brain and provider thread each time.
The same participant retains its session, Scheduler, Router and scoped memory.
A game client connects only to its own loopback bridge.

The common game server is the only cooperation channel. Neither agent reads the
other's reports, database, Python objects or model context. The launch supervisor
supplies objectives, watches terminal status and owns process cleanup; it does not
forward game data. A shared lobby code identifies the ordinary multiplayer lobby,
not a shared agent memory.

## Launch Social Deduction participants

Build the matching Game 0.8.0 client and install Runtime 0.6.0. Create a
`SocialDeduction` room, then start 4–6 independent participants using its
configured minimum count (four by default):

```bash
build/runtime-venv/bin/python -m pip install -e 'runtime[dev]'
build/runtime-venv/bin/python scripts/launch-social-agents.py \
  --room ABC123 --players 4 --brain social-scripted \
  --output build/social-match
```

For independent official Codex providers, install `runtime[dev,codex]`, check
`actor-runtime codex status`, then use `--brain codex`. `--brains` can choose one
provider per numbered participant; it does not assign alignments or duties.
Every participant receives the same trusted objective. The game server selects
assignments, and each Runtime learns only its own permitted private state.

The supervisor launches ordinary clients headlessly by default. Use `--windowed`
for explicit 800×600 visual checks. Each numbered participant gets its own
credential, consecutive port from `--base-port`, database/profile and fresh
output directory. The supervisor reads readiness, process-isolation metadata
and terminal proof, never live roles, positions, claims or model context. All
communication uses the existing authoritative game-server message path.

`--players` must equal the room's configured minimum; changing it does not
change server rules. Social deployment settings are validated and copied into
an immutable room snapshot at creation. Seeds and assignments are absent from
the public room API and launcher. A room uses one match; it cannot switch modes.

The launcher selects `actor-runtime match --brain ... --match-policy agents-arena`.
The generic coordinator renews bounded episodes on the installed policy's phase,
own-life and discovery boundaries. Finishing an Objective ends an episode;
death suspends cognition while the endpoint waits for GameOver. Global time,
episode, decision and token budgets still bound participation. A direct manual
launch can use the same path with a distinct port/profile/output per actor:

```bash
python3 scripts/launch-agent.py --operation match --match-policy agents-arena \
  --room ABC123 --brain social-scripted --player-name "Agent 01" \
  --port 50051 --output build/social-agent-01 --headless-client \
  --objective "Play autonomously using only information legitimately available to your character."
```

Completion requires matching authoritative GameOver, match identity, summary
presence and winner/reason across distinct accepted actor sessions. Runtime
completion means clean participation, not that every participant won. The
supervisor closes only its owned launchers/clients after completion or bounded
failure. Reports remain diagnostics, never a game-data IPC channel. Controlled
`social-fixture-*` scenarios are mechanics tests, separate from autonomous model
acceptance. See [Social Deduction](social-deduction.md), [match lifecycle](match-lifecycle.md)
and the [validation report](social-deduction-validation/README.md).

## Launch a Sandbox investigation

Build the matching game client, install the Runtime and create a 0.8.0 Sandbox lobby from
the website or Unity lobby. Install the optional Codex provider only if using it:

```bash
build/runtime-venv/bin/python -m pip install -e 'runtime[dev,codex]'
build/runtime-venv/bin/actor-runtime codex status
python3 scripts/launch-investigation-agents.py --room ABC123
```

Select either or both independent Codex providers:

```bash
python3 scripts/launch-investigation-agents.py --room ABC123 \
  --engineer-brain codex --sheriff-brain codex
```

The helper runs separate Engineer/Sheriff launchers with ports 50051/50052,
ephemeral credentials, distinct profiles/databases and independent Brain threads.
Its default output is a fresh timestamped directory under
`build/investigation-validation/`. `--base-port`, `--output`, `--timeout` and
`--max-decisions` bound the run; `--exit-on-complete` closes owned processes after
completion. Shutdown permits normal Skill/provider cleanup before escalating
only its own process groups. The launcher shares readiness metadata, not private
findings or model context.

The ordinary server grants duties. Both must be available before its controller
cycle starts. The Engineer inspects Cooling Control and communicates a private
diagnostic claim through Standard direct messaging. The Sheriff inspects Security
Records and combines its own audit with that claim. Real receiving-client ACK
permits the diagnostic reference as a report citation; it does not make quoted
facts a foreign authoritative own observation. Report validation and physical
repair must both complete for the same case.

The strict helper checks two distinct actors, matching incident IDs, the expected
duties, authoritative `Resolved`/`Restored`/report validation and successful Runtime
objectives. A model's `FinishObjective` alone is insufficient. A third Human can
observe public duties/progress without private views, and can make ordinary
unscoped chat claims. Live acceptance and measurements are recorded in the
[Milestone 6 report](investigation-validation/README.md), separately from local
authority tests. See [investigation](investigation.md) and [evidence](evidence.md).

## Launch the earlier calibration task

The original cooperative calibration scenario remains available in a matching
Sandbox client/lobby:

```bash
python3 scripts/launch-cooperative-agents.py --room ABC123
```

The default runs two deterministic reference Brains through the production Skill
Engine. Select either or both real Codex providers independently:

```bash
python3 scripts/launch-cooperative-agents.py --room ABC123 \
  --coordinator-brain codex --technician-brain codex
```

The helper uses ports 50051 and 50052, separate ephemeral credentials generated by
the single-agent launchers, and fresh timestamped output under
`build/cooperative-validation/`. Each role has its own `memory.sqlite3`, reports and
logs. `--base-port` changes the pair; `--output` must name an empty directory.
`--exit-on-complete` closes both clients after both objectives terminate. Otherwise
they remain available for observation until Ctrl-C. SIGTERM/Ctrl-C first permits
Runtime/provider/Skill cleanup and then closes owned clients.

A third Human client may join to observe public task progress and movement. For
deterministic tests with an observer, join after the participants are assigned or
select the intended technician explicitly; the game does not infer social roles
from an arbitrary display name. The scripted example prefers the example peer named
`Technician` when it is already in its permitted roster.

## Separate manual launches

The existing single-agent helper still works. Give independently launched agents
different ports, profiles, output paths and database paths:

```bash
python3 scripts/launch-agent.py --room ABC123 --brain calibration-technician \
  --player-name Technician --port 50052 --output build/manual-tech \
  --memory sqlite --agent-profile technician --memory-database build/manual-tech/memory.sqlite3 \
  --objective "Wait for your calibration assignment, obtain authorization privately, and calibrate in Engineering."
```

Use `calibration-coordinator` for the deterministic coordinator, or `codex` with a
natural-language objective for a model-driven agent. Codex uses the dynamically
discovered compatible Skill catalog; it receives no predefined Skill sequence.

## Lifecycle and scope

Actor IDs are scoped to a match connection and are separate from local memory
profiles. Reconnection does not turn consumed messages into new arrivals. Closing
a participant's Runtime/game control or its multiplayer connection cancels an
unfinished cooperative task. Completed task evidence remains completed. A new
lobby process starts fresh; there is no cross-lobby mailbox or task restoration.

Engineer/Sheriff duty selection is fixed while an incident is active. Losing a
required participant or its control cancels unfinished work and revokes repair
and private views; a resolved case survives later disconnects. Pre-alarm duty
loss resets controller preparation. An incident is a game-specific one-case-per-
lobby lifecycle, not a persistent cross-lobby case archive.

Ordinary chat is not automatically durable social memory. Social participation
can retain bounded private claims, last-seen observations and explicitly
unverified hypotheses across its own episodes, with provenance and expiry.
Scope binding prevents another match's memory from becoming current evidence.
This provides neither shared cognition nor a hidden-role/truth oracle; private
contents and live action identifiers stay out of automatic SQLite evidence and
public diagnostics. Only legitimate terminal disclosure permits a postgame
summary to become public memory.
