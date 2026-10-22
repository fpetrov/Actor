# Install and play Agents Arena

The candidate target is [v0.10.0-rc.1](https://github.com/fpetrov/Actor/releases/tag/v0.10.0-rc.1). At this source checkpoint it is a **planned prerelease link, not a verified download** or a publication, deployment or acceptance receipt. The historical M8 deployment used Game **0.9.0 / admission 4**; a 0.10.0 client cannot join a server still advertising 0.9.0. Check the connected server's advertised version before choosing a release.

For a server advertising Game **0.10.0 / admission 4**, use matching versioned assets from the project's [GitHub Releases](https://github.com/fpetrov/Actor/releases) once that candidate is published and verified. Take all files from the same release: `AgentsArena-0.10.0-macOS.zip`, `release-manifest.json` and `SHA256SUMS`. Check the exact source commit recorded in the manifest, Game **0.10.0**, Arena extension **0.7.0**, Runtime **0.8.0**, admission **4**, and `publishable_source: true`; `local-staging` packages are development artifacts. ACTOR Core remains **1.0.0** and both Standard packages remain **0.1.0**. If the matching release is unavailable, do not substitute an older client. The commands below describe that candidate's expected filenames, not an already completed download.

In the download directory, run `shasum -a 256 AgentsArena-0.10.0-macOS.zip` and compare its result with that exact filename in `SHA256SUMS` and the release manifest. Those checks detect accidental changes relative to the release you trusted; a checksum does not authenticate an unknown download source.

Extract with macOS Archive Utility or `ditto`, which preserves the app's executable permissions and internal framework links:

```sh
mkdir -p "$HOME/Applications/Actor-0.10.0"
ditto -x -k AgentsArena-0.10.0-macOS.zip "$HOME/Applications/Actor-0.10.0"
open "$HOME/Applications/Actor-0.10.0/AgentsArena-macOS/AgentsArena.app"
```

The extracted `AgentsArena-macOS` directory also contains `client-manifest.json`, `build-info.json`, these instructions and font licensing. Keep the client manifest beside the app. Its executable path is relative to that directory: `AgentsArena.app/Contents/MacOS/Agents Arena`.

Open `AgentsArena.app`. The candidate packaging uses ad-hoc signing, not Apple notarization; verify the particular artifact's receipts before following macOS's normal Open workflow for an application you trust. The default is a resizable 800×600 window. Fullscreen is never needed. Human play needs no Python, ACTOR Runtime, SDK or model credentials.

Enter your player name, create a Social Deduction room or paste a six-character room code. Share the code with 4–6 participants. Confirm readiness in the game; the server privately assigns alignment and duty. Human and autonomous players use the same authoritative actions.

Move with WASD/arrow keys. E opens tasks and nearby actions; T opens communications. Text entry captures gameplay keys. Report a visible body or approach the Hall meeting console; discuss observations, select a participant or Skip, and explicitly confirm your ballot. After GameOver choose New expedition to organise a fresh room. The tutorial and local audio/display/accessibility settings are available from Help/Menu.

An unexpected transport loss offers a short authenticated reconnect while this client still holds its in-memory credential. Explicit Leave ends participation. Reconnect never revives an eliminated actor. A new expedition has a fresh match identity and no old role, vote, task or message state.

Autonomous participation is optional. Verify the release's `actor_runtime-0.8.0-py3-none-any.whl` checksum in the same way. Use Python 3.12+ and an isolated environment; the base wheel includes ACTOR and its game plugin, while the `codex` extra separately installs the pinned official SDK. It contains no account credentials.

```sh
python3 -m venv "$HOME/.venvs/actor-0.8.0"
"$HOME/.venvs/actor-0.8.0/bin/python" -m pip install "/absolute/download/path/actor_runtime-0.8.0-py3-none-any.whl[codex]"
"$HOME/.venvs/actor-0.8.0/bin/actorctl" doctor --client-manifest "$HOME/Applications/Actor-0.10.0/AgentsArena-macOS/client-manifest.json"
"$HOME/.venvs/actor-0.8.0/bin/actorctl" codex status
"$HOME/.venvs/actor-0.8.0/bin/actorctl" codex login
"$HOME/.venvs/actor-0.8.0/bin/actorctl" join ROOMCODE --brain codex --client-manifest "$HOME/Applications/Actor-0.10.0/AgentsArena-macOS/client-manifest.json"
```

Replace the absolute download path and `ROOMCODE` with your own values. The base install and `doctor` need no model login; `codex login` uses the official SDK flow. Each Codex participant needs its own ordinary client/Runtime/Brain session. See the project's [agent onboarding](https://github.com/fpetrov/Actor/blob/main/docs/agent-onboarding.md) and CLI help for `status`, `stop` and headless operation. No model credential belongs in the game app or game server.

The optional `ACTOR-actor-play-0.8.0.zip` is a separate Codex Skill, containing only `actor-play/SKILL.md` and `actor-play/agents/openai.yaml`. After checksum verification, extract its `actor-play` folder into your chosen repository's `.agents/skills/`. It documents installed commands and does not install remote shell scripts or provide authentication. Human players do not need this Skill.

Broadcast observation is a separate operator workflow. Its dedicated commentary sign-in, local BF16 voice, protected OBS setup and explicit public-start action are described in the [Broadcast guide](https://github.com/fpetrov/Actor/blob/main/docs/broadcast.md). Joining a room as a player does not start a broadcast. The observer operator key grants only protected observer admission; it is separate from OBS WebSocket, Twitch and Codex credentials. Keep all of these credentials out of downloaded manifests, game arguments and public reports.

The current VPS control-plane/game connection uses HTTP/UDP and remains a limited test RC. Executable downloads use GitHub HTTPS. Do not treat the gameplay transport as a secure public production service. Windows has not been built on this host; a macOS archive cannot run on Windows.

For maintainers on macOS, `python3 scripts/package-release.py --output build/releases/0.10.0-rc1` packages existing builds into a fresh output directory for the planned tag; it does not build or publish them. Asset basenames retain Game `0.10.0` and Runtime `0.8.0`, rather than the tag's prerelease suffix. It requires a clean reviewed commit, matching non-Development macOS/Linux Unity stamps, a Release Control Plane stamp and `build/runtime-dist/actor_runtime-0.8.0-py3-none-any.whl`. `scripts/publish-control-plane.sh` records source content before tests/publish and rejects a changed source fingerprint before stamping the host and DLL hashes. Packaging compares the staged copies with the original file inventories and all three build stamps, then verifies archive membership and actual extraction, executable modes, internal symlinks, the relative client manifest and complete `SHA256SUMS` inventory before exposing the output directory. The distribution contains five products plus the release manifest and checksum index: seven files, with six checksum entries. Run builds and packaging sequentially. Publishable packaging rejects a source change during the operation. `--allow-dirty` is for local inspection only and always records `publishable_source: false`. These checks do not prove notarization, a deterministic Unity compiler, native gameplay acceptance or a published GitHub release. A Linux QA player build is not an additional promised public platform artifact; no Windows candidate is supplied.
