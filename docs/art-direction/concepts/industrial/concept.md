# Quiet Industrial — The Steady Shift

Independent Design Battle proposal, made before seeing the other entries. All visuals here are original editable vector drawings. These are design mockups, not Unity screenshots or tested production assets. Names, messages and outcomes in them are fictional UI examples.

![Main Hall](main-hall.png)

A working station with private intentions. Graphite structural frames, desaturated mineral floors, warm paper-colored service panels and short amber signal strips make the setting feel inhabited and maintained. The same restrained material language becomes smoked glass in the interface. The emotional contrast is deliberate: the station is calm; the uncertainty comes from people.

The distinguishing shape is a **chamfered structural frame with a pale inset**, repeated at room scale, on machinery, on the worker's separate helmet and on UI cards. Separated shoulders, hands and boots avoid a bean-shaped character silhouette. There is no decorative neon field, black-on-black HUD, universal cyan hologram or Alignment-coded costume.

## Visual boards

| Board | Editable source | Full-size preview |
|---|---|---|
| Six-screen overview | [contact-sheet.svg](contact-sheet.svg) | [contact-sheet.png](contact-sheet.png) |
| Main Hall + compact HUD | [main-hall.svg](main-hall.svg) | [main-hall.png](main-hall.png), 800×600 |
| Engineering | [engineering.svg](engineering.svg) | [engineering.png](engineering.png), 800×600 |
| Security | [security.svg](security.svg) | [security.png](security.png), 800×600 |
| Fog of war | [fog-of-war.svg](fog-of-war.svg) | [fog-of-war.png](fog-of-war.png), 800×600 |
| Find a room | [lobby.svg](lobby.svg) | [lobby.png](lobby.png), 800×600 |
| Ready / waiting lobby | [boarding.svg](boarding.svg) | [boarding.png](boarding.png), 800×600 |
| Discussion | [discussion.svg](discussion.svg) | [discussion.png](discussion.png), 800×600 |
| Voting | [voting.svg](voting.svg) | [voting.png](voting.png), 800×600 |
| Own death | [death.svg](death.svg) | [death.png](death.png), 800×600 |
| Game Over | [game-over.svg](game-over.svg) | [game-over.png](game-over.png), 800×600 |
| Character, dedicated corpse, kill | [character-motion.svg](character-motion.svg) | [character-motion.png](character-motion.png), 1600×900 |
| Palette, type, components, motion | [design-system.svg](design-system.svg) | [design-system.png](design-system.png), 1200×900 |

## One station, four useful identities

Main Hall is the communal working deck: a low pale meeting table, an amber console and the broadest clear floor. Engineering is denser and heavier: concentric pressure housing, grilles, vertical pipe runs and sea-glass coolant indicators. Security is ordered and cool: archive drawers, slate displays and repeated small records panels; screens never become remote surveillance feeds. Storage would reuse the same frame family with low wheat-colored bins, tied cargo and large aisle numbers. Corridors use continuous graphite edges and small destination labels; amber thresholds mark real openings, and solid material indicates actual obstruction.

Floor seams have low contrast and low frequency. No fake doorway, floor arrow that points into collision, bright scenery competing with a body, or light pulsing in response to an unseen event. The room drawings establish material/prop language rather than authorize a new collision layout. Production art should fit the current canonical floors and tested routes first.

## Human-first interface

- **Lobby:** own name and Human/Agent choice first; a short room list; one clear Join action; Join by Code/Create are secondary. The waiting view gives readiness and start conditions. Other participants are visually identical whether controlled by a person or an agent. No Python or gRPC text in the Human flow.
- **Exploration:** 44 px top strip, one collapsible task card, compact local messages and an interaction prompt. Own private Alignment has a small local-only badge. The center stays available for movement and bodies. A cooldown belongs to the own action control, never a distant-player marker.
- **Chat:** author/color marker, readable 15–16 px body, preserved scroll position, explicit channel, unread count and factual pending/accepted/failed status. A server-accepted message never receives a “read” tick. Enter focuses then sends; Escape returns to play; Tab and IME composition remain normal text input. Movement/interaction shortcuts are suppressed while text input owns focus.
- **Discussion:** a dedicated meeting surface with public rollcall on the left and broad messages on the right. Statements remain statements; the UI does not label an alibi as a verified observation. Own notes can be expanded from existing permitted history, with source and age. No extra truth classifier.
- **Voting:** select a card, then confirm. Skip is a separate secondary control. Pending confirmation is locked until a server receipt; accepted final ballot is visibly final. No foreign ballot indicator or tally before authorized resolution. Six cards fit a 3×2 grid at 800×600.
- **Death:** one quiet transition, explicit out-of-play state and no live actions. Watching preserves the existing information boundary, including no new remote world or roles. **Game Over** separates team victory from own survival, then presents authoritative terminal roles and a new-match/return flow.
- **Recovery/settings:** connection loss uses a compact top banner with retry/leave and zero active gameplay input. Settings use the same panel family for audio, text scale, blur quality and reduced motion. Development diagnostics stay behind a separate optional drawer.

