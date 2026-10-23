#!/usr/bin/env bash
source "$(dirname -- "$0")/common.sh"
mkdir -p "$ACTOR_ROOT/build/web"
rsync -a --delete --delete-excluded --exclude='.DS_Store' "$ACTOR_ROOT/web/frontend/" "$ACTOR_ROOT/build/web/"
test -f "$ACTOR_ROOT/build/web/index.html"

# The presentation is a separate buildless page under the same static host.
# Keep research notes and QA screenshots in Git, outside the public bundle.
mkdir -p "$ACTOR_ROOT/build/web/presentation"
rsync -a --delete --delete-excluded --exclude='.DS_Store' \
  --include='/index.html' --include='/style.css' --include='/app.js' \
  --include='/station-data.js' --include='/CREDITS.md' --include='/assets/***' \
  --exclude='*' "$ACTOR_ROOT/presentation/" "$ACTOR_ROOT/build/web/presentation/"
test -f "$ACTOR_ROOT/build/web/presentation/index.html"
