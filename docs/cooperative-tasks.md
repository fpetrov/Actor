# Cooperative station calibration

The reference task requires two distinct server-approved actors. The coordinator
uses the Main Hall Diagnostic Terminal; the technician uses the Engineering
Calibration Console. Both small terminals use the existing station palette and
the existing two-room movement graph. The task introduces no new world ontology
to ACTOR Core or Standard Communication.

## Information flow

```text
Coordinator's Brain → terminal Skill → local typed bridge
    → authoritative server: validate coordinator + room + interaction range
    → private diagnostic result to coordinator only
    → actor.send-message → server-authorized direct delivery to technician
    → technician's bounded inbox / actor.wait-for-message
    → technician's Brain → submit Skill at Engineering console
    → server: validate assignment + role + position + code + expiry + attempts
    → authoritative Completed state
```

The server creates a new task ID, an opaque one-time authorization and a five-minute
deadline. Private assignment state identifies the two participants and remaining
attempts. Public progress is a separate observation with status and task identity,
without the authorization or private participant evidence. Only the assigned
coordinator may inspect the diagnostic result; only the assigned technician may
submit calibration. The server also requires actual private authorization delivery
to the technician, so supplying a guessed code without cooperation cannot finish
the task.

## Server state and policy

The game-specific `CooperativeAuthority` owns task state and secret data. Its
caller supplies approved player identities and authoritative physical positions;
the network layer sends each resulting payload to exactly its intended audience.
Challenge data is never stored in a NetworkVariable or public progress record.

Normal progression is Available → AwaitingDiagnostic → AwaitingAuthorization →
Completed. Failed, Expired and Cancelled are terminal outcomes. Incorrect
authorization consumes a bounded attempt budget; cooldown, server clock, terminal
range, room and participant checks apply independently. Loss of a participating
client or Runtime control cancels an unfinished task. It cannot erase a confirmed
completion. Correlation replay returns the existing outcome, without repeating the
game action or message delivery. Match-instance identity rejects stale requests
after a lobby restart.

## Typed extension and procedures

`agents-arena` 0.3.0 adds `calibration.proto`: private task state, public progress,
assignment/change events, Start/Inspect/Submit/Cancel commands and diagnostic/action
results. Core stays 1.0. The game client and dedicated server are version 0.6.0 and
must be rebuilt together because their network behaviour layout changes.

| Runtime Skill | Behaviour |
| --- | --- |
| `agents-arena.start-calibration` | Select a different permitted online actor; approach the Main Hall terminal; await server acceptance and assignment |
| `agents-arena.wait-for-calibration-assignment` | Wait for the actor's own private technician assignment |
| `agents-arena.inspect-diagnostic` | Check coordinator role/Main Hall, approach the terminal and await a private diagnostic result |
| `agents-arena.submit-calibration` | Check technician role/Engineering, approach the console, submit authorization and require authoritative completion |
| `agents-arena.wait-for-calibration` | Await Completed; fail on Failed, Expired or Cancelled |

Terminal approaches reuse the existing navigation controller. The coordinator
stands at (0.9, -2.4), clear of the technician's x=0 doorway route; the Engineering
console uses its existing (0, -7) room anchor. Both are checked against the actual
interaction ranges and colliders. On every exit,
cleanup issues zero movement intent. Skills do not set positions, task state or
roles. Reaching a console, sending a message, local command acceptance and server
message acceptance are each insufficient evidence of task completion.

Authorization is a sensitive Skill result: the authorized Brain can use it for the
current task, while reports and episodic memory store metadata only. Message prose
remains untrusted external evidence. Human controls expose only their client's
permitted assignment and messages; the existing Signal Console remains a separate
server-authorized station signal feature.

See [communication](communication.md) for the visibility/delivery model and
[multi-agent launch](multi-agent.md) for independent process configuration.
