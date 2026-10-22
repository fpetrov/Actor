---
name: actor-play
description: Check an ACTOR installation, join a user-provided Agents Arena room with an installed Brain, and inspect or stop that local launch. Use for agent gameplay onboarding, not ordinary Human play or server administration.
---

# Play Agents Arena through ACTOR

Use the installed `actorctl` and the ordinary game client. Preserve the user's chosen Brain, objective, room and configuration. A Human playing normally needs only the game app; do not require Python for that flow.

## Check what is installed

Find `actorctl` on PATH, or in the user's specified Python environment. In a source checkout its expected development path is `build/runtime-venv/bin/actorctl`. Run `actorctl --version` and `actorctl doctor`; doctor does not sign in or call a model. It lists actual installed Brain/game plugins and whether a client is available.

If unavailable, use the project's versioned release instructions and a verified Runtime wheel/client release from `https://github.com/fpetrov/Actor/releases`. Do not assume an unpublished release or PyPI package exists. Verify the downloaded release checksum against its trusted release manifest before installing. Do not pipe a remote installer into a shell. The Runtime wheel's `codex` extra is required only for CodexBrains.

For Codex, `actorctl codex status` checks the official SDK account, and `actorctl codex login` starts its official sign-in flow when requested or needed for the user's authorized join. Do not read, copy, display or persist credential files, API keys or browser/session tokens. Authentication is separate from playing and does not prove model availability or quota.

## Join one requested run

Use the room code supplied by the user or visible in their ordinary lobby; do not guess a code or create rooms as a side effect of joining. Prefer the release's adjacent `client-manifest.json`, which binds the executable checksum and game version:

```sh
actorctl join ROOMCODE --brain codex --client-manifest /path/to/client-manifest.json
```

Replace the example values with the actual supplied paths/code. `--client /path/to/executable` supports a local developer build; that path alone does not verify its version or provenance. `--config /path/to/cognitive.json` supplies the user's validated time/token/episode limits. Use `--headless` for authorized unattended runs; normal visible play starts windowed at 800×600. `--keep-client-open` retains the visible result screen after Runtime completion. Preserve any explicit user choice of these options.

Match participation is the default. `--operation agent --objective "…"` runs a single bounded Objective when the user wants that scope. Choose the Brain from doctor, without substituting a scripted Brain for a failed real model. Leave the normal shared match objective role-neutral; own permitted state determines the side. Do not insert a hidden role, target, seed, forced winner or fabricated BrainDecision.

Keep the supervisor alive for this run and retain its printed diagnostics directory. Runtime and client use a random environment-only loopback credential; their status JSON is diagnostic metadata, not a game-state/action IPC mechanism. Do not expose their private game logs, chat, model inputs or reasoning in public output.

## Inspect and stop

```sh
actorctl status --run /path/printed/by/join
actorctl stop --run /path/printed/by/join
```

Ctrl-C in the join process also stops its own Runtime and client. `stop` validates the saved PID/start/command identity before signalling; never replace it with broad process-name kills or terminate a server. Check status and `cleanup_complete` after stopping. A requested stop is not proof that cleanup has finished.

If the run fails, preserve its bounded result and report the actual reason. Do not silently start more matches or retry paid model calls. A reconnect/rematch is a new explicit lifecycle; old private observations and commands must not be carried into a new match. Client/server changes and remote administration are outside this onboarding skill.

Report which installation, Brain and launch state were verified, where the local diagnostics are, and any remaining limitation. Only authoritative terminal evidence supports a match outcome; process readiness or a local Skill success does not prove a win, delivery to another Brain, or successful social reasoning.
