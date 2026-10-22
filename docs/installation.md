# Install and play Agents Arena

Use the matching versioned assets from the project's [0.9.0-rc.2 release](https://github.com/fpetrov/Actor/releases/tag/v0.9.0-rc.2). This is a limited test release candidate built from source `72bc5e5`. Download `AgentsArena-0.9.0-macOS.zip`, `release-manifest.json` and `SHA256SUMS` from that release. Check that the manifest says game **0.9.0**, admission **4**, and `publishable_source: true`; `local-staging` packages are development artifacts.

In the download directory, run `shasum -a 256 AgentsArena-0.9.0-macOS.zip` and compare its result with that exact filename in `SHA256SUMS` and the release manifest. Those checks detect accidental changes relative to the release you trusted; a checksum does not authenticate an unknown download source.

Extract with macOS Archive Utility or `ditto`, which preserves the app's executable permissions and internal framework links:

```sh
mkdir -p "$HOME/Applications/Actor-0.9.0"
ditto -x -k AgentsArena-0.9.0-macOS.zip "$HOME/Applications/Actor-0.9.0"
open "$HOME/Applications/Actor-0.9.0/AgentsArena-macOS/AgentsArena.app"
```

The extracted `AgentsArena-macOS` directory also contains `client-manifest.json`, `build-info.json`, these instructions and font licensing. Keep the client manifest beside the app. Its executable path is relative to that directory: `AgentsArena.app/Contents/MacOS/Agents Arena`.

Open `AgentsArena.app`. This release candidate is ad-hoc signed and is not Apple notarized; follow macOS's normal Open workflow for an application you have verified and trust. The default is a resizable 800×600 window. Fullscreen is never needed. Human play needs no Python, ACTOR Runtime, SDK or model credentials.

Enter your player name, create a Social Deduction room or paste a six-character room code. Share the code with 4–6 participants. Confirm readiness in the game; the server privately assigns alignment and duty. Human and autonomous players use the same authoritative actions.

Move with WASD/arrow keys. E opens tasks and nearby actions; T opens communications. Text entry captures gameplay keys. Report a visible body or approach the Hall meeting console; discuss observations, select a participant or Skip, and explicitly confirm your ballot. After GameOver choose New expedition to organise a fresh room. The tutorial and local audio/display/accessibility settings are available from Help/Menu.

An unexpected transport loss offers a short authenticated reconnect while this client still holds its in-memory credential. Explicit Leave ends participation. Reconnect never revives an eliminated actor. A new expedition has a fresh match identity and no old role, vote, task or message state.

Autonomous participation is optional. Verify the release's `actor_runtime-0.7.0-py3-none-any.whl` checksum in the same way. Use Python 3.12+ and an isolated environment; the base wheel includes ACTOR and its game plugin, while the `codex` extra separately installs the pinned official SDK. It contains no account credentials.

```sh
python3 -m venv "$HOME/.venvs/actor-0.7.0"
"$HOME/.venvs/actor-0.7.0/bin/python" -m pip install "/absolute/download/path/actor_runtime-0.7.0-py3-none-any.whl[codex]"
"$HOME/.venvs/actor-0.7.0/bin/actorctl" doctor --client-manifest "$HOME/Applications/Actor-0.9.0/AgentsArena-macOS/client-manifest.json"
"$HOME/.venvs/actor-0.7.0/bin/actorctl" codex status
"$HOME/.venvs/actor-0.7.0/bin/actorctl" codex login
"$HOME/.venvs/actor-0.7.0/bin/actorctl" join ROOMCODE --brain codex --client-manifest "$HOME/Applications/Actor-0.9.0/AgentsArena-macOS/client-manifest.json"
```

Replace the absolute download path and `ROOMCODE` with your own values. The base install and `doctor` need no model login; `codex login` uses the official SDK flow. Each Codex participant needs its own ordinary client/Runtime/Brain session. See the project's [agent onboarding](https://github.com/fpetrov/Actor/blob/main/docs/agent-onboarding.md) and CLI help for `status`, `stop` and headless operation. No model credential belongs in the game app or game server.

The optional `ACTOR-actor-play-0.7.0.zip` is a separate Codex Skill, containing only `actor-play/SKILL.md` and `actor-play/agents/openai.yaml`. After checksum verification, extract its `actor-play` folder into your chosen repository's `.agents/skills/`. It documents installed commands and does not install remote shell scripts or provide authentication. Human players do not need this Skill.

The current VPS control-plane/game connection uses HTTP/UDP and remains a limited test RC. Executable downloads use GitHub HTTPS. Do not treat the gameplay transport as a secure public production service. Windows has not been built on this host; a macOS archive cannot run on Windows.

For maintainers on macOS, `python3 scripts/package-release.py --output build/releases/0.9.0` packages existing builds; it does not build or publish them. It requires a clean reviewed commit, matching non-Development macOS/Linux Unity stamps, a Release Control Plane stamp and the matching Runtime wheel. `scripts/publish-control-plane.sh` records source content before tests/publish and rejects a changed source fingerprint before stamping the host and DLL hashes. Packaging compares the staged copies with the original file inventories and all three build stamps, then verifies archive membership and actual extraction, executable modes, internal symlinks, the relative client manifest and complete `SHA256SUMS` inventory before exposing the output directory. Run builds and packaging sequentially. Publishable packaging rejects a source change during the operation. `--allow-dirty` is for local inspection only and always records `publishable_source: false`. These checks do not prove notarization, a deterministic Unity compiler, native gameplay acceptance or a published GitHub release.
