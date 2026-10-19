# Milestone 5 cooperation validation

This report covers game 0.6.0 / Runtime 0.4.0 and the Milestone 5 specification.
**The main live cooperation demonstration passed:** all four remote scenarios
meet strict acceptance, including two independent real CodexBrains. Both agents
finished only with matching authoritative Completed evidence; each retained an
independent Runtime, identity and memory. A third Human verified public progress
without private access, and actual Objective cancellation also passed.

All four actual faults, final-client 13/13 prediction checks and native Human
chat sending/receiving passed. macOS/Linux builds succeeded and the matching
atomic VPS release is deployed. **Both hosted CI workflows passed for functional
code commit `25402be`.** The exact tested code is identified below; subsequent
documentation-only revisions can be checked through the dynamic main-branch link.

Only safe aggregates are published here. No authorization, bridge credential,
chat body, Objective prose, private working context, SQLite database or raw log
is included. Scripted/synthetic success is not evidence of two live CodexBrains.

| Evidence | Scope | Result |
| --- | --- | --- |
| [scripted-summary.json](scripted-summary.json) | Two independent scripted providers using real Skills, Unity clients and remote authoritative multiplayer | Both Objective results succeeded; both clients report Completed |
| [package-smoke.json](package-smoke.json) | Fresh installed Runtime wheel; deterministic tests without Codex/Jev SDKs or model authentication | 316/316 passed, 0 skipped |
| [Mixed A: Codex coordinator + scripted technician](mixed-a-summary.json) | Live model/game integration, strict acceptance | Both Objectives passed and both authoritative tasks Completed; five actual Codex turns |
| [Mixed B: scripted coordinator + Codex technician](mixed-b-summary.json) | Live model/game integration, strict acceptance | Both Objectives passed and tasks Completed; six actual Codex turns |
| [Codex + Codex](codex-summary.json) | Required main demonstration, two real independent providers | All 11 strict checks passed; five/six model turns and four/five successful Skills |
| [Authoritative server metadata](server-evidence.json) | Independent dedicated-server trace for all four accepted cases | Start→Inspect→private communication→Submit/Completed; no content or authorization |
| [Third Human observer](human-observer.json) | Independent client present before both scripted agents; public progress and private filtering | Public Completed, zero private messages/diagnostic results, no private assignment |
| [Final-client prediction/control](human-prediction.json) | One-client development probe, matching final build | Connected, completed, 13/13 checks, zero errors/warnings; diagnostic intent is not native key latency |
| [Native Human chat](human-chat.json) | Sender composition paste, recipient selection/Send and receiver inbox view | One ServerAccepted/ClientReceived each; one received message; text focus suppressed movement; body omitted |
| Current-build Human input/terminal/message controls | Native manual regression | D/S Main Hall→Engineering, T inbox and selected console checked; no controlled native latency claim |
| [Objective cancellation](cancel-safety.json) | Actual participant Objective cancellation while both clients/bridges remain live | Both Objectives terminate, authoritative task Cancelled, commands stable, post-cleanup zero velocity |
| [Live fault coverage](fault-coverage.json) | Cancel, bridge, abrupt participant and isolated match-server loss | All four requested cases passed; protected Human lobby unchanged |
| [Deployment](deployment.json), [health](health.json) | Matching final macOS/Linux builds, atomic server switch and healthy services | Source c289e1a; release 20261019T1804Z-c289e1a; healthy rooms preserved |
| [Controlled admission retry](admission-retry.json) | Two pending reservations in a fresh max-two-player lobby | Ordinary Human client retried 409 five times, then 200 and actor spawn at 63.013 seconds |

Earlier startup failures are retained separately. One mixed-A attempt never
connected the technician to a Runtime. Four fast retries could exhaust while a
disconnected max-two-player fault lobby still held a pending admission reservation
for 45 seconds; HTTP 409 was independently observed in nginx. Current source
therefore uses at most eight normal admission attempts, with exponential
2.5→5→10→20-second capped backoff, the original 0.5-second initial delay and a
90-second retry window. A 20-second connection watchdog releases a stuck attempt.
The final client/server rebuild and remaining fault reruns now passed. Separate
intermittent UTP startup failures have not been fully explained; they are not all
attributed to the HTTP reservation issue.

A [controlled live admission experiment](admission-retry.json) independently filled
two reservations, then launched an ordinary Human client. That client received
five HTTP 409 outcomes, then one 200 and an approved actor after 63.013 seconds,
within the 90-second outer window. It reported approximately 119.09 FPS, no
errors/warnings and enabled keyboard input; its owned client and exact owned
server were cleaned. This validates HTTP reservation-expiry backoff, not a fix or
root cause for the original UDP/UTP failure. The wrapper's original exit code 1
was its final protected-room 404 after the parent independently retired the old
Human client. The reconciled report preserves that fact and makes **no unchanged
protected-lobby claim for this separate experiment**.

The supervisor waits for the technician's own connected game/bridge metadata
before launching the coordinator. An intermediate aggregate also failed because
a post-completion bridge reconnect rotated the displayed session ID; diagnostics
now retain `lastAcceptedSessionId`. All four accepted aggregates below pass the
corrected identity checks. Failed startup/diagnostic/fault-setup attempts are not
relabelled as successful runs.

