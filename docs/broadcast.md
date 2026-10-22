# ACTOR Broadcast v1

Implementation is in progress. This document distinguishes the intended contract
from results that have actually been measured. M8/RC3 validation remains historical.

## Delivery plan

1. Establish official Twitch/OBS access and isolated local capture/audio spikes.
2. Add a separate signed observer admission and server-projected Arena view.
3. Present that view in one windowed client and acknowledge rendered frames.
4. Connect bounded commentary, persistent budgets and a persistent Qwen worker.
5. Integrate the local operator UI, OBS preview, public stream and owned shutdown.
6. Run privacy/regression tests, real local E2E, bounded model benchmarks and faults.

Twitch is the primary delivery path after a successful local preview. Creating a
preview never starts publication. The owner has authorized public testing in this
session; the product still exposes an explicit public-start action and source audit.

## BroadcastObservationPolicy

The observer is a separate read-only session, never a Crew/Impostor, late player,
fake player object, collider, readiness slot or victory participant. Initial v1
supports SocialDeduction only. Core protocol remains engine independent.

Admission uses a separate signed observer audience, a single-use nonce, a bounded
lifetime and a match/session/connection-generation binding. Observer tickets do not
verify as player or resume tickets. Operator issuance is disabled unless configured;
the protected Control Plane route is reachable through an authenticated local/SSH
connection, not the public website. No Twitch credential is used as game admission.

The server creates the allowed view before replication. Observers receive authored
static geometry and a bounded BroadcastView, not player/body NetworkObjects, owner
observations, private messages, private replay, roles, fake-task truth, kill cooldowns,
seeds, player cognition or memory. Cosmetic proxies have no gameplay authority.

The view contains only publicly permitted phase/progress/alarm information, current
actors/bodies in the server-validated radius and LOS, explicitly public discussion,
permitted vote results and a whitelisted authoritative terminal result. An unseen
kill does not create an observer event, revision, camera switch or diagnostic counter.
Body discovery does not establish a killer. Only an independently checked pre-action
visible witness may identify a visible attack. Player claims remain attributed claims.

The initial camera uses a public Hall anchor. Camera requests are server checked;
following is limited to an already permitted actor, with a minimum eight-second
dwell. Automatic choices use only current permitted data and public authored anchors.
No global actor minimap or hidden-event camera director is provided.

Each coherent view is bound to match, broadcast session, connection generation,
view epoch, observer-local sequence, server observation time and freshness. Positions
are replaced rather than merged across loss of visibility. Semantic revisions are
independent of transform ticks. Unknown, stale or revoked views fail closed.

The local bridge publishes a view for cognition only after its coherent camera,
proxies, fog and public UI were rendered at the end of a non-headless frame. This
presentation ACK is distinct from network receipt, cosmetic ledger acceptance, OBS
capture and audio playback. Headless rendering never manufactures this ACK.

Already presented historical observations may be retained only within their explicit
retention, provenance and dependency limits. Source revocation/expiry invalidates
dependent notes and pending speech. Reconnect preserves permitted same-match history
while replacing current state and epochs; a new match clears history and coverage.

## Commentary, memory and spending

The broadcaster has its own bounded WorkingKnowledge scope and immutable evidence
references. It never opens a player's memory database or imports a player policy.
SpokenLedger records completed playback and separately records interruptions; generated
text and finished synthesis are not evidence of what was heard. Without word alignment,
partial playback is a consumed sample range, never a fabricated word prefix.

Selection is deterministic local code. Important events are coalesced; normal pacing,
critical finite reserves and one provider turn prevent event/transform/chat floods from
creating a request per update. The default idle deadline is 20 seconds after the latest
noteworthy observation, actual commentary playback end and preview/live readiness.
It is configurable from 5 to 120 seconds. There is one timer/due flag, not a recap queue.
Useful recent facts and relevant older claims may trigger one recap/callback turn;
empty, stale, covered, expired and budget-blocked selections cause zero model calls.

All modes, retries, repairs, cancellations and optional analysis share one persistent
finite spending ledger. Idle limits are a subset. Reservations precede admission;
actual unique SDK usage reconciles reservations, unknown usage is never zero, and
restart/thread rotation/new match never resets the hourly limit. The local budget is
not the account's global subscription quota. No silent switch to a paid API exists.

