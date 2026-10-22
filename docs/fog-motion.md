# Human fog motion

The Human fog samples the current presentation eye on each rendered frame while
the eye, radius or epoch changes, or while a radial contour is still opening.
Every such mesh update performs the same 193 current-eye sight queries. A settled
eye retains periodic 30 Hz geometry checks. The idle deadline advances on its
original cadence instead of accumulating a late render frame on every tick.

This changes presentation timing only. It does not change server interest,
permitted observations, radius, occluders, 192 angular sectors, feather width or
the opaque annulus. Directions are calculated once instead of each geometry
refresh. Closing clamps to the newly checked permitted distance in the same
refresh; opening retains the existing 75 ms exponential filter, then snaps the
last at-most 0.0001 m to the freshly checked permitted distance so float rounding
cannot leave the moving-rate query loop running forever. Epoch or feed
revocation still removes the old hole immediately. No old LOS polygon is moved
or interpolated to a new eye. An opening step also obtains fresh LOS even when
the eye has stopped moving. Stable idle geometry is checked at the existing
30 Hz cadence; there is no indefinite idle LOS cache.

Broadcast observers retain their scheduled 30 Hz fog updates and exact
camera/epoch presentation gate. This patch does not smooth the observer's
separately received camera or actor snapshots.

The Social client requests 60 fps. At that cadence the moving/opening case can
perform 11,580 sight queries/s, compared with 5,790/s at 30 updates/s. These are
counts, not measured CPU, frame-rate or GPU results. A higher actual render
cadence can produce a higher moving query rate. Precomputed directions do not
establish that the total cost is unchanged. One actual same-binary A/B pair is
recorded below; representative broadcast-load measurements remain pending.

## Actual native A/B pair

`baseline-04` and `candidate-04` used the same clean `9ce9e10` macOS executable,
SHA256 `d4f394024738416190d6ee15a95aaf493a230b1fa11371a7c0558c714d6d2303`.
Both owned clients exited normally with code0: child lifetimes84.12s and85.34s,
with77.19s and78.65s of active Human fog samples. Each received the same six
fixed key-input cycles over29seconds. Ordinary spawn positions and physical
collisions differed, so these were not identical trajectories. Each run used
one windowed Human client in Waiting, idle OBS and no TTS. No other game window
or broadcast/GPU workload is represented by this pair.

The ignored local receipt is
`build/broadcast-validation/fog-motion-native/comparison-04.json`, with the
original reports and normal-exit receipts in `baseline-04/` and `candidate-04/`.
The comparison validates numeric report shapes; source identity and run
conditions come from their separate receipts. These local artifacts are not
published native-video evidence.

| Measurement | legacy30 baseline | Current candidate |
| --- | ---: | ---: |
| Active Human callbacks/s, whole active window |119.8|119.8|
| Presentation-eye lag p99, px |7.1745|0|
| Fog `LateUpdate` CPU p95, ms |0.4994|0.6524|
| Callback interval p99, ms |9.7429|9.6078|
| Actual sight queries/s |5,048.09|12,483.09|

Fog CPU p95 increased by0.1530ms and sight queries/s by2.473× in this sample.
The candidate's sampled presentation-eye lag was0. These counters do not
establish subjective smoothness or a causal frame-rate improvement: the small
callback-interval difference comes from one pair with different trajectories.
The observed119.8Hz is the actual Human callback cadence, not a GPU presentation
or display-FPS measurement. Percentiles retain the most recent4,096 active
frames, while query rates use whole-window totals. No performance budget PASS,
GPU timing, GC allocation, OBS/TTS co-load or subjective movement-video proof
is claimed.

The attempt history remains in the same ignored directory:

| Attempt | Recorded outcome | Use in this comparison |
| --- | --- | --- |
| `baseline-01` |Normal exit0, valid fog report; pilot.|Retained pilot, not another matched pair.|
| `baseline-02` |Expired room; no fog report, exit−15.|Invalid performance trial, preserved.|
| `candidate-02` |Tunnel refused before client launch; no report.|Setup failure, preserved.|
| `candidate-03` |Expired room; normal exit0 but no fog report.|Invalid performance trial, preserved.|
| `baseline-04` |Normal exit0 and valid report.|Accepted baseline of the single pair.|
| `candidate-04` |Normal exit0 and valid report.|Accepted candidate of the single pair.|

A normal process exit alone does not validate a fog measurement. Three
independent matched pairs under representative movement and OBS/TTS load remain
a recommended follow-up, not completed work.

## Opt-in measurements

No diagnostics run by default. For an explicitly owned, windowed Human client,
set these variables in that Unity child's environment only:

```text
ACTOR_FOG_PERF_REPORT=/absolute/existing/private/directory/fresh-baseline.json
ACTOR_FOG_PERF_VARIANT=legacy30
```

For the candidate use a different fresh `.json` path and omit
`ACTOR_FOG_PERF_VARIANT`. `legacy30` is effective only with a valid report path;
there is no standalone timing override. Both variants use the same precomputed
directions, so this compares timing within one binary, rather than reproducing
every CPU detail of the old binary. The variables are consumed by the renderer
and cleared. No command or observation capability is added.

On normal renderer destruction a new file is created; existing files and
symlinked parents are refused. macOS/Linux Player output uses mode 0600.
Reporting failure cannot skip mesh/material destruction. Forced process death
can leave no report or an incomplete report; that is not a successful measurement.

The report contains only counts, elapsed time, distributions and eye lag in
pixels. It has no identity, role, coordinates, names, chat or evidence. Timing
arrays are bounded to the most recent 4,096 active Human frames; whole-run totals
and maxima are labelled separately. Geometry CPU includes sight queries, radial
math and vertex construction. Mesh CPU includes vertex upload and bounds update.
Fog CPU covers the renderer's active Human `LateUpdate`. Frame intervals are
between those callbacks, not GPU presentation measurements or display FPS.
Instrumentation itself adds overhead and is enabled in both A/B variants.

Further representative measurements should:

1. Wait for the actual Twitch/local preview preparation gate before taking any
   separate game or GPU lease. Use one owned Human client, the same resolution,
   quality, source binary, OBS/TTS load and public authored route in each run.
2. Collect three independent 30-second baseline/candidate pairs, including
   stationary, straight movement and a doorway/corner. Keep the normal command
   path, role assignment and visibility policy. Do not infer equal trajectories
   or hardware load merely from equal durations.
3. Stop each owned client normally to flush its fresh report. Record exact
   executable hash and own-process CPU/RSS separately. Preserve failed runs.
   Read-only process sampling must bind the owned PID and its start identity;
   do not sample or stop unrelated applications.
4. Compare numeric reports with the ignored
   `build/broadcast-validation/compare-fog-performance.py` helper. Inspect actual
   captured movement for smoothness separately. Neither counters nor the helper
   certify visual quality, GPU cost, privacy end-to-end, or complete gameplay.

```sh
python3 build/broadcast-validation/compare-fog-performance.py \
  --baseline /absolute/private/baseline.json \
  --candidate /absolute/private/candidate.json \
  --output /absolute/private/fresh-comparison.json
```

This QA helper validates bounded numeric shapes and refuses contradictory query
counts, nonfinite values, unknown fields, overwritten output and mismatched
variants. It reports deltas, not a performance PASS. GC allocation and whole
process CPU/RSS are not measured by the fog report and need separate collection.

The actual pair above is separate from the pure timing/diagnostics tests;
those tests themselves do not execute Unity, OBS or native input and do not
supply additional measured pairs.
