# Investigation evidence and provenance

**Milestone 6 implementation contract; live acceptance pending.** Typed schemas
and deterministic checks do not establish deployed recipient isolation or a
successful live paired-scenario run.

Evidence is an Agents Arena concept. `EvidenceProvenance` records a server-issued
identity, incident, source entity, observation/expiry times, server revision,
match, recipient actor/duty and `EvidenceVisibility.RolePrivate`. A client cannot
invent an authoritative evidence identity by writing one into a message or
report. The server resolves references against its own issuance/disclosure state.

| Private view | Authorized observer | Structured contents |
| --- | --- | --- |
| `DiagnosticEvidence` | Assigned Engineer | Circuit, bounded sensor window, at most eight finite pressure samples, equipment wear, controller revisions |
| `SecurityEvidence` | Assigned Sheriff | At most six permitted records, explicit covered interval/circuits and completeness |

Security records include their own IDs/source, circuit, time window, observed
action, real actor identity when present, confirmed-interaction flag and prior /
resulting controller revisions. Read-only inspection must not fabricate a
controller write. The physical valve adjustment leaves controller revision
unchanged in both origins. Controller revisions and equipment wear are independent
of the server's default secret circuit selector; their parity is not an answer key.

## Bounded coverage and negative evidence

`coverage_window_start_unix_ms`, `coverage_window_end_unix_ms`,
`complete_for_listed_circuits` and at most two `circuit_refs` define the exact
authorized security slice. Absence of a qualifying confirmed flow adjustment is meaningful only
inside that covered interval and listed circuits, with completeness confirmed.
The server must not silently truncate qualifying records and still claim a
complete view. A partial view supports uncertainty rather than a mechanical
classification by default.

The reference server bounds a completed cycle to one real adjustment per circuit.
Its security bundle contains two primary circuit records and three explicitly
neutral or nonqualifying records. Real action intervals are coarsened to the
completed shared cycle; the diagnostic window represents that same bounded
sensor uncertainty interval. Coverage must
not extend into the future merely to make an earlier action appear correlated.
Pressure sample timestamps likewise cannot be later than the inspection.

Strong paired-view tests require a real standby adjustment in the mechanical
hard-negative as well as a real active-circuit adjustment in the manual case.
When a complete natural-failure audit contains no player adjustments at all,
that absence can legitimately support a mechanical conclusion from the audit.
That simpler case alone does not demonstrate information asymmetry.

The reference report cites exactly three identities: the issued diagnostic
bundle, issued security bundle and one correlated `MaintenanceRecord.RecordId`.
These are different reference kinds, validated against their authoritative
objects. Matching a circuit string alone is insufficient: time, observed action,
controller context and coverage must agree.

## Observation versus another actor's claim

`DiagnosticEvidenceEvent` and `SecurityEvidenceEvent` are private correlated
inspection outcomes. `OwnInvestigationViewObservation` retains only the actor's
own legitimately inspected bundle in the existing WorldState, allowing later
decision turns to use it. The oneof cannot hold both duty views simultaneously.
The game emits an explicit cleared view when evidence is unavailable; omitting
an observation does not clear WorldState's merged cache.

The Engineer can quote selected findings and a reference through ordinary
Standard direct communication. The receiver sees a server-identified sender and
untrusted message contents, not a newly inspected authoritative evidence source.
The game's disclosure ledger can require the intended receiving client's ACK of
an issued reference before a report cites it. This proves client delivery, not
that a Brain read, understood or believed the claim. Crew statements follow the
same communication path and acquire no evidence privileges through assertive prose.

## Privacy and lifetime

Private bundles and Skill results use installed `session_only` policy. Their
working contents may reach the authorized Brain as external game data, while
public reports/SQLite omit raw private payloads. Generic observation policy
preparation must apply the same privacy and expiry metadata as private events;
it must not classify every observation frame or introduce another world cache.
Own views and inspection events expose top-level expiry for generic handling.

Own observed facts eligible for future durable memory need explicit provenance
and policy. The MVP's raw role-private bundles and ordinary messages are not
automatically persistent evidence. Each agent keeps its own game/profile-scoped
MemoryStore; neither can query the other's database or WorldState.

Visibility filtering happens on the dedicated server before targeted game RPCs.
Python-side redaction cannot repair a private network broadcast. Conversely,
Protobuf typing does not enforce visibility, numerical/record bounds or report
permissions; authority tests must verify those separately.

See [communication](communication.md), [memory](memory.md),
[context builder](context-builder.md) and [investigation](investigation.md).
