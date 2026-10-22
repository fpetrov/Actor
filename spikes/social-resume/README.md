# M8 live reconnect acceptance probe

This opt-in fixture keeps an ordinary headless Human-mode Unity client process alive, drops its **opaque UDP traffic**, restores transport and calls the normal authenticated resume API. Three independent ordinary Runtime/Unity peers start the match. The supervisor never parses UDP payloads, reads another actor's role or supplies game truth. It does not create rooms, change CP configuration, deploy binaries or signal a server.

Run the offline checks without Unity or credentials:

```sh
build/runtime-venv/bin/python -m pytest spikes/social-resume/test_run_probe.py -q
```

The local UDP test needs loopback sockets. The process cleanup tests create and stop their own short-lived Python process groups, including an orphan child with a cleanup marker; a foreign process session remains alive.

After the owner of the isolated staging lease has deployed the M8 client/server, create a **fresh** ordinary SocialDeduction room using public API `http://127.0.0.1:5081`, admission `4`, game `0.9.0`, UDP `7781`, capacity 4–6. The trusted public test profile must use `KillCooldownMs >= 120000`, `MatchDurationMs >= 240000` and sufficient WaitingPlayers timeout. These public timing choices prevent a normal peer kill from ending the reconnect prerequisite. The match is otherwise unseeded and uses ordinary public readiness.

```sh
build/runtime-venv/bin/python spikes/social-resume/run_probe.py \
  --room ROOM00 --match-id EXACT_32_HEX_MATCH_ID \
  --api-url http://127.0.0.1:5081 \
  --proxy-port 19090 --base-port 19100 --timeout 220 \
  --output build/social-resume/actual-01
```

The default client is the built `build/agents-arena-client/AgentsArena.app`. `--client` may select another owned M8 build. The output directory must be empty. The supervisor rejects a different match/version/mode/UDP, occupied room or non-loopback CP API. Run it with the installed Runtime virtualenv; `social-fixture-parity` and its ordinary Skills must be installed.

The primary client alone receives `--social-resume-report` and `--resume-probe-proxy-port`. Both hooks require batch mode. The proxy changes only that client's transport destination to a loopback relay; the signed admission and server target remain unchanged. The resume bearer stays inside `MatchSession` memory and a targeted owner RPC. Neither the probe nor supervisor can retrieve it.

Strict acceptance requires all of the following:

- An actual connected duplicate-resume request is rejected by CP, after its connected heartbeat; the pure registry tests independently verify the final server takeover guard.
- Actual packets are dropped in both directions until the client reports a real transport disconnection. Traffic resumes before the probe's bounded fault timeout.
- The same client OS process stays alive, resumes the same own actor/match and own assignment, preserves own Alive state, and receives a higher authenticated connection generation during Exploration.
- All required own observation bundles hydrate. The old generation intent is rejected by an authoritative private `connection_changed` result; a start acknowledgement cannot satisfy this check.
- Fresh coherent authoritative motion remains stopped for at least two seconds. Every owned launcher/Runtime/client is stopped, with no known process or owned group remaining.

`acceptance.json` is an explicit whitelist of booleans, packet counts and durations. `resume.json` contains the same safe client evidence and wall-clock fault timings. Local `launch.json` and ordinary launcher reports retain their existing private isolation metadata and are **not** public artifacts. Do not publish raw stdout/logs or arbitrary report trees.

This probe proves transport recovery of a live actor with memory intact. It does **not** prove native GUI recovery, process-restart resume, a killed/ejected actor's resurrection rejection, a ghost-target kill, model behavior or whole-match success. Pure authority tests cover vulnerable retained avatars, irreversible death/ejection, grace expiry, explicit forfeiture and old-generation rejection; actual network versions of those cases remain separate acceptance work. Room/server automatic cleanup may outlast local child cleanup because the grace and CP empty-room timers remain in effect.