## Reproduce and collect evidence

Build matching 0.6.0 clients/server and create a fresh ordinary lobby for each
scenario. Install the Runtime and, for model scenarios, the optional official
Codex provider. Check authentication through the existing status command; do not
copy credentials into reports.

```bash
build/runtime-venv/bin/python -m pip install -e 'runtime[dev,codex]'
build/runtime-venv/bin/actor-runtime codex status

python3 scripts/launch-cooperative-agents.py --room ABC123 \
  --output build/cooperative-validation/scripted-new --exit-on-complete

python3 scripts/launch-cooperative-agents.py --room DEF456 \
  --coordinator-brain codex --technician-brain scripted \
  --output build/cooperative-validation/mixed-a-new --exit-on-complete

python3 scripts/launch-cooperative-agents.py --room GHI789 \
  --coordinator-brain scripted --technician-brain codex \
  --output build/cooperative-validation/mixed-b-new --exit-on-complete

python3 scripts/launch-cooperative-agents.py --room JKL012 \
  --coordinator-brain codex --technician-brain codex \
  --output build/cooperative-validation/codex-new
```

Use real six-character lobby codes in place of the examples. Each output directory
must be empty. The helper gives each role a separate Runtime, Unity client,
loopback port, ephemeral bridge token, profile and SQLite file. `--base-port`
changes the default 50051/50052 pair. Each Codex provider creates its own SDK
instance/thread. Startup readiness reads only connection metadata; the supervisor
does not exchange assignment, message, authorization or context between agents.
With the final command, leave clients open for observation and use Ctrl-C for
owned-process cleanup. The helper's Objective-success notification is not, by
itself, authoritative gameplay acceptance.

After the run has stopped updating its reports, generate the public aggregate:

```bash
python3 scripts/summarize-cooperation.py build/cooperative-validation/codex-new \
  --output build/cooperative-validation/codex-new-summary.json --require-success
```

The current summarizer requires both `objective.passed=true` with `succeeded`
status, both authoritative client task statuses `Completed`, the same nonempty
task ID, distinct/matching actor and bridge-session IDs, distinct loopback Runtime
endpoints, separate actual database files/configurations and structural SQLite
redaction checks. `FinishObjective` alone never passes. Exit code 1 means that
acceptance remains incomplete or failed; without `--require-success`, partial
metrics can still be inspected with `passed=false`.

All four published aggregates use the current strict endpoint,
identity, database and completion checks. Their `actual_pids_verified=false`
field describes the offline summarizer, which does not inspect processes.
Separate owner-verified [scripted](scripted-processes.json),
[mixed-A](mixed-a-processes.json), [mixed-B](mixed-b-processes.json) and
[two-Codex](codex-processes.json) launch metadata records two distinct Runtime
PIDs and two Unity PIDs per run, separate ports, profiles and database paths.
Credential values/fingerprints and absolute database paths are omitted from the
public projection. Keep underlying failed runs separate and publish only reviewed
safe aggregates.

## 1. Communication capability architecture

Communication is an optional, independently versioned Standard extension. Core
does not assume chat, players or a particular game. The game advertises typed
peers, send-message, message-received and delivery-result capabilities through the
existing manifest and local full-duplex bridge. Both local agents cooperate only
through the authoritative multiplayer server. See [communication](../communication.md).

## 2. New Protobuf schemas

- `proto/actor/standard/communication/v1/communication.proto`: typed send,
  received-message, delivery-result and authorized-peer payloads.
- `proto/extensions/agents_arena/v1/calibration.proto`: own assignment/task state,
  separate public progress, private diagnostic/action results and typed
  Start/Inspect/Submit/Cancel commands.

Normal generation emits C#, Python, descriptor resources, schema hashes and
manifest helpers. Existing StationSignal remains a separate game feature.

## 3. Extension and application versions

| Component | Version | Evidence/status |
| --- | --- | --- |
| ACTOR Core protocol | 1.0 | Unchanged |
| Standard movement extension | 0.1.0 | Unchanged; installed-wheel probe |
| Optional Standard Communication | 0.1.0 | Installed-wheel probe |
| Agents Arena extension | 0.3.0 | Installed-wheel probe |
| Python Runtime | 0.4.0 | Fresh installed wheel |
| Agents Arena client/server contract | 0.6.0 | All four manifests and matching [deployed assembly identities](deployment.json) |
| Admission protocol | 3 | Unchanged |
| Optional official Codex SDK | 0.162.1 | Pinned integration; this deterministic package probe does not install/use it |

## 4. Server-authoritative messaging

`NetworkCooperation` accepts owner-authorized reliable requests on the existing
approved player object. `CooperativeSession` supplies identities and current
server physical positions to the pure `CooperativeAuthority`. The authority
validates requests and creates server IDs, ordering and outcomes. The command has
no sender-ID field: connection approval and ownership determine the sender.
Neither Runtime can change server task fields or supply another actor's identity.

## 5. Private visibility

