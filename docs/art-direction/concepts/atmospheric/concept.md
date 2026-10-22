# Atmospheric Minimalism — Quiet Company

**A quiet station makes people conspicuous.** Large chalky rooms, dark architectural voids and small, distinct figures carry the social tension. The station feels maintained and inhabited, rather than hostile. Suspicion comes from other players' behaviour.

Independent concept proposal for Agents Arena M8. All illustration, character, icon and layout geometry is original. No other competition entry was inspected while producing this proposal. These are vector studies, **not Unity screenshots or acceptance evidence**.

## Visual materials

- [Contact sheet](contact-sheet.png) / [editable SVG](contact-sheet.svg): six complete screens at an 800×600 design viewport.
- [Main Hall](main-hall.png), [Engineering](engineering.png), [Security](security.png), [Lobby](lobby.png), [Discussion](discussion.png), [Voting](voting.png). Matching `.svg` files retain editable geometry and text.
- [Character and motion board](character-motion.png) / [SVG](character-motion.svg): silhouettes, directional studies, dedicated corpse and six key poses over **0.64 seconds**.
- [Transparent corpse source](corpse-source.svg), [death and GameOver](death-gameover.png), [design system](design-system.png), [machine-readable tokens](tokens.json).

Mockup names and dialogue are invented. A message's server acceptance is shown separately from its status as a player's claim. No actor is visually identified as Human versus Agent.

## The system

| Token | Colour | Purpose |
|---|---|---|
| Void | `#171F29` | Occluded space and deep background |
| Ink | `#26343D` | Character boots, apertures, readable panel foundations |
| Paper | `#EEE9DC` | Text and the station's warm light |
| Sage | `#95B6A4` | Hall furnishings, Engineering identity, ordinary progress |
| Clay | `#D9947E` | Primary controls and one freely selectable skin |
| Lilac | `#A7ACC9` | Security identity and another freely selectable skin |
| Signal | `#E9C781` | Interaction indicators and time pressure |

One warm light direction, two flat material tones, one soft contact shadow. No continuous neon outlines. Floors have large quiet insets; walls have a darker outer mass and a narrow light top edge. Engineering uses rounded plant machinery; Security uses folded desks and horizontal record bands. Storage extends the same system with ochre crate groupings. Corridors remain sparse. All decoration must fit the actual collision footprint; room concepts are composition studies, not replacement navigation geometry.

Fog suppresses dynamic facts, not just colour. The Hall preview separates bright current sight from subdued static map memory; it does not propose revealing characters, bodies, sounds, shadows or activity through that memory. Production must use the existing server-permitted observer state. The Engineering/Security compositions show their asset language without a fog demonstration.

**Type:** proposed Noto Sans regular/medium/semibold for Latin and Cyrillic, with actual font sourcing/licence/glyph checks before integration. No font binary is bundled. These previews use local Arial/system sans. Body 15px/22px, normal labels 13px, section titles 22–26px; 11–12px is reserved for secondary metadata. Names use 13px and an identity shape as well as colour. Reflow, scroll, focus, IME and long-name behaviour need implementation and rendered QA.

**Vellum panels:** deep tinted fill at roughly 95% opacity, one thin top highlight, 10–20px corners, restrained shadow. Glass comes from layering and contrast; the design does not depend on expensive blur. Input fields are darker, primary actions clay, selection sage plus an explicit check. A future blur option must retain this opaque fallback. Buttons have a 42px minimum height in the studies. No refractive displacement.

**Hierarchy:** compact corner HUD, central world kept clear. Lobby separates personal identity from room selection. Discussion gives claims a speaker and timestamp/status space. Voting has explicit selection and final confirmation, an unavailable eliminated card, Skip, and no early public tally. Death is passive and explicit. GameOver separates team outcome from personal survival and reveals alignments only after authoritative completion. Rematch controls shown here are a design proposal, not a claim that rematch is implemented.

## Fieldkeepers: character and motion

A short utility coat, narrow vertical face aperture, two separate boots and a centred chest mark form the silhouette. The face aperture replaces the familiar wide spacesuit visor. Shoulder masses and a broad hem remain readable at the small gameplay scale. Four skins share geometry; skin and mark never encode alignment. Centre marks survive mirroring. Colour and shape distinguish players together.

