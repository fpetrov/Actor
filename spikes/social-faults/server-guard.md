# Exact staging server-loss guard

`server_guard.py` is a standalone standard-library fixture for the isolated
Linux staging installation. It does not use SSH itself, create a lobby, inspect
roles, read `/proc/*/environ`, call a game command, or signal a process by name.
The root validation operator executes it only after the staging lease is free
and an ordinary lifecycle observer is ready. It cannot target production: API
`127.0.0.1:5081`, UDP `7781`, both staging executable paths, the CP PID file and
the staging log roots are fixed in the source.

The operator supplies the exact own room, lobby GUID and independently verified
current staging CP PID/start ticks. `inspect` validates a fresh lobby response,
the CP's actor UID/executable/start time/PID file/TCP listener, one structured
allocation record, the server's actor UID/executable/start time/actual public
identity arguments, its current CP parent, exclusive UDP ownership, common
network namespace, and one exact READY log line. Only Ready/Running lobbies
created within 30 minutes are accepted. Sandbox and SocialDeduction are both
supported; their actual mode must agree throughout the checks.
The fixed CP stdout log alone may be owned by root or actor, because a root
shell can open its redirect before switching the CP process to actor. It must
still be a regular non-symlink file without group/other write permissions;
this exception does not apply to the actor PID file or match READY log.

Run this remotely with root-owned SSH, passing the local script as stdin. The
following are **remote Python arguments**, not an instruction to run a fault
without the lease and observer:

```text
python3 - inspect --room ROOM01 --lobby-id EXACT_GUID \
  --control-plane-pid VERIFIED_CP_PID \
  --control-plane-start-ticks VERIFIED_CP_START_TICKS \
  --identity /tmp/UNIQUE_PRIVATE_IDENTITY.json
```

The identity file is created exclusively with mode `0600`; an existing file or
symlink is refused. It contains the private scope and process identity. Do not
copy it into public acceptance artifacts. Use a fresh filename for a new
inspection.

After copying the current observer's `ready.json` to a root-owned remote file,
perform the explicit termination operation within 30 seconds of inspection:

```text
python3 - terminate --room ROOM01 --lobby-id EXACT_GUID \
  --control-plane-pid VERIFIED_CP_PID \
  --control-plane-start-ticks VERIFIED_CP_START_TICKS \
  --identity /tmp/UNIQUE_PRIVATE_IDENTITY.json \
  --observer-ready /tmp/UNIQUE_OBSERVER_READY.json --signal SIGKILL
```

Readiness must use the existing `actor.social-lifecycle-acceptance.v1` schema,
case `server-loss-observer`, `ready=true`, the same room/GUID, legitimate own
actor/session identity, and `created_at_unix_ms` no older than 30 seconds. A
copied file's mtime is insufficient. The observer refreshes readiness only while
the client, session, foreground Skill and authoritative motion remain live.

Termination opens a Linux pidfd for the inspected server, repeats every live
check twice and rechecks readiness each time. The inspected PID/start ticks,
current CP and game mode must remain unchanged. The default signal is SIGTERM;
explicit `--signal SIGKILL` tests abrupt loss. Either signal goes only to the
held pidfd; there is no numeric-PID fallback, process-name kill or Control
Plane termination. The stdout JSON contains only constant labels, booleans and
the signal classification. It omits PIDs, IDs, paths, raw logs, errors, payloads
and credentials. A failed check returns nonzero without a signal.

A successful guard report means that the exact-instance checks passed and
the selected signal dispatch succeeded. SIGTERM must not be labelled an abrupt
crash. Neither operation claims process exit, a game result,
client survival, stale-motion detection, bridge disconnect or provider/Skill
cleanup. Keep the independent lifecycle observer acceptance as separate proof.
The existing production-specific `event-routing/runtime_faults.py` must not be
used as a substitute for this staging guard.

Offline tests launch no process, listener, SSH connection or signal:

```sh
build/runtime-venv/bin/python spikes/social-faults/test_server_guard.py
```
