# Ordinary owned-RPC negative probe

This explicit local fixture sends typed commands through the real `NetworkSocialMatch.Submit` path. It reads only its local owner's permitted state and private authoritative action-result events. It has no reference to the server authority, hidden role assignment, foreign private component state or debug movement. The fixture does not launch a client, server, Runtime or model.

Add this flag to one ordinary client in an isolated SocialDeduction match:

```text
--social-protocol-fault-report /absolute/protocol-client.json
```

Six checks are independent of Alignment: wrong-match rejection, expired request, stale phase, self-target kill rejection, exact replay of that rejection, and cross-type reuse of the same correlation ID. All kill requests target the sender itself and must fail. No target is chosen from hidden state.

The remaining two checks need real gameplay prerequisites. During an ordinary Voting phase, the probe first submits a Skip only if this owner has no ballot, then attempts a different final ballot using a new correlation ID. It requires the server to reject the second ballot while the owner's original ballot remains unchanged. During Exploration, when the ordinary player is stationary beside a visible available task, the probe submits Work and immediately Cancel using the start correlation as the work identifier, without waiting for the start ACK. It requires the real start acceptance, interrupted work result and cancel acceptance, with no completion. The probe never navigates, calls a meeting, reports a body, sabotages, changes another actor, or forces a winner. Keep other controllers from issuing concurrent work/votes for this test owner.

Each receipt has a ten-second deadline. The complete probe has a 300-second limit. If a required phase/nearby task never occurs, the report stays incomplete until it fails at that deadline; it never substitutes a pure unit test for missing live coverage. Early client shutdown similarly leaves a non-passing partial report. The passive privacy fixture normally never meets the Voting prerequisite and is a separate run.

Reports contain only constant case names, constant outcome classifications, counts and elapsed time. Correlations, work IDs, match IDs, actors, positions, roles and action payloads remain internal. Project a completed report with:

```sh
build/runtime-venv/bin/python spikes/social-protocol/verify_report.py \
  --report build/protocol-client.json --output build/protocol-acceptance.json
```

Only all eight real checks can produce a strict PASS. Auth-free projection tests:

```sh
build/runtime-venv/bin/python spikes/social-protocol/verify_report.py --self-test
```

Those tests verify bounded, privacy-preserving report validation. They are not evidence that the live negative requests have run.

## Optional ordinary-game supervisor

The isolated plugin in this directory registers one fixture Brain, one terminal-approach Skill and a local policy subclass. Production Runtime and entrypoints remain unchanged. Install it only in the local validation environment:

```sh
build/runtime-venv/bin/python -m pip install -e spikes/social-protocol --no-deps --no-build-isolation
```

Use an isolated ordinary four-player SocialDeduction room with trusted public rules such as `KillCooldownMs=45000`, `DiscussionDurationMs=10000`, `VotingDurationMs=10000`, `MatchDurationMs=90000`. The supervisor launches three `social-fixture-vote` participants first and the role-agnostic protocol owner fourth. All receive the same standard match Objective. It uses four independent Runtime processes, tokens, ports, profiles and SQLite files; no model is requested.

```sh
build/runtime-venv/bin/python spikes/social-protocol/controller.py \
  --room ROOM01 --api-url http://127.0.0.1:5081 \
  --output build/social-protocol-01 --timeout 180
```

The fourth Brain waits eight seconds before readiness, giving the initial rejection tests a stable Waiting phase. It approaches its own public Main Hall task through the normal Scheduler, waits 12 seconds without issuing work, approaches the currently visible meeting console and calls a normal meeting. The local Skill reuses the authored Social navigator and a public lower Hall staging lane to avoid the stationary spawn row. It sends only the normal locomotion capability and has ordinary cancellation/ownership guards. The policy subclass delegates all lifecycle decisions and all other Skill permissions to production `SocialMatchPolicy`.

During Discussion/Voting the Brain waits while the probe owns its ballot. After a legitimate meeting it waits for the real game result; it does not manufacture termination. The other participants retain their normal permitted-context vote fixture. The Brain never reads probe reports, process arguments, server scenario files, hidden role/pose data or supervisor commands. Its behavior is identical for either own Alignment.

The supervisor keeps clients alive until all four Runtime participations receive authoritative terminal summaries, drains Runtime cleanup, then stops its clients. Its public `acceptance.json` requires all eight real probe cases, four same-scope completed participations, independent process credentials/profiles/ports and no remaining owned processes. Logs, launch metadata and SQLite files remain ignored local evidence. This optional plugin is not part of the production wheel and should be removed from the local environment after validation:

```sh
build/runtime-venv/bin/python -m pip uninstall -y actor-social-protocol-fixture
```

Additional auth-free provider/policy/supervision tests:

```sh
build/runtime-venv/bin/python spikes/social-protocol/test_fixture.py
```
