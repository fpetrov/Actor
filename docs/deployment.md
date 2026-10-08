# Deployment

Current public site: **http://5.181.134.236/**. Source repository: **https://github.com/fpetrov/Actor** (private).

Production is Ubuntu 24.04 x86-64. nginx is installed as a system package; the .NET 10 Control Plane is a self-contained Linux x64 application running as the non-login system user `actor`. Unity Dedicated Servers are its child processes. No Unity Editor, Docker socket or container orchestrator is installed on the VPS.

## Layout and ports

| Path / port | Purpose |
| --- | --- |
| `/opt/actor/control-plane/current` | Symlink to published Control Plane release |
| `/opt/actor/agents-arena/current/AgentsArenaServer` | Linux Dedicated Server executable |
| `/opt/actor/*/releases/<timestamp>-<revision>` | Versioned deployment artifacts |
| `/var/www/actor` | nginx-only static frontend |
| `/var/lib/actor/actor.db` | SQLite metadata |
| `/var/log/actor/matches/<lobbyId>.log` | Per-match Unity log |
| `/etc/actor/control-plane.env` | Production environment (root-readable) |
| TCP 80 | Public nginx |
| TCP 127.0.0.1:5080 | Private Kestrel API |
| UDP 7777–7781 | Five direct Unity gameplay ports |
| Existing TCP 22 | SSH administration |

The existing VPS firewall was inspected and left unchanged (UFW inactive, no nftables rules). Public HTTP and actual UDP client connections were verified. Kestrel is not publicly bound; internal callbacks are not proxied. Do not proxy gameplay UDP through nginx.

## Build requirements

- Unity 6000.6.4f1, Linux Mono and Linux Dedicated Server build modules.
- .NET 10 SDK; this milestone uses SDK 10.0.401 and patched ASP.NET/SQLite packages 10.0.12.
- Git LFS, SSH, rsync/scp.
- For macOS client builds, the matching Mac build module.

Close the Editor before CLI builds of the same checkout. If .NET 10 is installed outside the system default, set `DOTNET=/absolute/path/to/dotnet`. Scripts also detect the local SDK at `build/tools/dotnet/dotnet`.

```bash
# First prepare networking assets in Unity: ACTOR > Prepare multiplayer assets.
# Set the central Assets/Settings/LobbyClient.asset API URL for your environment.
scripts/build-server.sh
scripts/build-client.sh
scripts/publish-control-plane.sh
scripts/build-web.sh

# Host/user/key are deployment arguments; private keys are never copied into the repo.
scripts/deploy-vps.sh <host> <ssh-user> <private-key-path>
```

Build outputs stay under ignored `build/`. Server build uses `StandaloneLinux64` with `StandaloneBuildSubtarget.Server` and Mono; client build is a macOS development build for diagnostics. The Control Plane publish is self-contained, so the VPS requires no system .NET runtime. Ubuntu dependencies are listed in `scripts/provision-vps.sh`; review/run that script only on an authorized fresh host. It does not change SSH or firewall configuration.

## Configuration

See `.env.example` and `src/Actor.ControlPlane/appsettings.json`. Required production values include PublicGameHost, UnityServerExecutable, DatabasePath, log directory and loopback InternalApiBaseUrl. Defaults: MaxConcurrentGameServers=5, ports 7777–7781, eight players, startup 45s, empty lifetime 300s, empty grace 60s, heartbeat timeout 20s, ticket lifetime 45s, GameVersion=0.3.0, ProtocolVersion=3.

The deployment script preserves an existing `/etc/actor/control-plane.env`; edit it deliberately before redeploying if the host or versions change. There is no persistent signing secret to configure: every allocated match gets a random environment-only key. Never print `/proc/.../environ`, full join tickets or private-key contents.

## Deployment behavior

