#!/usr/bin/env bash
source "$(dirname -- "$0")/common.sh"
ACTOR_HOST="${1:?Usage: deploy-vps.sh HOST USER SSH_KEY}"
ACTOR_SSH_USER="${2:?Missing SSH user}"
ACTOR_SSH_KEY="${3:?Missing SSH key path}"
ACTOR_RELEASE="$(date -u +%Y%m%dT%H%M%SZ)-$(git -C "$ACTOR_ROOT" rev-parse --short HEAD)"
test -x "$ACTOR_ROOT/build/control-plane/Actor.ControlPlane"
test -x "$ACTOR_ROOT/build/agents-arena-server/AgentsArenaServer"
test -f "$ACTOR_ROOT/build/web/index.html"
# Restrict remotely interpolated values to prevent shell injection.
[[ "$ACTOR_HOST" =~ ^[a-zA-Z0-9.-]+$ && "$ACTOR_SSH_USER" =~ ^[a-zA-Z0-9_-]+$ ]]
[[ -f "$ACTOR_SSH_KEY" && "$ACTOR_SSH_KEY" != *"'"* ]]
ACTOR_REMOTE="$ACTOR_SSH_USER@$ACTOR_HOST"
ACTOR_SSH=(ssh -i "$ACTOR_SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 "$ACTOR_REMOTE")
ACTOR_RSYNC_SSH="ssh -i '$ACTOR_SSH_KEY' -o BatchMode=yes -o ConnectTimeout=15"
"${ACTOR_SSH[@]}" "test -d /etc/actor && test -d /var/lib/actor && id actor >/dev/null && mkdir -p /opt/actor/control-plane/releases/$ACTOR_RELEASE /opt/actor/agents-arena/releases/$ACTOR_RELEASE /opt/actor/staging/$ACTOR_RELEASE"
rsync -a -e "$ACTOR_RSYNC_SSH" "$ACTOR_ROOT/build/control-plane/" "$ACTOR_REMOTE:/opt/actor/control-plane/releases/$ACTOR_RELEASE/"
rsync -a -e "$ACTOR_RSYNC_SSH" "$ACTOR_ROOT/build/agents-arena-server/" "$ACTOR_REMOTE:/opt/actor/agents-arena/releases/$ACTOR_RELEASE/"
rsync -a -e "$ACTOR_RSYNC_SSH" "$ACTOR_ROOT/build/web/" "$ACTOR_REMOTE:/opt/actor/staging/$ACTOR_RELEASE/web/"
scp -i "$ACTOR_SSH_KEY" -o BatchMode=yes "$ACTOR_ROOT/deploy/nginx/actor.conf" "$ACTOR_ROOT/deploy/systemd/actor-control-plane.service" "$ACTOR_REMOTE:/opt/actor/staging/$ACTOR_RELEASE/"
"${ACTOR_SSH[@]}" bash -s -- "$ACTOR_RELEASE" "$ACTOR_HOST" <<'REMOTE'
set -euo pipefail
release="$1"; public_host="$2"
stage="/opt/actor/staging/$release"
mkdir -p "$stage/backup"
cp -a /etc/nginx/sites-enabled "$stage/backup/"
if [[ -f /etc/nginx/sites-available/actor.conf ]]; then cp -a /etc/nginx/sites-available/actor.conf "$stage/backup/actor.conf"; fi
install -m 644 "$stage/actor.conf" /etc/nginx/sites-available/actor.conf
# Only the distro's empty default virtual host is replaced; other virtual hosts remain.
if [[ -L /etc/nginx/sites-enabled/default && "$(readlink /etc/nginx/sites-enabled/default)" == /etc/nginx/sites-available/default ]]; then
  rm /etc/nginx/sites-enabled/default
fi
ln -sfn /etc/nginx/sites-available/actor.conf /etc/nginx/sites-enabled/actor.conf
if ! nginx -t; then
  rm /etc/nginx/sites-enabled/actor.conf
  cp -a "$stage/backup/sites-enabled/." /etc/nginx/sites-enabled/
  if [[ -f "$stage/backup/actor.conf" ]]; then cp -a "$stage/backup/actor.conf" /etc/nginx/sites-available/actor.conf; fi
  exit 1
fi
install -m 644 "$stage/actor-control-plane.service" /etc/systemd/system/actor-control-plane.service
if [[ ! -f /etc/actor/control-plane.env ]]; then
  umask 077
  cat > /etc/actor/control-plane.env <<ENV
ASPNETCORE_ENVIRONMENT=Production
ASPNETCORE_URLS=http://127.0.0.1:5080
Match__PublicGameHost=$public_host
Match__UnityServerExecutable=/opt/actor/agents-arena/current/AgentsArenaServer
Match__DatabasePath=/var/lib/actor/actor.db
Match__GameServerLogDirectory=/var/log/actor/matches
Match__InternalApiBaseUrl=http://127.0.0.1:5080
Match__MaxConcurrentGameServers=5
Match__GameServerPortStart=7777
Match__GameServerPortEnd=7781
ENV
fi
chmod 600 /etc/actor/control-plane.env
chmod +x "/opt/actor/agents-arena/releases/$release/AgentsArenaServer" "/opt/actor/control-plane/releases/$release/Actor.ControlPlane"
systemctl stop actor-control-plane.service 2>/dev/null || true
ln -sfn "/opt/actor/control-plane/releases/$release" /opt/actor/control-plane/current
ln -sfn "/opt/actor/agents-arena/releases/$release" /opt/actor/agents-arena/current
rsync -a "$stage/web/" /var/www/actor/
systemctl daemon-reload
systemctl enable --now actor-control-plane.service
systemctl reload nginx
for attempt in {1..20}; do
  if curl --fail --silent http://127.0.0.1:5080/health >/dev/null; then exit 0; fi
  sleep 1
done
systemctl status actor-control-plane.service --no-pager
exit 1
REMOTE
printf 'Deployed %s to http://%s/\n' "$ACTOR_RELEASE" "$ACTOR_HOST"
