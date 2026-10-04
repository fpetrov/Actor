# ACTOR

ACTOR will connect interchangeable autonomous agent brains to real-time games. `Agents Arena/` is its Unity reference game. The current milestone is a local, playable Main Hall; networking, agent runtimes and social-deduction systems are future work.

## Run

Open `Agents Arena/` in **Unity 6000.6.4f1** and open `Assets/Scenes/MainHall.unity`. Press Play and focus the Game view. Move with **WASD**, **arrow keys**, or a gamepad left stick. The build scene list already points to Main Hall.

Git is rooted here, not inside the Unity project. Install Git LFS before cloning/pulling art; run `git lfs pull` if a checkout contains pointer files. Assets, Packages and ProjectSettings are versioned; Unity caches and local verification output are ignored.

## Character architecture

`PlayerInputSource` implements `IMovementInputSource` using the existing Input System `Player/Move` action. `ActorCharacter` passes commands to `CharacterMotor2D`; the motor clamps input magnitude and accelerates/decelerates `Rigidbody2D.linearVelocity` in `FixedUpdate`. It owns facing, independent of the visual. `CharacterAnimationView` observes motor facing and actual velocity and controls the child Animator and sprite reflection. `CameraFollow2D` follows the interpolated visual position in LateUpdate with room bounds.

Use `ActorCharacter.SetInputSource(IMovementInputSource)` to substitute agent, network or replay commands. No motor/animation changes are needed. Passing null stops movement. The interface preserves analog magnitude, normalizes excessive input, and rejects non-finite values at the motor boundary.

`CaptureState()` returns a data-only snapshot containing ActorId, position, velocity, room, facing, moving, role and interaction state. Scene instances have authored IDs; freshly instantiated prefabs obtain unique runtime IDs. A future save/network spawning system can supply persisted IDs before Awake. The only current role is Unassigned and interaction is None. Room tracking is explicit through `SetCurrentRoom`; the single room needs no room detector yet. `WorldEntity` supplies a separate ID and room for the crystal.

## Art and animation

The six character sheets are **1774 × 887**, with **4 × 2** complete poses each. Their existing alpha-based automatic slicing split body fragments and noise into separate sprites. Eight **240 × 350** rectangles per sheet now capture complete poses; each rectangle is aligned to the head centre and foot baseline, using the same pivot `(0.5, 8/350)` and **320 PPU**. `Assets/Editor/CharacterSliceLayout.json` records the measured source rectangles. No original PNG has been modified or generated.

The art is antialiased illustration, so textures use bilinear filtering, no compression, no mipmaps, clamped wrapping and full-rectangle meshes. Main Hall uses 100 PPU (12.54 world units); the separate crystal uses 700 PPU to match room/character scale. The full station map is retained as reference art.

There are **12 looping clips**: Idle and Walk for Down, Up, Left, Right, UpRight and DownRight. Walk clips use all eight supplied poses at **10 fps**, with playback rate following physical speed. Idle holds pose 1 because the source has no separate idle sheets. A flat Animator controller has no transitions or blend-tree ambiguity; the view selects a state only when facing or movement changes and preserves walk phase on direction changes. UpLeft and DownLeft reflect the right diagonal clips with `flipX`. Left uses its own supplied sheet.

## Physics, scene and lighting

The reusable character prefab has a dynamic Rigidbody2D, zero gravity, frozen rotation, continuous collision detection and interpolation. A horizontal **0.34 × 0.18** feet capsule uses a frictionless, non-bouncing material. Defaults: **2.6 units/s**, **22 units/s² acceleration**, **30 units/s² deceleration**. These settings are editable in the Inspector.

Main Hall has 42 separate wall segments under `EnvironmentCollision`, tracing the actual walkable floor seam and four corridors. The corridor endpoints are closed boundaries until other rooms exist. Collision geometry is separate from the sprite. The crystal is a reusable entity prefab with a **0.95 × 0.35** base capsule, independent of its tall visual.

The existing **URP 2D renderer** is retained. A 0.95 Global Light keeps the room readable; the unlit crystal and a restrained blue additive Point Light (radius 2.3) provide the glow. The hall renders behind actors; character/crystal sorting uses their foot/base pivots and the camera's Y sort axis. Camera smoothing is 0.12 seconds at orthographic size 4.1.

## Editor tooling and verification

`ACTOR > Verify playable in Play Mode` opens Main Hall, enters actual Play Mode and runs movement, Input System, directional animation, mirroring, idle facing, wall, crystal and camera checks using real physics updates. It restores local input and leaves the scene playable. The report and screenshots are written to ignored `Agents Arena/Verification/`. Keep the Game view focused during the keyboard checks. The accepted milestone passed **101 checks**, with no errors or warnings; see [validation evidence](Docs/VALIDATION.md).

`ACTOR > Build first playable Main Hall` regenerates imports, clips, controller, prefabs and scene from measured source data. **It replaces the authored Main Hall scene and prefab setup**; use it deliberately when rebuilding this milestone, not after making custom scene edits. The fixed file commands in `Verification/editor-command.txt` (`build`, `verify`, `status`, `stop`) support the same workflow in an already open Editor; the tooling is editor-only.

Removed unused template content: `Assets/Welcome/`, `Assets/Settings/Scenes/URP2DSceneTemplate.unity`, `Assets/Settings/Lit2DSceneTemplate.scenetemplate` and the empty `Assets/Scenes/SampleScene.unity`, including their metadata. References were checked before removal; the original Git snapshot preserves them. Pipeline settings, Input System bindings and all nine user PNGs remain.

## Next milestone

Add one adjacent room with explicit room volumes and a minimal interactable contract for the crystal. Then feed a deterministic scripted/replay command source into the same character facade to validate the future agent-control boundary before introducing networking or an agent runtime.

## Unity API references

- [Rigidbody2D.linearVelocity](https://docs.unity.com/en-us/engine/6000.0/script-reference/unityengine/rigidbody2d/linearvelocity) for physics velocity.
- [Sprite Editor data provider API](https://docs.unity.cn/Packages/com.unity.2d.sprite%401.0/manual/DataProvider.html) for stable sprite IDs and import metadata.
