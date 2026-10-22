# Design Battle review — UI/UX Art Director

Independent review after completing Quiet Industrial. Reviewed all three contact sheets, both competing character/motion boards, native-size Main Hall/Lobby/meeting/voting previews, Atmospheric's concept notes and the SVG generators. Luminous's `concept.md` was not present when review began; its visual system/production boards and source notes were available. No production implementation, actual UI input or performance acceptance is inferred from any mockup.

## Same ten criteria, same scale

Scores are **1–10, higher is better**. Asset production effort scores reward a direction that can be completed with less effort while retaining its shown quality. Performance and maintainability scores estimate the proposed production approach; they are not measured FPS or tested engine behaviour. Totals are unweighted and the one-point lead is a preference, not a scientific distinction.

| User criterion | Atmospheric Minimalism | Luminous Station | Quiet Industrial |
|---|---:|---:|---:|
| Visual cohesion | 9 | 9 | 9 |
| Originality | 9 | 8 | 7 |
| Readability at 800×600 | 8 | 9 | 9 |
| Gameplay clarity | 8 | 9 | 8 |
| Fog-of-war readability | 9 | 6 | 9 |
| UI usability | 8 | 9 | 8 |
| Character animation feasibility | 8 | 9 | 7 |
| Performance | 9 | 9 | 9 |
| Asset production effort | 9 | 8 | 8 |
| Maintainability | 9 | 9 | 9 |
| **Total / 100** | **86** | **85** | **83** |

My first preference is **Atmospheric Minimalism as a complete visual family**, narrowly ahead of Luminous. Luminous is the strongest immediately legible six-player UI proposal. Industrial is the most conservative typography/engineering proposal, but I would not select it solely because I authored it.

## Atmospheric Minimalism — strengths, problems, improvements

The narrow vertical face aperture, broad utility-coat hem and small centered identity mark give the Fieldkeepers a stronger proprietary silhouette than my familiar helmeted worker. The clay/sage/lilac room family feels inhabited and calm without defaulting to neon-blue spaceship lighting. The warm room mass against deep void creates a clear figure/ground distinction. The native Main Hall preview actually shows a sight boundary and subdued static map, rather than relying on a statement that fog will eventually exist.

The Discussion layout is appropriately conversational. Its wording distinguishes player statements from what a player personally witnessed; server acceptance is not presented as truth or Brain readership. Voting requires selection and confirmation, and the unavailable participant is visibly different. Original vector geometry, two-tone shading and a sparse module set make this a plausible whole-game production target.

The main weaknesses are visible, not merely hypothetical:

- Main Hall, Engineering and Security currently have broadly similar flat room masses. Their colors change more than their architecture. The main Hall crystal is a decorative focal point, while the callable meeting console is a tiny wall object; the interaction prompt can feel detached from the actual target.
- Pale sage machinery on pale floor loses useful object separation. A small lilac corpse or a folded limb may merge with its own contact shadow and neighboring shapes at game size. The character's good large-scale identity still needs a 40–50 px silhouette check in all directions.
- The shown Voting composition uses four cards in a 2×2 grid plus a large explanatory sidebar. Six players and long localized names would consume the remaining space; the six-person state is not shown. Some small task/footer hints are 10–11 px in the source and should not carry required instructions.
- The hard circular sight edge communicates radius, but the visual proposal still needs one real wall/door occlusion example. A circle alone must not become the implementation model for permission.

I would improve it by assigning each room one large unmistakable landmark silhouette, adding a darker structural base under functional equipment, and making interactable consoles carry a consistent small physical paper-colored service tab. I would replace most of the Voting advice sidebar with a collapsible hint at minimum resolution and prove six cards plus a pinned footer. Raise required metadata to at least 12 px, preserve 15–16 px chat, and test the dedicated corpse on each room floor before polishing effects. These refinements can stay entirely inside Atmospheric's own palette and shape language.

## Luminous Station — strengths, problems, improvements

The layered ceramic wall ribs are the strongest environmental construction in the competition. Main Hall's offset border, quiet plate seams and readable corridor opening imply actual depth while leaving generous traversable space. The consoles read as physical interactables. Engineering's pressure module and Security's archive banks are coherent variations of the same construction. Character light bodies against the darker floor are immediately identifiable.

