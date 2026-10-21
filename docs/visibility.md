# Social Deduction visibility

Compatibility status: the actual NGO 2.13.3 probe passed all 30 stages on three independent macOS clients and the Linux dedicated server, including copied server observer evidence. The [safe compatibility report](social-deduction-validation/visibility-compatibility.json) records the checks and scope. Integrated Social privacy acceptance remains separate; geometry and compatibility alone do not prove the Runtime boundary.

The server decides visibility. `SocialVisibilitySystem.ObserverPolicy` receives an authenticated viewer client ID and a server `NetworkObject`. The Social match session applies its own life/phase/radius/line-of-sight policy. Unknown policy, exceptions, unknown viewers and missing update integration deny remote observers. The owner always retains its own controller object, including after death or GameOver.

`SocialVisibilityMember` installs `SpawnWithObservers=false` and `CheckObjectVisibility` in `OnNetworkPreSpawn`. This must be present on the prefab before its first spawn. In installed NGO 2.13.3, `NetworkSpawnManager.AddPlayerObject` otherwise adds players to each other's observers without consulting the callback. Setting a renderer invisible or installing the callback after spawn cannot close that initial replication path.

The system diffs server observer sets immediately before NGO `NetworkPreUpdate`, after physics and before NetworkVariable/NetworkTransform network ticks. Unity's public PlayerLoop API inserts this callback; it does not modify the NGO package. The exact pinned NGO stage is checked by the compatibility probe. Missing integration fails closed for remote observers. The match session also calls `RefreshNow()` after authoritative phase/life changes, before permitted deliveries. A new manually spawned body uses `SpawnWithObservers=false`, returns from `Spawn()`, and only then gets `RefreshNow()`/`NetworkShow`. Calling Show inside `OnNetworkSpawn` for a manually spawned object queues a duplicate initial spawn.

Every active server visibility pass records its elapsed time and the actual number of visible observer/object pairs after the diff, including the pre-network passes. Bounded performance samples are saved only after GameOver; they do not become live observations or expose actor identities.

`NetworkHide` removes a server observer and despawns/destroys the dynamic remote object. `NetworkShow` sends a fresh spawn and initial transform state. The owner is never hidden: the existing owner's despawn handler would disable keyboard/prediction and disconnect its bridge. The same controller is retained for its private terminal view. Alignment and hidden duty never use an Everyone-readable variable, even on a visible avatar. Owner-targeted RPCs are deliberately independent of observer filtering; permitted event audiences must therefore be selected by the server, not inferred from the RPC API.

Spatial DTOs are complete owner-targeted replacement lists, including empty lists. Removing an actor from current visibility must not leave a current position in Runtime's merged WorldState. Historical sightings can remain explicitly historical. The visibility audience must also govern body facts, kill witnesses, interaction availability and phase/proximity communication. Sandbox/Investigation keep their existing policy. Social mode excludes the legacy public duty roster, global cooperation peers and station-wide signal bypass.

Only authored `SocialVisibilityOccluder` physical colliders block `LineOfSight`. Player colliders and interaction triggers do not. Nonfinite points and a saturated bounded ray-hit buffer fail closed. The Social world builder attaches these markers to its real walls and opaque fixtures.

## Repeatable compatibility fixture

Use an isolated SocialDeduction room, containing no ordinary players, and a server process started with trusted diagnostic flags:

```text
--visibility-spike-role server --visibility-spike-output /absolute/server-visibility.json
```

These flags are process configuration, not a public game command. The fixture replaces visibility policy only in that explicit diagnostic process. It does not launch a Runtime or model and is not gameplay acceptance evidence.

Build the ordinary client/server with `SocialVisibilityMember` on the registered player prefab, then run:

```sh
build/runtime-venv/bin/python spikes/visibility/run_probe.py \
  --room ROOM01 --output build/visibility-compatibility-01 --timeout 150
```

An isolated loopback control plane can be selected with `--api-url http://127.0.0.1:5081`. This is forwarded only to the ordinary game client; the probe accepts no bridge credential and starts no Runtime.

The launcher starts three independent ordinary clients named Visibility-A/B/C with `-batchmode -nographics`, 800×600, fullscreen disabled and a 60 FPS cap. It owns and stops its children. The server must be in the isolated diagnostic mode before they join. A copied server report from the same run can additionally be supplied with `--server-report /absolute/server-visibility.json`; the summary explicitly states whether server evidence was checked.

Thirty stages check initial owner-only spawn, all-visible state, selective B→A hiding, same-frame pending Show cancellation, twenty hide/show cycles and actual manual non-player spawn/hide/move/show/despawn. Each client validates its actual `SpawnedObjects` set, stable owner instance, fresh authoritative owner reconciliation, and current permitted body transform. The server validates observer sets and waits for three actual client acknowledgements at every stage. Client completion requires the server's final confirmation, avoiding process-cleanup races. Reports contain bounded checks and counts, no hidden positions, roles, tokens or raw network payloads. The fixture does not claim packet-capture evidence or an SDK/Runtime no-leak check; those require the integrated Social mode acceptance run.

Auth-free launcher projection tests:

```sh
build/runtime-venv/bin/python spikes/visibility/run_probe.py --self-test
```

Geometry verification is available as `Actor.Editor.SocialVisibilityVerification.Run` through Unity CLI or the fixed Editor command delegated by the build owner. It checks actual Physics2D wall/trigger behavior and the prefab's early visibility member; it does not start a network session.

## Authored Social world

`SocialWorldBuilder.AddToCurrentSocialScene()` operates only on the separate saved SocialDeduction clone. The existing Main Hall/Engineering scene and source `map.png` remain unchanged. The Social clone opens the eastern route and adds Security and Storage with the same 50 PPU atlas transform. Its corridor union includes explicit square turn landings; simple butt-ended rectangles left real capsule-sized gaps at angled turns. The physical corridor width is 1.15 m.

The graph includes all six terminal approach nodes, including a western Engineering detour around the reactor. `ValidateCurrentSocialScene()` checks every node and both directions of every edge with the actual 0.28×0.81 m capsule, including ±0.08 m offsets. Each terminal additionally needs a free approach, actual authoritative room resolution, clear sight and a physical interaction distance within 1.25 m. Failed checks name bounded collider blockers instead of weakening clearance.

After validation, the builder exports `Assets/World/SocialLayout.json` from the same geometry: floor polygons and interaction points use physical capsule-center coordinates; graph nodes and approach points use actor-root coordinates. The exact root-to-center offset is `(0, 0.46)`. Runtime receives this public authored map rather than maintaining another manually copied graph. It contains no dynamic player location or hidden match state.

The final terminal checks exposed an Edit-mode room-registry lifecycle failure and a blocked life-support interaction: the terminal at `(-0.8, -6)` sat outside the narrow Engineering entry floor. Moving it south then blocked the western Engineering route in the real capsule sweep. The final authored placement uses the clear eastern flank at `(0.8, -6.6)`, with approach root `(0.3, -7.15)`. Validation initializes and releases the actual runtime room registration for its test. The corrected scene passed all 195 actual geometry checks and a complete server-authoritative sabotage match.

The fog mesh is presentation only. It follows predicted local presentation for responsive Human movement, reads its radius from the permitted own visible-world projection, and is disabled in headless/server execution. All serialized fields have the same layout in Editor, client and server builds.
