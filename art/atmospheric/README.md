# Atmospheric Minimalism — production source

Original vector artwork and short synthesized audio for ACTOR, authored for this repository. No image, sprite, recording, external font or model output was copied. Concept sheets remain frozen in `docs/art-direction/concepts/`; these are independent editable production masters.

Run from the repository root:

```sh
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib PYTHONPATH=build/art-tools python3 art/atmospheric/generate.py
python3 -m unittest discover -s art/atmospheric -p 'test_*.py'
```

`source/*.svg` are self-contained editable vectors. The generator writes 28 SVGs, 28 PNGs and eleven mono 48kHz PCM cues. `manifest.json` records exported-byte hashes and the semantic hash of the existing public Social layout. Regeneration does not modify that layout, physics, original atlas, legacy scenes or original character assets.

World sprites use 100 pixels per unit (the 2× export of the existing logical 50PPU world). The floor union spans `(-4.5,-12.25)` to `(9.6,6.4)`; its sprite is centered at `(2.55,-2.925)`. Decorative inlays are clipped to the exact public floor polygons. Unity layered walls follow the existing composite boundary, including its real door openings. Terminal and reactor artwork adds no collision or interactive authority. Three wall landmarks (Hall directory, Security instrument/archive bank, Storage cargo rack) are positioned from editable placements.json entirely beyond the walkable floor union; polygon/rectangle intersection tests verify that they add no deceptive walk-through floor furniture. Their diagrams are pictograms, while readable names remain retained UI labels.

Six directional sheets contain eight 240×350 frames each at 320PPU, with pivot `(0.5,8/350)`. `UpLeft`/`DownLeft` retain the existing animation view's mirroring of right diagonals. The original controller graph and its 10Hz walk cadence stay intact through an override controller. A dedicated magenta fabric mask is replaced by the original palette shader; the hood, two ivory eyes, boots and shadow remain neutral. The server-approved spawn slot selects six distinct mantle colours and six visible stitch shapes, stays stable through resume and is captured on the body at death, independently of alignment, duty, input source or display name. `character-review.png` is a coloured reference, never a character runtime asset.

`fold-0..2.svg` are separate authored folding silhouettes, not a rotated standing sprite. `fold-2` is the persistent body; a recipient-filtered server presentation event may play the intermediate poses for 640ms. The static body does not infer or replay an attack. Cosmetic audio/presentation never delays authoritative death, movement or reporting.

Unity integration: `Actor.Editor.AtmosphericArtImporter.Prepare()` imports assets, builds an independent Resources catalog and override clips, and adds only cosmetic MonoBehaviours to generated multiplayer prefabs. `MultiplayerBuild.Prepare()` invokes the original Social renderer through `SocialWorldBuilder`; Sandbox keeps its existing art and the original offline prefab remains untouched. No new NetworkBehaviour is introduced by this art pipeline.

Audio uses original quiet deterministic sine-based envelopes. `ArenaAudio.PlayUI(Cue)` handles local UI; the permitted event consumer handles message/task/meeting/emergency/game-over cues, and only the allowed kill-presentation recipients hear a world impact. Master volume belongs to the UI's AudioListener setting; sources apply only EffectsVolume. Music is optional and no music track is claimed. Headless/server processes create no presentation or audio host.

Source checks are not native acceptance. Root-owned Unity compile/import, original geometry/prediction checks, actual standalone readability, colour contrast, LOS loss, witness/victim/attacker/reconnect behaviour, settings and rendered performance must be verified before release.
