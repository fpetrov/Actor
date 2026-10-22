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
establish that the total cost is unchanged. Actual A/B measurements remain
pending.

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

Root's later actual measurement should:

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

No actual Unity, OBS, native-input or performance run is represented by the
pure timing/diagnostics tests.
