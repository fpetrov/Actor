# Unity gRPC compatibility spike

The isolated spike completed **before** ACTOR bridge/Runtime implementation. Python is the loopback gRPC server; Unity is the client. Both use generated `google.protobuf.BytesValue` messages over one real HTTP/2 bidirectional stream.

| Environment | Checks | Median RTT | p95 RTT |
| --- | ---: | ---: | ---: |
| Python fixture/selftest | 6/6 | 0.293 ms | 0.414 ms |
| Unity 6000.6.4f1 macOS Editor / Mono | 7/7 | 0.487 ms | 1.572 ms |
| macOS standalone / Mono / .NET Standard 2.1 | 7/7 | 0.897 ms | 3.587 ms |

The standalone continued its normal frame loop (106 frames during the exchange). Tests covered server messages before any client payload, concurrent binary echo and periodic server messages, cancellation, a new stream after reconnect, graceful half-close and invalid-token rejection. Reports: [Editor](editor.json), [standalone](standalone.json), [Python](python-selftest.json).

Chosen stack: **Grpc.Net.Client / Grpc.Core.Api 2.84.0**, **Google.Protobuf 3.36.2**, **Cysharp YetAnotherHttpHandler 1.11.6**, with `Http2Only=true` for loopback cleartext HTTP/2. The handler is MIT-licensed; managed dependency licenses and archive/assembly hashes are preserved in the transport package. The ordinary Mono HTTP handler was not used as an assumed HTTP/2 implementation. No gRPC-Web or custom TCP fallback was introduced. [Official handler documentation](https://github.com/Cysharp/YetAnotherHttpHandler), [Microsoft platform requirements](https://learn.microsoft.com/en-us/aspnet/core/grpc/netstandard).

Current production desktop and dedicated builds use Mono. The installed macOS Unity module contains Mono variations and no IL2CPP player variation; **IL2CPP was not verified**. Windows and Linux client streaming were not exercised on this Mac. The Linux Dedicated Server does not run the local bridge.

Fixture source lives in `spikes/grpc/`, separate from the generic ACTOR protocol and SDK. `spikes/grpc/python/README.md` describes the Python endpoint and launcher. The Unity test scripts are copied into an isolated project under ignored `build/grpc-spike/Unity`; its package manifest points to the pinned official YAHA package. The same managed dependency selection is reproducible with `scripts/fetch-unity-grpc-dependencies.py`.

Editor execution (without `-quit`, because the asynchronous smoke method exits after its result):

```bash
Unity -batchmode -projectPath /absolute/path/build/grpc-spike/Unity \
  -executeMethod GrpcSmokeEditor.Run \
  --spike-token-file /absolute/path/build/grpc-spike/token \
  --spike-report /absolute/path/build/grpc-spike/editor.json
```

Build with `GrpcSmokeEditor.Build --spike-output /absolute/path/GrpcSmoke.app`, then launch the compiled player with the same token/report arguments and `--spike-exit true`. The ignored token file is fixture configuration only. Observations/commands travel over the stream; files are not a Game ↔ Runtime IPC mechanism.
