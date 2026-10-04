# First playable milestone validation

Verified on 5 October 2026 (Europe/Moscow), using Unity **6000.6.4f1**, the existing URP 2D pipeline and the MainHall scene in real Editor Play Mode.

**101 checks passed, 0 failed, 0 runtime errors, 0 warnings.** The current code also compiled with no C# errors or warnings; the Unity Console was inspected through Computer Use.

The [complete Play Mode report](playmode-report.json) records:

- Acceleration and deceleration in all eight directions, ending at zero velocity after release.
- Equal axial and diagonal speed: 2.600 units/s (diagonal components about 1.838).
- Directional walk frames, idle state, preserved last facing and both mirrored diagonal directions.
- The existing Input System Move action with eight keyboard combinations, using a temporary keyboard device.
- Each of 42 wall segments, both sides of the north corridor under simultaneous forward/outward input, and four approaches to the crystal base collider.
- Camera follow/clamping, both 2D lights, authored actor/room identifiers and physics configuration.

The visual review included the live Unity Game view, all eight captured idle directions, sprite alignment, the room, the crystal glow and the camera. Screenshot capture waits for the render frame before changing direction. Physics contact assertions allow the engine's configured contact tolerance while still preventing passage through geometry.

![Main Hall in Play Mode](main-hall-playmode.png)

The runtime source PNGs remain unchanged. All 48 character frames have generated metadata with measured rectangles and a common foot pivot. There are 12 animation clips and reusable character/crystal prefabs. Asset GUID checks found no duplicates; every authored Unity asset has metadata.

Repeat using **ACTOR > Verify playable in Play Mode** with the Game view focused. Generated local reports and screenshots are ignored in `Agents Arena/Verification/`; this directory stores the accepted milestone evidence.
