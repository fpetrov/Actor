# ACTOR Unity SDK

This local UPM package contains transport-neutral protocol messages, typed extension registration and an asynchronous bridge boundary. Its assembly has `noEngineReferences: true`. It knows no Agents Arena mechanics, NGO, Unity objects or gRPC channel. The separate `com.actor.transport.grpc` package implements `IActorTransport`.

Create an `ActorBridgeClient` after the controlled actor's stable identity is available, on the game thread. The constructor registers adapter capabilities and captures its manifest. Call `StartAsync` from that same thread and call `Pump(monotonicSeconds)` each game frame. Capture delegates and command handlers run only inside Pump; background tasks handle Protobuf and transport I/O.

```csharp
var bridge = new ActorBridgeClient(adapter, () => new GrpcActorTransport());
await bridge.StartAsync(new BridgeEndpoint("http://127.0.0.1:50051", sessionToken));
// Game update, on the creating thread:
bridge.Pump(monotonicSeconds);
```

The game adapter owns local control mode and the lifetime of its input source. `OnDisconnected()` must clear that source. The SDK invokes it on the creating game thread; failures observed on a background thread queue a reset for the next Pump. Abort/Stop called on the game thread reset immediately. A movement capability's game-side input source must also enforce its own stale-intent timeout, independently of connection heartbeats. The SDK does not assume that every game's commands describe movement.

State transitions are Disconnected → Connecting → Connected, or Error. `StartAsync` completes after transport connection/worker startup; State becomes Connected only after Runtime confirms the validated manifest. `CanRetry` is true for transient transport/timeouts, and false for rejected authentication/protocol/package compatibility errors. Reconnecting uses a new transport and session; old workers cannot mutate new session state.

Defaults are 15 Hz observation capture, 1 s heartbeat, 3 s peer timeout, 5 s handshake timeout, 128 reliable messages, 128 pending commands, and a single latest observation slot. Command capability delivery metadata selects ordered or latest-value behavior. Superseded pending commands receive an explicit result. Commands are checked for expiration again on game-thread dispatch. Writer sequence numbers are assigned on actual send. Events and observations share one increasing bridge revision; an older pending observation is discarded before a priority event.

`Abort()` immediately cancels/aborts the transport and clears queues. `StopAsync()` additionally awaits bounded cleanup. Dispose waits at most 500 ms for managed workers before returning, so an unresponsive Runtime cannot indefinitely block Editor shutdown. Transports must implement nonblocking Abort/Dispose and honor cancellation whenever possible.

Diagnostics expose observation/command/result/message/byte counters, current queue sizes and Pump duration. Session tokens never appear in endpoint formatting or SDK diagnostics; Runtime error messages are redacted if they echo the token.

The .NET SDK project and tests compile the same source without Unity. `Directory.Build.props` keeps every MSBuild intermediate/output in ignored root `build/sdk-obj` and `build/sdk-bin`, outside this UPM package. See `proto/README.md` for generated source and descriptor policies.
