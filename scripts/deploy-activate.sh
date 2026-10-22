#!/usr/bin/env bash
# Invoked only by deploy-vps.sh after an immutable release upload and early
# zero-room guard. No credential contents, environment or journal are printed.
set -euo pipefail
release="${1:?Missing release}"; public_host="${2:?Missing public host}"; game_version="${3:?Missing game version}"
[[ "$release" =~ ^[A-Za-z0-9_-]{1,96}$ && "$public_host" =~ ^[A-Za-z0-9.-]+$ && "$game_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { printf 'Invalid activation arguments.\n' >&2; exit 2; }
stage="/opt/actor/staging/$release"; backup="$stage/backup"
service=actor-control-plane.service
marker=/run/actor-admission-drain
exec 9>/run/actor-deploy.lock
flock -n 9 || { printf 'Deployment refused: another activation owns the lock.\n' >&2; exit 1; }
[[ ! -e "$marker" && ! -L "$marker" ]] || { printf 'Deployment refused: admission is already drained.\n' >&2; exit 1; }
[[ ! -e "$backup" && ! -L "$backup" ]] || exit 1
[[ -x "/opt/actor/control-plane/releases/$release/Actor.ControlPlane" && -x "/opt/actor/agents-arena/releases/$release/AgentsArenaServer" ]] || exit 1
[[ -f "$stage/actor.conf" && -f "$stage/actor-control-plane.service" && -f "$stage/web/index.html" ]] || exit 1
# Never follow an unexpected mutable alias while overwriting managed files.
for path in /etc/actor/control-plane.env /etc/systemd/system/actor-control-plane.service /etc/nginx/sites-available/actor.conf /var/www/actor; do
  [[ ! -L "$path" ]] || exit 1
done
old_cp=""; old_game=""
for path in /opt/actor/control-plane/current /opt/actor/agents-arena/current; do
  [[ -L "$path" || ! -e "$path" ]] || exit 1
done
if [[ -L /opt/actor/control-plane/current ]]; then old_cp="$(readlink /opt/actor/control-plane/current)"; fi
if [[ -L /opt/actor/agents-arena/current ]]; then old_game="$(readlink /opt/actor/agents-arena/current)"; fi
old_active=false; old_enabled=false; nginx_active=false
if systemctl is-active --quiet "$service"; then old_active=true; fi
if systemctl is-enabled --quiet "$service"; then old_enabled=true; fi
if systemctl is-active --quiet nginx; then nginx_active=true; fi
umask 077
mkdir -m 700 "$backup"
paths=(/etc/actor/control-plane.env /etc/systemd/system/actor-control-plane.service /etc/nginx/sites-available/actor.conf /etc/nginx/sites-enabled/actor.conf /etc/nginx/sites-enabled/default)
names=(control-plane.env service-unit nginx-available nginx-enabled nginx-default)
existed=()
for index in "${!paths[@]}"; do
  path="${paths[$index]}"
  if [[ -e "$path" || -L "$path" ]]; then
    [[ -f "$path" || -L "$path" ]] || exit 1
    cp -a "$path" "$backup/${names[$index]}"; existed+=(true)
  else existed+=(false); fi
done
if [[ -f "$backup/control-plane.env" ]]; then chmod 600 "$backup/control-plane.env"; fi
web_existed=false
if [[ -d /var/www/actor ]]; then
  web_existed=true; mkdir -m 700 "$backup/web"; rsync -a /var/www/actor/ "$backup/web/"
else [[ ! -e /var/www/actor ]] || exit 1; fi
service_touched=false; enable_touched=false; web_touched=false; drain_owned=false; nginx_touched=false; activation_mutated=false

wait_health() {
  local expected="${1:-}"
  for attempt in {1..20}; do
    if systemctl is-active --quiet "$service" && curl --fail --silent --max-time 3 http://127.0.0.1:5080/health >/dev/null; then
      if [[ -z "$expected" ]]; then return 0; fi
      if curl --fail --silent --max-time 3 http://127.0.0.1:5080/api/v1/game/version | python3 -c 'import json,sys; v=json.load(sys.stdin); sys.exit(0 if v.get("gameVersion")==sys.argv[1] and v.get("protocolVersion")=="4" else 1)' "$expected" >/dev/null 2>&1; then return 0; fi
    fi
    sleep 1
  done
  return 1
}
rollback() {
  local failed=0
  if ! $activation_mutated; then return 0; fi
  if $nginx_touched; then timeout --kill-after=5 30 systemctl stop nginx >/dev/null 2>&1 || failed=1; fi
  # A refusal before activation must never interrupt existing game processes.
  if $service_touched; then systemctl stop "$service" >/dev/null 2>&1 || failed=1; fi
  if $enable_touched; then
    if $old_enabled; then systemctl enable "$service" >/dev/null 2>&1 || failed=1
    else systemctl disable "$service" >/dev/null 2>&1 || failed=1; fi
  fi
  if [[ -n "$old_cp" ]]; then ln -sfn "$old_cp" /opt/actor/control-plane/current || failed=1
  else rm -f /opt/actor/control-plane/current || failed=1; fi
  if [[ -n "$old_game" ]]; then ln -sfn "$old_game" /opt/actor/agents-arena/current || failed=1
  else rm -f /opt/actor/agents-arena/current || failed=1; fi
  for index in "${!paths[@]}"; do
    rm -f "${paths[$index]}" || failed=1
    if "${existed[$index]}"; then cp -a "$backup/${names[$index]}" "${paths[$index]}" || failed=1; fi
  done
  if $web_touched; then
    if $web_existed; then rsync -ac --delete "$backup/web/" /var/www/actor/ || failed=1
    else rm -rf /var/www/actor || failed=1; fi
  fi
  systemctl daemon-reload >/dev/null 2>&1 || failed=1
  if $service_touched && $old_active; then
    systemctl start "$service" >/dev/null 2>&1 || failed=1
    wait_health || failed=1
  fi
  if (( failed == 0 )) && $nginx_active; then
    nginx -t >/dev/null 2>&1 && timeout --kill-after=5 30 systemctl start nginx >/dev/null 2>&1 || failed=1
  fi
  if (( failed != 0 )); then
    # The old config/CP may not understand the new marker during migration.
    # Keep ingress stopped as well as retaining the marker on failed rollback.
    timeout --kill-after=5 30 systemctl stop nginx >/dev/null 2>&1 || true
  fi
  return "$failed"
}
finish() {
  local status=$? restored=true
  trap - EXIT HUP INT TERM
  if (( status != 0 )); then
    printf 'Activation failed; restoring the previous deployment.\n' >&2
    set +e
    if ! rollback; then restored=false; printf 'Rollback failed: ingress remains drained; restore the backup, validate CP/nginx health, then explicitly remove /run/actor-admission-drain.\n' >&2
    else printf 'Previous deployment restored.\n' >&2; fi
  fi
  if $drain_owned && $restored; then rm -f "$marker"; fi
  exit "$status"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
check_no_rooms() {
python3 - <<'GUARD'
import json, urllib.request
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:5080/api/v1/lobbies", timeout=10) as response:
    rooms = json.load(response)["lobbies"]
if rooms:
    raise SystemExit("Deployment refused at activation: production rooms are active")
GUARD
}
: > "$marker"; chmod 644 "$marker"; drain_owned=true
check_no_rooms
if $nginx_active; then
  # A pre-marker request may still be buffering its body in an old nginx
  # worker. Neither an idle timeout nor reload establishes a total barrier.
  # Stop the exact service and prove its entire cgroup empty before the final
  # serialized CP guard. No existing CP/game process is stopped at this point.
  kill_mode="$(systemctl show --property=KillMode --value nginx)"
  [[ "$kill_mode" == control-group || "$kill_mode" == mixed ]] || { printf 'Cannot establish the nginx request barrier.\n' >&2; exit 1; }
  nginx_group="$(systemctl show --property=ControlGroup --value nginx)"
  [[ "$nginx_group" =~ ^/[A-Za-z0-9_.@:/-]+$ && "$nginx_group" != *..* ]] || { printf 'Cannot establish the nginx request barrier.\n' >&2; exit 1; }
  activation_mutated=true; nginx_touched=true
  timeout --kill-after=5 30 systemctl stop nginx
  if systemctl is-active --quiet nginx; then printf 'Cannot establish the nginx request barrier.\n' >&2; exit 1; fi
  python3 - "$nginx_group" <<'CGROUP'
from pathlib import Path
import sys
root = Path("/sys/fs/cgroup")
if not (root / "cgroup.controllers").is_file():
    raise SystemExit("Cannot verify a unified nginx service cgroup")
group = root / sys.argv[1].lstrip("/")
if group.exists():
    state = dict(line.split() for line in (group / "cgroup.events").read_text().splitlines())
    if state.get("populated") != "0":
        raise SystemExit("Cannot establish the nginx request barrier")
CGROUP
fi
check_no_rooms
activation_mutated=true
install -m 644 "$stage/actor.conf" /etc/nginx/sites-available/actor.conf
if [[ -L /etc/nginx/sites-enabled/default && "$(readlink /etc/nginx/sites-enabled/default)" == /etc/nginx/sites-available/default ]]; then rm /etc/nginx/sites-enabled/default; fi
ln -sfn /etc/nginx/sites-available/actor.conf /etc/nginx/sites-enabled/actor.conf
if ! nginx -t >/dev/null 2>&1; then printf 'Candidate nginx configuration is invalid.\n' >&2; exit 1; fi
install -m 644 "$stage/actor-control-plane.service" /etc/systemd/system/actor-control-plane.service
if [[ ! -f /etc/actor/control-plane.env ]]; then
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
python3 - "$game_version" <<'ENVVERSION'
from pathlib import Path
import sys
path = Path("/etc/actor/control-plane.env")
lines = [line for line in path.read_text().splitlines() if not line.startswith(("Match__GameVersion=", "Match__ProtocolVersion=", "Match__AdmissionDrainFile="))]
lines.extend(["Match__GameVersion=" + sys.argv[1], "Match__ProtocolVersion=4", "Match__AdmissionDrainFile=/run/actor-admission-drain"])
path.write_text("\n".join(lines) + "\n")
path.chmod(0o600)
ENVVERSION
service_touched=true
systemctl stop "$service"
ln -sfn "/opt/actor/control-plane/releases/$release" /opt/actor/control-plane/current
ln -sfn "/opt/actor/agents-arena/releases/$release" /opt/actor/agents-arena/current
web_touched=true
mkdir -p /var/www/actor
rsync -ac "$stage/web/" /var/www/actor/
systemctl daemon-reload
enable_touched=true
systemctl enable "$service" >/dev/null 2>&1
systemctl start "$service"
if ! wait_health "$game_version"; then printf 'Candidate CP failed health/version validation.\n' >&2; exit 1; fi
nginx_touched=true
timeout --kill-after=5 30 systemctl start nginx
