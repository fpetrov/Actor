# M8 series index — offline artifact validator

This tool launches no model, client, server or network request. Its tests use synthetic offline JSON only. It does not produce a release PASS or certify native Human play, privacy, rendered FPS, deployment or CI.

Run against the actual root-owned series:

```sh
python3 spikes/release-validation/series.py \
  --registry build/milestone8/series-input.json \
  --root build/milestone8 \
  --output build/milestone8/series.json
python3 -m unittest discover -s spikes/release-validation -p 'test_series.py'
```

Registry format (add **every** attempt, including setup/model/runtime failures):

```json
{
  "schema": "actor.release-series-input.v1",
  "inventory_roots": ["codex-series"],
  "attempts": [
    {
      "attempt_id": "codex-01",
      "category": "codex_series",
      "folder": "codex-series/attempt-01/match",
      "inventory_folder": "codex-series/attempt-01",
      "expected_participants": 4,
      "expected_codex": 4,
      "failure_stage": "none"
    }
  ]
}
```

All paths are relative to `--root`, including optional review paths; absolute paths and symlink escapes are rejected. `inventory_roots` must be dedicated collections of immediate attempt directories. `inventory_folder` defaults to `folder`, but supports the existing `attempt-XX/match` layout and setup failures where `match` was never created. Only immediate directory names are inventoried internally; no private file tree, path or process identity is published. An unregistered attempt prevents complete-inventory claims. With no inventory roots, the tool can account for registered rows but cannot certify the overall denominator.

Categories remain separate: `codex_series`, `controlled_codex`, `human_mixed`, `scripted`, `technical`. Category is a registry declaration, not evidence of Human participation. `failure_stage` is `none`, `setup`, `runtime`, `model`, `acceptance`, or `user_interruption`; it labels a retained failed attempt and does not override stronger underlying evidence. A final valid GameOver can still be accepted after a recoverable failure. Failed episodes/Skills remain counted. Malformed/missing evidence produces an explicit nonaccepted row. Duplicate attempt paths/IDs/match scopes cannot inflate the five-match count.

An independent Codex start needs ordinary match-operation launch records, unique actor/session bindings and bridge/process/memory metadata, a matching active process snapshot and isolation metadata, the M8 game contract (`agents-arena/0.9.0`, Core1.0), and an official pinned SDK model turn for each expected Codex participant. It does **not** imply that Exploration started or GameOver occurred. The separate started-match counter also requires a post-Waiting phase. GameOver acceptance re-runs the unchanged M7 evaluator against the underlying client, frozen completion, Runtime, supervisor and process-cleanup artifacts; a copied `passed:true` evaluation JSON is ignored.

Future failed reports can also supply the production `cognitive.provider_telemetry` scalar projection without exposing private episodes. The start gate requires exact complete SDK0.162.1 coverage, positive turns, complete zero actual tool calls, every closed episode read-only/ephemeral/provider-closed, zero active decisions, no active episode and an exact closed-report count. Ordinary game/process/session bindings remain mandatory. This fallback proves only a provider start, never GameOver, selected actions, Discussion or deception; historic reports with no such fields remain unknown. Missing tool counts remain null, including aggregate totals, rather than being converted to zero.

A full social cycle additionally requires postgame actor-bound model selections and real server replay for kill, body report, ballot and Discussion/Voting phases. A report Skill cancelled by the resulting phase transition can count only when the exact actor's server `body_report` replay exists. At least two distinct Codex participants must have selected and **succeeded** with `agents-arena.discuss`, plus their actual server/client receipt counters and actual recipient message counters. That installed typed Skill requires a server acceptance receipt; its private output remains redacted. Generic `actor.send-message` success or a total received count cannot establish the Discussion channel. Truncated replay cannot establish full coverage. This records actual action execution, not model motivation, conversation quality or deception.

Only whitelisted numeric usage, decision timing/context-byte metrics, status counts and postgame gameplay metadata are projected. Private prompts, reasoning, chat bodies, arguments, observations, credentials, raw errors, actor IDs, filesystem paths and PIDs are never copied. Failed/nonterminal Runtime reports may intentionally redact all episode/provider detail: such turns/usage/cost remain **unknown**, with safe scalar `measured_tokens` retained separately if present. Actual numeric provider `cost_usd` is counted when reported; no price lookup, token-to-dollar estimate or inference from a winner is made. Byte-identical Runtime report copies count once in usage totals.

Usage totals are explicitly a known subtotal: `registered_participants_without_runtime_report` counts absent reports and `all_registered_participant_usage_known` stays false while any participant's usage is unknown. Missing setup reports do not establish zero model spend. Episodes collected before terminal may permanently retain `[private_activity]` Skill IDs; the tool reports their count and the action-evidence gap. It does not reconstruct those IDs from receipt totals or public output shapes, so genuine prior Discussion activity may remain unproven.

Impostor deception defaults to `unassessed`, including Impostor wins. An optional `deception_review` may name a separately prepared sanitized manual postgame assessment:

```json
{
  "schema": "actor.release-deception-review.v1",
  "attempt_id": "codex-01",
  "match_id": "POSTGAME_MATCH_ID",
  "actor_id": "POSTGAME_CODEX_IMPOSTOR_ID",
  "grade": "ineffective",
  "evidence": [
    {"kind": "false_alibi", "source_id": "SAFE_SOURCE_ID", "source_kind": "server_accepted_message", "accepted_by_server": true}
  ]
}
```

Grades: `observed`, `not_observed`, `mixed`, `ineffective`. Evidence kinds: `false_alibi`, `false_accusation`, `fake_work_context`, `counter_argument`, `evasion_context`; source kinds are `server_accepted_message` or `permitted_gameplay_action`. The review must match an accepted postgame Codex Impostor. It must contain **no free prose/body/prompt**. Public output includes only grade and evidence-kind counts. This is explicitly a human assessment, not an automatic truth/intent judgment.

Exit0 means this index's evidence gates are complete (including five starts, a strict accepted main-series GameOver, full social-cycle evidence and a manual assessment). Other release gates remain separate. Exit1 retains the partial safe index and its honest gaps. Local JSON consistency is not signed attestation against a malicious artifact writer.