Direct content is sent to the actual recipient's owned NetworkBehaviour through
a server-invoked owner-targeted RPC. Only the sender receives its delivery receipt.
Private assignment/diagnostic events use the same recipient boundary. Public
progress has a separate schema without authorization. No global chat/code
NetworkVariable exists. The adapter publishes received content under the
recipient's own entity ID, while sender identity remains typed payload metadata.
This defines game visibility, not encrypted NGO transport. The actual
third-client check is recorded in section 21; broader live adversarial/fault
cases are recorded with their observed scope in section 23.

## 6. Recipient selection and validation

The server advertises approved online peers and `direct`/`station` channels.
Direct messages require an actual permitted recipient; station requests use an
empty recipient and produce per-recipient delivery payloads. Validation includes
the match instance, bounded identifiers, 512 UTF-8-byte text limit, 1–300-second
TTL, per-owner/global rate gates, ACK rate gate and bounded pending/replay caches.
Declared sensitive messages and issued task authorization cannot use station
broadcast; task authorization can go only from coordinator to assigned technician.

## 7. Delivery confirmation

Local bridge command acceptance, authoritative `ServerAccepted`, recipient-game
`ClientReceived` ACK and actual Runtime/Brain processing are distinct states.
Station `ClientReceived` requires every actual recipient ACK. Server UUIDs support
deduplication; correlation replay returns the latest outcome without duplicate
delivery. Reusing a correlation for another command type/payload is rejected.
Neither an RPC send nor an ACK proves that a Brain understood the message.

## 8. SendMessage and WaitForMessage

`actor.send-message` validates typed arguments and advertised peers/channels,
sends through the existing command stream and waits for a matching authoritative
result. Local acceptance is insufficient. `actor.wait-for-message` awaits a
matching sender/channel/task/correlation without periodic model calls. Its bounded
WorldState inbox retains early arrivals, orders and deduplicates server identities,
expires old items and consumes each matching message once. Timeout, cancellation
and disconnect end the foreground Skill normally.

## 9. EventRouter and ContextBuilder

The installed optional communication package registers EventRules and bindings.
Ordinary message prose is routine, untrusted external game evidence; a sender's
urgent wording cannot create trusted preemption authority. Relevant sender/task,
recency and current-Skill evidence receives priority within existing context byte
and item budgets. Codex sees it through `ExternalMessage`, never as replacement
developer instructions or a new trusted Objective.

## 10. Assignment

An approved actor at the Main Hall terminal requests a different online partner.
The server assigns the caller coordinator and the selected actor technician.
Participants receive their own private assignment/state; an unassigned client
receives no private task membership. Public progress is separate. Runtime Skills
wait for these authoritative observations/results instead of assigning roles locally.

## 11. Task state machine

Normal server progression is `Available → AwaitingDiagnostic →
AwaitingAuthorization → Completed`; `Failed`, `Expired` and `Cancelled` are
terminal. The authority owns the five-minute deadline, three attempts, two-second
attempt cooldown and revisions. Nonterminal work is cancelled on participant
disconnect or Runtime-control loss. Completed evidence persists after normal
Runtime completion/disconnect. Correlation replay cannot restart a terminal task.

## 12. Hidden diagnostic information

The server creates a fresh 128-bit, 32-hex authorization for the task. Only the
assigned coordinator at the live Diagnostic Terminal can inspect it. The
technician obtains it through explicit authorized direct communication. It is
absent from public state, public logs and observer observations. Working Skill
results use sensitive/session-only policy; public diagnostics and persistent
memory retain redacted metadata. No actual authorization is included in this report.

## 13. Unauthorized interactions

The server independently checks ownership, task/match identity, distinct assigned
roles, active state, room, finite physical capsule position, distance from the
actual live terminal, expiry, attempts and cooldown. Completion additionally
requires the assigned technician's client ACK of the permitted authorization
transfer. Pure-authority tests cover forged/wrong roles, observer access,
wrong/far/nonfinite poses, missing/moved terminals, malformed identifiers,
incorrect/expired authorization, replay, rate/capacity bounds and stale matches.
Actual live fault coverage is recorded in section 23.

## 14. Game-specific Skills

The dynamically discovered Arena package adds `start-calibration`,
`wait-for-calibration-assignment`, `inspect-diagnostic`, `submit-calibration` and
`wait-for-calibration`. They share the existing typed registry, scheduler,
observation/event path and navigation. Coordinator approach `(0.9, -2.4)` leaves
the technician's x=0 route free; Engineering approach remains `(0, -7)`. Cleanup
zeros movement; no Skill teleports, assigns state or bypasses server range checks.
See [cooperative tasks](../cooperative-tasks.md) and [game world](../game-world.md).

## 15. Two ScriptedBrains

The [strict final remote run](scripted-summary.json) used two independent production
Runtime/Skill pipelines. Both passed, with authoritative Completed at revision 3
for public task `8289c04e28114347b5c7d90ee4a6a66e`.

| Metric | Coordinator | Technician |
| --- | ---: | ---: |
| Objective status | succeeded | succeeded |
| Objective duration, seconds | 15.036 | 21.744 |
| Decisions | 6 | 6 |
| Successful / failed / cancelled Skills | 5 / 0 / 0 | 5 / 0 / 0 |
| Skill duration total, seconds | 14.803 | 21.436 |
| ACTOR commands | 63 | 214 |
| Client errors / warnings | 0 / 0 | 0 / 0 |
| Runtime errors | 0 | 0 |

