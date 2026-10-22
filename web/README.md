# Agents Arena website

`frontend/` is a small static room list/create page. It uses same-origin public
Control Plane endpoints; gameplay runs in the ordinary Unity application. It
never requests join tickets, resume credentials or model credentials.

Run `scripts/build-web.sh` from the repository to copy only `frontend/` into
ignored `build/web/`. There is no npm dependency install, server rendering or
bundled local environment. `web/tests/` stays outside the served directory.

The release link points to the project's HTTPS GitHub Releases page. It does not
assert that a particular new release is published or manufacture an asset URL.
Players should choose a version matching the connected game server and verify
the archive's `SHA256SUMS`; the release pipeline supplies the macOS archive,
install notes and manifests. Windows remains unavailable until an actual Windows
build is produced. The persistent limited test RC notice distinguishes HTTPS
downloads from the current unencrypted HTTP/UDP gameplay transport.

Room names remain text, never HTML. Decoded room lists and fields are bounded
and validated before replacing the displayed list; this is not a network-byte
limit on the response body. Failure keeps the last list explicitly marked as
stale. A version mismatch clears cached compatibility data for the next user
attempt, and an in-flight create cannot allocate a second room. The same-origin
Content Security Policy permits no inline scripts, external script dependencies,
objects or alternate base URL; it does not make HTTP transport authenticated.

Offline interaction checks require Node18+ and use DOM fixtures, not a browser:

```bash
node --test web/tests/app.test.cjs
node --check web/frontend/app.js
scripts/build-web.sh
```

These checks cover failed/stale lists, text rendering, create fencing and version
retry, clipboard focus restoration and static accessibility/release semantics.
They do not prove rendered layouts, native clipboard support, screen-reader
behavior or deployment. Browser QA should check keyboard navigation, narrow
viewports/zoom, loading/offline states and a real copy gesture separately.
