# Sandbox regression on isolated staging

This opt-in helper runs the unchanged cooperative/investigation launch plans or
the existing prediction probe against an ordinary **Sandbox** room. It does not
create rooms, alter server configuration, transfer evidence between agents or
use a game debug API.

Reserve the isolated staging lease first. Create a fresh Sandbox room through
the normal `http://127.0.0.1:5081/api/v1/lobbies` API, using the game and admission
versions returned by `/api/v1/game/version`, capacity 4, and wait for `Ready`.
Use a different fresh room for each case; terminal tasks/incidents are retained.

```sh
build/runtime-venv/bin/python spikes/sandbox-regression/run_case.py \
  --case investigation --room ROOM01 \
  --output build/social-deduction-validation/sandbox-regression/investigation-retry \
  --base-port 18830 --timeout 600 --execute
```

Cases are `cooperation`, `investigation` and `movement`. Omit `--execute` to
prepare only: no process or room is started. Output must be new/empty. Timeout
is bounded to 60–600 seconds. Every client is forced to staging, headless mode,
and non-fullscreen 800×600. The wrapper inherits ordinary credential environment
variables without reading or serializing them.

Acceptance requires both own Objective successes **and** same authoritative
`Completed` calibration task, or same `Resolved` investigation with `Restored`
cooling and validated report. Investigation uses the ordinary natural mechanical
clock; this is not a manual-origin hard-negative or model-intelligence test.
Movement requires all 13 distinct prediction checks and finite bounded metrics;
its input is synthetic owned input on a headless client, not a native keyboard
or displayed-frame measurement.

Only child process groups created by this invocation are signalled. Its guarded
launch manifests establish independent processes, credentials, memory and ports.
`acceptance.json` reports cleanup; successful pair cases require four Runtime/
client PIDs checked and zero remaining children. The movement child is the direct
owned Popen and must also be closed. A failed attempt remains a failed artifact;
rerun into a fresh directory and room after diagnosing it.

Raw local logs, plans, reports and memories are private. Publish only the bounded
metadata projection: no tokens, chat/evidence bodies, actor/session/match IDs or
authorization codes. A successful Objective alone is insufficient acceptance.

```sh
python3 spikes/sandbox-regression/test_regression.py
```