The technician navigated for 10.882 seconds, then `wait-for-message` completed in
0.001356 seconds: the previously delivered authorization survived arrival before
the waiter. The sender recorded a real recipient-client ACK. This proves the
scripted flow through the actual multiplayer path; it is not a Codex result.

## 16. Codex plus ScriptedBrain

**Mixed A passed strict acceptance.** The
[accepted aggregate](mixed-a-summary.json) records both authoritative Completed
states at revision 3 for task `803bb89cb5c34680b50e7a3149f750d9`, both successful
Objectives and all identity/endpoint/database/redaction checks. The model selected
four successful Skills: StartCalibration, InspectDiagnostic, SendMessage and
WaitForCalibration. It did not need the scripted provider's extra navigation call.
All five real Codex turns completed without tool calls or interrupted turns.

| Mixed-A metric | Codex coordinator | Scripted technician |
| --- | ---: | ---: |
| Objective duration, seconds | 38.893 | 44.069 |
| Decisions / real Codex turns | 5 / 5 | 6 / 0 |
| Successful / failed / cancelled Skills | 4 / 0 / 0 | 5 / 0 / 0 |
| Skill duration total, seconds | 4.468 | 43.810 |
| Client errors / warnings | 0 / 0 | 0 / 0 |
| Runtime errors | 0 | 0 |

**Mixed B also passed strict acceptance.** The
[reverse-direction aggregate](mixed-b-summary.json) records both Completed at
revision 3 for task `80f5d33c37914545ad5723ed2141f9be`, both successful Objectives
and all 11 checks. The scripted coordinator completed five Skills in a
46.467-second Objective. The real Codex technician used six turns and five
successful Skills in 55.231 seconds; its Skill-duration total was 10.232 seconds.
It selected WaitForAssignment, WaitForMessage, SendMessage, NavigateToRoom and
SubmitCalibration. Both clients and Runtimes recorded zero errors, client
warnings were zero, and each recipient received one direct message.

## 17. Two independent live CodexBrains

**Passed with two actual independent Codex providers in remote multiplayer.**
The [accepted aggregate](codex-summary.json) has all 11 checks true; the
[separate PID record](codex-processes.json) has two Runtime and two Unity PIDs,
independent ports, profiles, credentials and database paths. Both provider reports
are `codex`, with five/six unique real SDK turns. No model tools were called and
no turn was interrupted. The launch/provider architecture creates separate SDK
instances/threads; no shared WorldState/context, report reading or direct Python
channel transfers the authorization.

The coordinator selected StartCalibration → InspectDiagnostic → SendMessage →
WaitForCalibration. The technician selected WaitForAssignment → SendMessage →
NavigateToRoom → WaitForMessage → SubmitCalibration. The different sequences were
model-selected from the typed catalog, not an injected scripted SkillCall list.
Only the coordinator received a diagnostic result; its task-bound private message
reached the technician, whose later server-validated submission completed the task.
The technician's message waiter completed in 0.001247 seconds after navigation,
showing the early-delivery inbox path also worked in this real model run.

| Two-Codex metric | Coordinator | Technician |
| --- | ---: | ---: |
| Objective status | succeeded | succeeded |
| Objective duration, seconds | 50.597 | 56.128 |
| Actual SDK turns / decisions | 5 / 5 | 6 / 6 |
| Successful / failed / cancelled Skills | 4 / 0 / 0 | 5 / 0 / 0 |
| Skill duration total, seconds | 15.309 | 10.204 |
| Client errors / warnings | 0 / 0 | 0 / 0 |
| Runtime errors / pending cognitive cleanup tasks | 0 / 0 | 0 / 0 |
| Authoritative task status / revision | Completed / 3 | Completed / 3 |

The shared public task ID is `2e1afaa25ba045f7831fb8524903c4b9`. This is live
model/game evidence, distinct from the deterministic package and scripted tests.

## 18. Codex turns and usage

The scripted scenario made **zero Codex turns** and used no model authentication.
Mixed A's coordinator made **five actual Codex turns**, totaling 27.890 seconds
of recorded turn duration, with zero interruptions/tool calls. Its reported
usage totals are 97,972 input, 63,616 cached input, 703 output and 98,675 total
tokens; cached input is a subset, not added again to input. The technician used
zero model turns. These are provider-reported totals, not estimates from context
bytes or sums of repeated cumulative turn usage.

| Actual model provider | Turns | Turn-duration total, s | Input tokens | Cached input | Output tokens | Total tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Mixed A coordinator | 5 | 27.890 | 97,972 | 63,616 | 703 | 98,675 |
| Mixed B technician | 6 | 37.676 | 124,051 | 97,408 | 939 | 124,990 |
| Two-Codex coordinator | 5 | 32.038 | 100,282 | 61,184 | 681 | 100,963 |
| Two-Codex technician | 6 | 41.924 | 124,134 | 96,896 | 1,050 | 125,184 |

