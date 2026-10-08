using System;
using System.Threading;
using System.Threading.Tasks;
using Actor.Protocol.Core.V1;

namespace Actor.Sdk
{
    public interface IActorTransport : IDisposable
    {
        Task ConnectAsync(BridgeEndpoint endpoint, CancellationToken cancellation);
        Task SendAsync(GameToRuntime message, CancellationToken cancellation);
        Task<RuntimeToGame> ReceiveAsync(CancellationToken cancellation);
        Task DisconnectAsync(CancellationToken cancellation);
        void Abort();
    }

    public sealed class BridgeEndpoint
    {
        public Uri Address { get; }
        public string Token { get; }
        public BridgeEndpoint(string address, string token) : this(new Uri(address, UriKind.Absolute), token) { }
        public BridgeEndpoint(Uri address, string token)
        {
            if (address == null || !address.IsAbsoluteUri || address.Scheme != "http" ||
                !address.IsLoopback || (address.Host != "127.0.0.1" && address.Host != "localhost" && address.Host != "[::1]" && address.Host != "::1") ||
                !string.IsNullOrEmpty(address.UserInfo) || address.AbsolutePath != "/" ||
                !string.IsNullOrEmpty(address.Query) || !string.IsNullOrEmpty(address.Fragment))
                throw new ArgumentException("ACTOR bridge requires a loopback HTTP endpoint without path or credentials.", nameof(address));
            if (token == null || token.Length < 32 || token.Length > 1024)
                throw new ArgumentException("Bridge token must contain 32–1024 ASCII characters.", nameof(token));
            foreach (char character in token)
                if (character < 33 || character > 126)
                    throw new ArgumentException("Bridge token must contain printable ASCII without whitespace.", nameof(token));
            Address = address;
            Token = token;
        }
        public override string ToString() => Address.ToString();
    }

    public sealed class ActorTransportException : Exception
    {
        public string Code { get; }
        public bool Retryable { get; }
        public ActorTransportException(string code, string message, bool retryable = true, Exception innerException = null)
            : base(message, innerException)
        { Code = Require.Text(code, nameof(code)); Retryable = retryable; }
    }

    public enum ActorConnectionState { Disconnected, Connecting, Connected, Error }

    public sealed class ActorBridgeOptions
    {
        public double ObservationRateHz { get; set; } = 15;
        public double HeartbeatIntervalSeconds { get; set; } = 1;
        public double PeerTimeoutSeconds { get; set; } = 3;
        public double HandshakeTimeoutSeconds { get; set; } = 5;
        public int QueueCapacity { get; set; } = 128;
        public int MaxCommandsPerPump { get; set; } = 128;
        internal ActorBridgeOptions ValidatedCopy()
        {
            if (double.IsNaN(ObservationRateHz) || double.IsInfinity(ObservationRateHz) || ObservationRateHz < 1 || ObservationRateHz > 60 ||
                HeartbeatIntervalSeconds <= 0 || double.IsNaN(HeartbeatIntervalSeconds) || double.IsInfinity(HeartbeatIntervalSeconds) ||
                PeerTimeoutSeconds <= HeartbeatIntervalSeconds || double.IsNaN(PeerTimeoutSeconds) || double.IsInfinity(PeerTimeoutSeconds) ||
                HandshakeTimeoutSeconds <= 0 || double.IsNaN(HandshakeTimeoutSeconds) || double.IsInfinity(HandshakeTimeoutSeconds) ||
                QueueCapacity < 2 || QueueCapacity > 4096 || MaxCommandsPerPump < 1 || MaxCommandsPerPump > 4096)
                throw new ArgumentException("Invalid bridge timing/buffer options.");
            return new ActorBridgeOptions
            {
                ObservationRateHz = ObservationRateHz,
                HeartbeatIntervalSeconds = HeartbeatIntervalSeconds,
                PeerTimeoutSeconds = PeerTimeoutSeconds,
                HandshakeTimeoutSeconds = HandshakeTimeoutSeconds,
                QueueCapacity = QueueCapacity,
                MaxCommandsPerPump = MaxCommandsPerPump
            };
        }
    }

    // Snapshot copied under a lock; callers never observe partially updated counters.
    public sealed class ActorBridgeDiagnostics
    {
        public long ObservationsCaptured { get; internal set; }
        public long ObservationsSent { get; internal set; }
        public long ObservationsCoalesced { get; internal set; }
        public long CommandsReceived { get; internal set; }
        public long CommandsDispatched { get; internal set; }
        public long CommandsSuperseded { get; internal set; }
        public long CommandResultsSent { get; internal set; }
        public long MessagesSent { get; internal set; }
        public long MessagesReceived { get; internal set; }
        public long BytesSent { get; internal set; }
        public long BytesReceived { get; internal set; }
        public int PendingReliableMessages { get; internal set; }
        public int PendingCommands { get; internal set; }
        public int PendingObservations { get; internal set; }
        public double LastPumpMilliseconds { get; internal set; }
        public double MaxPumpMilliseconds { get; internal set; }
        internal ActorBridgeDiagnostics Copy() => (ActorBridgeDiagnostics)MemberwiseClone();
    }
}
