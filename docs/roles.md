# Agents Arena station duties

Station duties and permissions are enforced by the game server. See the
[validation report](investigation-validation/README.md) for live role separation
and independent-agent results.

`CrewRole` is a reference-game duty, separate from the temporary calibration
Coordinator/Technician assignment. All role types live in the `agents-arena`
extension, version 0.4.0. Core and the optional Standard packages contain no
Engineer/Sheriff branches.

| Duty | Intended authority |
| --- | --- |
| Crew | Public incident state, ordinary communication and permitted physical maintenance controls |
| Engineer | Private cooling diagnostics and physical repair; permitted maintenance controls |
| Sheriff | Private bounded security records and investigation report submission |

The server derives the caller from the approved connection and owned player
object. `SelectRoleCommand` requests a duty; it does not assign one locally. The
server enforces one Engineer and one Sheriff, distinct actors, permitted role
transitions and the active-investigation lock. Selection is a small global duty
UI action; it does not pretend that choosing a profession requires proximity to
a particular console. Individual work commands do require physical interaction.

`OwnRoleObservation` exposes the controlled actor's duty, identity, selection
lock, match and server revision. `DutyRosterObservation` exposes only public
duty entries. This is an explicit visibility choice for these duties, not a
universal permission to publish future hidden affiliations or alignments.
`RoleResultEvent` is the correlated authoritative selection outcome; local bridge
command acceptance is insufficient.

An active incident binds its two role holders. Neither may switch duties to
perform the other's branch. Loss of a required participant/control must produce
a defined terminal/cleanup outcome before a replacement investigation can bind
new role holders. A resolved incident remains resolved. Missing eligible duties
must cause bounded waiting or a structured unavailable outcome, never synthetic
completion. Exact lifecycle behavior is verified by game-authority tests.

The pre-alarm controller cycle also depends on the current required duties.
Changing or losing a required role before its deadline resets the unfinished
cycle and pending effect. An accepted maintenance operation does not bind a
new participant through a stale role assignment. Once an incident is active,
the fixed-role lock and cancellation rules apply.

## Using the existing SDK

A game extension registers its generated types through `IActorExtensionRegistrar`:

```csharp
public ExtensionPackage Package => AgentsArenaExtensionPackage.Create();

public void Register(IActorExtensionRegistrar scope)
{
    scope.RegisterObservation<OwnRoleObservation>(
        "agents-arena.own-role", "Server-assigned own duty", _ => CaptureOwnRole());
    scope.RegisterCommand<SelectRoleCommand>(
        "agents-arena.select-role", "Request a permitted duty",
        (_, command) => SendToApprovedGameServer(command));
}
```

The helper carries canonical descriptors and their hash; registration derives
type URLs, `Any` envelopes and manifest entries. The example's capture/handler
functions belong to the game adapter. Server validation remains mandatory even
when a Runtime Skill has checked its cached own-role observation.

Another game can define its own duties, observations and permissions in its own
package. It need not change Core, the generic SkillScheduler or the Codex adapter.
See [extensions](extensions.md), [incidents](incidents.md) and
[investigation](investigation.md).