The two-Codex pair used 11 turns and 226,147 reported tokens in total. Each
provider recorded zero interrupted turns and zero tool calls. The other agents
in the mixed runs are scripted and have no model usage.

## 19. Messages and latency

The scripted coordinator recorded one `ServerAccepted`, one `ClientReceived`,
zero rejected receipts and zero incoming messages. The technician recorded one
incoming private message and zero diagnostic results. The coordinator alone
recorded one diagnostic result. Final scripted technician mean message latency
was **863 ms**; mixed A recorded one direct message/ACK with a **375 ms** mean.
This is receiver wall clock minus server Unix time across hosts, so clock offset
affects it; it is not a synchronized one-way-network measurement or bridge RTT.
Mixed B and two-Codex each exchanged two direct messages: one incoming message
per actor and one ServerAccepted plus one ClientReceived receipt per sender.

| Accepted run | Incoming messages C/T | ServerAccepted C/T | ClientReceived C/T | Mean receive latency C/T, ms |
| --- | --- | --- | --- | --- |
| Scripted | 0 / 1 | 1 / 0 | 1 / 0 | — / 863 |
| Mixed A | 0 / 1 | 1 / 0 | 1 / 0 | — / 375 |
| Mixed B | 1 / 1 | 1 / 1 | 1 / 1 | 113 / 145 |
| Two Codex | 1 / 1 | 1 / 1 | 1 / 1 | 133 / 215 |

C/T means coordinator/technician. All four runs recorded zero rejected receipts;
only the coordinator had a diagnostic result (one versus zero). These are
one-message-per-recipient samples, not latency distributions. Station-message
manual checks remain outside this accepted direct-message flow.

## 20. Completion evidence

In every accepted case both client task states/public progress are `Completed`,
with the same per-case task ID and revision 3, and both independent Objective
results passed. Separate PID metadata supports each two-Runtime/two-client
topology. The [dedicated-server trace](server-evidence.json) independently records
Start/assignment revision 1, accepted Inspect/diagnostic revision 2, typed direct
delivery metadata and accepted technician Submit/Completed revision 3 for the
same actor identities. It contains 10/10/11/11 allowlisted lines for scripted,
mixed A, mixed B and Codex respectively; message IDs are metadata, never text.

Several post-Objective client snapshots show bridge `Error` after the successful
Runtime exited (`RuntimeDisconnected`), with keyboard disabled and completion
retained. The two-Codex technician was still Connected at its saved snapshot.
These states are reported as captured; a completed task does not imply a bridge
must stay connected after its launcher exits. Final source/release identities and
actual faults, prediction and successful functional-code CI are recorded below.

## 21. Third-client privacy and native Human chat

**Observed with a real third client.** Human actor
`11b9e1a84e564d44a821834119d52f8d` was already online in room `DL945F` before
the two scripted participants joined and completed cooperation. Its
[safe diagnostic record](human-observer.json) shows public `Completed`,
`commReceivedCount=0`, `diagnosticResultsCount=0`,
`ownCalibrationAssignment=false`, empty private task ID and private status
`Unspecified`. This checks filtering in an actual third NGO client, in addition
to pure-authority tests; it does not rely solely on Runtime filtering.

The owner used native controls: T opened an empty communications inbox, D/S moved
from Main Hall into Engineering, and E selected the Calibration Console through
the nearby-interaction chooser. The console showed public Completed without an
authorization or private assignment. **The observer did not click Submit, and a
disabled Submit button is not claimed.** Its grey theme alone does not establish
that it was disabled. Server rejection for unassigned actors is covered by the
pure-authority tests, separately from this native observation. The screenshots
contain no participant message/code/composition content.

![Human observer sees public Completed and an empty inbox](observer-private-inbox.png)

![Human reaches Engineering with no authorization or private assignment](human-engineering-observer.png)

The separate [native Human chat proof](human-chat.json) used T, permitted-recipient
selection, composition focus/paste and Send. The sender recorded one
ServerAccepted and one ClientReceived receipt; Human Peer recorded one received
message and zero diagnostic results. At the accepted snapshots both had enabled
Human keyboards and zero errors/warnings. The owner verified that the sender did
not move while composition was focused, then opened the recipient's T inbox and
saw the message from HumanPrediction. The reliable text-entry check used paste;
automated rapid character typing is not claimed reliable. The message body and
any screenshot containing it were deliberately not retained. FPS snapshots were
approximately 119.07 for the sender over 196.4 seconds and 118.97 for the
recipient over 108.3 seconds; these do not guarantee zero warnings throughout an
open client's lifetime.

Safe peer-startup phases record one actual ordinary-CLI recovery: the first
attempt reached its 20-second deadline, returned to the lobby, obtained fresh
admission and then connected through transport/NGO. This verifies one bounded
recovery, not a universal diagnosis/fix for the original UDP failures.

## 22. Memory and context isolation

The scripted snapshot has distinct actual SQLite files, matching configurations,
one profile/actor/session per file and separate actor/bridge-session IDs.
Each database contains five Skill results plus one Objective result and **zero
observed chat events**. Coordinator/technician each made six memory retrievals
returning 15 entries cumulatively. Private payload fields were redacted: 8/8
coordinator, 4/4 technician; no structural violations were found. These checks do
not prove the absence of an arbitrary paraphrase of a secret.

