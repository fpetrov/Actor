# Broadcast pipeline timings

`CommentaryScheduler.report()["latency"]` contains passive numeric distributions.
The scheduler owns one `LatencyTelemetry` registry and automatically binds it to
the production `SpeechAudioOutput`. No Native binding, model-context field or
game transport protocol is added. An injected test speech sink without this binding
has no audio timings; missing stages are omitted, never filled with zero.

The real callbacks record these boundaries:

| Boundary | Meaning |
| --- | --- |
| Presentation | Controller receipt timestamp of the coherent native presentation ACK, with exact permitted fact versions. This is not a GPU timestamp. |
| Admission | A valid scheduler candidate has passed the existing budget reservation and scope checks. |
| Provider dispatch | The scheduler enters `provider.generate`; this is not an internal remote model compute timestamp or a separate timestamp for each repair turn. |
| First validated text | First ordered, grounded, closed `ProviderSegment` available from the final-answer stream, before speech-capacity waiting. A later provider failure does not retroactively make this a completed turn. |
| Speech submit/queue | Submission dispatch and actual acceptance into the bounded output queue. |
| Dequeue/start | Queue removal, then controller entry into synthesis iteration after renderer readiness and the audio `begin` send. This controller boundary is distinct from worker start. |
| First PCM | First real, matching, ordered PCM chunk after the BrowserAudioSink PCM/frame-bound checks, before renderer backpressure. This does not mean anyone has heard it. |
| Synthesis done | Matching generator terminal, with actual generated frame count. Truncated/incomplete generation is counted separately and excluded from complete-generation RTF. |
| Positive consumption | First accepted playback ACK with `consumed_source_frames > 0`, after the scheduler's existing scope/epoch and SpokenLedger checks. This certifies the local browser audio graph only. |

Latency names include units (`_ms`, `_seconds`); RTF and speedup are ratios.
Synthesis pipeline wall time runs from synthesis iteration to accepted generator
completion. Its RTF is wall seconds divided by actual frames/24,000; speedup is
the inverse. It includes controller/worker IPC, worker waits and renderer
backpressure. It is **not worker compute time** or standalone BF16 throughput.
Worker timing is recorded separately below. Twitch viewer latency remains unavailable.

## Persistent worker generation metrics

The private, bounded `WorkerGenerationMetrics` IPC event carries durations and
MLX allocator counters for one exact scope/segment. It is not an audio chunk and
does not enter the PCM queue or change `SynthesisDone`. The reader rejects unknown
fields, invalid numeric bounds and inconsistent terminal metadata. Foreign,
fenced, duplicate or late metrics cannot be associated with another utterance.
The callback runs only after ordered PCM and its matching terminal frame count
are accepted. Synthetic route probes and fallback audio have no admitted
provider lineage in the registry and remain excluded.

`worker_complete_compute_ms` sums monotonic wall spans around iterator creation,
each `next()` (including the final EOS/`StopIteration` computation), and
`MlxModel.pcm`. The pinned implementation materializes output through NumPy.
Socket sends, waits in those sends, iterator close and model reset are excluded
from this sum. It is **not** CPU utilization, isolated GPU-kernel time or a claim
that all device work is synchronized independently of output materialization.
`worker_complete_start_to_first_pcm_ms` starts inside the worker and ends when
the first nonempty emitted PCM is materialized, before its first socket send;
it does not use the controller synthesis-start timestamp.

`worker_complete_wall_ms` covers worker start through generator close and reset,
including PCM socket send/backpressure time, but excludes the final metrics/Done
sends. `worker_complete_pcm_send_ms`, `worker_complete_iterator_close_ms` and
`worker_complete_reset_ms` expose those separate scopes. Complete-generation
`worker_compute_rtf` divides compute seconds by actual frames/24,000, and
`worker_compute_speedup` is its inverse. These ratios require nonempty audio,
positive compute time and an untruncated `generator_completed` terminal.
Truncated, expired or cancelled terminals use `worker_incomplete_*` fields and
separate counts; they never enter complete-generation ratios. Already cancelled
scheduler records discard late worker metrics rather than fabricating samples.

