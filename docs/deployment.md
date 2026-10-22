# Deployment

Current public site: **http://5.181.134.236/**. Source repository: **https://github.com/fpetrov/Actor** (public).

The current source targets **Game 0.10.0 / admission 4 / Arena 0.7.0 / local Runtime 0.8.0 / Core 1.0.0**. This is a compatibility requirement, not a claim that these products are already deployed or publicly downloadable. The accepted [M8 C1 deployment](release-validation/deployment/activation-c1.json) records Game 0.9.0/admission 4; retain that historical scope and check the live API before a new rollout. Exact game versions must match even when the admission version is unchanged.

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

Build outputs stay under ignored `build/`. Server build uses `StandaloneLinux64` with `StandaloneBuildSubtarget.Server` and Mono; the normal macOS client build is non-Development. The Control Plane publish is self-contained, so the VPS requires no system .NET runtime. Ubuntu dependencies are listed in `scripts/provision-vps.sh`; review/run that script only on an authorized fresh host. It does not change SSH or firewall configuration.

Freeze one clean reviewed source commit before building all three native products. `scripts/publish-control-plane.sh` tests the solution and stamps the actual executable/DLL hashes and source fingerprint. Export the matching Runtime0.8.0 wheel to `build/runtime-dist/actor_runtime-0.8.0-py3-none-any.whl`, then run `python3 scripts/package-release.py --output build/releases/0.10.0` with a fresh output directory. Packaging rejects mixed-source native stamps, stale wheel payloads and nonpublishable builds. `deploy-vps.sh` uploads the current build roots; it does not perform this complete source-coherence check for you. Reverify the roots if anything changed after packaging. See [installation and packaging](installation.md) for the seven-file distribution. Building, packaging, publishing and deployment are separate actions.

## Configuration

See [`.env.example`](../.env.example) and [`appsettings.json`](../src/Actor.ControlPlane/appsettings.json). Required production values include PublicGameHost, UnityServerExecutable, DatabasePath, log directory and loopback InternalApiBaseUrl. Defaults: MaxConcurrentGameServers=5, ports 7777–7781, eight Sandbox players (SocialDeduction 4–6), startup45s, empty lifetime300s, empty grace60s, heartbeat timeout20s, ticket lifetime45s, GameVersion=0.10.0, ProtocolVersion=4. Social rules are trusted server settings, snapshotted per room; do not copy a controlled staging profile into production.

Keep `/etc/actor/control-plane.env` a root-owned regular file with mode0600. Activation preserves its existing settings while explicitly setting the built GameVersion, ProtocolVersion=4 and `Match__AdmissionDrainFile=/run/actor-admission-drain`. Check the host, paths and rule overrides deliberately before rollout. Every allocated match gets a random environment-only signing key; there is no persistent match signing secret to provision. Never print environment contents, `/proc/.../environ`, full join/resume tickets or private keys.

`Match__BroadcastOperatorKey` is separate: empty disables observer admission. To enable it, provision the distinct owned key matching the local macOS Keychain item `org.actor.broadcast.v1` / `broadcast-server-operator-key` through a protected stdin channel and an atomic mode0600 env-file update. Keep it out of command arguments, shell tracing, logs and tracked configuration. Do not transfer OBS, Twitch or Codex credentials to the VPS. The service reads the new key only after its gated restart; changing the env file alone does not update a running process.

Observer POST/DELETE routes under `/internal/broadcast-observers/` require direct loopback, no forwarded-origin headers, and `X-Actor-Broadcast-Operator`. They remain excluded from public nginx routing. The local [Broadcast installation](broadcast.md) can use its owned SSH forward to production remote port5080; staging5081 is a different endpoint. Do not expose Kestrel or the internal route publicly to avoid a tunnel.

## Deployment behavior

`deploy/nginx/actor.conf` serves static files, proxies `/api/` and `/health`, rejects `/internal/` and includes basic headers/gzip. It uses no production IP and no SPA fallback. Activation backs up the managed env/unit/nginx configuration and website, takes an exclusive deployment lock and owns an admission-drain marker. It checks for zero rooms, stops the nginx service, verifies its cgroup is empty, then repeats the serialized loopback CP zero-room check before stopping the CP. A room appearing during uploads causes refusal; wait for natural retirement instead of forcing the restart. Other virtual-host files are preserved, but the nginx service pause affects shared HTTP ingress and is not a continuous-availability guarantee.

`deploy/systemd/actor-control-plane.service` uses User=actor, EnvironmentFile, Restart=on-failure, private tmp and read/write access limited to data/log paths. `KillMode=control-group` ensures children do not survive an application restart, so do not bypass the zero-room guards. Activation switches the two current release symlinks, copies web content, restarts the CP and requires health plus Game0.10.0/admission4 before starting nginx again. The two symlink changes are not one atomic filesystem operation. SQLite and logs remain. Website copies do not automatically delete extra destination files; compare the full owned inventory before claiming exact deployed bytes.

```bash
systemctl is-active actor-control-plane.service nginx.service
nginx -t
curl --fail --silent --max-time 5 http://127.0.0.1:5080/health
curl --fail --silent --max-time 5 http://127.0.0.1:5080/api/v1/game/version
```

