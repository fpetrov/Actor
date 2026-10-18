using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using Actor.Protocol.Core.V1;

namespace Actor.Sdk
{
    // Generic main-thread boundary. Background tasks exchange only Protobuf messages.
    public sealed class ActorBridgeClient : IDisposable
    {
        private sealed class PendingCommand
        {
            public CommandEnvelope Command;
            public string LatestKey;
        }

        private sealed class Session
        {
            public readonly object Gate = new object();
            public readonly string Id = Guid.NewGuid().ToString("N");
            public readonly CancellationTokenSource Cancellation = new CancellationTokenSource();
            public readonly Queue<GameToRuntime> Reliable = new Queue<GameToRuntime>();
            public readonly LinkedList<PendingCommand> Commands = new LinkedList<PendingCommand>();
            public readonly Dictionary<string, LinkedListNode<PendingCommand>> LatestCommands =
                new Dictionary<string, LinkedListNode<PendingCommand>>(StringComparer.Ordinal);
            public readonly SemaphoreSlim WriteSignal = new SemaphoreSlim(0, 1);
            public readonly ActorBridgeDiagnostics Diagnostics = new ActorBridgeDiagnostics();
            public IActorTransport Transport;
            public BridgeEndpoint Endpoint;
            public GameToRuntime Observation;
            public Task ConnectTask = Task.CompletedTask;
            public Task ReaderTask = Task.CompletedTask;
            public Task WriterTask = Task.CompletedTask;
            public Task ShutdownTask;
            public long StartedAt = Stopwatch.GetTimestamp();
            public long LastReceivedAt = Stopwatch.GetTimestamp();
            public ulong IncomingSequence, OutgoingSequence, LastCommandId, Revision, LastEventRevision;
            public double NextObservation = double.NaN, NextHeartbeat = double.NaN, LastPump = double.NegativeInfinity;
            public bool Accepted;
            public int Ending, DisconnectNotified;
        }

        private readonly object stateGate = new object();
        private readonly int gameThread;
        private readonly IActorGameAdapter adapter;
        private readonly Func<IActorTransport> transportFactory;
        private readonly ActorBridgeOptions options;
        private readonly ActorCapabilityRegistry registry;
        private readonly CapabilityManifest manifest;
        private readonly Queue<Session> disconnectNotifications = new Queue<Session>();
        private Session current, lastSession;
        private ActorConnectionState state;
        private string lastErrorCode = "", lastErrorMessage = "";
        private bool canRetry, disposed;

        public ActorConnectionState State { get { lock (stateGate) return state; } }
        public string LastErrorCode { get { lock (stateGate) return lastErrorCode; } }
        public string LastErrorMessage { get { lock (stateGate) return lastErrorMessage; } }
        public bool CanRetry { get { lock (stateGate) return canRetry; } }
        public string SessionId { get { lock (stateGate) return current?.Id ?? lastSession?.Id ?? ""; } }
        public ActorBridgeDiagnostics Diagnostics
        {
            get
            {
                Session session;
                lock (stateGate) session = current ?? lastSession;
                if (session == null) return new ActorBridgeDiagnostics();
                lock (session.Gate)
                {
                    var snapshot = session.Diagnostics.Copy();
                    snapshot.PendingCommands = session.Commands.Count;
                    snapshot.PendingReliableMessages = session.Reliable.Count;
                    snapshot.PendingObservations = session.Observation == null ? 0 : 1;
                    return snapshot;
                }
            }
        }

        public ActorBridgeClient(IActorGameAdapter adapter, Func<IActorTransport> transportFactory,
            ActorBridgeOptions options = null)
        {
            this.adapter = adapter ?? throw new ArgumentNullException(nameof(adapter));
            this.transportFactory = transportFactory ?? throw new ArgumentNullException(nameof(transportFactory));
            this.options = (options ?? new ActorBridgeOptions()).ValidatedCopy();
            gameThread = Thread.CurrentThread.ManagedThreadId;
            registry = new ActorCapabilityRegistry();
            adapter.Register(registry);
            manifest = registry.BuildManifest(adapter.Identity, adapter.SelfActorId);
            registry.EventPublished += OnEvent;
        }

