using System.Text;
using Actor.Networking;
using Actor.Protocol.Core.V1;
using Actor.Sdk;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Google.Protobuf.WellKnownTypes;

namespace Actor.Sdk.Tests;

public sealed class StationSignalTests
{
    [Theory]
    [InlineData(false, true, true, 1, 10, 0, 0, 1, StationSignalRules.Rejection.Unavailable)]
    [InlineData(true, false, true, 1, 10, 0, 0, 1, StationSignalRules.Rejection.NotAuthorized)]
    [InlineData(true, true, false, 1, 10, 0, 0, 2, StationSignalRules.Rejection.NotAuthorized)]
    [InlineData(true, true, true, 2.57, 10, 0, 0, 1, StationSignalRules.Rejection.TooFar)]
    [InlineData(true, true, true, double.NaN, 10, 0, 0, 1, StationSignalRules.Rejection.TooFar)]
    [InlineData(true, true, true, double.PositiveInfinity, 10, 0, 0, 1, StationSignalRules.Rejection.TooFar)]
    [InlineData(true, true, true, 1, 10, 10.01, 0, 1, StationSignalRules.Rejection.Cooldown)]
    [InlineData(true, true, true, 1, 10, 0, 10.01, 2, StationSignalRules.Rejection.Cooldown)]
    [InlineData(true, true, true, 1, double.NaN, 0, 0, 1, StationSignalRules.Rejection.Cooldown)]
    [InlineData(true, true, true, 1, 10, 0, 0, 0, StationSignalRules.Rejection.InvalidAction)]
    [InlineData(true, true, true, 1, 10, 0, 0, 255, StationSignalRules.Rejection.InvalidAction)]
    [InlineData(true, true, true, 1, 10, 10, 10, 1, StationSignalRules.Rejection.None)]
    [InlineData(true, true, true, 1, 10, 10, 10, 2, StationSignalRules.Rejection.None)]
    public void ServerRulesRequireAvailableApprovedNearbyOwnerAndValidAction(bool available, bool approved,
        bool mainHall, double squaredDistance, double now, double ownerAt, double stationAt, byte kind, object expected)
    {
        Assert.Equal((StationSignalRules.Rejection)expected,
            StationSignalRules.Validate(available, approved, mainHall, squaredDistance, now, ownerAt, stationAt, kind));
    }

    [Theory]
    [InlineData(StationSignalKind.RoutineNotice)]
    [InlineData(StationSignalKind.UrgentRecall)]
    public void AuthoritativeSignalHasStableBoundedIdentityAndExplicitPublicAudience(StationSignalKind kind)
    {
        var signal = StationSignalRules.Create(kind, "approved-actor", 1790000000000, 4);
        Assert.Equal(32, signal.SignalId.Length);
        Assert.True(Guid.TryParseExact(signal.SignalId, "N", out _));
        Assert.NotEqual(signal.SignalId, StationSignalRules.Create(kind, "approved-actor", 1790000000000, 5).SignalId);
        Assert.Equal("approved-actor", signal.SourceActorId);
        Assert.Equal("main-hall", signal.TargetRoomId);
        Assert.Equal(StationSignalAudience.StationWide, signal.Audience);
        Assert.InRange(Encoding.UTF8.GetByteCount(signal.Message), 1, StationSignalRules.MaxMessageUtf8Bytes);
        Assert.Equal(signal, Any.Parser.ParseFrom(Any.Pack(signal).ToByteArray()).Unpack<StationSignalEvent>());
        Assert.Equal("type.googleapis.com/actor.extensions.agents_arena.v1.StationSignalEvent", ActorProtocol.TypeUrl<StationSignalEvent>());
    }

    [Fact]
    public void UnspecifiedActionAndMissingServerFactsCannotMintSignals()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => StationSignalRules.Create(StationSignalKind.Unspecified, "actor", 1, 1));
        Assert.Throws<ArgumentException>(() => StationSignalRules.Create(StationSignalKind.UrgentRecall, "", 1, 1));
        Assert.Throws<ArgumentException>(() => StationSignalRules.Create(StationSignalKind.UrgentRecall, "actor", 0, 1));
        Assert.Throws<ArgumentException>(() => StationSignalRules.Create(StationSignalKind.UrgentRecall, "actor", 1, 0));
    }

    private sealed class ArenaEventExtension : IActorExtension
    {
        internal ActorEventPublisher<StationSignalEvent>? Publisher;
        public ExtensionPackage Package => AgentsArenaExtensionPackage.Create();
        public void Register(IActorExtensionRegistrar registry) => Publisher = registry.RegisterEvent<StationSignalEvent>(
            "agents-arena.station-signal", "Server-validated public training signal.");
    }

    [Fact]
    public void TypedPublicationUsesArenaMinorVersionAndExistingGenericEventEnvelope()
    {
        var registry = new ActorCapabilityRegistry();
        var extension = new ArenaEventExtension(); registry.RegisterExtension(extension);
        GameEventEnvelope? observed = null; registry.EventPublished += signal => observed = signal;
        var signal = StationSignalRules.Create(StationSignalKind.UrgentRecall, "human-actor", 1790000000000, 9);
        extension.Publisher!.Publish(signal, signal.SourceActorId, signal.ServerRevision);
        var manifest = registry.BuildManifest(new GameIdentity("agents-arena", "0.5.0"), "agent-actor");
        Assert.Equal(AgentsArenaExtensionPackage.Version, manifest.Extensions.Single().ExtensionVersion);
        Assert.Equal(CapabilityKind.Event, manifest.Capabilities.Single().Kind);
        Assert.Equal("human-actor", observed!.EntityId);
        Assert.Equal(9ul, observed.Revision);
        Assert.Equal("agents-arena", observed.ExtensionId);
        Assert.Equal(AgentsArenaExtensionPackage.Version, observed.ExtensionVersion);
        Assert.Equal(signal, observed.Payload.Unpack<StationSignalEvent>());
    }
}
