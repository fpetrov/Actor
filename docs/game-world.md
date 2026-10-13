# Agents Arena world and passage

The multiplayer reference world contains two rooms, Main Hall and Engineering. Both rooms and their connecting corridor now use the same unchanged `map.png` atlas at 50 pixels per unit. Separate authored silhouette masks remove neighboring atlas content and rectangular crop edges. These masks are generated from vector outlines by the Editor builder and serialized as ordinary SpriteMask assets; they do not alter the source PNG or allocate meshes during gameplay.

The original offline MainHall scene, original character prefab, source PNGs, character collider, motor and owner prediction remain unchanged. Multiplayer geometry is prepared independently from the current offline scene. Existing crystal/light overrides are retained.

## Stable route and room identities

Room identities remain `main-hall` and `engineering`. Server membership still uses the physical capsule center, with the shared boundary at world y=-5.21. For the authored character capsule size0.28×0.81 and offset(0,0.46), this corresponds to root y=-5.67.

The existing game-specific route remains:

```text
(0,-1.4) → (0,-4.7) → (0,-6) → (0,-7)
```

Returning uses the same centerline. Core protocol, Standard schemas and extension schemas do not change. The reference game version changes because its authoritative world/collisions change.

## Human passage clearance

The old Engineering doorway trapped a human-sized capsule between its side pillars and the reactor circle. The capsule needed root y≤-7.195 to clear the pillars sideways, while the old reactor blocked downward movement around root y=-7.045. A straight bot could stop on the centerline, masking the lateral collision defect.

The narrow stem now ends at y=-6.17, and the reactor collision footprint follows its lower painted housing base at(0.02,-7.95), radius0.6. The shared doorway spans x[-0.48,0.48]. Two authored capsules can pass on separate centerlines, and a player can leave the stem and walk around either side of the reactor.

A safe side approach is `(0,-7.25) → (±0.9,-7.25) → (±1.5,-7.8)`. The larger downward coordinate accounts for the full capsule height against the room's visible upper curve. The lower Main Hall spawn row uses x[-0.75,-0.25,0.25,0.75] at y=-2.5; the first four spawn positions remain stable. All16 supported spawn slots are tested against the actual colliders.

Closed branches have visible amber seals. The real south corridor has directional room labels. Camera orthographic size3.4 and bounds(-4.16,-12.25)…(4.16,2.27) frame the unified atlas world while preserving the existing predicted-target follow and smoothing.

## Reproduce geometry verification

Run **ACTOR → Prepare multiplayer assets** in Unity, or `Actor.Editor.MultiplayerBuild.Prepare` from the existing Unity CLI. It rebuilds only multiplayer assets from the authored offline scene and performs58 checks:

- Room volumes and the shared authoritative boundary.
- Swept authored capsules through the passage in both directions at x0, ±0.12, ±0.20 and ±0.30.
- Diagonal approaches and returns from both Main Hall sides.
- All16 spawn capsules fit free Main Hall floor.
- Real Rigidbody2D simulation through both rooms, both side approaches and returns.
- The previously trapped human pose(0.203,-7.073) can escape to the right.
- A human capsule can pass a waiting actor on either side of the stem.
- Walls and reactor remain solid.

[Accepted geometry report](world-validation/geometry.json) and [native Editor visual check](world-validation/README.md). Local regenerated output is ignored under `build/world-polish/`. These Editor physics checks supplement real VPS multiplayer tests; they do not substitute for the Human observer run.

## Cognitive Human observer

The existing development-only observer retains ordinary keyboard input and watches a named remote actor through normal NGO replication. Add `--probe-cognitive` to require an observed stationary Engineering interval of at least2 seconds. `--probe-min-engineering-stop <seconds>` overrides that bound. Its frozen report records authoritative room transitions/revisions, remote root trajectories, Engineering dwell and longest stopped interval. An additional `-engineering-wait.png` screenshot records the pause.

```text
--probe-role O --auto-room ROOMCODE --player-name "Human Observer"
--observed-player "Agent Pilot" --probe-timeout 600
--probe-cognitive --probe-output /absolute/path/observer.json
```

No diagnostic commands replace the Human input source. The observer verifies network-visible behaviour; Brain decisions and Skill lifecycle are independently recorded by the Runtime.
