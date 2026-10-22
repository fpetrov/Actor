# Version-bound acceptance on isolated staging

`run_acceptance.py` reuses the existing acceptance helpers with an explicit
public-version and exact-room preflight. It adds no game command, Runtime plugin,
schema, scenario or credential. The historical helpers and evidence retain their
original defaults. Planning launches nothing; `--execute` requires the separately
coordinated staging lease.

The default target is game **0.9.0 / admission 4**, loopback API
`http://127.0.0.1:5081` and gameplay UDP **7781**. Execution requires both public
version endpoints to agree, the caller's exact room code and match GUID, a
`Ready` room created within ten minutes, zero connected players and capacity
4–6. Public API redirects, production endpoints and oversized responses fail
closed. Only allowlisted helper options after `--` are forwarded; they cannot
replace the room, client, endpoint or output using aliases or abbreviations.
The existing helper owns its ordinary Runtime/client processes and cleanup.

Create a fresh ordinary room through the isolated public API after configuring
its trusted public rules, record its GUID, then use a fresh output directory.
The wrapper neither creates rooms nor edits Control Plane configuration.
`--game-version` and `--protocol-version` can explicitly select another installed
release; they never change the versions sent by the built client.

## Scoped privacy and actual out-of-sight rejection

For the existing four-client privacy fixture, use public `KillCooldownMs=5000`,
`MatchDurationMs=180000` and the installed fixture's 100-second ordinary attack
delay. One explicit negative kill request occurs earlier. Both the negative
request and the later legitimate kill must overlap measured stable distant
intervals; a visit to Security after the kill does not pass.

```sh
build/runtime-venv/bin/python spikes/release-validation/run_acceptance.py \
  --suite privacy --room ROOM01 --match-id EXACT_MATCH_GUID \
  --output build/milestone8/privacy-actual-01 --execute \
  -- --timeout 260 --base-port 18400 --probe-out-of-sight-kill
```

Omit `--execute` to check the launch plan without network access. Strict final
privacy acceptance also needs the same match's actual postgame server summary,
copied by the lease owner without exposing it to a Brain. Recompute without
another launch or preflight:

```sh
build/runtime-venv/bin/python spikes/social-privacy/run_probe.py \
  --summarize-only --output build/milestone8/privacy-actual-01 \
  --server-summary build/milestone8/privacy-actual-01/server-summary.json
```

The M8 wire auditor recognizes own WaitingPlayers, TaskWork and KillPresentation
payloads, checks their recipients, and rejects attacker identity disclosed to an
own victim or a fresh hidden attack presentation during a distant interval.
This measures permitted delivery, not native rendering quality or animation
timing. Its payload is never serialized into the public audit report.

## Ordinary protocol and client/Runtime faults

The optional protocol fixture plugin is installed only in the validation
environment. See [its setup and public timing prerequisites](../social-protocol/README.md).
Its real eight negative checks continue through the ordinary owned RPC and
fresh `owner.Context()` generation.

```sh
build/runtime-venv/bin/python spikes/release-validation/run_acceptance.py \
  --suite protocol --room ROOM02 --match-id EXACT_MATCH_GUID \
  --output build/milestone8/protocol-actual-01 --execute \
  -- --timeout 180 --base-port 18600

build/runtime-venv/bin/python spikes/release-validation/run_acceptance.py \
  --suite fault --room ROOM03 --match-id EXACT_MATCH_GUID \
  --output build/milestone8/runtime-loss-actual-01 --execute \
  -- --case runtime-loss --participant 1 --timeout 360 --base-port 18620
```

Use the existing fault helper's case-specific public prerequisites. M8 client
loss can include NGO disconnect detection followed by the bounded reconnect
grace; allow the corresponding time rather than assuming immediate forfeiture.
The `late-reconnect` case uses a fresh ticket and same display name to test a new
spectator identity. It does **not** test credential-authenticated M8 actor resume;
that remains the separate [UDP resume fixture](../social-resume/README.md).

## Legacy Sandbox regression

Create a fresh **Sandbox** room for each case. The wrapper checks its mode and
versions; the original pair launcher still requires matching own authoritative
terminal state plus both Objective successes.

```sh
build/runtime-venv/bin/python spikes/release-validation/run_acceptance.py \
  --suite sandbox --room ROOM04 --match-id EXACT_MATCH_GUID \
  --output build/milestone8/sandbox-movement --execute \
  -- --case movement --timeout 180

build/runtime-venv/bin/python spikes/release-validation/run_acceptance.py \
  --suite sandbox --room ROOM05 --match-id EXACT_MATCH_GUID \
  --output build/milestone8/sandbox-cooperation --execute \
  -- --case cooperation --base-port 18810 --timeout 360

build/runtime-venv/bin/python spikes/release-validation/run_acceptance.py \
  --suite sandbox --room ROOM06 --match-id EXACT_MATCH_GUID \
  --output build/milestone8/sandbox-investigation --execute \
  -- --case investigation --base-port 18830 --timeout 600
```

Movement uses the existing synthetic owned-input prediction probe, not a native
keyboard/frame-latency measurement. Investigation uses the actual natural
mechanical clock, not a manual-origin causation or model-intelligence test.

## Exact staging server-loss guard

`spikes/social-faults/server_guard.py` keeps its historical M7 defaults. Explicit
M8 options derive only these dedicated staging roots:

```text
--staging-name m8-validation --game-version 0.9.0 --protocol-version 4
```

The basename cannot contain a slash, traversal or an arbitrary production path.
It derives `/opt/actor/staging/<name>/{server,control-plane}` and
`/var/lib/actor/<name>`. The API and UDP ports remain 5081/7781. The private
inspect identity binds the profile, both versions, exact processes/start ticks,
room and match; changing the profile before `terminate` fails. All previous
UID, parent, allocation, READY, namespace, sole-socket and fresh-observer guards
remain mandatory. There is no numeric-PID signal fallback.

Start the ordinary observer with an exact fresh Sandbox GUID:

```sh
build/runtime-venv/bin/python spikes/release-validation/run_acceptance.py \
  --suite lifecycle --room ROOM07 --match-id EXACT_MATCH_GUID \
  --output build/milestone8/server-loss-observer --execute \
  -- --case server-loss-observer --port 18752 --timeout 900
```

Only the lease owner runs remote `inspect` and, once the same observer's ready
file is fresh, `terminate --signal SIGKILL` with the same M8 options and private
identity file. `SIGTERM` remains the default and is not abrupt-crash evidence.
Successful signal dispatch alone proves neither process exit nor client
cleanup; the independent observer must supply that result.

Public outputs are only the helper's whitelisted acceptance projections.
Do not publish launch manifests, raw client/Runtime reports, process metadata,
SQLite files, private identity files or logs. Failed attempts remain failed;
this wrapper never backfills gameplay evidence or upgrades a historical run.
