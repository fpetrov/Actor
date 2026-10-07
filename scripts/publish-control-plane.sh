#!/usr/bin/env bash
source "$(dirname -- "$0")/common.sh"
"$ACTOR_DOTNET" test "$ACTOR_ROOT/Actor.sln" --nologo --results-directory "$ACTOR_ROOT/build/test-results"
"$ACTOR_DOTNET" publish "$ACTOR_ROOT/src/Actor.ControlPlane/Actor.ControlPlane.csproj" \
  --configuration Release --runtime linux-x64 --self-contained true \
  --output "$ACTOR_ROOT/build/control-plane" --nologo