The SVG rectangles describe reference compositions, not a proposed runtime rectangle renderer. UI Toolkit Flexbox/Grid-equivalent reusable containers should drive production layout. At 800×600 the design uses 24 px margins, 40 px actions, 16 px primary text and 12–14 px supporting text. At 1280×720 and 1920×1080 the center meeting surface caps its readable line length; extra width gives world breathing room. Ultrawide must not stretch chat across the screen. Six-player lists or increased text size scroll inside their own regions, keeping the footer actions visible. Real native screenshots and text/focus tests remain required.

## Palette, typography and components

| Token | Value | Use |
|---|---|---|
| Void | `#101a20` | outside space, opaque fallback |
| Graphite | `#24343b` | structural walls, UI base |
| Mineral floor | `#657572` | quiet walkable surfaces |
| Paper | `#eae4cf` | helmet, service panels, strong text |
| Amber | `#e1b16c` | primary action, local attention |
| Sea glass | `#8dbdb3` | successful own work, coolant |
| Slate | `#8daebe` | archive equipment, identity color |
| Clay | `#d68f7f` | warnings and another identity color |

World materials do not assign moral meaning to costume colors. Status always has text or an icon as well as color. The concept previews use Arial/Courier fallbacks; production target is **Noto Sans + Noto Sans Mono**, acquired with their OFL attribution before shipping. Monospace is reserved for timers/codes. No condensed all-caps paragraph text. Cyrillic, English, paste and IME need the actual chosen font assets and native tests.

Components use 8 px control radii, 12 px panels, one restrained top highlight and a soft 5 px shadow. Large glass surfaces get a dark 94–97% body with a tiny gradient; this avoids losing text against a bright scene. Actual backdrop blur is an optional layer, not how contrast is achieved. No refraction/distortion. Paper text on the dark base exceeds 10:1 contrast; amber buttons use dark labels.

## Characters and action presentation

The worker has a rectangular pale helmet, a small dark visor, separate neck seal, short colored jacket and visible boot gap. Wheat, slate, sage and clay skins change jacket/identity accents only. Alignment is never encoded. The contact sheet includes front, back and side studies; production needs the existing six authored direction families plus mirrored diagonals and phase-matched walk loops. Keep current foot/root placement and capsule.

The corpse is **new folded-limb artwork**, not the live sprite rotated. It retains the helmet and jacket colors, sits on a compact contact shadow, and has a wider low profile that reads at game size. No glowing beacon or through-wall outline. The 0.64 s kill storyboard uses 90 ms anticipation, 70 ms compact impulse, 200 ms collapse and 280 ms settle. Four small paper-colored sparks replace blood. The server has already accepted the action; only visual children animate. Event identity deduplicates presentation. Observers, target and actor receive only the presentation authorized for them. Spatial impact sound is witness-scoped, never global.

Other timing: hover 80 ms, press 60 ms, panel 160 ms, phase transition 240 ms; task progress is steady rather than springy; a submitted vote gets one local mechanical tick. Reduced motion removes travel/overshoot and uses a short opacity step plus clear status text. Audio should be dry, low-volume service tones: soft switch click, two-note phase cue, short pressure impact and sparse public-emergency warning. No borrowed game audio is included.

## Unity fit and production gates

The audited project is Unity **6000.6.4f1**, URP **Renderer2D**, SpriteLit material and a six-direction idle/walk controller with mirrored diagonals. World atlas is 50 PPU; current character frames are 240×350 at 320 PPU with a bottom-biased pivot. Preserve that imported character extent/pivot initially, and the existing `(0,0.46)` capsule offset and `0.28×0.81` size. New art is a SpriteRenderer/atlas replacement, not a motor or prediction rewrite. SVG masters can be rasterized deterministically at appropriate module resolution; use sprite atlases, bounded decoration and low-cost contact shadows. No per-prop realtime blur or expensive 3D lighting is required.

Choose **UI Toolkit as the one primary UI stack**, with ViewModel → Presenter → reusable UXML/USS components. Presenters read existing permitted owner DTOs and send existing controller intents. Core/SDK never receives art/UI concepts. The installed URP asset currently has no opaque/depth texture requirement and Renderer2D has no configured feature; native backdrop effect must therefore pass the integrator's real compatibility/performance spike before assuming it works. Opaque tint fallback must look equivalent in layout and readability. Do not add uGUI just to decorate one panel.

Must measure before production approval: glass costs at 800×600/720p/1080p; text scaling/IME/focus; six-player meeting/vote; own death input lock; world/corpse contrast at game size; witness-only animation/audio; unchanged geometry routes and observer filtering; actual standalone rendered FPS. None is claimed by these vector mockups.

## Cost and tradeoffs

This direction favors modular environment frames, two-value prop shading and limited VFX, making a complete small station feasible. The main risk is becoming too utilitarian or empty. Add identity through a few authored mechanical landmarks and carefully staged light, rather than more decals. The character needs more directional animation work than a floating capsule; its separated limbs repay that cost in motion/body readability. Smoked glass is intentionally restrained; the result should feel tactile rather than like an operating-system clone.

All drawings and the renderer are original for this proposal; no external artwork/font files are embedded. [render.py](render.py) regenerates 13 SVG/PNG pages; [manifest.json](manifest.json) records dimensions. CairoSVG is a local build-only rasterizer. No production source, scene, prefab, protocol, gameplay or test state was changed. Other concepts were not inspected before completing this entry.