`deploy/nginx/actor.conf` serves static files, proxies `/api/` and `/health`, rejects `/internal/` and includes basic headers/gzip. It uses no production IP and no SPA fallback. Deployment backs up the relevant virtual-host configuration, replaces only the distribution's default host, runs `nginx -t` and reloads only on success. Other virtual hosts and persistent data are preserved.

`deploy/systemd/actor-control-plane.service` uses User=actor, EnvironmentFile, Restart=on-failure, private tmp and read/write access limited to data/log paths. `KillMode=control-group` ensures children do not survive an application restart. Deployment switches current release symlinks and restarts this service; **existing matches end during deployment**. SQLite and logs remain.

```bash
systemctl status actor-control-plane nginx --no-pager
journalctl -u actor-control-plane -n 50 --no-pager
nginx -t
ss -lntup
curl --fail http://127.0.0.1:5080/health
```

Releases are retained for rollback. Stop the Control Plane, repoint both `current` symlinks to matching known-good releases, then start it; retain production environment/data. Do not delete `/var/lib/actor` or SSH configuration as part of deployment.

## Verification

```bash
dotnet test Actor.sln
python3 scripts/verify-capacity.py http://<host>
```

The capacity script creates empty test rooms up to the configured limit, waits for real READY callbacks, verifies distinct ports and the sixth-room 409, and saves ignored local evidence. Empty test rooms expire automatically. Development clients support `--auto-room`, `--player-name`, `--probe-role A|B`, `--probe-output` for remote movement/collision/identity checks. Editor diagnostics consume `Agents Arena/Verification/multiplayer-probe.json`. These hooks are excluded from dedicated builds and require explicit diagnostic configuration.

Accepted evidence is in [multiplayer validation](multiplayer-validation/README.md). Backend tests cover concurrent creation, port uniqueness/reuse, failures, timeouts, stale persistence, admission claims and public Minimal APIs. Actual VPS checks include Editor + Standalone, names/movement/authoritative collision, local-transform correction, disconnect/reconnect and five real servers.

## HTTPS later

Point a real domain to this VPS and add nginx TLS termination and HTTP→HTTPS redirection, then update the client API URL. No domain or self-signed certificate was invented. Gameplay remains directly on the configured UDP range.

## Responsive movement validation

Run the isolated Unity query checks with `-batchmode -quit -executeMethod Actor.Editor.MovementPredictionVerification.Run` (report: ignored `build/verification/prediction-query-tests.json`). They cover wall sweeps/tangency, non-authoritative bodies, snapshot timeout/recovery/order/wrap, replay and preserving a recent release under a high RTT.

The development-only probe role `P` measures first visual movement versus the replicated root and checks release, reversals, convergence, wall holding/sliding, speed and server authority. Test a real room normally, then optionally add a loopback relay:

```bash
python3 scripts/latency-relay.py <server-host> <room-udp-port> --delay-ms 100 --jitter-ms 10 --loss-percent 3
# Launch the development client with:
# --auto-room ROOMCODE --player-name "Prediction Pilot" --probe-role P
# --probe-output /absolute/path/report.json --probe-relay-port 17777
```

The relay adds 100±10 ms in each direction (about 200 ms additional round trip) and drops 3% of UDP datagrams. It binds only loopback, accesses no tickets, and handles one client per run. The probe uses the real admission API and changes only that diagnostic connection's UDP endpoint. Dedicated builds exclude the probe.

## ACTOR local Runtime

Only the reference game server/Control Plane/static website are deployed to the VPS. ACTOR Runtime remains a local Python process on the client machine. It listens on loopback50051 (configurable), authenticates an ephemeral token and streams Protobuf over HTTP/2. No gRPC server, token, LLM or SDK brain service is deployed/publicly exposed on the VPS.

Local play/bridge instructions: [Python Runtime](python-runtime.md), [protocol](actor-protocol.md), [SDK milestone evidence](actor-validation/README.md). The generic transport abstraction currently has the verified Grpc.Net.Client + YetAnotherHttpHandler implementation. A game0.3.0 client requires matching admission protocol3; rebuild old clients before joining current rooms.
