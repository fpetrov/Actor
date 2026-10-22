# Actual M8 Sandbox regression

[summary.json](summary.json) retains four actual attempts against the isolated
game **0.9.0 / admission 4** staging release. Each used a distinct ordinary
Sandbox room; public versions, exact match identity and fresh Ready/zero-player
state were checked before launch. No model, GUI, scenario override, CP restart
or operator server mutation was used.

| Case | Actual result | Cleanup |
| --- | --- | --- |
| [Movement attempt 01](movement-attempt-01.json) | Setup failed; release P-probe did not execute | Owned direct client stopped |
| [Movement attempt 02](movement-attempt-02.json) | PASS: all 13 synthetic owned-input prediction checks | Owned direct client stopped |
| [Cooperation attempt 01](cooperation-attempt-01.json) | PASS: same Completed calibration, both own Objective successes | Two launchers and four Runtime/client children stopped |
| [Investigation attempt 01](investigation-attempt-01.json) | PASS: same Resolved incident, Restored cooling and validated report, both own Objective successes | Two launchers and four Runtime/client children stopped |

Movement retains its actual `regression_deadline` failure. The old P-probe was
compiled only for Editor/Development builds, while ordinary automatic admission
skipped clients carrying a probe role. No own actor or prediction report appeared
in this non-Development release attempt. This is an unavailable measurement,
not evidence of a movement defect or a prediction PASS. After a separate release
build added the explicit batch-mode P opt-in, a fourth fresh ordinary room ran
the retry. It passed all 13 checks with 48.03 ms local presentation response,
584.79 ms authoritative-root response, 113.92 ms release response and 0.00498
idle reconciliation error. These are one headless synthetic-input run's timings;
they do not measure physical keyboard or rendered-display latency. Native Human
movement is separate evidence. The earlier failure remains unchanged.

The unused movement room expired normally before the first controller's stop.
That controller had already created a different fresh cooperation room and
completed its strict acceptance and cleanup. It was stopped between cases, then
investigation ran under a second controller in a third fresh room. No room reuse
is claimed. The final public API inventory was empty and all owned local
processes had ended before the staging lease was returned. The movement retry
also ended with zero owned processes and an empty staging inventory.

Both pairs used installed scripted factories and independent bridge processes,
ports, credentials, profiles and memory files. Their success requires the actual
matching terminal game state as well as each Objective; the finisher alone is
insufficient. Investigation used the normal natural mechanical clock. It is
neither a manual-causation hard-negative nor a model-intelligence benchmark.

The ordinary launches were:

```sh
build/runtime-venv/bin/python spikes/release-validation/run_acceptance.py \
  --suite sandbox --room FRESH_CODE --match-id EXACT_GUID \
  --output build/milestone8/sandbox-actual-01/movement --execute \
  -- --case movement --base-port 19800 --timeout 180

build/runtime-venv/bin/python spikes/release-validation/run_acceptance.py \
  --suite sandbox --room FRESH_CODE --match-id EXACT_GUID \
  --output build/milestone8/sandbox-actual-01/cooperation --execute \
  -- --case cooperation --base-port 19810 --timeout 360

build/runtime-venv/bin/python spikes/release-validation/run_acceptance.py \
  --suite sandbox --room FRESH_CODE --match-id EXACT_GUID \
  --output build/milestone8/sandbox-actual-02/investigation --execute \
  -- --case investigation --base-port 19850 --timeout 600

build/runtime-venv/bin/python spikes/release-validation/run_acceptance.py \
  --suite sandbox --room FRESH_CODE --match-id EXACT_GUID \
  --output build/milestone8/sandbox-actual-03/movement --execute \
  -- --case movement --base-port 19900 --timeout 180
```

Substitute each new ordinary room's actual code and GUID, reserve the isolated
lease, and use new output directories for retries. Raw reports, plans, launch
identities, logs and SQLite data remain private. The public files contain only
whitelisted result/cleanup aggregates; they expose no room, actor, session,
process identity, credential, task evidence or chat body.
