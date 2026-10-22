#!/usr/bin/env bash
source "$(dirname -- "$0")/common.sh"
mkdir -p "$ACTOR_ROOT/build/web"
rsync -a --delete --delete-excluded --exclude='.DS_Store' "$ACTOR_ROOT/web/frontend/" "$ACTOR_ROOT/build/web/"
test -f "$ACTOR_ROOT/build/web/index.html"
