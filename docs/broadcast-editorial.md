# Player watch and public discussion

The commentator follows the broadcast observer's permitted view. It never reads
the players' private roles, tasks, memories, observations or ballots. Public
in-game discussion works independently of optional Twitch chat OAuth.

`CameraDirector` samples an 18–26 second dwell once per confirmed camera. When
the scheduler has no active provider/speech and no pending P0/P1, Native may
request the least recently watched **currently visible** player. The server
still checks the scope, freshness, visibility and its eight-second minimum
dwell. If no other player is visible, the director rotates between the four
authored room anchors; it cannot look up a hidden player's position. A
continuously fresh empty view can seek another public anchor after ten seconds
on the confirmed camera and at least five seconds of observed emptiness.
Missing/stale frames do not count toward that interval. A successful
selection response is only acceptance. A later matching actually presented
camera epoch confirms the cut. Rejections and missing confirmations back off;
discussion, voting and GameOver hold camera selection. Thus fair rotation is an
opportunity among visible players, not a guarantee of omniscient coverage.

The public HUD names the followed player. The speech context names that same
visible player and supplies relevant dated evidence. A coarse `movement-seen`
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
