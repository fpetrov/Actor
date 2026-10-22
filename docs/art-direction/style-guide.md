# ACTOR — Atmospheric Minimalism production guide

Selected by the [design battle](design-battle.md). Original quiet station interiors, hooded fieldkeepers and restrained glass panels form one family. Source concept tokens: [tokens.json](concepts/atmospheric/tokens.json).

## Shape, colour and material

Large low-noise floor planes, gently chamfered room edges, shallow layered wall depth and a few functional landmarks. Broad shapes carry the room; fine repeated grids do not. Dark structural equipment bases distinguish props from the floor. Service tabs use warm paper and a small sage/gold accent. Decorative lights remain quieter than interactive objects.

| Token | Hex | Use |
|---|---|---|
| void | #171F29 | unseen space |
| ink | #26343D | structure, figures, primary dark text |
| panel | #24313C | glass fallback |
| paper | #EEE9DC | light surface |
| floor | #D9D8C9 | neutral Hall floor |
| sage | #95B6A4 | Engineering, constructive action |
| clay | #D9947E | Storage, restrained danger |
| lilac | #A7ACC9 | Security |
| gold | #E9C781 | selection, attention |
| muted | #B5C0C2 | secondary text on dark |
| white | #FAF7F0 | primary text on dark, eyes |
| edge | #5B6A72 | borders |
| shadow | #52615F | baked physical shadow |

Skins are stable cosmetic actor-identity choices independent of alignment, duty, Human/Runtime mode and hidden facts. Their small identity mark supplements colour. The black hood and two ivory eyes remain recognisable; the folded cloak gives weight without delaying the motor. Preserve six directional families, diagonal mirroring, existing pivot/capsule and movement prediction. Corpse has its own drawn folded mass, boots and retained identity colour; it is never a rotated idle character.

Hall: broad navigation inlay and meeting service point. Engineering: sage mechanical workbench/pipes and the reactor containment landmark. Security: lilac records rack/instrument bank. Storage: clay cargo/storage landmark. Corridors retain this material family. Landmarks must fit existing geometry, leave doors and terminal approaches clear, and add no unvalidated solid obstacle. Keep the legacy Sandbox artwork/geometry unchanged.

## UI and typography

One retained UI Toolkit stack, screen-space overlay, responsive flex layout. Shared presenters/view models submit existing server intents; no hidden-state lookup to decorate UI. Use a shipped Unicode font with a recorded redistribution licence. Default body/chat15–16px, required labels13px, titles26px; long names wrap/ellipsis with accessible full text. Spacing4/8/12/16/24/32px. Dark glass has opaque-enough tint for contrast without blur. Native blur is optional and switchable; tinted fallback is a full supported appearance.

At800×600, meeting participants use a compact six-person grid and scrolling discussion; final vote actions remain visible. Selection is reversible until explicit confirmation. Show pending/server-accepted/rejected message states without claiming Brain readership or truth. Own role and cooldown are local; final public alignment revelation occurs only when server postgame authorises it. Menu never implies multiplayer pause.

Focused text/IME/paste and modal navigation suppress movement/actions synchronously; closing/disposal releases capture. Unicode chat, keyboard access, unread counts, retained scroll and focus must be verified in the actual native client. Settings persist master/music/effects volume, UI scale, blur, reduced motion, windowed mode and resolution.

## Motion, sound and privacy

Focus120ms, panel180ms, meeting240ms, kill640ms. Reduced motion removes nonessential travel/pulse. Authoritative death/report/action does not wait for presentation. Presentation uses visual children, stable event identity and bounded lifetime; repeated snapshots/reconnect cannot replay a kill. Effects and world audio are scoped to events the owner is allowed to receive and cease when visibility is lost. Never derive global presence from lights, names, sounds or hidden object inspection. Local UI feedback may be generated from the owner's accepted action.

Audio should be original short quiet tones/noise with a reproducible source generator and provenance. Music is optional, not a reason to mask message/task cues. Camera and rendering remain economical: atlas ordinary sprites, bake decorative shadows, avoid per-prop realtime lights or bloom dependency.

## Production and acceptance

Editable source → deterministic raster export → Unity import with documented pixels-per-unit, pivot, filtering and atlas policy. Record author/licence for every external asset. Concept PNGs are design evidence only. Capture real standalone Lobby/HUD/Chat/Discussion/Voting/Death/GameOver at800×600,1280×720,1920×1080 plus ultrawide usability, real input and rendered performance separately. Do not label a fixture preview or headless loop as Human acceptance or client FPS.
