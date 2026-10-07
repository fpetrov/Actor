# Remote multiplayer milestone evidence

The main end-to-end checks were performed on **7 October 2026**, with final packaging on 8 October (Europe/Moscow).

- .NET backend: **18 unit/integration tests passed**, covering ten concurrent creates, capacity, unique/reused ports, startup/process failures, empty/heartbeat timeouts, SQLite restart state, ticket integrity/expiration/lobby/version checks and public Minimal APIs/OpenAPI.
- Linux Dedicated Server and macOS Standalone builds completed successfully.
- Public nginx website created room `C2LDVF`; the real Unity process bound UDP 7777 and reported READY. The site and Unity room list showed that room.
- Unity Editor (`Editor Pilot`) and macOS Standalone (`Standalone Pilot`) connected to that VPS match, received distinct ActorIds, saw replicated names and moved independently.
- The Editor probe passed **9 checks**, including server-authoritative wall blocking, normalized speed, stopping and correction of a local transform moved to `(50,50)`.
- The repeated Standalone probe passed **8 checks**, including diagonal speed and observing the remote Editor movement. Both probes recorded zero runtime errors and warnings.
- Leave room returned to the menu, the old server player despawned, player count decreased, and a new connection reused the room with a new client identity. No ghost player remained.
- Five real match processes reached Ready/Running on **7777, 7778, 7779, 7780, 7781**. Sixth allocation returned **409 `server_capacity_reached`**; health remained 200.
- One disposable empty capacity-test process was killed to exercise ProcessExited. Other empty test rooms were subsequently removed by configured lifetime cleanup. A fresh crash test on 8 October confirmed immediate port release and a 201 replacement allocation reusing UDP 7777; see [release.json](release.json).

The JSON probe files contain check results and their final observer state; peer arrays can become empty after clients disconnect, while screenshots capture the completed two-player test. Join tickets and process secrets are not stored in this evidence.

![Two remote clients in the Main Hall](standalone-probe.png)

See [capacity.json](capacity.json), [editor-probe.json](editor-probe.json), [standalone-probe.json](standalone-probe.json). The first Standalone attempt timed out waiting for the Editor to enter Play Mode; the archived repeated probe is the passing result.

![Deployed nginx website](website.png)

On 8 October, the final Standalone UI also created room `PFJZDP` from the game, showed Starting feedback, automatically joined when Ready, and moved through ordinary keyboard input against the remote server. Empty room-name validation was visible in the menu.

![Create and join from the final Unity client](unity-create-join.png)
