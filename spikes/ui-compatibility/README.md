# Runtime UI compatibility probe

The optional `--ui-compat-probe OUTPUT` mode is offline. It joins no room, starts
no Runtime/model, builds an authored camera checker behind two real UI Toolkit
screen-overlay cards and exits after bounded frame sampling. The launcher owns
and stops only its own process group. Native window size is800×600, fullscreen off.

Use the open Editor's fixed `prepare-ui` command before building. The serialized
`Resources/UI/ArenaPanelSettings.asset` retains the native filter shader references;
a purely runtime-created settings object left the blur shader absent in the first
actual build. `Compatibility.uss` also makes intended filter usage explicit.

```sh
python3 spikes/ui-compatibility/run_probe.py --output build/milestone8/ui-probe-NEW
```

`compatibility.json` records actual Metal/URP rendering, screenshot edge contrast
and 90frames per condition. A missing graphics device, Unity exception, missing
screenshot result or owned-process timeout cannot pass. `passed` establishes a
working translucent fallback; `native_backdrop_supported` separately requires
actual checker smoothing. Timing is exploratory, affected by warmup and vsync,
and does not demonstrate relative performance or hardware presentation cadence.

Production uses this same UI Toolkit stack and serialized resource pipeline.
Its actual screens, Human interaction and gameplay privacy require separate QA.

Primary API reference: [Unity6.6 backdrop filters](https://docs.unity.com/en-us/engine/6000.6/manual/uitoolkits/uielements/uie-uss/uss-filter/backdrop-filter).
