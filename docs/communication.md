# Typed communication

ACTOR does not implement universal game logic. It provides typed and extensible communication, observation, action and cognitive orchestration interfaces. A game's authoritative server defines visibility, permissions and gameplay outcomes.

`actor.standard.communication` is an optional extension, independently versioned at
0.1.0. Core remains 1.0 and the existing Standard movement extension remains
0.1.0. Its schema is
`proto/actor/standard/communication/v1/communication.proto`; the normal generator
produces C#, Python, descriptors, schema hashes and manifest metadata.

## Capabilities and authority

| Capability | Typed payload | Purpose |
| --- | --- | --- |
| `actor.standard.communication.peers` | `CommunicationPeersObservation` | Game-authorized current recipients, channels and match instance |
| `actor.standard.communication.send-message` | `SendMessageCommand` | Request delivery; ordered command, never movement coalescing |
| `actor.standard.communication.message-received` | `MessageReceivedEvent` | Server-identified sender, message ID, ordering, expiry and content |
| `actor.standard.communication.delivery-result` | `MessageDeliveryResultEvent` | Correlated authoritative acceptance/rejection and delivery stage |

The sender ID is absent from the command. Agents Arena derives it from the
approved connection's owned player object. A client cannot choose a different
actor by changing its request. The server also supplies the message ID, timestamp,
sequence and match instance. Client correlations are bounded idempotency keys,
scoped to their actor and match; reusing one for different bytes is rejected.

Agents Arena supports `direct` and `station`. The server checks the channel,
online recipient, task association, text size (512 UTF-8 bytes), request/identifier
bounds, expiry (1–300 seconds), per-actor/global rates and bounded pending delivery
capacity. An offline recipient produces a failure; there is no durable offline
mailbox. A new lobby process has a different match identity, so an old request
cannot address the new match accidentally.

## Delivery has distinct meanings

1. Bridge command acceptance means that the local adapter accepted a typed request.
2. `ServerAccepted` means that the authoritative game server accepted its policy checks.
3. `ClientReceived` requires acknowledgement from the intended receiving game client.
4. A Runtime Skill or Brain processing the message is a later local action.

Neither an RPC send nor `ClientReceived` proves that a Brain read or understood
the message. Sender delivery receipts contain metadata, not a second copy of the
private message. Duplicate requests reuse their result and never deliver another
copy. Pending delivery, replay history and local inboxes have explicit bounds.

## Private visibility

Direct content travels in a targeted NGO RPC to its recipient's player object.
Only the sender receives its receipt. A third client receives neither the direct
payload nor private diagnostic events, including through its game adapter,
observations, event history or snapshots. Public task progress uses a separate
schema with no authorization field. There is no globally replicated chat log or
NetworkVariable holding private text.

These are game visibility guarantees. They do not claim network encryption or
protection from the dedicated server or the authorized recipient's computer.

The game labels diagnostic authorization as sensitive and restricts its permitted
forwarding to the assigned partner through a direct message. Sensitive data stays
ephemeral. Ordinary chat is also excluded from durable episodic memory by default.
Private Skill outputs remain available in the receiving agent's working decision
context; public report projections and SQLite records contain metadata only.
Known-secret redaction also covers free-form result messages and nested details.

## Runtime integration

The installed `actor_standard_communication` package registers typed bindings,
Skills and EventRules. It uses the existing RuntimeSession stream and WorldState
ingestion. No additional socket, transport or agent-to-agent API is introduced.

`actor.send-message` validates strict input, issues a typed command and waits for
a matching authoritative result. A local command result cannot finish it.
`actor.wait-for-message` waits without model calls, supports sender/task/correlation
filters and returns the server-identified message metadata. A bounded ordered
WorldState inbox retains early messages independently of the short recent-event
ring, deduplicates server IDs and consumes matched messages once. Cancellation,
disconnect and timeout release the foreground Skill normally.

Communication EventRules route ordinary prose as routine external evidence;
another player cannot force a model replan by writing an urgent-sounding sentence.
Context Builder selects relevant events within its existing count/byte limits.
Codex receives other players' prose through `ExternalMessage`, alongside other
game-originated state; it never becomes objective or developer instructions.

See [multi-agent launch](multi-agent.md), [the cooperative task](cooperative-tasks.md)
and [Skills](skills.md).
