using System.Collections.Concurrent;
using System.Diagnostics;
using System.Threading.Channels;
using Actor.Protocol.Core.V1;
using Actor.Protocol.Standard.V1;
using Actor.Sdk;
using Google.Protobuf.WellKnownTypes;

// These fixtures deliberately keep the creating game thread; all fake async I/O has no captured context.
#pragma warning disable xUnit1031

namespace Actor.Sdk.Tests;

public sealed class BridgeTests
{
    private const string Token = "0123456789abcdef0123456789abcdef";
    private static BridgeEndpoint Endpoint => new("http://127.0.0.1:50051", Token);

    private sealed class FakeTransport : IActorTransport
    {
        private readonly Channel<RuntimeToGame> incoming = Channel.CreateUnbounded<RuntimeToGame>();
        private long sequence;
        private int reads;
        public readonly ConcurrentQueue<GameToRuntime> Sent = new();
        public bool AutoHello = true;
        public ActorTransportException? ConnectError;
        public TaskCompletionSource? WriteGate;
        public TaskCompletionSource? ConnectGate;
        public TaskCompletionSource<RuntimeToGame>? LateRead;
        public bool HangingDisconnect;
        public ManualResetEventSlim? AbortEntered, AbortRelease;
        public string SessionId = "";
        public int Aborts, Disposals, SendAttempts;

        public Task ConnectAsync(BridgeEndpoint endpoint, CancellationToken cancellation) => ConnectError != null
            ? Task.FromException(ConnectError) : ConnectGate?.Task.WaitAsync(cancellation) ?? Task.CompletedTask;

        public async Task SendAsync(GameToRuntime message, CancellationToken cancellation)
        {
            Interlocked.Increment(ref SendAttempts);
            if (WriteGate != null) await WriteGate.Task.WaitAsync(cancellation).ConfigureAwait(false);
            Sent.Enqueue(message.Clone());
            SessionId = message.SessionId;
            if (message.Manifest != null && AutoHello)
                Push(new RuntimeToGame { Hello = new RuntimeHello { Accepted = true, ProtocolVersion = ActorProtocol.Version } });
        }

        public async Task<RuntimeToGame> ReceiveAsync(CancellationToken cancellation)
        {
            if (Interlocked.Increment(ref reads) > 1 && LateRead != null)
                return await LateRead.Task.ConfigureAwait(false); // Deliberately ignores cancellation to test isolation/bounded shutdown.
            try { return await incoming.Reader.ReadAsync(cancellation).ConfigureAwait(false); }
            catch (ChannelClosedException) { return null!; }
        }

        public void Push(RuntimeToGame message)
        {
            if (message.SessionId == "") message.SessionId = SessionId;
            if (message.Sequence == 0) message.Sequence = (ulong)Interlocked.Increment(ref sequence);
            incoming.Writer.TryWrite(message.Clone());
        }

