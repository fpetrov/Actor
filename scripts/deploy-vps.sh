#!/usr/bin/env bash
source "$(dirname -- "$0")/common.sh"
ACTOR_HOST="${1:?Usage: deploy-vps.sh HOST USER SSH_KEY}"
ACTOR_SSH_USER="${2:?Missing SSH user}"
ACTOR_SSH_KEY="${3:?Missing SSH key path}"
ACTOR_RELEASE="$(date -u +%Y%m%dT%H%M%SZ)-$(git -C "$ACTOR_ROOT" rev-parse --short HEAD)"
test -x "$ACTOR_ROOT/build/control-plane/Actor.ControlPlane"
test -x "$ACTOR_ROOT/build/agents-arena-server/AgentsArenaServer"
test -f "$ACTOR_ROOT/build/web/index.html"
ACTOR_GAME_VERSION="$(python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["development"] is False and v["options"] == "None"; print(v["game_version"])' "$ACTOR_ROOT/build/agents-arena-server/build-info.json")"
[[ "$ACTOR_GAME_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { printf 'Invalid built game version.\n' >&2; exit 2; }
# Restrict remotely interpolated values to prevent shell injection.
[[ "$ACTOR_HOST" =~ ^[a-zA-Z0-9.-]+$ && "$ACTOR_SSH_USER" =~ ^[a-zA-Z0-9_-]+$ ]] || { printf 'Invalid deployment host/user.\n' >&2; exit 2; }
[[ -f "$ACTOR_SSH_KEY" && "$ACTOR_SSH_KEY" != *"'"* ]] || { printf 'Invalid deployment SSH key path.\n' >&2; exit 2; }
ACTOR_REMOTE="$ACTOR_SSH_USER@$ACTOR_HOST"
ACTOR_SSH=(ssh -i "$ACTOR_SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 "$ACTOR_REMOTE")
ACTOR_RSYNC_SSH="ssh -i '$ACTOR_SSH_KEY' -o BatchMode=yes -o ConnectTimeout=15"
# Read-only early guard. Repeat with external admission drained immediately
# before activation, because uploads can take several minutes.
"${ACTOR_SSH[@]}" python3 - <<'GUARD'
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:5080/api/v1/lobbies", timeout=10) as response:
    rooms = json.load(response)["lobbies"]
if rooms:
    raise SystemExit("Deployment refused: production rooms are active")
GUARD
"${ACTOR_SSH[@]}" "test -d /etc/actor && test -d /var/lib/actor && id actor >/dev/null && test ! -e /opt/actor/control-plane/releases/$ACTOR_RELEASE && test ! -e /opt/actor/agents-arena/releases/$ACTOR_RELEASE && test ! -e /opt/actor/staging/$ACTOR_RELEASE && mkdir -p /opt/actor/control-plane/releases /opt/actor/agents-arena/releases /opt/actor/staging && mkdir /opt/actor/staging/$ACTOR_RELEASE && mkdir /opt/actor/control-plane/releases/$ACTOR_RELEASE /opt/actor/agents-arena/releases/$ACTOR_RELEASE"
# Reuse unchanged files on the destination without linking mutable release
# inodes. Unity's large native libraries rarely change between game milestones.
# rsync still transfers changed content and preserves each rollback release.
ACTOR_CP_BASIS=(); ACTOR_GAME_BASIS=()
if "${ACTOR_SSH[@]}" "test -d /opt/actor/control-plane/current"; then ACTOR_CP_BASIS=(--copy-dest=/opt/actor/control-plane/current/); fi
if "${ACTOR_SSH[@]}" "test -d /opt/actor/agents-arena/current"; then ACTOR_GAME_BASIS=(--copy-dest=/opt/actor/agents-arena/current/); fi
rsync -a "${ACTOR_CP_BASIS[@]}" -e "$ACTOR_RSYNC_SSH" "$ACTOR_ROOT/build/control-plane/" "$ACTOR_REMOTE:/opt/actor/control-plane/releases/$ACTOR_RELEASE/"
rsync -a "${ACTOR_GAME_BASIS[@]}" -e "$ACTOR_RSYNC_SSH" "$ACTOR_ROOT/build/agents-arena-server/" "$ACTOR_REMOTE:/opt/actor/agents-arena/releases/$ACTOR_RELEASE/"
rsync -a -e "$ACTOR_RSYNC_SSH" "$ACTOR_ROOT/build/web/" "$ACTOR_REMOTE:/opt/actor/staging/$ACTOR_RELEASE/web/"
scp -i "$ACTOR_SSH_KEY" -o BatchMode=yes "$ACTOR_ROOT/deploy/nginx/actor.conf" "$ACTOR_ROOT/deploy/systemd/actor-control-plane.service" "$ACTOR_ROOT/scripts/deploy-activate.sh" "$ACTOR_REMOTE:/opt/actor/staging/$ACTOR_RELEASE/"
"${ACTOR_SSH[@]}" "bash /opt/actor/staging/$ACTOR_RELEASE/deploy-activate.sh '$ACTOR_RELEASE' '$ACTOR_HOST' '$ACTOR_GAME_VERSION'"
printf 'Deployed %s to http://%s/\n' "$ACTOR_RELEASE" "$ACTOR_HOST"
