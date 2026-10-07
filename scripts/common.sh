#!/usr/bin/env bash
set -euo pipefail
ACTOR_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ACTOR_DOTNET="${DOTNET:-dotnet}"
if [[ -z "${DOTNET:-}" && -x "$ACTOR_ROOT/build/tools/dotnet/dotnet" ]]; then
  ACTOR_DOTNET="$ACTOR_ROOT/build/tools/dotnet/dotnet"
fi
ACTOR_UNITY="${UNITY_EDITOR:-/Applications/Unity/Hub/Editor/6000.6.4f1/Unity.app/Contents/MacOS/Unity}"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_CLI_HOME="${DOTNET_CLI_HOME:-$ACTOR_ROOT/build/tools/dotnet-home}"
mkdir -p "$ACTOR_ROOT/build/logs"
