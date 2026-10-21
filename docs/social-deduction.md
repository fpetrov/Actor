# Social Deduction

Agents Arena 0.8 adds `SocialDeduction` as a separate room mode. Choose it on
the website or in Unity's Lobby menu. `Sandbox` retains the investigation,
calibration, station-signal and communication rules from earlier milestones.
The mode cannot be changed by a game command. One room runs one match.

The Control Plane validates `Match:SocialDeduction` deployment settings, copies
them when creating a Lobby, persists that immutable snapshot in SQLite, and
passes it to the allocated dedicated server. The signed admission ticket binds
the participant to that server's match. The server selects the separate
`SocialDeduction.unity` scene. Admission protocol remains 3.

## Authority and permitted views

`SocialMatchAuthority` is a pure server rule machine. `SocialMatchSession`
supplies only approved NGO identities, current physical capsule centers,
actual room membership and authored interaction geometry. It applies authority
deliveries to the selected owner's `NetworkSocialMatch`, filters communication,
and creates visibility-controlled body objects. Neither Runtime nor a model
chooses truth, alignment, elimination or the winner.

`CrewRole` and `SocialAlignment` have distinct meanings. Duty remains a public
station assignment in Sandbox. In Social Deduction, both duty and alignment
are hidden from other active players. The server assigns one Impostor among
4–6 ready participants using cryptographic randomness. Independent duty
assignment does not imply alignment. Deterministic seeds are available only
through trusted match-scoped server fixture files; no public request or gameplay
command accepts a seed or assignment.

| Data | Live audience |
|---|---|
| Phase, deadlines, immutable rules, aggregate Crew progress, emergency | All participants |
| Alignment, duty, life, readiness, cooldowns, assigned tasks | That actor's owner |
| Actor poses, bodies, nearby interactions | Recipients with actual radius and line of sight |
| Attack witness | Living observer who saw both participants at the instant of attack |
| Work animation/activity | Living observers who saw the worker; same representation for real/fake work |
| Meeting rollcall, discussion, resolved tally | Meeting participants; no poses or alignments |
| Individual ballot before resolution | Its owner |
| Final alignments, individual outcomes, bounded ordered replay | Postgame only |

The complete visible-world bundle replaces its predecessor, including empty
lists. The adapter clears its current world view after a 0.5-second receive gap.
Runtime's private working knowledge can retain a dated last-seen observation;
that historical location is never a current position. Player statements remain
unverified claims with sender and delivery provenance. There is no station-wide
position roster, cooling feed, diagnostic feed or legacy duty roster in this mode.

## A match

All eligible participants confirm readiness. An incomplete room cannot assign
roles. The authoritative phases are WaitingForPlayers, RoleAssignment,
Exploration, Discussion, Voting, VoteResolution and GameOver. Movement is
available while waiting and exploring; meetings and terminal phases stop server
physics input. Eliminated participants retain their own network endpoint so
their Runtime can wait passively for the team outcome.

The Impostor can kill only a currently visible target within the configured
physical range after its cooldown. A successful kill stops the victim, removes
their live communication and actions, and creates a filtered body without a
killer field. Body reporting requires actual sight and range. Emergency meetings
require proximity to the authored console and obey match/player limits.
See [elimination](elimination.md), [voting](voting.md) and
[visibility](visibility.md) for checks and timing details.

Exploration allows visible direct and proximity communication. Discussion and
voting allow the living discussion audience through the existing Standard
communication extension. Dead actors and spectators cannot send live messages.
Delivery receipts retain their existing meaning: client receipt does not prove
that a Brain read a message.

Crew can win by eliminating every Impostor or reaching the task threshold.
The Impostor can win by parity or an unrepaired life-support emergency. Match
timeout is a draw. Terminal evaluation is irreversible and denies further
gameplay. A player's survival and their team's victory are separate results.

Tasks and emergency repair require continuous physical work near their terminal.
Moving away, losing control, stale/invalid physical state, death or a phase
change interrupts work. Acceptance starts a lease; a later authoritative
completion confirms its outcome. Crew completion is private to its owner while
aggregate progress is public. Impostor fake work has the same visible activity
but contributes no Crew progress. A life-support emergency pauses during a
meeting, then resumes with the remaining time.

## External agents

Use the ordinary built client and an installed local Runtime:

```bash
build/runtime-venv/bin/python scripts/launch-social-agents.py \
  --room ABC123 --players 4 --brain social-scripted \
  --output build/social-match
```

Select `--brain codex` for independent official Codex SDK providers, or use
`--brains codex codex codex codex`. Every participant gets the same trusted
objective, its own process, local bridge credential, port, memory database and
profile. Roles are learned from their own private game observations. The
supervisor does not supply roles, positions, model decisions or a winner.
The launcher uses headless clients by default; `--windowed` explicitly selects
800×600 windows. The room's minimum player count must match the requested group.

The generic [match lifecycle](match-lifecycle.md) composes bounded Objective
episodes with fresh Brain instances and provider threads. Arena supplies the
phase/life interpretation and conditional Skill availability. Phase, life and
terminal events invalidate the previous episode before further commands; the
game server repeats every permission check. Finishing an Objective ends one
episode. Only an authoritative terminal result completes match participation.

The `social-fixture-*` providers are controlled test algorithms. Their explicit
scenarios intentionally constrain behavior to exercise a rule; their wins are
not evidence of autonomous model intelligence. Actual acceptance, exclusions,
failures and model usage are recorded in the
[validation report](social-deduction-validation/README.md).

## MVP boundaries

There is no authenticated resumable player identity yet. A lost game connection
or Runtime control forfeits that participant after assignment. Rejoining with a
new admission creates a stationary spectator; a display name never restores a
hidden role. See the validation report for actual fault results. Rematch,
spectator free-camera, vents, progression, voice chat, inventory, training and
statistical evaluations of social intelligence are outside this milestone.
