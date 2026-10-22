# Player watch and public discussion

The commentator follows the broadcast observer's permitted view. It never reads
the players' private roles, tasks, memories, observations or ballots. Public
in-game discussion works independently of optional Twitch chat OAuth.

The native HUD presents up to six stable public camera-selector identities and
names. This list carries no position, room, life, role or private task information;
membership is not removed on death, disconnect or loss of sight. Its separate
`camera_targets_presented` ACK requires the full list to be readable in the exact
rendered scope/sequence. An unshown list is removed from both the server's
presented permissions and the cognitive view. Legacy views can still select a
currently visible actor. Neither a selector name nor a rejected selection is
evidence of a player's current life, visibility or position.

`CameraDirector` samples an 18–26 second dwell once per confirmed camera and
rotates through the least recently watched identities in the current presented
selector. When due, it pauses new normal commentary admission at an actual speech
clause boundary, fences and cancels remaining old-focus speech/provider work, and
requires the existing confirmed audio flush before dispatching a camera request. This
prevents continuous P2 commentary from starving rotation. P0/P1 retain priority;
failed cancellation never authorizes a cut. Normal commentary stays paused until
a matching newer presented camera epoch, rejection or the five-second timeout.
An accepted command alone never confirms a camera or advances a target's visit.

The server checks public roster permission, exact scope, freshness and its
eight-second minimum dwell. Selection success does not depend on private life or
pose availability. A fresh finite pose may position the canonical camera; otherwise
the selected identity and current eye are retained without announcing why. The
server damps ordinary following before radius, viewport, LOS and fact projection;
cuts, long gaps, discontinuities and blocked wall corners snap instead. The client
uses that canonical eye directly, with actual render-pose/projection and fog checks
before ACK; it never interpolates a hole between camera epochs.

If no alternate selector target is eligible, the director uses authored room
anchors. A continuously fresh empty view can seek another target after ten seconds
on the confirmed camera and five seconds of observed emptiness. Missing/stale
frames do not count. Rejections and missing confirmations back off, without
treating a rejected target as visited; discussion, voting and GameOver hold
selection. Fair selection does not promise a visible player in every frame.

The public HUD names the selected player. Narration of that player's actions
still requires a currently visible actor and relevant dated evidence; a selected
but unseen identity does not create a `player_watch` observation. A coarse
`movement-seen`
fact describes visible movement at its observation time, never a destination or
private intention. Its label is the public name, room is the observed room, and
value is 1 north, 2 northeast, 3 east, 4 southeast, 5 south, 6 southwest, 7 west,
8 northwest, or 9 stationary. Only the visible focused player can produce these
facts. Baselines reset at selection/visibility loss. Meaningful displacement
and an eight-second minimum interval prevent transform-rate narration. Facts
must still be displayed and acknowledged by the native HUD before cognition.

During Discussion/Voting/VoteResolution, speech selects up to six recent public
arguments, giving each speaker a place before a prolific speaker's second line.
It can add dated permitted sightings as comparison material, up to the existing
eight-reference total. The presenter asks for 3–4 short clauses, about 40–65
words: attributed argument summaries, how the replies connect, and a cautious
opinion where useful. This is bounded summarization; it cannot promise to read
every chat message verbatim when agents produce messages faster than one voice
can speak. The next selection revisits unheard material, with a 30-second
attempt backoff and a 120-second argument window. Completed audio coverage,
not generated text or a provider response, suppresses heard arguments. Identical
claim/sighting/movement material under a new source ID is also suppressed for
120 seconds after completed audio. Changed claims remain eligible. P0/P1
preemption, source expiry/revocation and the single voice/request remain intact.

Comparing at least two public speakers can replace one normal commentary turn
with `analysis`. The default enables this bounded behavior; set
`commentary.analysis_enabled=false` to disable it. The durable common ledger
allows at most one analysis per meeting and three per match, alongside all
ordinary rate/hour/token limits. This starts no classifier, additional model,
tool call or paid fallback. That one turn requests medium effort only when the
live account/model catalog supports it; otherwise it uses the verified minimal
effort. P0/P1 speech never requests medium. Internal reasoning is neither spoken
nor logged. Analysis reserves 768 output tokens locally; the SDK exposes no
server output-token hard cap, so actual usage and possible one-turn overshoot
remain explicit. The existing 45-second provider/voice-backpressure deadline
and 12-second pending audio ceiling are unchanged.

Social launches sample short Russian names without replacement from a curated
24-name pool using OS randomness, independently of authoritative game RNG. The
resolved names are retained in launch manifests. `--player-names` supplies an
explicit unique name per participant; the single launcher preserves explicit
`--player-name`. Internal `agent-01` paths, bridge identities and player roles
do not change. Independent single launches need distinct explicit names if a
shared room requires a naming convention.

Pure tests establish selection, attribution, priority, effort/catalog and quota
wiring. Model naturalness, complete audio, actual camera changes and Twitch
appearance require native acceptance against the exact final build; source
tests alone do not prove those outcomes. See [cadence and heard memory](broadcast-liveliness.md)
and [observer movement](broadcast-motion.md).
