# Native Human UI peers

This optional test plugin supplies deterministic ordinary-game peers. It is not
part of the Runtime wheel and provides no model-intelligence or native-input
acceptance claim. Install it only into a disposable validation environment that
already has Runtime 0.7.0; no optional model SDK is required:

```sh
VALIDATION_VENV/bin/python -m pip install --no-deps --no-build-isolation ./spikes/native-human
VALIDATION_VENV/bin/python -m pytest spikes/native-human/test_fixture.py -q -o asyncio_mode=auto
```

Use the same chosen Brain and trusted Objective for every agent peer. The
ordinary native app uses Human control and public player name `QA Human`.
Launch each peer through the ordinary installed launcher, with its own port, memory,
report directory and the existing `agents-arena` match policy:

```sh
VALIDATION_VENV/bin/actorctl join ROOMCODE --client-manifest CLIENT_MANIFEST_PATH \
  --operation match --brain native-human-witness-fixture --match-policy agents-arena \
  --objective "Play using only legitimate observations and ordinary Skills." \
  --port 19501 --headless --output build/native-human/peer-01
```

Check `VALIDATION_VENV/bin/actorctl join --help` for the client and
Control Plane options appropriate to the installation. This uses the same
validation environment for both plugin discovery and the launched Runtime;
the repository's developer `scripts/launch-agent.py` instead fixes its child
Runtime path to `build/runtime-venv`. The optional Brain names
are intentionally absent from the production multi-agent supervisor's provider
allowlist. Launching each peer explicitly avoids changing that validator.

`native-human-witness-fixture` works with five agent peers plus Human, or other
server-permitted participant counts. Crew wait without doing tasks, reporting
bodies or calling meetings. A fixture Impostor waits for public match elapsed
time 45 seconds and its actual own cooldown, approaches a currently visible
player whose public name is not `QA Human`, and requests one ordinary kill. It
then waits after observing that body, including legitimate private history.
This leaves body-report and task controls to the Human operator.

`native-human-victim-fixture` normally uses three peers plus Human. A fixture
Impostor first targets only a currently visible player with the unique public
name `QA Human`, after the same 45-second delay/cooldown. It requests a second
kill of a currently visible non-Human-name peer only after public match elapsed
time 180 seconds, its cooldown, and personal observation of the first victim's
body. Current-body observations and own legitimate history retain this evidence
across ordinary episodes. It never searches hidden positions. Ambiguous names
or absent observations produce waiting, not a guessed target. Parity remains a
server decision and is not guaranteed with different participant counts.

Both factories send at most one neutral discussion message per meeting. Crew
ballots use only a personally witnessed attack or an actually delivered scoped
discussion claim; unsupported accusations are not generated. Impostors skip.
No Brain automatically reports a body. No fixture assigns roles, resurrects a
player, selects an authoritative outcome, or reads another participant's report.
The public name is a visible targeting cue, not a Human-control or role oracle.
If the native player is the Impostor, fixture Crew remain passive; that run does
not prove the intended body/death coverage. Describe that limitation honestly.

Readiness, movement, kill, communication and ballots all use the existing typed
Skills, transport and server permissions. Actual UI input, task completion,
death/kill presentation, camera/audio samples and cleanup must be captured and
validated separately by the native operator. Do not publish raw screenshots or
private contexts as sanitized metadata evidence.

After the isolated validation, remove the optional plugin from that environment:

```sh
VALIDATION_VENV/bin/python -m pip uninstall -y actor-native-human-fixture
```
