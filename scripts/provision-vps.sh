#!/usr/bin/env bash
set -euo pipefail
# Run on the authorized Ubuntu 24.04 host. This never changes SSH or firewall settings.
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends nginx rsync libglib2.0-0t64 libstdc++6 libgcc-s1 libicu74 libssl3t64 zlib1g libcurl4t64
if ! id actor >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/actor --create-home --shell /usr/sbin/nologin actor
fi
install -d -o actor -g actor /var/lib/actor /var/log/actor/matches
install -d -o root -g root /opt/actor/control-plane/releases /opt/actor/agents-arena/releases /var/www/actor /etc/actor
