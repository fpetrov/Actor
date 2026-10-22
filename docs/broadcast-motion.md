# Observer motion and presentation

The observer has a separate 30 Hz source cadence. The authoritative social
refresh and private player feeds keep their 10 Hz cadence. Event handlers update
authoritative state immediately; the next observer tick coalesces that state and
samples current physical positions. Missed ticks do not cause a catch-up burst.
No world arrays are collected when there is no connected authenticated observer
or every observer transport is at capacity.

Each observer transport can own at most eight sent frames. An exact receipt for
an outstanding sequence retires that frame and all earlier outstanding frames.
Unsent, duplicate or already retired sequence numbers release nothing. A camera
epoch change does not reset this window. Only disconnect removes its ownership;
presentation expiry never releases reliable transport capacity. Serialization
and the 49,152-byte payload bound are checked before reserving a slot. An unknown
send failure retains the slot until a receipt or transport cleanup.

This removes the former one-frame round-trip bottleneck. Five real ICMP probes
from the development Mac to the VPS measured mean RTT 194.168 ms. A one-frame
pipeline at that RTT can deliver only about five poses per second even if OBS
encodes 30 video frames per second. This is a transport explanation, not a claim
that every Twitch frame previously had that interval.

Transport receipts do not commit observations. A source frame is acknowledged
as presented only after its own native render has finished, the public HUD is
visible, the fog matches that exact camera and epoch, input is not captured, and
the currently received and applied sequence and scope still match. The former
extra `yield return null` before `WaitForEndOfFrame` could replace the source
before its render was acknowledged. The coroutine now waits for the current
render's end without relaxing any of these checks.

The server retains at most 32 prepared frames, pruning frames older than the
existing 1,000 ms presentation-receipt limit. Source TTL and local freshness
remain 500 ms. There is no pose extrapolation, interpolation of hidden actors,
old-mask translation or invented camera position.

## Numeric measurements

The native factory supplies a fresh private `motion.json` destination within its
owned session directory. On ordinary shutdown, the presenter writes numeric
render, apply, receipt and presentation counters and interval distributions.
No actor identifiers, positions, roles, evidence, text or credentials enter this
report. A hard kill or crash can leave no report; that is missing evidence.
Samples retain the latest 1,024 intervals plus all-time count and maximum.

The server's existing postgame summary directory also receives a numeric
`.broadcast-performance.json` snapshot. It ends at authoritative GameOver,
before terminal/outro delivery. Payload bytes exclude NGO/UDP overhead and
private player feeds; projector build time excludes collecting actor arrays.
Source observed-time intervals are cadence, not a one-way network latency.
Rates, retained percentiles and counters must be interpreted over their stated
spans, including periods without motion.

For private acceptance only, an exact `ACTOR_BROADCAST_MOTION_FPS=30` or `60`
together with a valid fresh private report path requests software-paced native
rendering at that rate. Invalid values or paths leave display pacing alone.
Ordinary runs retain their display pacing. This diagnostic setting does not
change OBS FPS or permit headless presentation acknowledgements.

## Validation status

The 30 focused C# delivery/presentation cases pass, including modeled 194 ms
round trips, bounded stalls, skipped and forged receipts, independent 10/30 Hz
cadences, delayed exact presentation receipts and cache expiry. These are pure
tests, not native rendering, GPU or Twitch acceptance.

Actual native 30/60 FPS motion, encoded spatial changes, payload/CPU cost and
co-load with BF16 speech still require the candidate's local recording checks.
The higher source cadence can increase bandwidth and allocations; the queue
bound prevents unbounded in-flight work but does not make that work free.