        public async Task StartAsync(BridgeEndpoint endpoint)
        {
            RequireGameThread();
            if (endpoint == null) throw new ArgumentNullException(nameof(endpoint));
            Session previous;
            lock (stateGate)
            {
                if (disposed) throw new ObjectDisposedException(nameof(ActorBridgeClient));
                previous = current;
                if (previous != null && Volatile.Read(ref previous.Ending) == 0)
                    throw new InvalidOperationException("Bridge is already starting or connected.");
            }
            DrainDisconnectNotifications();
            if (previous != null) _ = ShutdownAsync(previous);
            var session = new Session { Endpoint = endpoint };
            lock (stateGate)
            {
                current = lastSession = session;
                state = ActorConnectionState.Connecting;
                lastErrorCode = lastErrorMessage = "";
                canRetry = false;
            }
            try
            {
                session.Transport = transportFactory() ?? throw new ActorTransportException("TransportUnavailable", "Transport factory returned no transport.");
                session.ConnectTask = session.Transport.ConnectAsync(endpoint, session.Cancellation.Token);
                await session.ConnectTask.ConfigureAwait(false);
                if (!IsActive(session)) return;
                EnqueueReliable(session, new GameToRuntime
                {
                    Hello = new GameHello
                    {
                        GameId = manifest.GameId, GameVersion = manifest.GameVersion,
                        SelfActorId = manifest.SelfActorId, ProtocolVersion = ActorProtocol.Version
                    }
                });
                EnqueueReliable(session, new GameToRuntime { Manifest = manifest.Clone() });
                session.WriterTask = WriterAsync(session);
                session.ReaderTask = ReaderAsync(session);
            }
            catch (OperationCanceledException) when (Volatile.Read(ref session.Ending) != 0) { }
            catch (ActorTransportException error) { Fail(session, error.Code, error.Message, error.Retryable); }
            catch (Exception) { Fail(session, "TransportUnavailable", "Could not connect to the local ACTOR Runtime.", true); }
        }