The text provider uses official Codex SDK 0.162.1 and existing ChatGPT sign-in,
with exactly gpt-6-luna and the lowest verified supported reasoning setting
(`low` in the measured catalog). A single stream consumer
admits only validated complete speech segments from final-answer text; reasoning,
tools, JSON fragments and diagnostic text never reach speech. Player BrainDecision
contracts remain unchanged. Episodes rotate with bounded permitted context and actual
usage. Model availability and performance require a real bounded benchmark.

The commentator has a separate capability policy from gameplay Brains. A public
account/model preflight precedes an owned, pinned model catalog that disables
model-selected tool registration as well as ordinary feature flags. This matters
because ResponsesLite can advertise tools inside `input.additional_tools`, even
when the top-level `tools` field is absent. An auth-free acceptance probe inspects
both locations using the actual pinned app-server and a local Responses fixture;
its positive control must first demonstrate nonempty tool definitions.

The official SDK merges its environment with the parent. A stdlib-only isolated
launcher replaces the final app-server environment with an allowlist, without
changing the parent or placing secret values in arguments. This is not process
isolation against another program running as the same macOS user. Global Codex
instructions and effective MCP configuration require additional fail-closed
checks; an empty feature table alone is not treated as a privacy guarantee.
The production installation uses a dedicated `codex_home`, defaulting to
`~/.actor/broadcast/codex-home`, with an ordinary official ChatGPT sign-in.
It never copies authentication files or extracts tokens from the main profile.
The presence of global `AGENTS.md` or `AGENTS.override.md` in the selected home
blocks preparation using metadata alone; their contents are never read by ACTOR.

Input reservations include a conservative 12,000-token SDK-prefix allowance,
plus the external context and retained-thread growth. Actual usage remains the
settlement source. Unresolved older reservations can only increase to the
current conservative floor; exact known usage settles once, transactionally.
There is no supported server-side output-token cap in this SDK contract, so the
UI/report must not describe local output bounds as a hard provider limit.

## Speech and OBS

The default is local MLX Audio, Qwen3-TTS-12Hz-1.7B-CustomVoice BF16, Ryan, Russian,
without any voice reference. Quantization or a different model is never a silent
fallback. A separate long-lived worker loads and warms the model once. It accepts
complete short text segments and emits bounded 24 kHz mono PCM chunks over a typed
local channel. Cancellation fences apply before synthesis, chunk delivery and playback.

An isolated local OBS Browser Source renders only allowed subtitles/overlay and voice,
using a bounded Web Audio ring and continuous resampling. Its renderer capability is
different from operator authorization and has no OBS control rights. Playback ACKs
record audio-graph consumption; final-scene local recording separately verifies routing.
No microphone, webcam, desktop, other-window or general system-audio source is allowed.
Game audio is a separate option, disabled by default.

OBS uses protected backups and dedicated `ACTOR Broadcast` profile and scene collection.
Preparing, Match, Ending and Safe scenes contain only audited owned sources. The exact
observer window/PID is checked; loss never falls back to desktop capture. obs-websocket
is authenticated and local-only or protected by a verified firewall. Stream credentials
remain in trusted OBS/Keychain handling, outside prompts, argv, public logs and Git.

The operator service is loopback-only with authenticated state changes, exact Origin/
Host validation, CSRF/rebinding protection and separate renderer credentials. The
public website cannot start local processes, spend quota or control OBS. Chat is
official EventSub input with minimal scopes, bounded filtering and no control authority.

The browser boundary protects against cross-site requests and DNS rebinding. It
does not isolate an untrusted process already running as the same macOS user.
The CLI uses a separate exact owned Keychain item and authenticated loopback header.

Chat is enabled by default and requires a registered public Twitch client plus
the official `user:read:chat` authorization. An operator may explicitly disable
it in installation configuration; the UI continues to display chat as disabled,
and game-event commentary keeps the same shared budget. This never fabricates chat.

## Local commands and configuration