Releases are retained for rollback. An activation failure attempts to restore the captured old aliases, environment, unit, nginx config and full website; a failed rollback leaves nginx stopped and admission drained for explicit recovery. After a successful rollout, rollback is another gated maintenance operation: record the known-good artifact identities and configuration, wait for zero rooms and drain ingress before restoring a coherent CP/server/web set. Simply repointing live symlinks is insufficient. Re-staging an old website through normal activation may leave newer extra files because its success path does not delete them; require a no-extra inventory proof or a separately reviewed exact-owned restoration. Keep DB/logs and SSH configuration. Source tests describe these paths; an actual rollback needs its own execution evidence.

## Verification

The capacity script allocates rooms and requires an authorized acceptance window; it is not a read-only health check.

```bash
dotnet test Actor.sln
python3 scripts/verify-capacity.py http://<host>
```

The capacity script is an allocating test: it creates empty rooms up to the configured limit, waits for real READY callbacks, verifies distinct ports and the sixth-room409, and saves ignored local evidence. Run it only in an authorized acceptance window; it is not a read-only health check. Empty test rooms expire automatically. Explicit diagnostic clients support `--auto-room`, `--player-name`, `--probe-role A|B`, `--probe-output` for movement/collision/identity checks. Editor diagnostics consume `Agents Arena/Verification/multiplayer-probe.json`. These hooks are excluded from dedicated builds and require explicit diagnostic configuration.

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

Local play/bridge instructions: [installation](installation.md), [Python Runtime](python-runtime.md), [protocol](actor-protocol.md), [SDK milestone evidence](actor-validation/README.md). Current source requires Game0.10.0/admission4, Arena0.7.0 and Runtime0.8.0; use a matching installed client manifest. Legacy Game0.5.0/admission3 products are historical and cannot join these rooms. The broadcaster is also local: its own observer admission is not a player Brain or a server-side model service.

## Historical Milestone 6 investigation conditions

Game0.7.0 and Arena extension0.4.0 require matching new client/server builds;
NGO admission protocol remains3. The Control Plane and local lobby settings
use0.7.0. Runtime0.5.0 remains local; no Brain or evidence service is deployed
to the VPS. The existing private internal heartbeat routes and loopback binding
are unchanged.

For reproducible acceptance, a trusted server administrator can install one
match-scoped file `/etc/actor/incident-scenarios/<matchIdN>.json` before the
first role command. The directory is root-owned, group `actor`, mode0750; files
are root-owned, group `actor`, mode0640 and bounded to4096 bytes. Fields are
`seed`, `naturalEnabled`, `naturalDelayMs`, `incidentTtlMs`, `repairDurationMs`
and `activeCircuit` (`alpha` or `beta`). No cause setter or public debug endpoint
exists. The ordinary Crew maintenance action must actually reach the server.
Without a file, private server entropy and normal server timing apply. Invalid files
fail role setup with a structured outcome and a content-free server error.

`spikes/investigation/run_case.py` creates an ordinary room, installs these
conditions over the authorized SSH connection and launches independent Runtime
and Unity processes. Its case label and server configuration never enter an
investigator Objective/context. Keep raw artifacts in ignored `build/`; publish
only the explicit projection from `scripts/summarize-investigation.py`.

## Historical Milestone 7 SocialDeduction conditions

Game0.8.0, Arena extension0.5.0 and local Runtime0.6.0 require matching
client/server builds. Admission protocol remains3. New rooms explicitly choose
`Sandbox` or `SocialDeduction`; Sandbox preserves the previous investigation and
cooperation gameplay. A Social lobby owns one immutable match configuration and
one server-authoritative result. Existing production environment overrides must
be checked for an older `Match__GameVersion` before rollout.

Social rules are trusted deployment settings under `Match__SocialDeduction__*`,
validated and snapshotted when a lobby is created. Defaults: 4–6 players, one
Impostor, 900 s match, 90 s Discussion, 60 s Voting, 30 s kill cooldown, 90 s
sabotage cooldown, 150 s emergency, 4 s uninterrupted work and four Crew task
completions. Public lobby creation cannot set assignments, seeds or these rules.
The allocator passes the private snapshot to its dedicated child. Live hidden
roles, chat, contexts, credentials and model conversations do not belong in
public diagnostics. Bounded summary/performance files are written only after
GameOver under the configured match-log directory's `social/` subdirectory.

The reference map, interest management and privacy acceptance are documented in
[social deduction](social-deduction.md), [visibility](visibility.md) and the
[M7 report](social-deduction-validation/README.md). Raw local acceptance artifacts
stay under ignored `build/`; Runtime and model credentials stay on the client.
Long-running player-agent fixtures used headless mode; Human checks used an 800×600 window. A Broadcast observer is a separate native windowed client and needs actual presentation ACKs; a headless process cannot substitute for that rendering proof.

M7 production release `20261021T193053Z-89c00dd` passed health/version/hash checks and an ordinary headless-client admission smoke. Both hosted CI workflows passed for functional commit `89c00dd`. Its final zero-room guard preceded the restart; the game-version override changed with a private backup. Its staging and SSH tunnel were then closed; that historical cleanup says nothing about a later staging session. See the [M7 release evidence](social-deduction-validation/deployment.json), [smoke](social-deduction-validation/production-smoke.json) and [cleanup](social-deduction-validation/cleanup.json). The later [M8 deployment](release-validation/deployment/activation-c1.json) and [54-section report](release-validation/README.md) retain their original Game0.9/admission4 scope and incomplete acceptance gates.