The Surveyors' dark hood, twin small eyes and folded mantle are simple enough for a controlled directional sprite set. The separate corpse keeps the recognizable mantle/hood rather than rotating the standing figure. The six-player Voting grid is already shown at a native 800×600 half-board, with a persistent selected-candidate area and final confirmation. Discussion demonstrates six participants, broad text, timestamps and a Cyrillic message. That is valuable concrete coverage absent from the other two meeting mockups.

The weaknesses I would fix before approving production are:

- Main Hall's fog study is a broad radial vignette. All three actors remain visible, with no crisp architectural occlusion example or demonstrated removal beyond the permitted region. It currently reads as attractive lighting more than as a gameplay sight limit. This is why its fog score is lower; the score does not allege an actual privacy bug.
- Teal highlights appear in the crystal, architecture, own-player ring, primary controls and selection. The meanings can blur. The character's nearly black head also sits close to the darkest architectural values; the bright torso rescues it, but rear-facing identification needs a small-scale check.
- In the Lobby, a Join link in every room row, another Join next to the code, and a large Create action compete. A player must determine which set of controls applies before the primary intention is clear. Required room-row hit areas and keyboard focus are not established by the visual.
- Small 10–11 px labels such as the exploration/own-state and control hints are vulnerable at the minimum viewport. The type hierarchy is generally good, but the smallest information should be optional.
- More polished multi-band wall ribs and angled room modules create a larger art-authoring burden than the flat Atmospheric kit. The system must cover corridor joins, not just three attractive isolated rooms.

I would produce a genuine wall-cut sight mockup with no hidden actor glyphs first. Keep the glow baked and restrained; reserve a consistent signifier for usable objects while limiting general trim emission. Establish one primary Lobby intention, make each room row a full clear target, and use separate Create/Join flows. Author the minimum corridor junction kit beside the room kit and test all player skins/head directions against the darkest floor. Luminous should retain its own folded-light and mantle language if selected; importing my industrial helmet or Atmospheric's face aperture would weaken it.

## Quiet Industrial — defense and honest limits

My core argument is that a social game benefits from a calm station whose UI can survive a difficult discussion. Paper-colored text against the graphite base has measured **10.12:1** contrast, all emitted type is at least 12 px, and the main message body is 15–16 px. The visual family supplies Lobby, waiting, exploration, fog, Discussion, Voting, death and Game Over without a second decorative vocabulary. Server acceptance, final-ballot state and team victory versus personal survival remain explicit. These are useful product constraints, not merely attractive panels.

The new folded-limb corpse, narrow-body silhouette and separate hands/boots also make a physical action easy to read. The 640 ms sequence can use the same stable visual child/motor boundary as the current game. Graphite frames, low-frequency seams and local amber service strips can be baked into a limited atlas. The low-cost glass fallback is part of the intended appearance, so blur is not a fragile dependency.

I nevertheless score my own originality and animation effort lower. The rectangular helmet/visor worker is recognizable but more conventional than the competing characters. Separate articulated arms and boots require more convincing directional walk/collapse work. The three octagonal room studies share too much perimeter geometry, and their repeated mechanical panels can feel like an orderly engineering prototype rather than a surprising place. My Lobby gives a clear selected-room action but does not yet show the actual code-entry/Create/error dialogs; its secondary text links still require generous invisible focus/hit regions in implementation. The room concepts are not canonical floor-plan replacements or production geometry proof.

If Industrial were chosen, its first art pass should create distinctive room construction and material landmarks, refine the character beyond a standard utility helmet, and demonstrate full transition/focus states before adding extra machinery. More thin seams, decals or warning stripes would not solve the weakness. I would preserve its UI hierarchy and restrained motion even if the environment gains more material depth.

## Decision recommendation

Choose **one** atmosphere, one character family, one room-construction system and one token set. My vote is Atmospheric, with explicit conditions: show six-player Voting, strengthen equipment/body separation, and add real architectural fog composition before production. Luminous remains a close alternative if the integrator prioritizes already-concrete six-player screen composition and richer wall construction.

Interaction semantics can be shared without an art collage: select then confirm, truthful delivery status, clear text focus, pinned final actions and no hidden-state UI are common product requirements. Do not combine Atmospheric characters, Luminous walls and Industrial panels just to keep everyone's favorite object. If Atmospheric wins, develop its own walls and UI further. If Luminous wins, develop its own fog and Lobby further. All directions still need a real UI-stack/blur spike, actual sprite/corpse integration, native input/localization checks and standalone performance evidence.
