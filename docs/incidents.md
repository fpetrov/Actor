# Cooling incident contract

**Milestone 6 implementation contract; live acceptance pending.** This describes
the typed/server boundary, not completed deployment evidence.

Agents Arena adds one small cooling incident above its existing authoritative
multiplayer world. The dedicated server owns the real cause, clocks, active
circuit, action history, evidence issuance and incident transitions. Runtime
commands request allowed interactions; they cannot set the origin or mutate
observations into server truth.

## Two independent completion branches

| State surface | Progression |
| --- | --- |
| Cooling | Operational → Failure → Repairing → Restored |
| Investigation | None → Active → Resolved, or Failed/Expired/Cancelled |

`Resolved` requires both physical restoration and a validated investigation
report. Repair alone is not whole-incident success; a correct report alone is
not physical restoration. `CoolingStateObservation` and `IncidentStartedEvent` /
`IncidentChangedEvent` expose public status, severity, location, alarm/expiry
times, repair deadline, report-validation flag and revision. They contain no
hidden origin or role-private evidence. Machine reasons must not encode the
hidden cause accidentally.

The public alarm timestamp is distinct from the Engineer's bounded sensor window.
Both origins use the same public schema. Public telemetry and timestamps must
not turn the security log into an answer key without technical evidence.

## Origins and real actions

Mechanical failure comes from trusted server-controlled gameplay scheduling.
Reproducible seeds and scenario controls belong to trusted test/server setup,
not a public endpoint or a client command that names a hidden cause.
The default active-circuit selector uses independent server entropy. The public
match identity and the seed-derived equipment wear/controller revisions cannot
reveal it. A trusted reproducible fixture may select a circuit explicitly;
that setting must never be forwarded to a Brain.

`MaintenanceOverrideCommand` addresses a public `alpha` or `beta` control. Crew
and Engineer may request the physical interaction; Sheriff may not. The server
checks the caller, role, actual object, distance, cooldown and conflicting work.
Changing the active circuit schedules a manual-origin fault; changing the standby
circuit is benign. Both operations have the same pre-alarm acceptance surface.
Thus a recent maintenance interaction does not itself prove that it caused the
cooling fault. Audit actor identities describe real observed interactions;
seeded controller history must not impersonate a player.

## Shared controller cycle

When both required duties are ready, the server starts a common controller
cycle. Its deadline is readiness plus `max(1000, NaturalDelayMs)` milliseconds.
An accepted active-circuit adjustment remains pending until that deadline;
the natural mechanical path uses the same deadline. There is no immediate
manual-only alarm timestamp that identifies the cause before inspecting the
technical source. With natural generation disabled, a standby adjustment alone
does not create a fault.

The server permits at most one accepted adjustment per circuit in a cycle.
Role changes or loss of a required participant reset an unfinished cycle.
The published security slice covers a completed interval only: real maintenance
records and the Engineer's sensor uncertainty window use that shared cycle
interval. Neither source claims an exact physical failure instant it did not
observe. Coarsening a record's interval does not invent its actor or action.
The bound on real actions keeps the declared complete slice from silently
dropping a qualifying write.

## Repair and cleanup

`RepairCoolingCommand` cites the current incident and the Engineer's legitimately
inspected diagnostic evidence. The default server repair lease is two seconds,
with ongoing proximity/permission/state checks. `RepairResultEvent.accepted=true`
may only mean that the lease started; the Skill must await `completed=true` or
equivalent authoritative restored state. Both stages share a correlation and
must survive event deduplication.

`CancelCoolingRepairCommand` releases unfinished physical work.
`CancelInvestigationCommand` supports bounded objective cleanup. Maintenance and
cancellation return `IncidentActionResultEvent` with a typed action. Replay,
stale incident/match, participant loss, expiry and competing work require stable
controlled outcomes. Cancellation cannot erase a resolved incident.

All these types are in `incidents.proto`, Arena extension 0.4.0. The integration
versions are game 0.7.0 and Runtime 0.5.0; admission remains separate.
Core 1.0, Standard motion 0.1.0 and Standard communication 0.1.0 remain unchanged.
The existing manifest-driven generator produces bindings, descriptors, package
metadata and the typed helper. See [evidence](evidence.md) and
[investigation](investigation.md).