Use [broadcast-config.example.json](broadcast-config.example.json) as the non-secret
installation template. Paths must match the installed verified release and local
BF16 environment. OBS, Twitch and controller secrets use separate owned Keychain
entries, never installation JSON fields. Codex manages its own authentication
storage through its official login flow in the dedicated profile; ACTOR never
parses or copies that storage.

```sh
actorctl broadcast doctor --config ~/.config/actor/broadcast.json
actorctl broadcast codex-status --config ~/.config/actor/broadcast.json
actorctl broadcast codex-login --config ~/.config/actor/broadcast.json
actorctl broadcast ui --config ~/.config/actor/broadcast.json
actorctl broadcast preview ROOM12 --config ~/.config/actor/broadcast.json
actorctl broadcast status --run /absolute/path/to/run
actorctl broadcast start --run /absolute/path/to/run --session SESSION_ID --confirm-public
actorctl broadcast stop --run /absolute/path/to/run
actorctl broadcast emergency-stop --run /absolute/path/to/run
```

`doctor` performs no account check, provider call, Keychain mutation or model load.
`codex-login` explicitly opens the official managed-auth flow for the dedicated
profile; `codex-status` checks only that profile, without starting a model turn.
Normal preview never starts an interactive sign-in or imports the main profile's
credentials. A missing/unauthenticated dedicated profile fails closed.
`ui` opens a loopback operator service and prints its non-secret URL. `preview`
prepares a selected room and leaves publication waiting for the explicit public
action. Start requires the current preview session; repeated Start cannot create
a second stream. Ctrl-C cleans up the owned controller, observer and SSH forward.

OBS authentication and the macOS capture permission are established interactively.
The authenticated websocket listener also requires verified local isolation before
it is enabled. The PF helper only changes a separate owned anchor; its check-only
root attestor issues fresh challenged proofs to the unprivileged controller.
Stopping that checker leaves firewall protection installed. Firewall removal is
an explicit administrator action after the OBS listener has been disabled.

The concrete administrator installer is `scripts/install-broadcast-firewall.py`.
Its default `plan` action changes nothing. Review the current digest, then use
that exact digest with `install --execute --expected-digest DIGEST`; explicitly
add `--allow-enable-pf` only when enabling previously disabled PF is approved.
Use `start --execute` to run the check-only root attestor. The installer copies
only the two reviewed stdlib helper modules into an immutable owned directory;
it never executes root Python imports from the writable repository.

The defaults are `/private/var/db/actor-broadcast-firewall/pf-state.json` and
`/private/var/db/actor-broadcast-firewall-run/control.sock`. The earlier direct
helper command using `/private/var/run/actor-broadcast-pf.json` is superseded.
Stopping the attestor preserves PF. This setup does not automatically restore
protection after reboot: disable OBS websocket before removing protection or
rebooting, and verify isolation before enabling it again.

## Lifecycle and temporal fog

Idle -> Preparing -> PreviewReady -> Live -> Ending -> Stopping -> Stopped; failures
enter a safe state. Duplicate starts are idempotent and one managed channel has one
owner. Live requires actual OBS confirmation. GameOver clears normal/idle work, speaks
the permitted result and a short outro, awaits playback and bounded mux drain, then
stops the owned stream. Emergency stop mutes/stops immediately. Disconnect is not a win.
A watchdog prevents an abandoned managed stream from exposing an unrelated window.

Temporal fog must keep the existing inward feather. Allowed visibility contracts
immediately; newly allowed radial space may open smoothly with display <= current
allowed distance. Cached 193-ray state avoids new render targets or extra raycasts.
Teleports, view changes and stale sight reset the filter. Old world vertices or whole
mask crossfades must not reveal newly occluded space. Actual motion/cost need native QA.

## Validation status

These are measured checkpoints on the implementation branch, not a declaration
of production acceptance:

