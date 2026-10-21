# Passive integrated Social privacy acceptance

This fixture observes an actual SocialDeduction match. It does not change game truth, move a player through a debug interface, assign Alignment, force a winner or create an attack. Its four ordinary clients use independent Runtime processes, bridge tokens, ports, profiles and SQLite databases.

All four use the installed `social-privacy-fixture` Brain. Its decisions use only legitimate own state: a Crew participant whose initial own X is negative navigates to Security; positive-X Crew remain in Main Hall. With the four authored spawn slots and one Impostor, this guarantees a distant Crew participant and a nearby Crew participant independently of the random assignment. The Impostor waits for its own cooldown and the fixture's public start delay, then approaches and requests one real visible kill. Distant Crew remain in Security. Allow a 90-second attack delay and an ordinary 180-second match timeout for the crowded route. This is privacy evidence, not a claim of model intelligence or a team victory.

The supervisor receives no Alignment, hidden position, seed or suggested target. The trusted server configuration stays outside objectives and Runtime arguments. Every client is headless, 800×600, fullscreen disabled and capped by the ordinary client at 60 FPS.

## Actual measurement points

`SocialPrivacyProbe` exists only with `--social-privacy-report /absolute/path.json`. It passively samples actual NGO `SpawnedObjects`, owner state and remote component defaults. The game calls `ObserveIncoming(value, IsOwner)` after bounded protobuf parsing but before endpoint/recipient rejection, on both world and reliable owner RPCs. It calls `ObserveAdapterCapture` on actual captured values and event publication. These hooks retain no payload, role, message text or position in reports.

The Unity probe checks wrong endpoints/recipients, remote private Own/Tasks state, forbidden legacy investigation/calibration state, actual radius/Physics2D line of sight, and a stable distant Security interval. A 0.75-second grace covers normal interpolation and an already-sent reliable Hide when auditing dynamic object removal. Stable far-room incoming/adapter facts receive no such grace. Legitimate older witnessed events are distinguished by their authoritative observation timestamp. Initial Hall actor IDs are remembered internally only when legitimately visible; reports expose only their count.

`runtime_audit.py` subclasses the ordinary `RuntimeServer.Connect` through the generic CLI `server_factory` injection seam. It wraps the consumed authenticated `GameToRuntime` iterator, audits each real typed protobuf envelope **before** `RuntimeSession.accept`, then yields the same message to the unchanged server. It checks canonical type URLs, manifest/capability surfaces, self entity IDs, private recipients, same-frame own transform plus capsule offset, and distant-room range for actor/body/attack facts. It never serializes or logs the payload and does not modify the Brain's WorldState. Unity supplies actual Physics2D LOS auditing; the Python wire auditor does not pretend to run Unity physics.

## Running

Build the client with the probes/hooks and temporal audit fields, and install the Runtime with `social-privacy-fixture`. Create an isolated ordinary SocialDeduction room using trusted `KillCooldownMs=90000` and `MatchDurationMs=180000` configuration, then run:

```sh
build/runtime-venv/bin/python spikes/social-privacy/run_probe.py \
  --room ROOM01 --api-url http://127.0.0.1:5081 \
  --output build/social-privacy-02 --timeout 260
```

The launcher owns all eight children and drains provider/Skill cleanup before stopping clients. `launch.json`, logs and SQLite databases are ignored local evidence. They can contain process/profile paths or legitimate private data and must not be copied into public artifacts. Only `acceptance.json` is an explicit safe projection of whitelisted counters and assertions.

A strict final PASS additionally needs the **same match's actual public postgame server summary** proving at least one real kill. The summary is never forwarded to a Brain. If it is copied after the launch ends, produce the final projection without launching anything again:

```sh
build/runtime-venv/bin/python spikes/social-privacy/run_probe.py \
  --summarize-only --output build/social-privacy-02 \
  --server-summary build/social-privacy-02/server-summary.json
```

The strict validator additionally aligns the server replay's actual kill time with authoritative public match start and continuous stable distant intervals from Unity incoming RPCs, actual adapter captures and raw gRPC. The same participant must cover a common interval of at least four seconds, with the kill at least one second after its start and before its end. A distant visit occurring only after a kill cannot pass. Each auditor stores at most 16 intervals internally; the public projection contains only temporal coverage counts, without timestamps or identities.

Without that summary, without all four completed participations, without temporal coverage of an actual kill, or with any forbidden fact, the strict projection fails. It also requires distinct credentials/profiles/ports and zero remaining owned children. It does not backfill missing live measurements. The first actual run supplied valid zero-violation measurements and later distant-room coverage, but the kill preceded that coverage; it remains partial evidence and fails this stronger acceptance requirement.

Auth-free deterministic rule/projection tests:

```sh
build/runtime-venv/bin/python spikes/social-privacy/test_probe.py
```

Those unit tests use synthetic typed messages to test the auditor itself. Only a separately run actual match supplies integrated privacy acceptance evidence.

## Separate active server-rejection check

`--probe-out-of-sight-kill` explicitly adds an active negative request to all four clients. This is separate from passive observation and must be described as such in evidence. Only the participant whose own private Alignment permits a kill submits one request, at public match start plus 90 seconds, after checking its own life, phase and cooldown. It chooses only an ID previously legitimately seen in Main Hall that is absent from a fresh current visible-world projection. The probe reads no hidden target pose or foreign role, and requires a real private authoritative `target_unavailable` rejection. Other participants skip this action.

Use trusted `KillCooldownMs=5000`, `MatchDurationMs=180000`, and the installed privacy fixture's **100-second** public attack delay for this combined case. The ordinary successful attack must occur later than the negative request:

```sh
build/runtime-venv/bin/python spikes/social-privacy/run_probe.py \
  --room ROOM01 --api-url http://127.0.0.1:5081 \
  --output build/social-privacy-active-01 --timeout 260 --probe-out-of-sight-kill
```

The strict aggregate requires exactly one attempt, one authoritative rejection and zero acceptances. Its request must coincide with another participant's measured stable distant interval on all three surfaces. The actual server replay must contain no kill, disconnect or ejection before the request receipt plus one second, and its successful ordinary kill must still satisfy the separate temporal privacy check. Reports retain only bounded measurement metadata; the public projection contains aggregate counts and checks without participant identity, role, target, position, timestamps or payload. Missing prerequisites or a missing reply fail; local Submit acceptance is insufficient.
