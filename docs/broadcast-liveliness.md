# Commentary cadence and presentation

This is a bounded editorial change. It does not change the model, BF16 voice,
evidence permissions, durable quotas, provider concurrency or audio queue.

Quiet-time eligibility uses the latest noteworthy observation, actual terminal
playback ACK or readiness timestamp. The default delay is 20 seconds with a
uniform ±25% jitter (15–25 seconds), clamped to 5–120 seconds. A sample is retained
for that anchor and cadence configuration. Polling, transforms and duplicate
heartbeats do not resample it. Eligibility still requires fresh presented data,
an allowed phase, useful retained material and the existing shared budget. An
empty candidate retains its bounded backoff; jitter does not add model calls.

The trusted context now supplies word targets and presentation by mode. P0 asks
for one short clause. Ordinary commentary prefers 2–3 clauses and about 25–45
words. A grounded recap/callback prefers three clauses and about 35–55 words; a
fourth is allowed only if brief and useful. These are targets, not padding
requirements. Each clause preferably has at most 16 words. The provider schema
and incremental parser both enforce the mode's segment-count ceiling; the
existing 55-word parser safety bound remains. A noncompliant long clause may
still hit the real 12-second audio cap and must remain interrupted, not completed.
The idle output reservation is now 512 tokens. It is a conservative ledger
reservation, **not** a guaranteed output cap in the pinned SDK's public turn API.

At most six actually completed heard clauses are supplied for context and trimmed
to the context budget. Generated, progress-only and interrupted text is not heard
history. A bounded ring suppresses an identical completed idle semantic
topic across the current broadcast/match, including refreshed versions of the
same lineage. Promotion requires genuine provider completion declaring its exact
clause count, exact candidate references and actual completed playback ACKs for
every clause whose combined citations cover that exact candidate. The topic key
includes canonical substantive value, kind and origin; unchanged payload/version
refreshes stay the same topic, while a substantive corrected value is new
material. Record version and observation/expiry timestamps do not define a new
topic. Whole-ID privacy revocation remains final and is never revived by this key.
A partially
heard, interrupted, cancelled or pruned utterance never promotes the whole topic.
Previously reactive material can still receive a historical
callback; a genuinely new connection can form a different topic. Historical
facts keep their original timestamps. Their age is computed against the actual
presented-view timestamp; skew is unknown, not zero. The fresh current phase does
not need a blanket stale-data disclaimer.

The coordinator marks public start only after output confirms Live. With a fresh
view and real retained game references, one local `neutral-intro` segment says
“Здравствуйте! Рады вас видеть.” before at most one generated grounded clause.
This never runs just because private preview started. The local greeting can run
when the shared model budget refuses the generated clause. It creates no fact,
evidence coverage, provider call or provider-text latency sample.

For Ending, at most one generated result clause must actually complete playback,
then a local `neutral-outro` says “Спасибо, что были с нами. До встречи!”. Without
a usable result (including an actual interrupted result-audio ACK after the
provider completed), the single neutral fallback says “Матч завершён. Спасибо, что
были с нами. До встречи!”. Terminal completion waits for the actual local farewell
ACK as well. Both neutral tags must be excluded from generated-speech acceptance
counts. P0 interruption, invalidation and emergency stop always take precedence;
an interrupted courtesy is not fabricated as complete or endlessly replayed.

Every segment still reserves the full configured ceiling, at most 12 seconds;
the pending aggregate remains at most 12 seconds and two segments. Progress does
not release capacity. The provider's 45-second deadline includes playback
backpressure, and Ending retains its 45-second drain deadline. Longer commentary
can therefore time out under slow synthesis/playback; the tests retain this
failure path instead of extending the queue or claiming a throughput SLO.

Auth-free checks use fake clocks/providers and explicit playback ACKs, including
timing bounds, priority, quota refusal, historical callbacks, incremental parser
overrun, courtesy interruption, three slow serialized clauses and a real short
timeout while waiting for ACK. They establish wiring and bounded behavior, not
model naturalness, BF16 speed or Twitch viewer timing. Actual voice quality,
truncation rate and audience cadence remain operator QA.
