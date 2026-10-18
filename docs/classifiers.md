# System-1 classifier providers

`EventClassifier.classify(ClassificationContext,RoutedEvent)` is asynchronous and
returns strict Pydantic `EventAssessment`; `close()` releases its resources.
`actor.classifiers` discovers factories. Future ONNX/local providers implement
this same interface without changing Router, Runner, Codex or Unity.

The default `rules` provider is local, deterministic and needs no key, optional
SDK or network. Game-specific rules come from `actor.event_rules`. Runtime
applies conservative policy after assessment: a model never owns commands,
cancels a Skill or overrides known critical lifecycle conditions.

## Optional Jev

The implementation uses official **typesafe-sdk0.7.5**, MIT, Python>=3.10,
verified against its installed public API on18October2026. ACTOR itself requires
Python>=3.12. Sources: [official Python SDK](https://docs.typesafe.ai/sdk/python),
[AsyncTypeSafeClient reference](https://docs.typesafe.ai/sdk/python/api/clients/async),
[official source/license](https://github.com/typesafe-ai/typesafe-sdk-python),
[PyPI release](https://pypi.org/project/typesafe-sdk/0.7.5/).

```bash
build/runtime-venv/bin/python -m pip install -e 'runtime[dev,codex,jev]'
# Supply TYPESAFE_API_KEY securely in the local process environment.
python3 scripts/launch-agent.py --room ABC123 --brain codex --classifier jev \
  --objective "Go to Engineering; return on urgent recall."
```

Only explicit `--classifier jev` or a local configuration enabling it permits
Jev transmission. The client uses `https://api.typesafe.ai`; credentials stay in
the SDK/environment. Its request contains a bounded objective excerpt, current
Skill identifier, policy tags and one event's typed content/metadata. It contains
no full WorldState, event history, memory database, bridge token or model
reasoning. Credential-named payload fields are redacted. Default request budget
is8KiB. Known decisive game rules, including station signals, short-circuit
locally and cannot be downgraded by the semantic provider.

Five questions share **one** `system_one` request:

| ID | Official primitive | Judgment |
|---|---|---|
| relevance | Choice | unrelated / relevant / uncertain for objective |
| urgency | Score | routine / important / immediate strategic review |
| remember | Noul | whether to retain the observed event |
| interrupt | Noul | whether the active Skill must stop |
| review | Noul | whether System-2 review is required |

Score's three levels run0…2. Noul returns a yes probability; Choice/Score expose
confidence. These are different quantities. The default.85 confidence gate and
.35… .65 Noul uncertainty band are operating policy, **not** proof of calibrated
accuracy. Uncertainty requests a bounded review rather than silently ignoring a
possibly important event. No live Jev accuracy claim is made without evaluation.

Default limits:1.5-second total classification deadline, one active external
request,30 requests/minute and one bounded retry on429/5xx. SDK automatic retries
are disabled so each attempt is counted/rate-limited. Long Retry-After causes
immediate local fallback instead of an early retry. Missing key, absent optional
package, timeout, rate/concurrency limits and errors fall back to deterministic
policy. Potentially critical metadata still receives conservative Router review.
No unbounded request/waiter queue is created. Async provider work never blocks
Unity or the gRPC receive loop.

`spikes/jev/sdk_contract.py` exercises the actual pinned SDK and all typed
questions through an offline HTTP transport. Normal CI installs neither cognitive
provider, uses public-surface fakes and requires no key. In this acceptance run
no Jev key was configured, so live latency/accuracy/cost are explicitly unmeasured.

## Evaluation and telemetry

Run `build/runtime-venv/bin/python scripts/evaluate-events.py`. The32 curated
cases include hard-negative policy pairs, routine/urgent duplicates, stale and
incomplete metadata, unknown semantics, action/reasoning phases and injection
text. This verifies installed **typed-event policy**; it is not a representative
semantic-language benchmark. Tests separately exercise protocol ordering,
reconnect, queue saturation and actual preemption.

Reports tie provider calls/latency/fallbacks/external attempts/confidence to the
session and Objective. Context bytes and estimated tokens are separate from
measured Codex token usage. [Validation results](event-validation/README.md).
