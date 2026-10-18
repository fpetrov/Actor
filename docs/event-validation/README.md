# Milestone4 acceptance — 18 October 2026

**Implemented and exercised in the actual Unity/VPS multiplayer game.** Runtime
0.3.0, game0.5.0, Arena extension0.2.0; Core1.0, Standard0.1.0 and admission3
remain unchanged. The deployment is `20261018T185733Z-42b32dc`.
[Machine-readable results](summary.json).

## Actual Codex + Human acceptance

All four runs used real remote lobby **J78LRT**, the official Codex SDK0.162.1
with ChatGPT sign-in, ordinary Unity clients and authoritative server physics.
No Python fixture injected a station signal. The Human operator pressed E and
used the native Signal Console; the server created and reliably broadcast each
typed event through NGO and the existing ACTOR bridge.

Objective: “Go to Engineering and wait there for30seconds. Ignore routine station
notifications, but if an urgent recall signal is received, return immediately
to Main Hall and finish.”

| Real scenario | Result | Duration | SDK turns | Replans |
|---|---|---:|---:|---:|
| Urgent while Wait runs | Wait cancelled; returned and stopped |64.015s|4|1|
| Urgent during active Brain turn | Old answer discarded; returned |67.327s|5|1|
| Memory recall after Runtime restart | Factual past-signal summary, no actions |18.731s|1|0|
| Original Engineering→Wait(2)→MainHall | All Skills succeed; Human observer11/11 |37.909s|4|0|

In the primary **Skill-preemption** run, navigation took8.138s, Wait was safely
cancelled after26.650s with `preempted_by_event`, and return navigation took6.719s.
The Objective remained Running during preemption and finished Succeeded. The
four model decisions took11.011,2.772,2.529 and1.810s; Skills executed a total
**41.508s without calling the Brain**. The urgent event's new context contained
the trigger ID/payload and cancelled SkillResult; Codex chose MainHall navigation.
No teleport, direct game tool or concurrent foreground procedure was used.

One routine signal in that run left Wait active and decision count at2. In the
separate reasoning run, **two** routine notices also left Wait active without an
extra turn. Its urgent signal arrived just after the full30-second wait, while
the next model decision was active: that answer became stale, the public SDK
turn was interrupted and a fresh decision returned the agent. This is a distinct
acceptance case, not claimed as a Wait interruption.

The Human remained connected with normal keyboard input and saw the agent leave
and return. [Native return screenshot](recall-human-return.png),
[routine console](routine-console.png), [urgent console](urgent-console.png),
[Human client](recall-human-client.json). The first reasoning run's independent
observer recorded the full room trajectory. The same observer was already
completed by the primary Skill run, so its11 checks are **not** attributed to that
later run. A fresh independent observer verified the original baseline11/11.

[Primary Runtime](codex-skill-recall-runtime.json), [primary Unity](codex-skill-recall-client.json),
[routine snapshot](routine-during-wait.json), [routine burst](routine-burst-during-wait.json),
[reasoning Runtime](codex-reasoning-runtime.json), [reasoning observer](reasoning-human-observer.json),
[baseline Runtime](codex-baseline-runtime.json), [baseline observer](baseline-human-observer.json).

## Context, memory and cognitive cost

Primary routing received/classified4 events: two room transitions, one routine
notice and one urgent recall. There was one replan, one remembered event, zero
queue drops/overflow and zero external classifier requests. Local classifier
median latency was **0.083ms**. Its four external contexts were
**1367,3110,4051,5799bytes**, average3581.75bytes, below the32KiB configured cap.
Estimated context tokens are a byte-based approximation; SDK measured usage is
reported separately.

Codex's cumulative primary usage was47,097 input tokens,24,064 cached input tokens
and366 output tokens,47,463 total. The reasoning-interruption run measured49,365
total tokens. Public unique SDK turn counts include the interrupted turn; joined
initial TextInput/ExternalMessage handles are counted once. No SDK items/hidden
reasoning are retained, and all live runs made **zero direct game/computer tool
calls**. Cumulative usage is not summed repeatedly across its own snapshots.

The persistent profile `milestone4-skill-recall` held5 entries after the primary
run: observed urgent signal,3 SkillResults and objective outcome. SQLite was36KiB
including another isolated test profile. A completely new Runtime/session/Actor
retrieved5 prior records from that same explicit profile; no new game event was
received. Codex returned: the remembered station-wide recall interrupted its
wait, it returned to MainHall and stopped. It used no Skill and sent no command.
The new outcome became entry6. [Memory-recall Runtime](codex-memory-recall-runtime.json),
[restart stats](memory-after-restart-stats.json). The SQLite file itself is local
and intentionally not committed.

Offline512-observation/64-event context comparison reduced **55,680→21,670bytes
(61.1%)**, preserving self state, critical trigger payload and the previous
preemption reason. [Context comparison](context-comparison.json).

The32-case curated **typed-policy** evaluation produced32/32 expected decisions,
interruption precision/recall1.0, zero important false negatives and zero
unnecessary interruptions. This does not establish general semantic accuracy or
calibrated confidence. [Dataset](../../runtime/evaluation/events.json),
[results](classifier-evaluation.json).

