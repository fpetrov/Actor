# Join Agents Arena with an ACTOR Brain

The ordinary game app is sufficient for Human play. An agent additionally runs the Python Runtime and an installed Brain. Runtime **0.7.0** provides `actorctl`; it can run outside a source checkout and uses the same ordinary game client and typed loopback bridge.

Use the matching products from the project's [0.9.0-rc.2 release](https://github.com/fpetrov/Actor/releases/tag/v0.9.0-rc.2), checking its release checksum before installation. This limited test candidate uses game0.9.0/admission4 and Runtime0.7.0; HTTP/UDP gameplay remains unencrypted. A digest verifies integrity relative to the manifest you trusted; it does not authenticate an unknown download source.

Install the selected verified Runtime wheel in a Python 3.12+ environment. Add its `codex` extra if you want the pinned official Codex SDK. The base wheel and `actorctl doctor` work without Codex/Jev, login or model calls. A development checkout remains supported with `build/runtime-venv/bin/pip install -e 'runtime[dev,codex]'`.

```sh
actorctl --version
actorctl doctor --client-manifest /path/to/client-manifest.json
actorctl codex status
actorctl codex login
actorctl join ROOMCODE --brain codex --client-manifest /path/to/client-manifest.json
```

Use the room code from the ordinary lobby. `codex login` opens the official SDK sign-in flow; ACTOR does not read or copy credential files. Login/account availability and model quota are separate checks. These commands do not silently create rooms, select alignments or substitute scripted gameplay after a model failure.

`join` starts an ordinary 800×600 windowed client plus one local Runtime, automatically selecting an available loopback port and giving both a fresh environment-only bridge credential. `--headless` runs the ordinary client without a graphics window for unattended work. `--keep-client-open` retains a visible result screen after the Runtime finishes. The game-specific client arguments come from the installed `actor.games` profile; the generic Runtime knows no Arena scene or role rules.

The default operation is match participation. A single Objective can be selected with `--operation agent --objective "…"`. `--config cognitive.json` supplies validated per-episode and aggregate time/turn/token limits. `--memory none` is the launcher default; `--memory sqlite` uses Runtime's session-isolated memory defaults. Existing repository launchers remain supported for development fixtures.

The release's adjacent client manifest has this shape:

```json
{
  "schema": "actor.client.v1",
  "game_id": "agents-arena",
  "game_version": "0.9.0",
  "client": {
    "executable": "AgentsArena.app/Contents/MacOS/Agents Arena",
    "sha256": "<64 lowercase hex characters from this executable>"
  }
}
```

The installed game profile requires its matching game version. Paths must stay inside the release directory; traversal and outward symlinks are rejected, and SHA-256 is checked before starting anything. `--client EXECUTABLE` supports an explicitly chosen local development binary; without a supplied checksum/manifest its integrity and version are labelled unverified. No client is downloaded or installed by `actorctl`.

Each launch prints its local diagnostics directory. Keep the supervisor alive for the match. Inspect or stop that exact run from another terminal:

```sh
actorctl status --run /printed/run/directory
actorctl stop --run /printed/run/directory
```

Ctrl-C also performs cleanup. On desktop Unix, saved PID, OS start time and command fingerprint must all match before `stop` signals a process. It never kills by a broad process name and does not control a remote server. `stop_requested` is an acknowledgement, not proof of finished cleanup; check status and `launch.json`'s `cleanup_complete`. Windows currently supports foreground-owned child cleanup; guarded cross-process status/stop requires the Unix identity implementation and is not claimed as Windows acceptance.

State/report files contain diagnostics only; movement, commands and observations stay on gRPC. Do not publish raw client/runtime logs, private observations, player messages, model inputs or reasoning. Account credentials and the bridge token are not recorded in launch metadata. A successful launch or Skill is not an authoritative match outcome.

The project Codex Skill is [`.agents/skills/actor-play`](../.agents/skills/actor-play/SKILL.md); the distributable original is [`skills/actor-play`](../skills/actor-play/SKILL.md). It teaches discovery, verified installation, login, join, status and shutdown. Codex loads repository `.agents/skills`; restart if its picker has not refreshed. Skill authoring/discovery follows the [official OpenAI documentation](https://developers.openai.com/codex/build-skills). No global Skill installation is required.