        public Task DisconnectAsync(CancellationToken cancellation) => HangingDisconnect
            ? new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously).Task : Task.CompletedTask;
        public void Abort()
        {
            Interlocked.Increment(ref Aborts);
            AbortEntered?.Set();
            AbortRelease?.Wait(TimeSpan.FromSeconds(5));
            incoming.Writer.TryComplete();
        }
        public void Dispose() { Interlocked.Increment(ref Disposals); incoming.Writer.TryComplete(); }
    }

    private sealed class Adapter : IActorGameAdapter, IActorExtension
    {
        public GameIdentity Identity { get; } = new("test-game", "0.3.0");
        public string SelfActorId => "test-actor";
        public ExtensionPackage Package => ActorStandardExtensionPackage.Create();
        public readonly List<(ulong Id, float X, int Thread)> Commands = new();
        public readonly List<int> Captures = new(), Disconnects = new();
        public ActorEventPublisher<Motion2DObservation>? Events;
        public bool ThrowCapture;
        public float Intent;

        public void Register(ActorCapabilityRegistry registry) => registry.RegisterExtension(this);
        public void Register(IActorExtensionRegistrar scoped)
        {
            scoped.RegisterObservation<Transform2DObservation>("actor.standard.transform2d", "Transform", _ =>
            {
                Captures.Add(Environment.CurrentManagedThreadId);
                if (ThrowCapture) throw new ArgumentException("invalid game state");
                return new Transform2DObservation { X = Intent, FacingY = 1 };
            });
            CommandOutcome Handle(CommandContext context, Locomotion2DCommand command)
            {
                Commands.Add((context.CommandId, command.DesiredMoveX, Environment.CurrentManagedThreadId));
                Intent = command.DesiredMoveX;
                return CommandOutcome.Accepted();
            }
            scoped.RegisterCommand<Locomotion2DCommand>("actor.standard.locomotion2d", "Latest", Handle, CommandDelivery.Latest);
            scoped.RegisterCommand<Locomotion2DCommand>("test.ordered", "Ordered", Handle, CommandDelivery.Ordered);
            Events = scoped.RegisterEvent<Motion2DObservation>("test.event", "Generic typed event fixture");
        }
        public void OnDisconnected() { Disconnects.Add(Environment.CurrentManagedThreadId); Intent = 0; }
    }

    private static CommandEnvelope Command(ulong id, float x = 1, string capability = "actor.standard.locomotion2d") => new()
    {
        CommandId = id,
        CapabilityId = capability,
        ExtensionId = "actor.standard",
        ExtensionVersion = "0.1.0",
        TargetActorId = "test-actor",
        IssuedAtUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        TtlMs = 1000,
        Payload = Any.Pack(new Locomotion2DCommand { DesiredMoveX = x })
    };

    private static void Eventually(Func<bool> condition, int timeoutMs = 2000)
    {
        var watch = Stopwatch.StartNew();
        while (!condition() && watch.ElapsedMilliseconds < timeoutMs) Thread.Sleep(1);
        Assert.True(condition(), "Timed out waiting for asynchronous bridge progress.");
    }

    private static void Connect(ActorBridgeClient bridge)
    {
        bridge.StartAsync(Endpoint).GetAwaiter().GetResult();
        Eventually(() => bridge.State == ActorConnectionState.Connected);
    }

    [Theory]
    [InlineData("http://0.0.0.0:50051")]
    [InlineData("http://192.168.1.1:50051")]
    [InlineData("https://127.0.0.1:50051")]
    [InlineData("http://127.0.0.1:50051/secret")]
    [InlineData("http://127.0.0.1:50051/?token=secret")]
    public void EndpointRejectsRemoteAndAmbiguousAddresses(string address) =>
        Assert.Throws<ArgumentException>(() => new BridgeEndpoint(address, Token));

    [Fact]
    public void EndpointNeverFormatsTokenAndValidatesItsStrength()
    {
        Assert.DoesNotContain(Token, Endpoint.ToString());
        Assert.Throws<ArgumentException>(() => new BridgeEndpoint("http://127.0.0.1:50051", "weak"));
        Assert.Equal("::1", new BridgeEndpoint("http://[::1]:50051", Token).Address.DnsSafeHost);
    }

    [Fact]
    public void RealDuplexContractSendsHelloThenManifestAndAssignsWriterSequence()
    {
        var transport = new FakeTransport();
        var adapter = new Adapter();
        using var bridge = new ActorBridgeClient(adapter, () => transport);
        Connect(bridge);
        bridge.Pump(0);
        Eventually(() => transport.Sent.Any(message => message.Observation != null));
        var sent = transport.Sent.ToArray();
        Assert.NotNull(sent[0].Hello);
        Assert.NotNull(sent[1].Manifest);
        Assert.Equal("test-actor", sent[1].Manifest.SelfActorId);
        Assert.All(sent, message => Assert.Equal(bridge.SessionId, message.SessionId));
        Assert.Equal(Enumerable.Range(1, sent.Length).Select(value => (ulong)value), sent.Select(message => message.Sequence));
        Assert.Equal(ActorConnectionState.Connected, bridge.State);
    }

    [Fact]
    public void BackgroundReceiveDoesNotTouchGameUntilMainThreadPump()
    {
        int mainThread = Environment.CurrentManagedThreadId;
        var transport = new FakeTransport();
        var adapter = new Adapter();
        using var bridge = new ActorBridgeClient(adapter, () => transport);
        Connect(bridge);
        var producer = new Thread(() => transport.Push(new RuntimeToGame { Command = Command(1, 0.7f) }));
        producer.Start(); producer.Join();
        Eventually(() => bridge.Diagnostics.PendingCommands == 1);
        Assert.Empty(adapter.Commands);
        bridge.Pump(0);
        Assert.Equal(mainThread, adapter.Commands.Single().Thread);
        Assert.All(adapter.Captures, thread => Assert.Equal(mainThread, thread));
        Exception? wrongThread = null;
        var worker = new Thread(() => { try { bridge.Pump(1); } catch (Exception error) { wrongThread = error; } });
        worker.Start(); worker.Join();
        Assert.IsType<InvalidOperationException>(wrongThread);
    }

    [Fact]
    public void LatestCommandsCoalesceWithoutReorderingOrderedCommandsAndReturnSupersededResults()
    {
        var transport = new FakeTransport();
        var adapter = new Adapter();
        using var bridge = new ActorBridgeClient(adapter, () => transport);
        Connect(bridge);
        transport.Push(new RuntimeToGame { Command = Command(1, 1) });
        transport.Push(new RuntimeToGame { Command = Command(2, 2) });
        transport.Push(new RuntimeToGame { Command = Command(3, 3, "test.ordered") });
        transport.Push(new RuntimeToGame { Command = Command(4, 4) });
        Eventually(() => bridge.Diagnostics.CommandsReceived == 4);
        Assert.Equal(2, bridge.Diagnostics.PendingCommands);
        bridge.Pump(0);
        Assert.Equal(new ulong[] { 3, 4 }, adapter.Commands.Select(command => command.Id));
        Eventually(() => transport.Sent.Count(message => message.CommandResult != null) == 4);
        var results = transport.Sent.Where(message => message.CommandResult != null).Select(message => message.CommandResult).ToArray();
        Assert.Equal(new ulong[] { 1, 2 }, results.Where(result => result.Error?.Code == "CommandSuperseded").Select(result => result.CommandId));
        Assert.Equal(2, bridge.Diagnostics.CommandsSuperseded);
        Assert.Equal(4, adapter.Intent);
    }

    [Fact]
    public void UnknownMalformedAndExpiredCommandsAreStructuredResultsWithoutBreakingConnection()
    {
        var transport = new FakeTransport();
        var adapter = new Adapter();
        using var bridge = new ActorBridgeClient(adapter, () => transport);
        Connect(bridge);
        var unknown = Command(1, capability: "unknown.capability");
        var malformed = Command(2, capability: "test.ordered"); malformed.Payload.Value = Google.Protobuf.ByteString.CopyFrom(new byte[] { 0xff });
        var expired = Command(3); expired.IssuedAtUnixMs -= 5000;
        transport.Push(new RuntimeToGame { Command = unknown });
        transport.Push(new RuntimeToGame { Command = malformed });
        transport.Push(new RuntimeToGame { Command = expired });
        Eventually(() => bridge.Diagnostics.PendingCommands == 3);
        bridge.Pump(0);
        Eventually(() => transport.Sent.Count(message => message.CommandResult != null) == 3);
        Assert.Equal(new[] { "UnknownCapability", "MalformedCommand", "CommandTimedOut" },
            transport.Sent.Where(message => message.CommandResult != null).Select(message => message.CommandResult.Error.Code));
        Assert.Empty(adapter.Commands);
        Assert.Equal(ActorConnectionState.Connected, bridge.State);
    }

    [Fact]
    public void SlowWriterKeepsOneLatestObservationAndIncreasingFrameEventRevisions()
    {
        var transport = new FakeTransport();
        var adapter = new Adapter();
        using var bridge = new ActorBridgeClient(adapter, () => transport);
        Connect(bridge);
        transport.WriteGate = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        bridge.Pump(0);
        Eventually(() => transport.SendAttempts >= 3);
        for (int i = 1; i <= 20; i++) bridge.Pump(i * 0.1);
        Assert.Equal(1, bridge.Diagnostics.PendingObservations);
        Assert.True(bridge.Diagnostics.ObservationsCoalesced >= 18);
        adapter.Events!.Publish(new Motion2DObservation { IsMoving = true }, "test-actor", 9999);
        bridge.Pump(2.2);
        transport.WriteGate.SetResult();
        Eventually(() => transport.Sent.Any(message => message.Event != null) && transport.Sent.Any(message => message.Observation?.Revision > 1));
        var revisions = transport.Sent.Where(message => message.Event != null || message.Observation != null)
            .Select(message => message.Event?.Revision ?? message.Observation.Revision).ToArray();
        Assert.Equal(revisions.OrderBy(value => value), revisions);
        Assert.DoesNotContain(9999ul, revisions);
        Assert.True(bridge.Diagnostics.PendingReliableMessages <= 128);
    }

    [Fact]
    public void ObservationRateIsIndependentOfPumpFrameRate()
    {
        var transport = new FakeTransport();
        using var bridge = new ActorBridgeClient(new Adapter(), () => transport);
        Connect(bridge);
        bridge.Pump(0); bridge.Pump(0.01); bridge.Pump(0.03); bridge.Pump(0.06);
        Assert.Equal(1, bridge.Diagnostics.ObservationsCaptured);
        bridge.Pump(0.067);
        Assert.Equal(2, bridge.Diagnostics.ObservationsCaptured);
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void InvalidSessionOrDuplicateSequenceIsFatalAndZerosOnlyOnGameThread(bool wrongSession)
    {
        int mainThread = Environment.CurrentManagedThreadId;
        var transport = new FakeTransport();
        var adapter = new Adapter { Intent = 1 };
        using var bridge = new ActorBridgeClient(adapter, () => transport);
        Connect(bridge);
        transport.Push(new RuntimeToGame
        {
            SessionId = wrongSession ? "old-session" : bridge.SessionId,
            Sequence = wrongSession ? 2ul : 1ul,
            Heartbeat = new Heartbeat()
        });
        Eventually(() => bridge.State == ActorConnectionState.Error);
        Assert.Equal("InvalidStreamOrder", bridge.LastErrorCode);
        Assert.False(bridge.CanRetry);
        bridge.Pump(0);
        Assert.Equal(0, adapter.Intent);
        Assert.Equal(mainThread, adapter.Disconnects.Single());
    }

    [Fact]
    public void ProtocolErrorsAreFatalAndRedactSessionSecret()
    {
        var transport = new FakeTransport();
        using var bridge = new ActorBridgeClient(new Adapter(), () => transport);
        Connect(bridge);
        transport.Push(new RuntimeToGame { Error = new ProtocolError { Code = "RequiredExtensionMissing", Message = "invalid " + Token } });
        Eventually(() => bridge.State == ActorConnectionState.Error);
        Assert.Equal("RequiredExtensionMissing", bridge.LastErrorCode);
        Assert.False(bridge.CanRetry);
        Assert.DoesNotContain(Token, bridge.LastErrorMessage);
    }

    [Fact]
    public void RuntimeUnavailableIsRecoverableAndReconnectCreatesFreshSession()
    {
        var adapter = new Adapter();
        var transport = new FakeTransport();
        int attempts = 0;
        using var bridge = new ActorBridgeClient(adapter, () => ++attempts == 1
            ? throw new ActorTransportException("RuntimeUnavailable", "Local runtime is unavailable.") : transport);
        bridge.StartAsync(Endpoint).GetAwaiter().GetResult();
        Assert.Equal(ActorConnectionState.Error, bridge.State);
        Assert.True(bridge.CanRetry);
        string oldId = bridge.SessionId;
        Connect(bridge);
        Assert.NotEqual(oldId, bridge.SessionId);
        Assert.Equal(2, attempts);
        Assert.Equal(1ul, transport.Sent.First().Sequence);
    }

    [Fact]
    public void AuthenticationTransportFailureIsFatalAndClosesResources()
    {
        var transport = new FakeTransport { ConnectError = new ActorTransportException("AuthenticationFailed", "Invalid session token.", false) };
        var adapter = new Adapter { Intent = 1 };
        using var bridge = new ActorBridgeClient(adapter, () => transport);
        bridge.StartAsync(Endpoint).GetAwaiter().GetResult();
        Assert.Equal(ActorConnectionState.Error, bridge.State);
        Assert.Equal("AuthenticationFailed", bridge.LastErrorCode);
        Assert.False(bridge.CanRetry);
        Assert.Equal(0, adapter.Intent);
        Eventually(() => transport.Disposals == 1);
    }

    [Fact]
    public void AbortCancelsStreamClearsBuffersAndOldTaskCannotAffectReconnectedActor()
    {
        var old = new FakeTransport { LateRead = new TaskCompletionSource<RuntimeToGame>(TaskCreationOptions.RunContinuationsAsynchronously) };
        var fresh = new FakeTransport();
        var adapter = new Adapter();
        int attempt = 0;
        using var bridge = new ActorBridgeClient(adapter, () => ++attempt == 1 ? old : fresh);
        Connect(bridge);
        bridge.Abort();
        Assert.Equal(ActorConnectionState.Disconnected, bridge.State);
        Assert.Equal(0, bridge.Diagnostics.PendingCommands);
        Assert.Equal(0, bridge.Diagnostics.PendingReliableMessages);
        Assert.True(old.Aborts > 0);
        Connect(bridge);
        fresh.Push(new RuntimeToGame { Command = Command(1, 0.7f) });
        Eventually(() => bridge.Diagnostics.PendingCommands == 1);
        bridge.Pump(0);
        old.LateRead!.SetException(new ActorTransportException("LateOldFailure", "Old stream failed."));
        Thread.Sleep(20);
        bridge.Pump(0.1);
        Assert.Equal(ActorConnectionState.Connected, bridge.State);
        Assert.Equal(0.7f, adapter.Intent);
        Assert.Single(adapter.Disconnects);
    }

    [Fact]
    public void HandshakeAndPeerTimeoutsAreRecoverableAndClearIntent()
    {
        var options = new ActorBridgeOptions { HeartbeatIntervalSeconds = 0.01, PeerTimeoutSeconds = 0.05, HandshakeTimeoutSeconds = 0.05 };
        var unavailable = new FakeTransport { AutoHello = false };
        var adapter = new Adapter { Intent = 1 };
        using (var bridge = new ActorBridgeClient(adapter, () => unavailable, options))
        {
            bridge.StartAsync(Endpoint).GetAwaiter().GetResult();
            Thread.Sleep(70); bridge.Pump(0);
            Assert.Equal("HandshakeTimedOut", bridge.LastErrorCode);
            Assert.True(bridge.CanRetry);
            Assert.Equal(0, adapter.Intent);
        }
        var transport = new FakeTransport();
        using (var bridge = new ActorBridgeClient(new Adapter(), () => transport, options))
        {
            Connect(bridge);
            Thread.Sleep(70); bridge.Pump(0);
            Assert.Equal("PeerTimedOut", bridge.LastErrorCode);
            Assert.True(bridge.CanRetry);
        }
    }

    [Fact]
    public void OrderedBufferOverflowFailsSafelyInsteadOfAccumulatingUnboundedCommands()
    {
        var transport = new FakeTransport();
        var adapter = new Adapter { Intent = 1 };
        using var bridge = new ActorBridgeClient(adapter, () => transport, new ActorBridgeOptions { QueueCapacity = 2 });
        Connect(bridge);
        for (ulong id = 1; id <= 3; id++) transport.Push(new RuntimeToGame { Command = Command(id, capability: "test.ordered") });
        Eventually(() => bridge.State == ActorConnectionState.Error);
        bridge.Pump(0);
        Assert.Equal("BufferOverflow", bridge.LastErrorCode);
        Assert.Equal(0, bridge.Diagnostics.PendingCommands);
        Assert.Equal(0, adapter.Intent);
        Assert.False(bridge.CanRetry);
    }

    [Fact]
    public void ErrorRevokesPendingCommandsBeforeSlowTransportTeardown()
    {
        using var entered = new ManualResetEventSlim();
        using var release = new ManualResetEventSlim();
        var transport = new FakeTransport { AbortEntered = entered, AbortRelease = release };
        var adapter = new Adapter { Intent = 1 };
        using var bridge = new ActorBridgeClient(adapter, () => transport, new ActorBridgeOptions { QueueCapacity = 2 });
        Connect(bridge);
        try
        {
            for (ulong id = 1; id <= 3; id++)
                transport.Push(new RuntimeToGame { Command = Command(id, capability: "test.ordered") });
            Assert.True(entered.Wait(TimeSpan.FromSeconds(2)));
            Assert.Equal(ActorConnectionState.Error, bridge.State);
            Assert.Equal(0, bridge.Diagnostics.PendingCommands);
            bridge.Pump(0);
            Assert.Equal(0, adapter.Intent);
            Assert.Empty(adapter.Commands);
        }
        finally { release.Set(); }
    }

    [Fact]
    public void GameAdapterExceptionFailsWithoutThrowingIntoGameplay()
    {
        var transport = new FakeTransport();
        var adapter = new Adapter { ThrowCapture = true };
        using var bridge = new ActorBridgeClient(adapter, () => transport);
        Connect(bridge);
        bridge.Pump(0);
        Assert.Equal("GameAdapterFailed", bridge.LastErrorCode);
        Assert.Equal(ActorConnectionState.Error, bridge.State);
        Assert.False(bridge.CanRetry);
        Assert.Single(adapter.Disconnects);
    }

    [Fact]
    public void DisposeHasBoundedWaitEvenWhenBrokenTransportIgnoresCancellation()
    {
        var transport = new FakeTransport
        {
            HangingDisconnect = true,
            LateRead = new TaskCompletionSource<RuntimeToGame>(TaskCreationOptions.RunContinuationsAsynchronously)
        };
        var adapter = new Adapter { Intent = 1 };
        var bridge = new ActorBridgeClient(adapter, () => transport);
        Connect(bridge);
        var watch = Stopwatch.StartNew();
        bridge.Dispose();
        Assert.True(watch.ElapsedMilliseconds < 800, "Dispose must not wait indefinitely on the game thread.");
        Assert.Equal(0, adapter.Intent);
        Eventually(() => transport.Disposals == 1);
        transport.LateRead!.TrySetResult(null!);
        Assert.Throws<ObjectDisposedException>(() => bridge.StartAsync(Endpoint).GetAwaiter().GetResult());
    }

    [Fact]
    public void PendingConnectCanBeCancelledWithoutWaitingForRuntime()
    {
        var transport = new FakeTransport { ConnectGate = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously) };
        var adapter = new Adapter { Intent = 1 };
        using var bridge = new ActorBridgeClient(adapter, () => transport);
        Task starting = bridge.StartAsync(Endpoint);
        Assert.False(starting.IsCompleted);
        Assert.Equal(ActorConnectionState.Connecting, bridge.State);
        bridge.Abort();
        Eventually(() => starting.IsCompleted);
        starting.GetAwaiter().GetResult();
        Assert.Equal(ActorConnectionState.Disconnected, bridge.State);
        Assert.Equal(0, adapter.Intent);
        Assert.Single(adapter.Disconnects);
        Eventually(() => transport.Disposals == 1);
    }

    [Fact]
    public void StopAsyncClearsPendingIntentDisposesTransportAndIsIdempotent()
    {
        var transport = new FakeTransport();
        var adapter = new Adapter { Intent = 1 };
        using var bridge = new ActorBridgeClient(adapter, () => transport);
        Connect(bridge);
        transport.Push(new RuntimeToGame { Command = Command(1) });
        Eventually(() => bridge.Diagnostics.PendingCommands == 1);
        bridge.StopAsync().GetAwaiter().GetResult();
        bridge.StopAsync().GetAwaiter().GetResult();
        Assert.Equal(ActorConnectionState.Disconnected, bridge.State);
        Assert.Equal(0, bridge.Diagnostics.PendingCommands);
        Assert.Equal(0, adapter.Intent);
        Assert.Single(adapter.Disconnects);
        Assert.Equal(1, transport.Disposals);
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void RejectedHelloOrCommandBeforeHandshakeCannotStartControl(bool rejectedHello)
    {
        var transport = new FakeTransport { AutoHello = false };
        var adapter = new Adapter();
        using var bridge = new ActorBridgeClient(adapter, () => transport);
        bridge.StartAsync(Endpoint).GetAwaiter().GetResult();
        transport.Push(rejectedHello
            ? new RuntimeToGame { Hello = new RuntimeHello { Accepted = false, ProtocolVersion = ActorProtocol.Version } }
            : new RuntimeToGame { Command = Command(1) });
        Eventually(() => bridge.State == ActorConnectionState.Error);
        bridge.Pump(0);
        Assert.False(bridge.CanRetry);
        Assert.Equal(rejectedHello ? "HandshakeRejected" : "UnexpectedMessage", bridge.LastErrorCode);
        Assert.Empty(adapter.Commands);
    }

    [Fact]
    public void OversizedMessageFailsBeforePayloadDispatch()
    {
        var transport = new FakeTransport();
        var adapter = new Adapter();
        using var bridge = new ActorBridgeClient(adapter, () => transport);
        Connect(bridge);
        transport.Push(new RuntimeToGame { Error = new ProtocolError { Code = "error", Message = new string('x', ActorProtocol.MaxMessageBytes) } });
        Eventually(() => bridge.State == ActorConnectionState.Error);
        bridge.Pump(0);
        Assert.Equal("MessageTooLarge", bridge.LastErrorCode);
        Assert.False(bridge.CanRetry);
        Assert.Empty(adapter.Commands);
    }
}
