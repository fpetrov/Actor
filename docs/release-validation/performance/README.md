# Actual dedicated-server measurements

[human-mixed-02-postgame.json](human-mixed-02-postgame.json) is the completed
server's independently fetched postgame instrumentation. Its **704.914-second**
window spans Social session initialization through GameOver, including Waiting
and subsequent phases. It is separate from the short Exploration sample below.

| Postgame instrument | Result |
| --- | ---: |
| Process CPU relative to one logical core | 8.52% |
| Process-lifetime peak working set | 147.33 MiB |
| FixedUpdate calls per measured second | 50.00 |
| Update calls per measured second | 59.77 |
| Visibility p95 / maximum | 0.2232 / 11.5315 ms |
| Social refresh p95 / maximum | 3.6041 / 38.2142 ms |
| Instrumented Social protobuf payload bytes / messages | 7,452,585 / 34,139 |

Both p95 values cover only the latest 1,024 samples; maxima cover all samples.
FixedUpdate frequency is **not** the NGO network tick rate. Payload counts omit
transforms, spawn traffic, Standard communication and RPC/NGO/UDP headers, so
their byte rate is not total network throughput. The public projection retains
numeric counters and independently anonymized recipient aggregates; it exposes
no actor identities, roles, messages or private state. These measurements do
not establish gameplay, native-input or model acceptance.

[mixed-server-03.json](mixed-server-03.json) records a separate **15.123-second**
sample with one native Human and three Codex clients connected. The native
operator confirmed Exploration immediately before sampling. No live private
phase, role, position, task state or message was read by the collector.

| Measurement | Result |
| --- | ---: |
| Mean dedicated-process CPU, relative to one logical core | 9.65% |
| Approximately three-second interval CPU range | 8.95–10.90% |
| RSS at start / end | 145.88 / 146.48 MiB |
| Process threads at start / end | 27 / 27 |
| Kernel UDP socket drop-counter increase | 0 |
| Largest sampled UDP receive queue | 6,400 bytes |

The read-only collector verified the exact room/match allocation, readiness
record, actor UID, staging executable, current Control Plane parent, sole UDP
socket owner and unchanged process start identity. Only aggregate values and
check booleans are public. It created no packet capture and changed no server
or game state. Four participants were recorded at both sample endpoints.

CPU is total dedicated-process CPU time divided by elapsed wall time, expressed
relative to **one** logical core; it is not a host-wide CPU percentage or engine
tick cost. RSS is process resident memory; its reported high-water mark covers
the process lifetime. Six samples do not establish a sustained queue maximum.
A zero socket-drop delta does not establish zero network loss or zero latency.

**UDP throughput and packet rate were not measured.** Neither were multiplayer
RTT, native client FPS, input latency or displayed-frame timing. The normal
tick/visibility/refresh/social-payload report is written only at GameOver and
was absent at this sample's end. It was later fetched separately as the postgame
report above; no tick rate or visibility cost is inferred from CPU, wall time or
socket queues. This short window alone does not establish
whole-match performance or successful mixed-gameplay acceptance.

The earlier successful sample belonged to WaitingForPlayers and remains
separate ignored evidence. It is not included as Exploration performance.
