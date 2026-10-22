# Milestone 8 design battle

Three agents independently authored complete visual proposals before opening competing work. Each includes native 800×600 compositions, editable vector sources, character/corpse/kill studies, palette, typography and implementation notes. These are **concept drawings**, not screenshots or gameplay acceptance evidence.

| Proposal | Visuals and source | Independent critique |
|---|---|---|
| Luminous Station | [Board](concepts/luminous/contact-sheet.png), [proposal](concepts/luminous/concept.md) | [Environmental director](concepts/luminous/critique.md) |
| Quiet Industrial | [Board](concepts/industrial/contact-sheet.png), [proposal](concepts/industrial/concept.md) | [UI/UX director](concepts/industrial/critique.md) |
| Atmospheric Minimalism | [Board](concepts/atmospheric/contact-sheet.png), [proposal](concepts/atmospheric/concept.md) | [Character/motion director](concepts/atmospheric/critique.md) |

All reviewers used the same ten equally weighted criteria: visual cohesion, originality, readability at 800×600, gameplay clarity, fog readability, UI usability, animation feasibility, performance, production effort and maintainability. Scores are subjective design assessments. Performance and effort are estimates, not measurements.

| Reviewer | Luminous /100 | Industrial /100 | Atmospheric /100 | First preference |
|---|---:|---:|---:|---|
| Environmental | 84 | 85 | 86 | Atmospheric |
| UI/UX | 85 | 83 | 86 | Atmospheric |
| Character/motion | 83 | 87 | 83 | Industrial |
| Mean | 84 | 85 | 85 | — |

## Integrator decision

**Atmospheric Minimalism is the production foundation.** It received two of three first preferences; the tied mean does not imply an objective winner. Its quiet pastel room planes, dark figures and restrained ink/glass panels are a coherent, feasible whole-game family. Industrial better demonstrated some interaction states; Luminous better demonstrated six-player meetings and continuity with the existing ACTOR hood. Those critiques become requirements inside the selected family, rather than a collage of competing assets.

Before acceptance, production must resolve the proposal's concrete gaps:

- Preserve ACTOR's hood and two ivory eyes, with a broad coloured mantle and a non-role identity mark. No helmet or face-aperture replacement.
- Prove six participants, long names and a pinned confirmation action at minimum resolution.
- Add waiting/ready, own alignment/duty, cooldown, message receipt, unavailable and terminal states.
- Keep essential labels at least 13px and body/chat text 15–16px at default scale.
- Give each room a distinct large landmark and darker equipment bases without blocking tested routes.
- Draw dedicated folded corpse and intermediate death poses; verify contrast on every floor.
- Render actual wall-clipped visibility. Hidden actors, bodies, effects and sounds must remain absent, rather than merely darkened.
- Use original editable assets, one token family and bounded motion. Preserve movement/prediction timing.

The subsequent [style guide](style-guide.md) governs implementation. The actual [UI compatibility probe](../release-validation/ui-compatibility/accepted.json) proves the selected engine can render overlay blur with a shipped shader and tinted fallback. It does not prove game UI usability, input or rendered game performance.