| Context metric | Coordinator | Technician |
| --- | ---: | ---: |
| Decision-context mean bytes | 7,056.0 | 7,150.5 |
| Decision-context maximum bytes | 9,527 | 9,364 |
| Stored entries / own-profile entries | 6 / 6 | 6 / 6 |
| SQLite main-file bytes | 32,768 | 32,768 |

The final recorded memory counters show one pending operation, so this snapshot
alone must not be described as proof of zero pending memory operations after
shutdown. Mixed A also has distinct files/identities and no stored chat events:
five coordinator entries (four Skills plus Objective), six technician entries,
8/8 and 4/4 redacted private payload fields. Mixed-A context mean/max was
6,955.8/8,813 bytes for the model coordinator and 7,201/9,358 for the technician.
Mixed B also has one actor/profile/session per separate database, six entries per
agent and no stored chat events. Both private-policy audits redact 8/8 fields.

| Additional context metric | Mixed-B coordinator | Mixed-B technician | Two-Codex coordinator | Two-Codex technician |
| --- | ---: | ---: | ---: | ---: |
| Mean context bytes | 7,274.0 | 7,102.5 | 7,220.8 | 7,151.2 |
| Maximum context bytes | 10,260 | 10,019 | 8,813 | 10,021 |
| Stored Skill + Objective entries | 5 + 1 | 5 + 1 | 4 + 1 | 5 + 1 |
| Retrieval calls / cumulative entries returned | 6 / 15 | 6 / 15 | 5 / 10 | 6 / 15 |
| Redacted / total private payload fields | 8 / 8 | 8 / 8 | 8 / 8 | 8 / 8 |

The two-Codex pair has separate actual database files/configurations, distinct
actor/session identities and independent Runtime PID/ports. Each file has one
actor, profile and session; both contain zero observed chat-event rows and pass
the structural redaction audit. Their working contexts are independently built
per Runtime; no shared database or direct Python communication supplies the
authorization. These observed isolation checks do not inspect hidden model
reasoning or assert cryptographic process isolation.

## 23. Cancellation, reconnect and other failures

Deterministic suites cover early arrival, ordering/deduplication, TTL, inbox and
replay limits, unavailable channels, invalid recipients, local-vs-server receipt
semantics, wait timeout/cancel/disconnect, privacy projection, stale task/match,
role/range rejection, participant loss and preservation of Completed. Objective
finalizer tests exercise the same scheduler/live bridge and bounded plugin failure.
These are automated fixtures, not a claim that every fault was injected into this
release's running VPS multiplayer.

**Actual cancellation passed:** the [safe fault report](cancel-safety.json) records
the technician's `ObjectiveRunner.cancel` while its foreground Skill awaited a
message and both game/bridge sessions were live. The technician Objective became
Cancelled, its partner Objective failed safely, neither retained an active Skill,
and the server task became Cancelled with `participant_cancelled`. Both
Objectives reached terminal states after 0.309 seconds; the first authoritative
terminal sample was recorded at 0.412 seconds. One explicit cancellation command
was added by the technician, then both command counts stayed stable for a second.
Both Unity clients were alive and stopped at the later cleanup sample; all owned
worker groups were drained. The report explicitly does **not** measure movement
fault-response latency from those terminal-state/sample times.

**All four requested actual fixture cases passed**, with separate
[coverage](fault-coverage.json) and metadata-only reports. An initial bridge-fault
setup failure is retained as a failure, not counted as a fault-injection pass;
the accepted retry uses the final startup-hardening build.

| Actual fault | Both Objectives terminal, s | Authoritative terminal or unavailable sample, s | Observed result |
| --- | ---: | ---: | --- |
| [Objective cancellation](cancel-safety.json) | 0.309 | 0.412 | Task Cancelled / participant_cancelled; both bridges remain live |
| [Coordinator Runtime bridge loss](bridge-safety.json) | 0.462 | 0.513 | Surviving partner sees Cancelled / participant_control_unavailable; lost bridge session closed |
| [Abrupt coordinator client loss](participant-safety.json) | 30.483 | 30.536 | Only the verified owned child is killed; surviving partner sees Cancelled / participant_disconnected |
| [Isolated match-server loss](server-safety.json) | 8.269 | 8.270 | Both bridge sessions close and Objectives fail safely; prior cached task state is not a new server transition |

The abrupt participant case uses the existing 30-second NGO disconnect timeout;
its 30.5-second detection is not an immediate game-task cleanup claim. Server loss
cannot produce a fresh authoritative Cancelled state: its last cached
AwaitingDiagnostic remains visible in the report, while session unavailability
terminates the Skills/Objectives. The safe projection labels that distinction.
Every case has stable command counts after the one-second check, no active Skill
after termination, stopped surviving Unity clients at the post-cleanup sample and
drained owned worker groups. These later velocity samples are not measured
movement fault-response latency. The run owner separately checked the protected
Human lobby unchanged after each fault; only each fixture's owned match/process
was targeted. Broader reconnect, stale-match, wrong-role and message-injection
coverage remains the deterministic tests described above, not an invented live
run of every adversarial variant.

