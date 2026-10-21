# Permitted Crew threat instrumentation

This opt-in fixture wraps the real `actor_codex.brain.CodexBrain`. It forwards the
same Objective, catalog and `DecisionContext`, returns the exact provider decision,
and forwards interruption/close. It never selects an action, changes a prompt,
retries with another provider, assigns an alignment, sets a seed or forces a win.
The SDK, Runtime, Skills, server and ordinary Unity client remain unchanged.

The existing postgame reports show selected/executed Skills and server outcomes.
They do not retain the facts inside the specific Context sent before a decision.
This fixture adds that missing, deliberately narrow ordering evidence.

The wrapper keeps own actor/match and permitted target identifiers only in RAM.
Before the actual provider call it validates typed, nonopaque, nontruncated,
unexpired, fresh own Match/OwnState and permitted Attack/VisibleWorld records.
Attack recipient/observer and current match must match the Context owner. Current
body facts must come from the complete own visible-world bundle. Player claims,
chat, hypotheses and activity labels never count as authoritative attacks.
Historical witnessed attacks are counted separately and do not satisfy the fresh
threat guard. Fresh means at most three seconds, including the source timestamp.

After the real call it records only local episode/decision/revision numbers,
booleans, bounded counts and an allowlisted returned Skill ID. It compares any
body/actor target to the already permitted facts in RAM and discards the actual
arguments. It never retains text, IDs, poses, prompts, contexts or reasoning.
Rows are bounded to 512; overflow or a changed match/owner fails closed.

The optional pass-through `agents-arena-threat-audit` policy delegates the ordinary
Social policy. Only its complete authoritative terminal directive can authorize
the projection, with the same own actor/match and a unique postgame Crew row. The
safe audit appears in terminal `outcome.threat_audit`; a closed Brain report may
also include it. Nothing is published while the match is active. An Impostor
assignment, no permitted fresh threat, death before a successful reaction,
provider failure or missing terminal proof is an honest non-pass.

`spikes/social-validation/evaluate.py` explicitly treats `codex-threat-audit` as
the real Codex provider. It still requires original `provider=codex`, official
SDK 0.162.1/read-only/ephemeral/closed/singleflight telemetry, positive
`adapter.unique_model_turns`, zero model tool calls and the ordinary four-client
terminal/identity/isolation/cleanup proofs. It joins each candidate by episode,
decision index and cognitive revision to accepted Runner decisions and ordered
actual successful Skill results. A stale/rejected/interrupted candidate, an old
unrelated Skill with the same ID or a selected but never dispatched call cannot
pass. Episodes collected before GameOver permanently mask both decision/result
Skill IDs as `[private_activity]`. For this normal production privacy path, the
join requires the same paired mask plus the exact episode/index/revision and
ordered result; it never searches for a later matching action. The transparent
wrapper's target-matching boolean retains the unmodified candidate's relation to
the permitted facts. The public projection contains only aggregate counts.

There is one narrow additional authoritative completion path. ReportBody and
CallMeeting deliberately emit their phase change before the action receipt, so
the real Skill can be locally cancelled even though the server accepted it. Only
after GameOver the policy compares the exact own server meeting replay entry
(`body_report` or `emergency_meeting`) against the matching actual candidate's
return-to-next-provider-call time window, held in RAM. Wrong actors, earlier or
later actions and ambiguous duplicates do not match. Only the confirmation
boolean is published; the IDs and times are discarded. The evaluator still
requires the exact accepted/dispatched decision and its ordered real result.
It reports `succeeded_reactions` and `server_confirmed_reactions` separately;
a local cancellation is never labelled a local success. Other cancelled Skills
cannot use this phase-action completion path.

This proves that permitted facts preceded an actual model selection and a
successful action. It does not prove internal motivation, message meaning,
general intelligence, or a match win caused by that action. A returned send-message
counts only as the selected communication Skill; its contents are never inspected.

