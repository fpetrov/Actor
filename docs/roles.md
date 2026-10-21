# Agents Arena duties and alignments

Station duties, hidden alignments and permissions are enforced by the game
server. Their visibility depends on the room mode. Sandbox retains the
investigation model below; Social Deduction has a separate assignment and
disclosure policy. See [Social Deduction](social-deduction.md) and its
[validation report](social-deduction-validation/README.md) for the implemented
mode and recorded acceptance, and the [investigation report](investigation-validation/README.md)
for earlier Sandbox evidence.

## Sandbox station duties

`CrewRole` is a reference-game duty, separate from the temporary calibration
Coordinator/Technician assignment. All role types live in the `agents-arena`
extension, currently version 0.5.0. Core and the optional Standard packages contain no
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

## Social Deduction assignments

`SocialAlignment` describes the Crew or Impostor side; `CrewRole` describes a
station duty. They are independently assigned and must not be inferred from
each other. Social mode assigns one Impostor among 4–6 ready participants. A
living participant receives only its own alignment, duty, life, readiness,
cooldowns and assigned tasks through targeted own-state observations. The
Sandbox duty roster and role-selection capabilities are absent in this mode.

Other actors' network-visible appearance and work activity do not identify
their alignment. Real Crew work and Impostor fake work use the same visible
activity representation. Meeting rollcall discloses the permitted participant
and life state, with no hidden affiliation; ballots are private until the
resolved tally. The bounded authoritative postgame summary reveals assignments
only after GameOver. A dead actor's team outcome remains distinct from survival.

Roles come from server entropy after readiness. Trusted server-only fixtures
may fix a test scenario, but public lobby requests, launchers, objectives and
Skills cannot select or query hidden assignments. Every agent receives the same
trusted match objective and learns its own assignment through the ordinary game
connection. Received accusations remain player claims. Neither an actor name,
an optional provider nor the installed Skill list is a hidden-role oracle.

Arena's installed Runtime policy filters Skills using legitimately received
own state. The Scheduler repeats availability and epoch checks before leasing
commands; the game server always repeats authorization. This improves agent
behavior without turning local cached state into permission. Elimination or
ejection suspends action and cognition while the client waits passively for the
authoritative match result. See [match lifecycle](match-lifecycle.md).

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