## 24. Backend, Unity, SDK and Runtime checks

| Check | Available evidence | Verification |
| --- | --- | --- |
| Installed Runtime wheel | [316/316 in 40.35 seconds](package-smoke.json), zero skipped, no optional provider SDKs/auth | Deterministic package snapshot |
| Final local Python total | 326/326 in 41.56 seconds: 316 Runtime + 10 script tests | Functional-code SDK/Runtime CI also passed |
| Final local .NET total | 125/125: 107 SDK + 18 backend | Functional-code hosted workflows also passed |
| C# SDK, including pure authority | 107/107, including 34 authority cases | Included in final .NET total |
| Protocol/schema freshness | 50 confirmed checks | Functional-code SDK/Runtime CI passed |
| Summary privacy/acceptance helper | 10/10 offline tests; no services/model | Included in final Python total |
| Cooperative fault helper | 9/9 offline self-tests; all four requested actual cases passed | [Safe live coverage](fault-coverage.json) |
| Backend | 18/18 confirmed for the current code | Not inferred from historical TRX |
| Unity scene/compilation | Prepare and final c289e1a macOS/Linux builds succeeded; geometry/terminal approach checks passed | Assembly hashes in deployment evidence |
| Prediction/control regression | Final-client P probe 13/13, connected/completed, no errors/warnings | [Safe report](human-prediction.json) |

Important deterministic suites include `test_communication_skills.py`,
`test_privacy.py`, `test_objective_finalizers.py`, `test_calibration_events.py` and
`CooperativeAuthorityTests.cs`. They run without Codex credentials or paid calls.
The final clean Python rerun passed 326/326 in 41.56 seconds after the synthetic
`test_scripted.py` boundary was aligned from -6 to the actual Unity boundary
-5.67, retaining the exact two ordered-transition assertion. Runtime production
behaviour was unchanged by that fixture correction; the earlier concurrent-load
failure is not hidden as a pass.

The run owner independently verified that Core and Standard movement Protobuf
bytes are unchanged from `f91efbf`, as are original PNGs, offline Main Hall and
the original ActorCharacter prefab. Motors/prediction remain unchanged. The
movement-input change is the required `PlayerInputSource.TextEntryFocused` gate
that prevents character movement while composing chat.

## 25. Performance

| Scripted measurement | Coordinator | Technician |
| --- | ---: | ---: |
| Mean client FPS | 112.85 | 111.14 |
| Maximum recorded bridge pump, ms | 3.348 | 10.193 |
| Command-result RTT median, ms | 6.488 | 5.957 |
| Command-result RTT p95, ms | 12.828 | 10.185 |
| RTT samples | 63 | 214 |
| Runtime CPU, one-core percent | 4.36 | 4.23 |

| Mixed-A measurement | Codex coordinator | Scripted technician |
| --- | ---: | ---: |
| Mean client FPS | 110.61 | 111.64 |
| Maximum recorded bridge pump, ms | 26.338 | 4.587 |
| Command-result RTT median, ms | 6.859 | 5.850 |
| Command-result RTT p95, ms | 32.969 | 14.682 |
| RTT samples | 61 | 203 |
| Runtime CPU, one-core percent | 6.26 | 3.32 |

| Mixed-B measurement | Scripted coordinator | Codex technician |
| --- | ---: | ---: |
| Mean client FPS | 117.65 | 117.81 |
| Maximum recorded bridge pump, ms | 3.022 | 2.909 |
| Command-result RTT median, ms | 5.269 | 5.541 |
| Command-result RTT p95, ms | 16.861 | 9.077 |
| RTT samples | 68 | 191 |
| Runtime CPU, one-core percent | 3.07 | 5.66 |

| Two-Codex measurement | Coordinator | Technician |
| --- | ---: | ---: |
| Mean client FPS | 114.54 | 115.06 |
| Maximum recorded bridge pump, ms | 3.495 | 3.554 |
| Command-result RTT median, ms | 5.223 | 5.388 |
| Command-result RTT p95, ms | 10.446 | 9.797 |
| RTT samples | 63 | 193 |
| Runtime CPU, one-core percent | 6.03 | 5.98 |

The Human observer's idle snapshot averaged approximately **119.37 FPS over
272.8 seconds**. The earlier approximately 111 FPS scripted sample included a
13-second startup/cooperation interval; the strict final scripted run above spans
approximately 15/22-second Objectives, and mixed A approximately 39/44 seconds.
Mixed B spans approximately 46/55-second Objectives and two-Codex approximately
51/56 seconds. These have different workloads and averaging windows: **this is
not a controlled performance comparison** and cannot isolate a messaging
regression. Some earlier medians are above the previous approximately 5.2 ms,
with a larger mixed-A coordinator RTT/pump tail. The two-Codex medians are
5.223/5.388 ms with approximately 115 FPS, but that single run is not statistical
equivalence to the old baseline. FPS is a report mean, not a worst-frame
percentile; p95 uses nearest rank over retained RTT samples.