Run the offline checks without installing the optional plugin or calling a model:

```sh
build/runtime-venv/bin/python -m unittest discover -s spikes/social-threat -p 'test_*.py' -v
build/runtime-venv/bin/python -m unittest discover -s spikes/social-validation -p 'test_*.py' -q
build/runtime-venv/bin/python -m unittest discover -s scripts/tests -p test_launch_social.py -q
```

For an explicitly leased actual case, install this fixture only after the final
normal package/discovery acceptance. The optional Codex SDK must already be
installed through the ordinary Runtime extra. No model or room is started by
installation:

```sh
build/runtime-venv/bin/pip install --no-deps --no-build-isolation -e spikes/social-threat
```

Create an ordinary SocialDeduction room with four participants and the agreed
server-only public scenario settings. Choose the model's public slot before
launch, keep the normal shared Objective, and do not inspect hidden assignment to
select a room/actor. All four receive the same delegating policy. Substitute an
existing ordinary room code and an empty output directory. The controlled public
server configuration is `KillCooldownMs=45000`, `DiscussionDurationMs=60000`,
`VotingDurationMs=60000`, `WorkDurationMs=30000`, `TaskThreshold=5`,
`MatchDurationMs=240000`, with four required participants. Configure it only when
the staging lease is free, before creating the room. No seed or hidden role is
configured. The three parity fixtures leave a freshly visible body available for
the model's own response; they do not immediately report it as the vote fixture
would. The next kill still requires its ordinary 45-second cooldown.

```sh
build/runtime-venv/bin/python scripts/launch-social-agents.py \
  --room ABC123 --players 4 --api-url http://127.0.0.1:5081 \
  --match-policy agents-arena-threat-audit \
  --brains codex-threat-audit social-fixture-parity social-fixture-parity social-fixture-parity \
  --config spikes/social-threat/controlled.json \
  --objective "Play the match autonomously. Preserve your life and pursue your own side's victory conditions. Use only observations, memory and communication legitimately available to your character. Communicate relevant observations and uncertainty through permitted ordinary messages, respond to threats you personally observe, and participate in Discussion and Voting when available. Treat other players' claims as claims. A finished Objective ends one bounded episode; only the authoritative game determines the match outcome." \
  --base-port 18820 --timeout 420 --max-decisions 24 \
  --output build/social-deduction-validation/crew-threat-01
build/runtime-venv/bin/python spikes/social-validation/evaluate.py \
  --input build/social-deduction-validation/crew-threat-01 \
  --require-crew-communication-vote \
  --output build/social-deduction-validation/crew-threat-01/evaluation.json
```

The wrapper factory uses finite per-episode limits: 24 turns, 300000-token warning
and 1000000 measured-token limit. The explicit controlled config also limits the
whole participation to 2000000 measured tokens, 96 decisions, 24 episodes and
420 seconds. The ordinary CLI `--max-decisions` still bounds the Runner. The alias is
not silently mapped to `codex`, and no SDK telemetry is fabricated. A repeat
after an unsuitable assignment is a separate retained attempt, never a hidden
role-selection step.

The strict evaluator flag requires, in addition to the fresh-threat reaction, an
accepted and successful real `actor.send-message` in the model's own Discussion
context using the discussion channel, and a successful real `cast-vote`. These
have their own exact decision/result joins even when the threat is now historical.
It does not infer them from the server's aggregate message or vote counts. Message
semantics and a correct ballot choice are not claimed. A fresh reaction without
both actions can pass the narrower threat check but fails this controlled scope.

After the leased experiment, stop only its owned launchers/clients using the
ordinary supervisor cleanup and remove the test plugin before final normal
discovery checks:

```sh
build/runtime-venv/bin/pip uninstall -y actor-social-threat-audit
```

Offline tests use explicit doubles and cannot be cited as actual SDK/game
acceptance. No actual case was run by these instructions alone.