| Check | Actual result | Limit |
| --- | --- | --- |
| .NET SDK tests | 331 passed | Protocol/rules, not native rendering |
| Control Plane tests | 42 passed | Includes observer admission/revocation races |
| Release/helper script tests | 146 passed plus 96 subtests | No administrator/PF mutation in these tests |
| Generated schemas | 56 fresh artifacts | Checked after extension 0.7.0 changes |
| Pinned actual SDK against local Responses fixture | 4 passed | Tool-positive controls, zero-tool policy, and full provider composition; authentication/catalog/output are synthetic |
| Clean installed Runtime wheel | 970 passed, no skips | Includes cross-version PF clock, disabled replay output and Twitch provisioning; no provider SDK/authentication/model installed |
| Unity release client and Linux server | Both clean-source builds passed at `7adf933`; macOS codesign verification passed | Later integration fixes affect the Python runtime; these binaries retain their original source identity |
| Real release observer against isolated remote staging | 76 received frames, zero player slots/commands; clean owned shutdown | Headless: **no presentation ACK**, no cognition/audio/OBS |
| Real Codex, 20 authored public scenarios | 20/20 completed; first segment p50 1.819 s, p95 5.098 s; 156,848 input / 1,274 output tokens | Text-only; measured before the final tool-policy hardening |
| Dedicated-profile final-policy real Codex smoke | 1/1 completed; first segment 3.357 s; 620 input / 70 output tokens; zero observed tools, cleanup passed | One synthetic public scene, no native presentation, speech or Twitch |
| Exact local BF16 worker | Load 4.477 s, warmup 1.957 s; first PCM 0.901 s; 1.04 s of complete Russian Ryan speech in 1.037 s | One short utterance; RTF compute/audio 0.997, speedup 1.003; no playback or joint-load SLO proof |
| BF16 cancellation and ownership | Genuine cancelled terminal, ACK 0.780 s, zero PCM after ACK; one worker reaped | Offline probe; no audible-latency claim |
| Actual macOS PF and OBS control | Fresh root-peer v2 proof; OBS 32.2.2 / websocket 5.7.4, 720p30 Apple H.264 | Idle graph only; video/audio capture not yet accepted |

The 20-case run used 20 unique turns and two thread rotations, reported zero
observed tool items with complete SDK lifecycle coverage, and completed cleanup.
Zero observed calls is distinct from physically absent tool definitions. Three
earlier failed attempts and one successful diagnostic remain in the shared
quota/history; failures are not discarded from usage accounting.

The actual staging smoke stopped only its own Control Plane process and left
ports 5081/7782 free. Production health remained good; production processes,
firewall rules and deployment were unchanged.

The BF16 load/warmup MLX allocator peak was 5,491,713,873 bytes (5.11 GiB),
active 4,583,019,744 bytes and cache zero after warmup. The sampled worker RSS
peak was 935,165,952 bytes; RSS excludes the full Metal/unified-memory footprint
and is not interchangeable with the allocator peak. Available-memory samples
fell from 51% to a minimum of 26%; the guard did not abort. The isolated probe
completed in 9.601 s and left no owned process group. It does not establish
generation peak memory, sustained throughput, or capacity alongside the rendered
observer and an active OBS encoder.

Native window/capture acceptance, joint-load BF16 performance, final
OBS audio/video recording, a complete autonomous match, the sustained fault run,
Twitch ingest and visual fog-motion cost are still unmeasured. No public stream
or successful audio route is claimed from unit tests, synthetic ACKs or text-only
benchmarks. Preserve that distinction in release notes and operator readiness.

A later real zero-turn policy startup check correctly stopped with
`commentary_policy_global_instructions_present` against the existing main Codex
home and completed cleanup. No model request was started. The dedicated-profile
official ChatGPT login subsequently completed; account/catalog startup passed
with zero model calls, followed by the one-turn final-policy smoke above. The
earlier 20-case timings do not establish acceptance of that final configuration.

An actual official-SDK anonymous status probe in a disposable private profile
returned `authenticated=false`, `signin_required=true`, `model_calls=0` and
`cleanup_completed=true` in 0.522 seconds. Its temporary home was removed; the
real profile and credentials were untouched. This verifies the status/setup
plumbing, not a completed commentator sign-in.

The real OBS login was verified as `fedorr_codex`; its protected authenticated
backup and opaque service binding are local-only. Global audio inputs are
disabled and all current outputs are idle. The separate Twitch chat application
could not be created: Twitch requires account 2FA, whose initial setup requires
an SMS-capable phone number. The current staging preview explicitly disables
chat; no chat OAuth token, EventSub connection or public-ingest success is
claimed. See [Twitch login provisioning](broadcast-twitch-login.md).