Native Human D/S traversal and context-sensitive terminal interaction were
observed. A controlled input-to-render benchmark and identical workload
comparisons were not recorded, so statistical responsiveness equivalence is not
claimed. The [accepted final-client prediction probe](human-prediction.json)
passed **13/13** in a fresh otherwise-empty lobby: presentation response,
authoritative movement, release/reconciliation, reversals, wall/slide/escape,
normalized speed and resistance to client-root tampering. It recorded 23.413 ms
to first local-presentation sample, 340.393 ms to its authoritative-root sample,
40.438 ms release response and 0.004574 world-unit idle reconciliation error.
The client remained connected and reported no errors/warnings.

Its [safe client snapshot](human-prediction-client.json) shows Human mode,
keyboard enabled, Main Hall and approximately 117.36 mean FPS over the first
20.73 seconds. The P probe temporarily substitutes diagnostic movement intent,
then restores normal `PlayerInputSource`; these are prediction/control samples,
**not native keyboard-to-render latency**. An earlier one-shot probe startup
failed to connect and is retained separately, not counted as a physics pass or
silently overwritten by the accepted retry. The probe client was initially left
open, then used for the native chat check and closed by its owner; ordinary
Human Peer remains playable in room `V2J6AA`. Empty-lobby isolation avoids other
bodies invalidating wall checks; no new model/CPU workload was required for the probe.

## 26. Git commits

The specification names baseline `f91efbf`. Published test/evidence commit
`7a13100291d6d5a297eaa9775127b31c4b480cce` added isolated-run, retention and
cleanup verification. Final built/deployed startup source is
`c289e1a00f84d7e7f32835d872033d87d2f94818`
(`fix(lobby): back off fresh admission retries until reservations expire`).
Functional/test code is `25402be5b2957015550e36a1a309c150395ab5b3`
(`test(runtime): align delayed room transitions with live capsule geometry`),
whose two successful hosted workflows are linked below. Documentation-only
follow-ups are tracked through the latest main-branch runs.

## 27. GitHub CI

Functional/test code commit `25402be` passed both
[Control Plane CI](https://github.com/fpetrov/Actor/actions/runs/39299875098) and
[SDK/Runtime CI](https://github.com/fpetrov/Actor/actions/runs/39299875159), verified
by the run owner. Earlier `7a13100` also passed
[Control Plane CI](https://github.com/fpetrov/Actor/actions/runs/39298771054) and
[SDK/Runtime CI](https://github.com/fpetrov/Actor/actions/runs/39298771059).
These are actual successful hosted runs for identified commits, distinct from
local passes. See [latest main runs](https://github.com/fpetrov/Actor/actions?query=branch%3Amain)
for subsequent documentation-only commits; an earlier run is not claimed to have
tested a later commit hash.

## 28. Deployment

All four accepted remote-authoritative scenarios record game 0.6.0. The
[deployment record](deployment.json) confirms final macOS client and Linux server
build success from `c289e1a`, and atomic VPS server release
`20261019T1804Z-c289e1a`. The deployed server assembly hash matches the local
assembly, `7f94ae1659cb0c96e900218cb3409b6731896318878ece05e4a36d10b53fe1b4`;
the client assembly hash is
`e7cd7be9f7a9fdbe089cb934981add439b1ed04321be8f49ae2f5c2e1fd33868`.
These are assembly identities, not a claim to hash every asset in the app bundle.
The [health snapshot](health.json) reports healthy API, game version 0.6.0,
admission protocol 3, five-server limit and both services active.
The server symlink switch affects new matches; no Control Plane restart or healthy
room interruption was required, and the existing Human match was preserved.
Both gameplay peers must use the matching network-behaviour layout; an older
client is not claimed compatible with the new game contract.

## 29. Remaining limits

- No durable offline mailbox, cross-lobby restoration, shared agent memory or
  direct Runtime-to-Runtime transport.
- Game-private visibility does not imply NGO transport encryption or secrecy from
  the authoritative server/authorized recipient's machine.
- Client ACK does not mean model processing. A message arriving before bridge
  negotiation has no durable replay guarantee; readiness matters.
- Five-minute authorization/retention windows and bounded retries are intentional;
  a model taking longer may encounter a structured expiry instead of completion.
- Process counts in the metrics aggregate are expected topology; its
  `actual_pids_verified=false` remains truthful. Separate published launch metadata
  records the actual two Runtime/two Unity PIDs for each of the four accepted
  runs. Launcher/Codex helper processes are excluded from those four game
  processes, not asserted absent.
- The four accepted snapshots are individual runs, not broad performance
  distributions or proof of every conceivable fault. Four actual faults,
  startup hardening, local regressions, prediction, native Human chat,
  deployment and functional-code hosted CI are recorded as passed. This is not
  statistical performance equivalence or universal transport recovery.

## 30. Recommended next milestone

With the Milestone 5 functional demonstration and release checks recorded, a
focused next milestone is reusable cooperation recovery: bounded reassignment
and resumption when a partner becomes unavailable, with explicit server policy,
task-scoped working knowledge and fault acceptance. Keep it an optional game/task
extension; do not add shared cognition or a general social ontology to ACTOR Core.
