# Isolated Unity gRPC compatibility fixture

This smoke fixture precedes ACTOR protocol/bridge implementation. It uses the
generated well-known Protobuf `google.protobuf.BytesValue` in both directions.
No files carry observations or actions; the ignored token file is configuration
for this isolated test only.

```bash
python3 -m venv build/grpc-spike/venv
build/grpc-spike/venv/bin/python -m pip install -r spikes/grpc/python/requirements.txt
build/grpc-spike/venv/bin/python spikes/grpc/python/launch.py
python3 spikes/grpc/python/status.py
build/grpc-spike/venv/bin/python spikes/grpc/python/selftest.py
```

Endpoint defaults to `127.0.0.1:50061`; `launch.py --port PORT` changes it. The
server binds exclusively to IPv4 loopback and accepts true HTTP/2 bidirectional
streaming at `/actor.spike.Echo/Connect`. Send metadata
`x-actor-spike-token: <ACTOR_SPIKE_TOKEN>`; absence/mismatch returns
`UNAUTHENTICATED`. The launcher creates a random 32-byte hex token in ignored
`build/grpc-spike/token`, permissions `0600`, and passes it through the process
environment. It never prints the token.

For every client `BytesValue.value`, the server returns `BytesValue.value =
b"echo:" + original_bytes`. Independently, while the input stream remains open,
it sends `b"tick:1"`, `b"tick:2"`, ... every 100 ms. Counters restart per stream;
new streams on the same channel or after reconnect work independently. A client
must identify responses by their byte prefix rather than assuming strict
request/reply alternation. Half-close drains pending echoes and finishes the
response stream. Cancellation releases the reader task; SIGINT/SIGTERM stops
the server with a one-second grace period. Echo queue capacity is 32 and maximum
wire message size is 64 KiB.

The status artifact contains endpoint, PID and counters only. Use Ctrl-C on the
foreground server to stop it; logs are structured JSON and contain no payloads
or tokens.

Implementation references: [stable gRPC AsyncIO API](https://grpc.github.io/grpc/python/grpc_asyncio.html),
[independent bidirectional streams](https://grpc.io/docs/languages/python/basics/).
