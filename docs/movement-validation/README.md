# Responsive movement validation — 8 October 2026

Production game **0.2.1**, protocol **2**, Unity 6000.6.4f1 / NGO 2.13.3. Client and Linux Dedicated Server built successfully. Runtime release: `20261007T224727Z-3fa1a33`. Control Plane tests: **18/18**, [Linux CI passed](https://github.com/fpetrov/Actor/actions/runs/37698445812).

The owner now predicts the Visual child, animation and camera. The replicated root and Rigidbody2D remain server authoritative. The original Main Hall, speed (2.6 units/s), acceleration, deceleration, artwork and authored colliders remain intact.

## Measured response

| Real VPS connection | First local visual movement | First replicated root movement | Local release to stopped velocity | Idle visual/root difference | Checks |
| --- | ---: | ---: | ---: | ---: | ---: |
| Normal | 31.0 ms | 331.5 ms | 97.1 ms | 0.0047 units | 13/13 |
| +200 ms round trip, ±10 ms per direction, 3% datagram loss | 22.3 ms | 498.1 ms | 101.1 ms | 0.0048 units | 14/14 |

These are individual measured runs, not latency guarantees. The probe polls a sprite displacement >0.003 units once per rendered frame; it conservatively includes frame scheduling. The replicated root column measures the unpredicted server/interpolation path in the same run, not a separate build of the old client. The relay adds its delay on top of the real network. Release intentionally preserves the existing 30 units/s² deceleration.

Both runs verified local response, quick release, rapid reversals, idle convergence, wall collision/holding/sliding, escape from walls, normalized speed and correction of a client root teleport to (50,50). Gameplay probe intervals recorded no errors/warnings. Source reports retain their sampled trajectories for inspection.

## Regression and multiplayer checks

- **12/12** isolated Unity query checks: immediate movement, untouched network body, static wall sweeps, tangential contact, contact-pose sliding, snapshot timeout/recovery, out-of-order/wrapped snapshots, input replay, and preserving the latest release when replay is bounded.
- **Editor 9/9 + Standalone 8/8**, two real clients through VPS room `3ZEJZG`: unique IDs/names, separate movement, normalized speed, authoritative collision, remote motion, release and local-root manipulation correction. Both gameplay probes recorded no errors/warnings.
- Native keyboard check after the probes restored `PlayerInputSource`: holding **A for 500 ms** moved Standalone Pilot from roughly x=0.38 to x=-0.79, followed by a stable stop.

Open the Editor normally and let startup/import finish before entering Play Mode. An initial attempt to enter Play directly during CLI startup interrupted Unity web-request coroutines across a domain reload; a normal GUI Play Mode restart passed the complete Editor/Standalone checks. The premature-start helper was removed. No startup-failure report is presented as accepted evidence.

Reports: [normal](normal.json), [delayed](delayed.json), [query checks](query-tests.json), [Editor](editor.json), [Standalone](standalone.json). Screenshots: [normal](normal.png), [delayed](delayed.png), [Editor](editor.png), [Standalone](standalone.png), [native keyboard](keyboard.png).

## Trying the update

Restart Play Mode in `Assets/Scenes/Lobby.unity`, or use the rebuilt `build/agents-arena-client/AgentsArena.app`. Version 0.2.0 clients need rebuilding: public admission rejects their incompatible version. See [networking](../networking.md) for reconciliation limits and [deployment](../deployment.md#responsive-movement-validation) for reproducible probe/relay commands. Other players and player-to-player physics still depend on network updates; large disagreement can cause a visible authoritative correction.
