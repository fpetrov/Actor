#!/usr/bin/env bash
source "$(dirname -- "$0")/common.sh"
ACTOR_SOURCE_SNAPSHOT="$(mktemp "${TMPDIR:-/tmp}/actor-cp-source.XXXXXX")"
trap 'rm -f -- "$ACTOR_SOURCE_SNAPSHOT"' EXIT
python3 "$ACTOR_ROOT/scripts/build-provenance.py" snapshot --output "$ACTOR_SOURCE_SNAPSHOT"
"$ACTOR_DOTNET" test "$ACTOR_ROOT/Actor.sln" --nologo --results-directory "$ACTOR_ROOT/build/test-results"
# An interrupted publish must not leave a previous build's trusted stamp.
rm -f -- "$ACTOR_ROOT/build/control-plane/build-info.json"
"$ACTOR_DOTNET" publish "$ACTOR_ROOT/src/Actor.ControlPlane/Actor.ControlPlane.csproj" \
  --configuration Release --runtime linux-x64 --self-contained true \
  --output "$ACTOR_ROOT/build/control-plane" --nologo
python3 "$ACTOR_ROOT/scripts/build-provenance.py" stamp-control-plane \
  --snapshot "$ACTOR_SOURCE_SNAPSHOT" --output "$ACTOR_ROOT/build/control-plane/build-info.json"