        // The host calls this from its game thread. All adapter captures/handlers run here.
        public void Pump(double monotonicSeconds)
        {
            RequireGameThread();
            if (double.IsNaN(monotonicSeconds) || double.IsInfinity(monotonicSeconds))
                throw new ArgumentOutOfRangeException(nameof(monotonicSeconds));
            long started = Stopwatch.GetTimestamp();
            DrainDisconnectNotifications();
            Session session;
            lock (stateGate) session = current;
            if (session == null || !IsActive(session)) return;
            if (monotonicSeconds < session.LastPump)
                throw new ArgumentException("Pump time must be monotonic.", nameof(monotonicSeconds));
            try
            {
                session.LastPump = monotonicSeconds;
                long now = Stopwatch.GetTimestamp();
                bool accepted;
                long lastReceived;
                lock (session.Gate) { accepted = session.Accepted; lastReceived = session.LastReceivedAt; }
                if (!accepted)
                {
                    if (Seconds(now - session.StartedAt) > options.HandshakeTimeoutSeconds)
                        Fail(session, "HandshakeTimedOut", "ACTOR Runtime did not complete the handshake.", true);
                    return;
                }
                if (Seconds(now - lastReceived) > options.PeerTimeoutSeconds)
                {
                    Fail(session, "PeerTimedOut", "ACTOR Runtime stopped responding.", true);
                    return;
                }

                for (int count = 0; count < options.MaxCommandsPerPump && IsActive(session); count++)
                {
                    PendingCommand pending;
                    lock (session.Gate)
                    {
                        if (session.Commands.Count == 0) break;
                        pending = session.Commands.First.Value;
                        session.Commands.RemoveFirst();
                        if (pending.LatestKey != null) session.LatestCommands.Remove(pending.LatestKey);
                    }
                    var result = registry.DispatchCommand(pending.Command, manifest.SelfActorId, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
                    lock (session.Gate) session.Diagnostics.CommandsDispatched++;
                    EnqueueReliable(session, new GameToRuntime { CommandResult = result });
                }
                if (!IsActive(session)) return;
                if (double.IsNaN(session.NextObservation) || monotonicSeconds >= session.NextObservation)
                {
                    session.NextObservation = monotonicSeconds + 1 / options.ObservationRateHz;
                    ulong revision;
                    lock (session.Gate) revision = ++session.Revision;
                    var frame = registry.CaptureObservations(manifest.SelfActorId, manifest.SelfActorId, revision);
                    if (frame.CalculateSize() > ActorProtocol.MaxMessageBytes - 128)
                    { Fail(session, "MessageTooLarge", "Observation frame exceeds the message size limit.", false); return; }
                    lock (session.Gate)
                    {
                        session.Diagnostics.ObservationsCaptured++;
                        if (frame.Revision < session.LastEventRevision)
                            session.Diagnostics.ObservationsCoalesced++;
                        else
                        {
                            if (session.Observation != null) session.Diagnostics.ObservationsCoalesced++;
                            session.Observation = new GameToRuntime { Observation = frame };
                        }
                    }
                    WakeWriter(session);
                }
                if (double.IsNaN(session.NextHeartbeat) || monotonicSeconds >= session.NextHeartbeat)
                {
                    session.NextHeartbeat = monotonicSeconds + options.HeartbeatIntervalSeconds;
                    ulong sequence;
                    lock (session.Gate) sequence = session.IncomingSequence;
                    EnqueueReliable(session, new GameToRuntime { Heartbeat = new Heartbeat { LastReceivedSequence = sequence } });
                }
            }
            catch (Exception)
            { Fail(session, "GameAdapterFailed", "Game adapter failed while capturing/dispatching ACTOR data.", false); }
            finally
            {
                double milliseconds = Seconds(Stopwatch.GetTimestamp() - started) * 1000;
                lock (session.Gate)
                {
                    session.Diagnostics.LastPumpMilliseconds = milliseconds;
                    session.Diagnostics.MaxPumpMilliseconds = Math.Max(session.Diagnostics.MaxPumpMilliseconds, milliseconds);
                }
                DrainDisconnectNotifications();
            }
        }

        private void OnEvent(GameEventEnvelope envelope)
        {
            RequireGameThread();
            Session session;
            lock (stateGate) session = current;
            if (session == null || !IsActive(session)) return;
            lock (session.Gate)
            {
                if (!session.Accepted) return;
                envelope = envelope.Clone();
                envelope.Revision = ++session.Revision;
                session.LastEventRevision = envelope.Revision;
                // Priority events must not be followed by an older pending observation revision.
                if (session.Observation != null)
                { session.Observation = null; session.Diagnostics.ObservationsCoalesced++; }
            }
            EnqueueReliable(session, new GameToRuntime { Event = envelope });
        }

        private async Task WriterAsync(Session session)
        {
            try
            {
                while (IsActive(session))
                {
                    GameToRuntime message = null;
                    lock (session.Gate)
                    {
                        if (session.Reliable.Count > 0) message = session.Reliable.Dequeue();
                        else if (session.Observation != null) { message = session.Observation; session.Observation = null; }
                        if (message != null) { message.SessionId = session.Id; message.Sequence = ++session.OutgoingSequence; }
                    }
                    if (message == null)
                    {
                        await session.WriteSignal.WaitAsync(session.Cancellation.Token).ConfigureAwait(false);
                        continue;
                    }
                    int bytes = message.CalculateSize();
                    if (bytes > ActorProtocol.MaxMessageBytes)
                    { Fail(session, "MessageTooLarge", "Outgoing message exceeds the message size limit.", false); return; }
                    await session.Transport.SendAsync(message, session.Cancellation.Token).ConfigureAwait(false);
                    lock (session.Gate)
                    {
                        session.Diagnostics.MessagesSent++;
                        session.Diagnostics.BytesSent += bytes;
                        if (message.Observation != null) session.Diagnostics.ObservationsSent++;
                        if (message.CommandResult != null) session.Diagnostics.CommandResultsSent++;
                    }
                }
            }
            catch (OperationCanceledException) when (Volatile.Read(ref session.Ending) != 0) { }
            catch (ActorTransportException error) { Fail(session, error.Code, error.Message, error.Retryable); }
            catch (Exception) { Fail(session, "TransportWriteFailed", "ACTOR Runtime stream could not be written.", true); }
        }

        private async Task ReaderAsync(Session session)
        {
            try
            {
                while (IsActive(session))
                {
                    RuntimeToGame message = await session.Transport.ReceiveAsync(session.Cancellation.Token).ConfigureAwait(false);
                    if (!IsActive(session)) return;
                    if (message == null) { Fail(session, "TransportClosed", "ACTOR Runtime closed its stream.", true); return; }
                    int bytes = message.CalculateSize();
                    if (bytes > ActorProtocol.MaxMessageBytes)
                    { Fail(session, "MessageTooLarge", "Incoming message exceeds the message size limit.", false); return; }
                    if (message.SessionId != session.Id || message.Sequence == 0 || message.Sequence <= session.IncomingSequence)
                    { Fail(session, "InvalidStreamOrder", "Runtime session/sequence is invalid.", false); return; }
                    lock (session.Gate)
                    {
                        session.IncomingSequence = message.Sequence;
                        session.LastReceivedAt = Stopwatch.GetTimestamp();
                        session.Diagnostics.MessagesReceived++;
                        session.Diagnostics.BytesReceived += bytes;
                    }
                    switch (message.BodyCase)
                    {
                        case RuntimeToGame.BodyOneofCase.Hello:
                            if (session.Accepted || !message.Hello.Accepted || message.Hello.ProtocolVersion == null ||
                                message.Hello.ProtocolVersion.Major != ActorProtocol.Major || message.Hello.ProtocolVersion.Minor != ActorProtocol.Minor)
                            { Fail(session, "HandshakeRejected", "ACTOR Runtime rejected or repeated the protocol handshake.", false); return; }
                            lock (session.Gate) session.Accepted = true;
                            lock (stateGate) if (ReferenceEquals(current, session) && IsActive(session)) state = ActorConnectionState.Connected;
                            break;
                        case RuntimeToGame.BodyOneofCase.Command:
                            if (!session.Accepted) { Fail(session, "UnexpectedMessage", "Command arrived before handshake acceptance.", false); return; }
                            EnqueueCommand(session, message.Command);
                            break;
                        case RuntimeToGame.BodyOneofCase.Heartbeat:
                            break;
                        case RuntimeToGame.BodyOneofCase.Error:
                            Fail(session, message.Error.Code, message.Error.Message, false);
                            return;
                        case RuntimeToGame.BodyOneofCase.Disconnect:
                            Fail(session, "RuntimeDisconnected", message.Disconnect.Reason, true);
                            return;
                        default:
                            Fail(session, "UnexpectedMessage", "Runtime message has no recognized body.", false);
                            return;
                    }
                }
            }
            catch (OperationCanceledException) when (Volatile.Read(ref session.Ending) != 0) { }
            catch (ActorTransportException error) { Fail(session, error.Code, error.Message, error.Retryable); }
            catch (Exception) { Fail(session, "TransportReadFailed", "ACTOR Runtime stream could not be read.", true); }
        }

        private void EnqueueCommand(Session session, CommandEnvelope command)
        {
            if (command.CommandId != 0 && command.CommandId <= session.LastCommandId)
            {
                EnqueueReliable(session, Rejected(command.CommandId, "CommandOutOfOrder", "Command ID must increase within its session."));
                return;
            }
            if (command.CommandId != 0) session.LastCommandId = command.CommandId;
            string latestKey = null;
            if (command.TargetActorId == manifest.SelfActorId && registry.TryGetCapability(command.CapabilityId, out CapabilityDefinition capability) &&
                capability.Kind == CapabilityKind.Command && capability.CommandDelivery == CommandDelivery.Latest)
                latestKey = command.CapabilityId;
            CommandEnvelope superseded = null;
            bool overflow;
            lock (session.Gate)
            {
                if (Volatile.Read(ref session.Ending) != 0) return;
                session.Diagnostics.CommandsReceived++;
                if (latestKey != null && session.LatestCommands.TryGetValue(latestKey, out LinkedListNode<PendingCommand> previous))
                {
                    superseded = previous.Value.Command;
                    session.Commands.Remove(previous);
                    session.LatestCommands.Remove(latestKey);
                    session.Diagnostics.CommandsSuperseded++;
                }
                overflow = session.Commands.Count >= options.QueueCapacity;
                if (!overflow)
                {
                    var node = session.Commands.AddLast(new PendingCommand { Command = command.Clone(), LatestKey = latestKey });
                    if (latestKey != null) session.LatestCommands.Add(latestKey, node);
                }
            }
            if (superseded != null)
                EnqueueReliable(session, Rejected(superseded.CommandId, "CommandSuperseded", "Newer latest-value command replaced this pending command."));
            if (overflow) Fail(session, "BufferOverflow", "Runtime command buffer is full.", false);
        }

        private static GameToRuntime Rejected(ulong id, string code, string message) => new GameToRuntime
        {
            CommandResult = new CommandResult
            {
                CommandId = id, Status = CommandStatus.Rejected,
                Error = new ProtocolError { Code = code, Message = message, CommandId = id }
            }
        };

        private void EnqueueReliable(Session session, GameToRuntime message)
        {
            bool overflow;
            lock (session.Gate)
            {
                if (Volatile.Read(ref session.Ending) != 0) return;
                overflow = session.Reliable.Count >= options.QueueCapacity;
                if (!overflow) session.Reliable.Enqueue(message);
            }
            if (overflow) Fail(session, "BufferOverflow", "Reliable ACTOR message buffer is full.", false);
            else WakeWriter(session);
        }

        private static void WakeWriter(Session session)
        {
            try { session.WriteSignal.Release(); }
            catch (SemaphoreFullException) { }
            catch (ObjectDisposedException) { }
        }

        private bool IsActive(Session session)
        {
            if (Volatile.Read(ref session.Ending) != 0) return false;
            lock (stateGate) return ReferenceEquals(current, session) && !disposed;
        }

        private void Fail(Session session, string code, string message, bool retryable)
        {
            lock (stateGate)
            {
                if (!ReferenceEquals(current, session) || Interlocked.CompareExchange(ref session.Ending, 1, 0) != 0) return;
                // Error is observable before transport Abort/Dispose finishes.
                // Revoke pending work atomically before publishing that state;
                // slow native teardown must not leave stale commands visible.
                lock (session.Gate)
                {
                    session.Commands.Clear(); session.LatestCommands.Clear();
                    session.Reliable.Clear(); session.Observation = null;
                }
                state = ActorConnectionState.Error;
                lastErrorCode = string.IsNullOrEmpty(code) ? "ProtocolError" : code;
                lastErrorMessage = (message ?? "").Replace(session.Endpoint.Token, "[redacted]");
                canRetry = retryable;
                // Publish the main-thread reset alongside Error, before any background cleanup.
                disconnectNotifications.Enqueue(session);
            }
            EndSession(session, false);
        }

        public Task StopAsync()
        {
            Session session = StopCore();
            return session == null ? Task.CompletedTask : ShutdownAsync(session);
        }

        public void Abort()
        {
            Session session = StopCore();
            if (session != null) _ = ShutdownAsync(session);
        }

        private Session StopCore()
        {
            Session session;
            lock (stateGate)
            {
                session = current;
                current = null;
                state = ActorConnectionState.Disconnected;
                lastErrorCode = lastErrorMessage = "";
                canRetry = false;
                if (session != null) Interlocked.Exchange(ref session.Ending, 1);
            }
            if (session != null) EndSession(session);
            if (Thread.CurrentThread.ManagedThreadId == gameThread) DrainDisconnectNotifications();
            return session;
        }

        private void EndSession(Session session, bool notify = true)
        {
            try { session.Cancellation.Cancel(); } catch (ObjectDisposedException) { }
            try { session.Transport?.Abort(); } catch (Exception) { }
            lock (session.Gate)
            {
                session.Commands.Clear(); session.LatestCommands.Clear(); session.Reliable.Clear(); session.Observation = null;
            }
            if (notify) lock (stateGate) disconnectNotifications.Enqueue(session);
            if (Thread.CurrentThread.ManagedThreadId == gameThread) DrainDisconnectNotifications();
            _ = ShutdownAsync(session);
        }

        private void DrainDisconnectNotifications()
        {
            RequireGameThread();
            while (true)
            {
                Session session;
                lock (stateGate)
                {
                    if (disconnectNotifications.Count == 0) return;
                    session = disconnectNotifications.Dequeue();
                }
                if (Interlocked.Exchange(ref session.DisconnectNotified, 1) == 0)
                {
                    try { adapter.OnDisconnected(); }
                    catch (Exception) { /* Cleanup must still cancel and release the transport. */ }
                }
            }
        }

        private Task ShutdownAsync(Session session)
        {
            lock (session.Gate)
            {
                if (session.ShutdownTask == null) session.ShutdownTask = CleanupAsync(session);
                return session.ShutdownTask;
            }
        }

        private static async Task CleanupAsync(Session session)
        {
            try
            {
                Task disconnect;
                using (var timeout = new CancellationTokenSource(TimeSpan.FromMilliseconds(500)))
                {
                    try { disconnect = session.Transport?.DisconnectAsync(timeout.Token) ?? Task.CompletedTask; }
                    catch (Exception) { disconnect = Task.CompletedTask; }
                    Task workers = Task.WhenAll(session.ConnectTask, session.ReaderTask, session.WriterTask, disconnect);
                    Task completed = await Task.WhenAny(workers, Task.Delay(500)).ConfigureAwait(false);
                    if (completed == workers) { try { await workers.ConfigureAwait(false); } catch (Exception) { } }
                    else _ = workers.ContinueWith(task => { _ = task.Exception; }, TaskContinuationOptions.OnlyOnFaulted);
                }
            }
            finally
            {
                try { session.Transport?.Dispose(); } catch (Exception) { }
                session.Cancellation.Dispose();
                session.WriteSignal.Dispose();
            }
        }

        public void Dispose()
        {
            lock (stateGate) { if (disposed) return; disposed = true; }
            registry.EventPublished -= OnEvent;
            Session session = StopCore();
            if (session != null)
            {
                try { ShutdownAsync(session).Wait(500); } catch (Exception) { }
            }
        }

        private void RequireGameThread()
        {
            if (Thread.CurrentThread.ManagedThreadId != gameThread)
                throw new InvalidOperationException("ActorBridgeClient must be pumped/started on its creating game thread.");
        }
        private static double Seconds(long ticks) => (double)ticks / Stopwatch.Frequency;
    }
}
