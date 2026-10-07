#!/usr/bin/env bash
source "$(dirname -- "$0")/common.sh"
"$ACTOR_UNITY" -batchmode -quit -projectPath "$ACTOR_ROOT/Agents Arena" \
  -buildTarget StandaloneOSX -standaloneBuildSubtarget Player \
  -executeMethod Actor.Editor.MultiplayerBuild.BuildMacClient \
  -actor-output "$ACTOR_ROOT/build/agents-arena-client/AgentsArena.app" \
  -logFile "$ACTOR_ROOT/build/logs/client-build.log"
test -d "$ACTOR_ROOT/build/agents-arena-client/AgentsArena.app"