The synthetic stress comparison uses the real Scheduler/Runner/SQLite and a fake
Brain. Wait20 with1,000 routine events,1,000 unrelated events and25 duplicate
urgent envelopes reacted after19.809s with routing disabled, versus21.755ms with
rules. Both had2 Brain decisions and zero routine-triggered extra decisions;
the rule case preempted exactly once. Queue/pending high-water marks128,999
aggregations,873 explicitly evicted low-priority entries,25 duplicates and no
critical overflow. Additional traced Python peak was970,195bytes; native
allocations are excluded. [Comparison and limits](routing-comparison.json).

No TypeSafe key was configured. The pinned official SDK's async request/typed
response contract passed against an offline HTTP transport; missing-key,
timeout,429,rate/concurrency limits, malformed numeric results and fallback were
tested. **Live Jev accuracy, latency and monetary cost are unmeasured.**
[Official SDK contract](jev-sdk-contract.json).

## Regression, faults and performance

- **255 Python**, **68 SDK**, **18 backend** tests passed. CI uses fake model
  providers and installs neither optional SDK.
-37 generated artifacts passed reproducible freshness; Unity compilation,
  client/Linuxserver builds,58 geometry,4 console and12 prediction checks passed.
- A separately built wheel was executed outside checkout with both cognitive
  SDK imports blocked: plugin/resources, authenticated loopback gRPC,
  Wait→Finish and profile-scoped memory stats/clear passed. [Package proof](package-smoke.json).
- Actual Runtime loss after12 commands stopped the agent, failed the Objective,
  left Unity alive and sent no further commands. [Bridge fault](bridge-disconnect-safety.json).
- An isolated test match server was terminated only after verifying its lobby,
  UID, executable, exclusive UDP owner and READY identity. The Objective failed,
  velocity became zero and command count stayed68. The primary lobby was
  independently checked unaffected. [Multiplayer fault](multiplayer-disconnect-safety.json),
  [cleanup](fault-cleanup.json).
- Public SDK nonterminal interruption reused one client/thread with
  interrupted→completed, maximum one active decision,0 tools/commands.
  [Provider-only proof](codex-soft-interrupt.json).

Primary agent **118.99FPS**, Human approximately119FPS;291 command-result RTT
samples had median **5.156ms**,p95 **8.765ms**. Runtime averaged4.48% of one CPU
core, traced Python peak18.64MB. This excludes native gRPC/SDK-process memory.
The unchanged baseline measured118.88FPS and5.311/9.132ms median/p95. Active
bridge snapshots, not fresh post-retry zero counters, are used where appropriate.

Human correctness passed13/13 in a direct run and13/13 in a new isolated lobby;
the100±10ms-each-direction/3% loss relay run passed14/14. First visual samples
were31.46ms direct,31.79ms with relay and41.73ms isolated; another foreground
diagnostic measured23.92ms. The prior milestone's single sample was21.91ms.
These startup/frame/threshold samples vary and do **not** establish statistical
equivalence to22ms. NetworkCharacter, OwnerMovementPrediction and CharacterMotor2D
sources are unchanged; classification/model work runs in a separate process and
the isolated Human test used no Runtime. FPS and bridge latency show no material
regression; a strict22ms input budget would need a controlled multi-sample
input-to-render benchmark rather than these correctness probes.

The shared-room foreground diagnostic had two wall checks fail while other
not-yet-disconnected peer bodies remained on that wall. It is retained as
[diagnostic evidence](human-movement-shared-diagnostic.json), not counted as an
isolated geometry pass. The new lobby had exactly one peer and passed all13.
[Direct movement](human-movement-direct.json), [isolated movement](human-movement-isolated.json),
[relay movement](human-latency-relay.json).

## Requested technical report

1. Added EventRouter/classifier API, centralized cognitive config, SQLite
   MemoryStore, context selection and game-native station signals; extended the
   existing Runner/Scheduler/Codex/CLI.
2. RuntimeSession creates ingress after manifest negotiation and feeds decoded
   events before the64-entry WorldState ring. No second ingestion/world loop.
3. Strict multidimensional assessments advise deterministic Runtime policy;
   installed game rules supply trusted semantics, not Core game-ID branches.
4. Jev batches Choice(relevance),Score(urgency) and3 Noul judgments
   (remember/interrupt/review) into one official async request.
5. Missing key/SDK,timeout,rate limits,429/5xx and invalid responses use bounded
   deterministic fallback. ML cannot downgrade known critical conditions.
6. Bounded priority queues reserve triggers; protected-set overflow enters an
   explicit fail-safe and stops the Objective with cleanup. Shutdown flushes
   required evidence under a deadline.
7. Stream session/sequence rejects wire duplicates; game semantic IDs dedup
   logical events. Critical IDs survive routine floods; memory is idempotent
   across bridge reconnects when the plugin supplies a global logical ID.
8. Soft preemption cancels only the foreground execution with structured
   trigger IDs and `preempted_by_event`.
