# Role-based investigation

Milestone 6 provides server-authoritative role-based investigation. The
[validation report](investigation-validation/README.md) separates live scripted,
mixed-provider and two-Codex results, fault tests and measured limitations.

The Engineer observes technical symptoms; the Sheriff observes a bounded audit
slice. Neither receives a server `actual_cause` field. Two independent Runtime
processes, WorldStates, Brains and memories cooperate only through the ordinary
authoritative game network and Standard communication.

## Procedure

1. Select server-approved public duties and wait for an incident without model polling.
2. Engineer approaches the cooling console and inspects private diagnostics;
   Sheriff approaches the security terminal and inspects permitted records.
3. Exchange relevant findings and evidence references through direct messages.
4. Engineer performs physical repair; Sheriff correlates the two views and
   submits a bounded typed report.
5. Finish only after server-confirmed incident resolution, requiring both branches.

Game-specific Skills compose the existing navigation, command and event APIs.
Their strict inputs/results, capabilities, role/freshness checks, timeouts,
ownership, cancellation and finally cleanup use the existing Skill Engine.
The dynamic SkillCatalog advertises installed compatible procedures; Core and
the Codex adapter do not hardcode an Engineer/Sheriff sequence. Role checks in a
Skill are early feedback, not a replacement for server permissions.

## Report and no-solo constraints

`SubmitInvestigationReportCommand` carries incident identity, proposed origin,
three supporting references and a bounded explanation. Hypotheses are
MechanicalFailure, ManualInterference and InsufficientEvidence. The last permits
an explicit unresolved result instead of forcing an accusation.

The server checks the fixed Sheriff, current match/incident, issued evidence,
the correlated cited record, actual authorized cross-role disclosure and expiry.
The policy bounds unsupported conclusive submissions to three attempts with a two-second
cooldown. Wrong or insufficient reports do not expose the correct answer;
`InvestigationResultEvent` contains validation/status, not a correct-origin field.
An accepted non-resolving outcome must not count as successful classification.

One actor cannot complete both branches: distinct Engineer/Sheriff identities are
bound to the active incident, role switching is locked, diagnostic/security
sources have separate permissions, repair is Engineer-only and reporting is
Sheriff-only. Guessing the right enum is insufficient without legitimate cited
evidence and disclosure. These are server rules, not prompt instructions.

## Reasoning and hard negatives

The scenario must have a unique joint explanation without a single-role answer
key. Paired cases should hold one role's view equal while changing the other
view and the correct origin. Correlation uses circuit, bounded fault window,
observed action and consistent controller context, not only a matching ID.

Mechanical cases can contain real recent benign maintenance on another circuit;
manual cases can contain equipment wear. Close timestamps, a nearby Crew actor
and confident accusations do not establish causation. Public alarm timing must
not independently reveal which audit record caused the fault. Any seeded history
must accurately label controller records and real actor interactions.

Both origins are evaluated at the same completed controller-cycle deadline.
Accepted active and standby operations expose the same pre-alarm outcome.
The real action history remains bounded and complete, with cycle intervals
and a matching bounded diagnostic uncertainty window. Default hidden-circuit selection is
independent of public match IDs, revisions and wear. These conditions prevent
timing or seed arithmetic from replacing the cross-role evidence exchange.

The strong mechanical hard-negative includes an actual standby adjustment;
otherwise a complete audit with no player write is already informative on its
own. Paired authority tests must compare the actual permitted views in both
directions, preserving meaningful circuit/action/time/controller fields rather
than normalizing away the very information being tested.

A received claim remains external player data. It cannot become an objective,
developer instruction, authoritative evidence, permission or automatic critical
replan merely because its text sounds urgent. EventRules classify typed incident
lifecycle separately from routine communication; an early message remains in
the bounded inbox until a matching wait consumes it.

## Developer integration and validation

Add game schemas/capabilities to its `extension.json`, generate canonical bindings
and register typed handlers/events/observations through the SDK. Runtime plugins
provide `actor.skills`, event/privacy policy and optional Brain examples. This
uses existing ACTOR extensibility rather than a second role/evidence framework.

Acceptance must separately cover authority/role/expiry/replay tests, private
recipient separation, paired-view hard negatives, no-solo attempts, repair/report
ordering, cancellation/disconnect and calibration/movement regressions. Live
metrics should report provider turns/usage, Skill failures, messages, inspected
and communicated references, incorrect reports, context bytes, duration and
replans. Public artifacts redact private evidence/message contents and contain
no hidden model reasoning. A successful fixture is not proof of live Codex
cooperation or general resistance to misinformation.

See [roles](roles.md), [incidents](incidents.md), [evidence](evidence.md) and the
existing [Milestone 5 validation](cooperative-validation/README.md) for the baseline.