Six authored directions cover the existing animation controller's front/back/left/right/two right diagonals; the two left diagonals mirror. Preserve existing clip names, the walk's normalised phase, velocity-driven playback and the physical motor. The illustrated direction strip is a pose study, not a finished eight-direction sprite sheet. Idle has a slow 1.8s breath; walking targets eight poses over 0.64s at nominal speed. Work/fake-work share the same visible gesture.

**Dedicated body:** the coat folds sideways, head rests low, both boots bend into a new silhouette. It is drawn separately, with recognisable skin and aperture. It is not an upright sprite rotated 90°. The transparent source is available for evaluation; pivot/collider/body-ID integration is still required.

| Time | Pose | Presentation |
|---|---|---|
| 0–80ms | Notice / gather | Small arm and shoulder contraction |
| 80–160ms | Contact | Short visual reach; one paper-coloured impact shape |
| 160–280ms | Fold | Victim's mass drops into the dedicated body silhouette |
| 280–440ms | Settle | Small local coat settle; no blood or physics impulse |
| 440–640ms | Recover | Attacker returns to the correct directional locomotion pose |

Presentation starts only from an accepted authoritative kill available to this observer. The simulation does not wait 640ms. The animation changes a visual child, not the replicated motor root or authoritative range. No extra command, camera teleport or hidden target lookup. The durable body is bound to the actual server body ID and permitted position; deduplication prevents repeated presentation. Own death disables actions immediately, before the gesture finishes. Reduced-motion mode uses the same outcome with a short opacity transition and no shake.

Panel focus 120ms, entrance 180ms, phase dim 240ms. Avoid slow full-screen wipes. Ballot confirmation is a check-and-tone; ejection is a brief silhouette fade, not a long punishment cinematic. Audio direction is dry felt footsteps, a quiet ceramic contact, and three soft phase tones. Local/spatial sound must obey the same permitted-event boundary; this proposal contains no audio assets yet.

## Unity feasibility and cost

Inspected current `CharacterAnimationView`, directional clips and slice metadata, `SocialBody`, `SocialClientUI`, `LobbyMenuView`, `SocialFogRenderer`, `SocialWorldBuilder`, package manifest and player prefab. Current art uses **240×350 frames / PPU 320**; the player capsule is **0.28×0.81**, offset **(0, 0.46)**. Keep those import/slice conventions during the first art replacement and align the new feet in each frame against the existing visual-to-motor registration. Do not change the collider to fit a prettier drawing. The contact-sheet characters use illustration coordinates and are not drop-in slices.

Use PNG sprite atlases from these vector sources; no runtime SVG renderer is necessary. Use separate environment layers for floor/wall/props, existing sorting and observer filtering, and the existing six-direction controller. A few reusable modular room objects and recolourable character parts keep production scope manageable. Texture/noise and dynamic lights are deliberately limited.

**UI Toolkit is a candidate**, because the project already has its module and the screens are flex/grid-like panels. The project also has uGUI 2.6.0 and URP 17.6.0. Actual Unity 6.6 font, input, layering, render-texture/blur and 800×600 performance must be proven by the integrator's compatibility spike before selecting the UI stack. These mockups do not demonstrate working backdrop filters.

Main risks: the minimal coat needs a small-scale silhouette pass; large flat floors need enough authored landmarks; face apertures must remain legible against each skin; dense discussion copy needs keyboard and localisation QA. No production files, scenes, gameplay rules, runtime, networking or GUI processes were changed for this concept.

## Reproduce

```sh
python3 docs/art-direction/concepts/atmospheric/render_concept.py
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib PYTHONPATH=build/art-tools python3.12 -c 'from pathlib import Path; import cairosvg; [cairosvg.svg2png(url=str(p), write_to=str(p.with_suffix(".png"))) for p in Path("docs/art-direction/concepts/atmospheric").glob("*.svg")]'
```

The second command uses the shared local CairoSVG tooling. SVG originals need only a normal vector editor; they do not embed raster screenshots, reference external assets or require the renderer to remain editable.
