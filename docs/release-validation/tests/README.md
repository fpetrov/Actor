# Auth-free auxiliary checks

[auxiliary-verification.json](auxiliary-verification.json) preserves the earlier
offline/local-IPC snapshot: **311 tests and 332 subtests**, schema freshness for
**54 generated artifacts**, and **32/32** deterministic routing cases with zero
model or external requests. It also records four separately invoked self-test
sets. These counts can overlap and must not be added into a unique-case total.

[auxiliary.json](auxiliary.json) keeps that earlier result distinct from the
subsequent M8 helper run: **159 tests and 242 subtests** covering resume,
release-validation, privacy, protocol, fault and Sandbox regression helpers.
The version/path-specific subset independently passed **31 tests and 100
subtests**. Schema freshness remained **54 artifacts**. The report also records
the earlier bounded mixed-validator review. Its later frozen review separately
passed **20 tests and 54 subtests**, including contradictory cleanup, unsupported
final lives/ejection/parity and active-provider rejection. These review runs
overlap; they are not additional unique test totals.

The CI art dependency is an explicit pinned **Pillow 13.0.0** install. It was not
added to the production Runtime dependency set. Auxiliary tests use synthetic
artifacts and, where needed, owned local UDP/process fixtures. They launched no
Unity player, GUI, model, SSH operation, remote fault or deployment.

The new wrapper defaults to a launch plan only. Its tests prove version,
staging-path, exact-room and forwarding guards; they do not prove that an M8
privacy match, protocol negative, server crash or legacy Sandbox case actually
ran. [Reproducible helper commands](../../../spikes/release-validation/README.md)
remain separate from actual acceptance artifacts.