The pinned MLX 0.32.4 `reset_peak_memory()` is called before each generation.
Active, cache and peak allocator bytes are read **before** iterator close,
decoder reset and cache clearing. Per-generation peak includes resident model
weights; it is not an allocation delta or process RSS. A failed/unavailable peak
reset omits the generation peak sample rather than reporting a cumulative peak
as per-generation. Missing memory APIs/counters are omitted, not replaced by
zero. Only aggregate numeric distributions leave the registry; raw scope/keys
remain bounded in RAM. `worker_compute_timing_available` becomes true only after
a valid associated worker event has actually been observed. Old runs retain
their original absence of these measurements.

The auth-free tests use a deterministic fake generator and framed in-memory or
socketpair transport. They separate a slow socket send from compute, include EOS
work, exercise partial/cancelled terminals and invalid/stale/duplicate IPC, and
verify allocator reads precede cleanup. They do not measure actual BF16
performance, memory use or GPU behavior.

`selected_event_to_presentation_receipt_wall_ms` combines the server fact wall
timestamp with controller wall/monotonic conversion at receipt. It depends on
clock synchronization. Negative, nonfinite or implausibly old samples are
omitted; they are not converted to zero. The receipt-age metric uses the maximum
age among selected exact versions where all their timings are retained. Idle recap and
memory callback intentionally use old material, so their fact age is not a
reactive-event latency SLO. Local presentation-to-admission/consumption metrics
use the current admitted view's ACK receipt; retained-evidence timing is named
separately.

End-to-end distributions are sampled directly at the first positive accepted
consumption ACK: `evidence_first_presentation_to_first_positive_consumption_ms`
starts at the earliest selected game-fact first presentation receipt, and
`selected_event_to_first_positive_consumption_wall_ms` starts at the earliest
selected wall-event time projected into the local monotonic domain. The latter
retains the clock-dependency and historical-idle qualifications. All selected
versions must have retained baselines; missing, skewed or invalid baselines do
not generate zero samples. These p50/p95 values are not sums of stage percentiles.

Distributions are grouped by commentary mode, including `reactive`, `idle_recap`
and `memory_callback`, and by `first_in_run` / `subsequent_in_run`. This order is
the first admitted provider utterance in that registry, including attempts later
cancelled; it is not inferred from first audio or a worker cold-start claim.
Multiple segments share their utterance's order. Counts cover the whole run;
p50/p95 use the most recent bounded samples per metric (default 128).

Scope, utterance/segment keys and evidence-version timing are held only in
bounded RAM tables. Defaults retain at most 128 utterances, 128 segments and
512 fact timings. Scope changes clear correlation tables. Cancelled/evicted
records cannot accept late PCM or ACK timings. Reports contain no text, IDs,
evidence, positions, source timestamps, credentials or PCM. Counts expose
cancellation, truncation, eviction and invalid timing. Existing valid partial
measurements remain counted after cancellation; cancellation does not manufacture
a completed segment. Synthetic setup audio and the neutral fallback outro have
no admitted/validated provider lineage and are excluded.

## Bounded speech capacity

Each validated provider segment reserves the full configured speech frame
ceiling, 12 seconds by default. Word count is no longer used to impose a guessed
6-second cutoff on Russian speech. A following segment waits until a real
terminal playback ACK frees the reservation; positive progress alone does not
free it. The aggregate pending ceiling remains 12 seconds and the segment-count
ceiling remains two. An explicitly lower configured hard ceiling is preserved.
Audio output still caps actual generation at 12 seconds, and truncated output
still yields `interrupted`, never textual completion.

The provider receives guidance to use short, complete clauses, preferably no
more than 16 words per segment. This is guidance, not evidence that all voices
finish within the limit. An actual voice run must still prove full generator
completion and actual local consumption. No historical Native06/07 timing is
backfilled from this new instrumentation, and the code/fake tests do not measure
native rendering, BF16 performance, OBS routing or Twitch viewer delay.
