# Initial retained UI integration check

Actual rendered non-Development macOS standalone0.9.0, Unity6000.6.4f1, Metal, window800×600. Working-tree integration build made2026-10-21T21:00Z; these are **intermediate UI checks**, not final art/gameplay/release acceptance.

- [Lobby](lobby-800x600.png): corrected dark ordinary buttons and sage primary actions. The earlier build used lowercase USS type selectors, which failed to style Unity's `Button` type; the failure was seen natively and corrected.
- [Settings](settings-800x600.png): actual background blur, local settings and visible keyboard focus. A separate compatibility probe contains the measured fallback/blur comparison.
- [Unicode paste](unicode-paste-800x600.png): native click, Select All and paste of `Тест ACTOR` into the ordinary player-name field; verified visually. This proves Cyrillic paste/rendering, **not IME composition**, gameplay input suppression or chat delivery.
- [Initial frame sample](lobby-initial.json):121 rendered frame intervals over1.008s,120.05fps mean, p95 9.10ms. This short warm UI-only sample is not a game performance benchmark or a hardware presentation measurement.

No room was created or joined. Native settings were opened/closed; the client was closed after the checks. No model calls, private chat or credentials appear in these screenshots. A subsequent source refinement expands the mode dropdown, brightens toggle marks and keeps voting confirmation visible at reduced viewport size; it needs the next integrated build.
