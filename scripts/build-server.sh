#!/usr/bin/env bash
source "$(dirname -- "$0")/common.sh"
"$ACTOR_UNITY" -batchmode -quit -projectPath "$ACTOR_ROOT/Agents Arena" \
  -buildTarget Linux64 -standaloneBuildSubtarget Server \
  -executeMethod Actor.Editor.MultiplayerBuild.BuildLinuxDedicatedServer \
  -actor-output "$ACTOR_ROOT/build/agents-arena-server/AgentsArenaServer" \
  -logFile "$ACTOR_ROOT/build/logs/server-build.log"
test -x "$ACTOR_ROOT/build/agents-arena-server/AgentsArenaServer"
