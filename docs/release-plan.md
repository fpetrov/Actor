# Milestone 8 production plan

Baseline: `4a4cbbd` (functional M7 `89c00dd`). The working tree was clean before this milestone. [Baseline checks and inventory](release-validation/baseline.json) passed before production edits: 491 Runtime + 69 script tests, 96 subtests, 232 SDK + 23 Control Plane tests, 54 generated-artifact checks.

This milestone is a product release candidate. Compilation, fixtures and CI alone do not satisfy the release gates.

## Work sequence

1. Three independently authored, visual whole-game concepts; mutual critique and ten-criterion review. The root integrator selects one coherent art direction before production art changes.
2. Actual Unity 6000.6.4f1 / URP17.6 screen-overlay UI Toolkit compatibility and blur/fallback measurement; establish retained UI components, view models and one shared token system.
3. Integrate original editable environment/character/corpse source assets, private recipient-scoped kill presentation, restrained local audio, settings and quality options. Preserve movement prediction and the legacy Sandbox map.
4. Replace runtime IMGUI across player flows with retained UI: menu, lobby, HUD, communication, meetings, ballot, death, endgame, return/rematch and tutorial. Controllers continue to submit server-validated intents.
5. Improve generic bounded provenance/knowledge/context orchestration and Arena-specific social guidance, discussion batching, navigation/pursuit and task variety. Establish balance by comparable actual experiments rather than forced win rates.
6. Add authenticated short-lived resume, fenced connection generations, stale-intent rejection and fresh-instance rematch isolation. Keep game ontology out of ACTOR Core.
7. Run unit/regression, simulated integration, actual headless multiplayer, rendered/native Human and real independent Codex matches as separate evidence classes. Preserve all attempted runs and technical failures in the denominator.
8. Produce versioned macOS client and Linux server, checksummed manifest, HTTPS release assets, compatible VPS deployment with a final zero-room guard, green CI and clean source state.

## Release gates

- Visual: integrated original assets; standalone Lobby/HUD/Chat/Discussion/Voting/Death/GameOver screenshots at 800×600, 1280×720 and 1920×1080; ultrawide usability; blur fallback and rendered performance.
- Human: actual native mouse/keyboard, Unicode/IME/paste/focus, report, meeting, ballot, death, endgame and rematch; no Python/SDK requirement for Human players.
- AI: several independent Codex full matches, aiming for five valid starts, plus every failure; real kill, discovery/report, multi-agent discussion, ballot and an evidence-based assessment of attempted deception. Outcomes alone do not prove social reasoning.
- Mixed: ordinary Human and Codex participants using the same server authority, without privileged Human/Agent metadata.
- Recovery: actual runtime/client/server faults, resume identity fencing and rematch privacy; no ghost input, duplicate kill/ballot or post-terminal command.
- Release: generated freshness, installed-wheel verification, legacy regressions, actual rendered/server/network measurements, safe artifacts and deployment. HTTP-only gameplay remains a limited test RC unless valid HTTPS is available; downloads use a verified HTTPS release channel.

## Baseline asset inventory

The Unity Assets tree initially contained nine PNGs, twelve animation clips, one controller and two materials; no shipped font, audio, UXML or USS assets. Six authored character directions provide eight directions through diagonal mirroring. Runtime UI used IMGUI in lobby, Social, communication, investigation, interaction prompts, control mode, wayfinding and nameplates. The installed project uses UI Toolkit module, uGUI2.6, Input System1.20, URP17.6 and NGO2.13.3.

Core1.0 and both Standard0.1 packages remain universal. Resume/admission changes belong to the game admission contract; social capabilities belong to Arena. Production gameplay and model credentials are not public validation artifacts.

All long clients run headless. Native checks use small windowed clients; fullscreen is never selected by the agent. No active production room is restarted for deployment.