9. Objective retains Running status; the same existing loop requests a fresh
   decision instead of calling terminal `cancel()`.
10. Scheduler revokes execution rights, runs bounded cleanup/zero intent and
    releases its shared lease before replacement dispatch. Stale tasks are denied.
11. Events arriving during reasoning accumulate; one active turn is interrupted
    and drained, then a current snapshot resumes the same Codex thread.
12. Strategic epochs guard acceptance, Finish and deferred Skill dispatch,
    including races during async waiter cleanup. Transform frames do not change
    the strategic epoch.
13. Existing reflective ContextBuilder ranks freshness/capabilities/objective
    relevance and projects triggers, previous result and retrieved evidence.
14. Configured limits:32KiB external,24 observations,12 recent events,6 memories,
    4KiB event payload; trigger/previous result are protected. No opaque bytes.
15. SQLite defaults:1,000 total rows,4KiB content,7-day TTL, bounded pages and
    worker queue. It never mirrors every movement update.
16. Retrieval filters profile/game and optional type/capability/tags, then ranks
    lexical overlap,importance and recency. Evidence is distinct from live state.
17. Stable profile is explicit; network ActorId is not persistent identity.
    Default SQLite is session-local. Admin stats is read-only and clear scopes
    exactly one game/profile, preserving other records and database limits.
18. New Arena-only `StationSignalEvent`,kind/audience enums and fields
    signal_id/source/target/message/time/server_revision; descriptors/bindings
    regenerated. No Core schema change.
19. MainHall console uses the existing palette/shapes; nearby Human presses E,
    then Routine Notice or Urgent Recall. No image generation/new room required.
20. Owner RPC validates approved player/object/room/range/cooldown/action;
    server mints the signal and reliable NGO delivers its explicit station-wide
    audience to client adapter→existing gRPC→Runtime.
21. Routine notice and two-notice cases left Wait running and Brain count2.
22. Urgent recall cancelled Wait at26.650s, preserved Objective and produced one
    replan. Typed logical duplicates are separately proven by stress/tests.
23. Real Codex chose MainHall navigation, server confirmed membership/stopping,
    and Finish succeeded. Separate active-turn urgent case discarded stale output.
24. 4 SDK turns for Skill preemption;5 for reasoning interruption;1 for memory;
    4 for the unchanged baseline. Zero direct game tools.
25. Primary Skills ran41.508s with zero Brain calls during execution; baseline
    Skills17.649s. Waiting-for-model intervals are reported separately.
26. Primary4 classifications,median0.083ms,one replan; no semantic API calls.
27. Actual primary contexts1.37–5.80KiB; synthetic selection55.68→21.67KiB.
28. Primary profile5 persisted entries,4 retrievals/6 returned entries; restart
    retrieved5 and added one new outcome. SQLite36KiB across two test profiles.
29. 32 curated typed-policy cases matched expectations; no general Jev accuracy
    or calibration claim. Offline comparison records false positives/negatives,
    latency,queue pressure and memory bounds.
30. 255 Python/68 SDK/18 backend; freshness37; wheel isolation and model fakes.
31. Actual multiplayer baseline and fresh Human observer11/11; Human13/13 and
    relay14/14; both bridge/server loss safely stopped commands.
32. 118.99FPS,5.156ms median/8.765ms p95 RTT; Human sample variation and its
    measurement limits are recorded above without claiming an exact22ms match.
33. Added Python plugin groups/config/memory/event APIs and optional
    `interrupt_decision`; existing Skill calls and terminal cancellation remain.
    SDK failure now clears queues before publishing Error, fixing a baseline race.
34. Runtime0.3.0/Game0.5.0/Arena0.2.0; Standard0.1.0/Core1.0/admission3.
35. Reproducible Mono/macOS client and Linux server deployed; ControlPlane/nginx
    healthy, lobby creation/admission and website verified. Providers stay local.
36. Commits:ab09553(SDK),42b32dc(game/schema),a2223e9(cognition),
    8405438(reliability/evaluation); final evidence/docs commit follows these.
37. [Source-branch CI](https://github.com/fpetrov/Actor/actions/runs/39157850574) passed
    on8405438. Main workflow state and final commit are recorded in the delivery.
    CI explicitly verifies both optional SDKs are absent before deterministic tests.
38. Live Jev lacks a configured key; no calibrated semantic classifier benchmark,
    arbitrary-language rule interpretation, embeddings/beliefs, social gameplay,
    background Skills, IL2CPP or other client-platform verification is claimed.
    Game-native signal visibility is station-wide only; no historical signal
    replay is promised on a new bridge session.
39. Next useful milestone: typed game interaction/task Skills and richer observed
    world facts, composed through this event/context/memory layer. Social beliefs
    and semantic retrieval remain independent future work.

See [routing](../event-routing.md), [classifiers/privacy](../classifiers.md),
[context](../context-builder.md), [memory](../memory.md),
[Signal Console](../station-signals.md), [Brain/Codex lifecycle](../codex.md).

A fresh Human client was left in room **5K436N**, with native WASD and the
Signal Console opened. This is a new playground; the historical measurements
above belong to J78LRT.
