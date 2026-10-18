using Actor.Protocol.Core.V1;
using Actor.Protocol.Standard.V1;
using Actor.Sdk;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Google.Protobuf.WellKnownTypes;

namespace Actor.Sdk.Tests;

public sealed class RegistryTests
{
    private const string ActorId = "stable-actor-id";
    private const long Now = 1_790_000_000_000;

    private sealed class TestExtension : IActorExtension
    {
        private readonly Action<IActorExtensionRegistrar> register;
        public ExtensionPackage Package { get; }
        public TestExtension(ExtensionPackage package, Action<IActorExtensionRegistrar> register)
        { Package = package; this.register = register; }
        public void Register(IActorExtensionRegistrar registrar) => register(registrar);
    }

    private static ActorCapabilityRegistry Registry(Action<Locomotion2DCommand>? accepted = null)
    {
        var registry = new ActorCapabilityRegistry();
        registry.RegisterExtension(new TestExtension(ActorStandardExtensionPackage.Create(), scoped =>
        {
            scoped.RegisterObservation<Transform2DObservation>("actor.standard.transform2d", "Transform",
                _ => new Transform2DObservation { X = 2, Y = -3, FacingY = 1 });
            scoped.RegisterObservation<Motion2DObservation>("actor.standard.motion2d", "Motion",
                _ => new Motion2DObservation { VelocityX = 2.6f, IsMoving = true });
            scoped.RegisterCommand<Locomotion2DCommand>("actor.standard.locomotion2d", "Move", (_, command) =>
            {
                accepted?.Invoke(command);
                return CommandOutcome.Accepted();
            }, CommandDelivery.Latest);
        }));
        return registry;
    }

    private static CommandEnvelope Command() => new()
    {
        CommandId = 42,
        CapabilityId = "actor.standard.locomotion2d",
        ExtensionId = ActorStandardExtensionPackage.Id,
        ExtensionVersion = ActorStandardExtensionPackage.Version,
        TargetActorId = ActorId,
        IssuedAtUnixMs = Now - 10,
        TtlMs = 250,
        Payload = Any.Pack(new Locomotion2DCommand { DesiredMoveX = 0.5f, DesiredMoveY = -1 })
    };

    [Fact]
    public void StandardAnyRoundTripsTypedBinaryPayload()
    {
        var value = new Transform2DObservation { X = 1.25f, Y = -2, FacingX = -1 };
        var decoded = Any.Parser.ParseFrom(Any.Pack(value).ToByteArray()).Unpack<Transform2DObservation>();
        Assert.Equal(value, decoded);
    }

    [Fact]
    public void GameExtensionAnyRoundTripsWithoutCoreKnowingItsType()
    {
        var value = new RoomMembershipObservation { RoomId = "engineering" };
        var envelope = new ObservationEnvelope
        {
            CapabilityId = "agents-arena.room-membership", ExtensionId = "agents-arena", ExtensionVersion = AgentsArenaExtensionPackage.Version,
            EntityId = ActorId, Payload = Any.Pack(value)
        };
        Assert.Equal(value, ObservationEnvelope.Parser.ParseFrom(envelope.ToByteArray()).Payload.Unpack<RoomMembershipObservation>());
    }

    [Fact]
    public void TypeUrlIsCanonicalAndStable()
    {
        Assert.Equal("type.googleapis.com/actor.standard.v1.Transform2DObservation", ActorProtocol.TypeUrl<Transform2DObservation>());
        Assert.Equal("type.googleapis.com/actor.extensions.agents_arena.v1.RoomMembershipObservation", ActorProtocol.TypeUrl<RoomMembershipObservation>());
    }

    [Fact]
    public void RegisteredCapabilitiesAutomaticallyBuildIndependentDeterministicManifest()
    {
        var registry = Registry();
        var identity = new GameIdentity("test-game", "0.3.0");
        var manifest = registry.BuildManifest(identity, ActorId);
        Assert.Equal(ActorId, manifest.SelfActorId);
        Assert.Equal(ActorProtocol.Version, manifest.ProtocolVersion);
        Assert.Equal(ActorStandardExtensionPackage.SchemaHash, manifest.Extensions.Single().SchemaHash);
        Assert.Equal(new[] { "actor.standard.locomotion2d", "actor.standard.motion2d", "actor.standard.transform2d" },
            manifest.Capabilities.Select(capability => capability.CapabilityId));
        var command = manifest.Capabilities[0];
        Assert.Equal(CapabilityKind.Command, command.Kind);
        Assert.Equal(CommandDelivery.Latest, command.CommandDelivery);
        Assert.Equal(ActorProtocol.TypeUrl<Locomotion2DCommand>(), command.InputTypeUrl);
        Assert.Equal("", command.OutputTypeUrl);
        command.Description = "externally mutated";
        Assert.Equal("Move", registry.BuildManifest(identity, ActorId).Capabilities[0].Description);
    }

    [Fact]
    public void ObservationCaptureAutomaticallyPacksIdentityAndTypedPayload()
    {
        var frame = Registry().CaptureObservations(ActorId, ActorId, 7);
        Assert.Equal(7ul, frame.Revision);
        Assert.Equal(2, frame.Observations.Count);
        Assert.All(frame.Observations, envelope =>
        {
            Assert.Equal(ActorId, envelope.EntityId);
            Assert.Equal("actor.standard", envelope.ExtensionId);
            Assert.Equal("0.1.0", envelope.ExtensionVersion);
        });
        Assert.Equal(2, frame.Observations.Single(value => value.CapabilityId.EndsWith("transform2d")).Payload.Unpack<Transform2DObservation>().X);
    }

    [Fact]
    public void CommandDispatchInvokesTypedHandlerAfterValidation()
    {
        Locomotion2DCommand? received = null;
        var result = Registry(command => received = command).DispatchCommand(Command(), ActorId, Now);
        Assert.Equal(CommandStatus.Accepted, result.Status);
        Assert.Equal(42ul, result.CommandId);
        Assert.Equal(0.5f, received!.DesiredMoveX);
        Assert.Equal(-1, received.DesiredMoveY);
        Assert.Null(result.Error);
    }

    [Fact]
    public void UnknownCapabilityReturnsStructuredError()
    {
        var command = Command(); command.CapabilityId = "new-game.unknown";
        Assert.Equal("UnknownCapability", Registry().DispatchCommand(command, ActorId, Now).Error.Code);
    }

    [Fact]
    public void CanonicalTypePrefixIsStrictEvenWhenProtobufCouldUnpackAlternativeUrl()
    {
        var command = Command(); command.Payload.TypeUrl = "my-prefix/actor.standard.v1.Locomotion2DCommand";
        Assert.Equal("TypeUrlMismatch", Registry().DispatchCommand(command, ActorId, Now).Error.Code);
    }

    [Fact]
    public void KnownButWrongMessageTypeIsRejected()
    {
        var command = Command(); command.Payload = Any.Pack(new Motion2DObservation());
        Assert.Equal("TypeUrlMismatch", Registry().DispatchCommand(command, ActorId, Now).Error.Code);
    }

    [Fact]
    public void MalformedProtobufIsStructuredErrorAndDoesNotInvokeGameCode()
    {
        bool invoked = false;
        var command = Command(); command.Payload.Value = ByteString.CopyFrom(new byte[] { 0xff });
        Assert.Equal("MalformedCommand", Registry(_ => invoked = true).DispatchCommand(command, ActorId, Now).Error.Code);
        Assert.False(invoked);
    }

    [Fact]
    public void CommandExpiryIsCheckedAtDispatchSoStaleTransportIntentCannotRefreshMovement()
    {
        bool invoked = false;
        var command = Command(); command.IssuedAtUnixMs = Now - command.TtlMs;
        var result = Registry(_ => invoked = true).DispatchCommand(command, ActorId, Now);
        Assert.Equal(CommandStatus.TimedOut, result.Status);
        Assert.Equal("CommandTimedOut", result.Error.Code);
        Assert.False(invoked);
    }

    [Theory]
    [InlineData(0, 250)]
    [InlineData(Now + 1001, 250)]
    [InlineData(Now, 0)]
    [InlineData(Now, 5001)]
    [InlineData(long.MaxValue, 250)]
    public void InvalidTimestampAndTtlAreRejected(long issuedAt, uint ttl)
    {
        var command = Command(); command.IssuedAtUnixMs = issuedAt; command.TtlMs = ttl;
        Assert.Equal("MalformedCommand", Registry().DispatchCommand(command, ActorId, Now).Error.Code);
    }

    [Fact]
    public void OnlyControlledActorCanReceiveCommand()
    {
        var command = Command(); command.TargetActorId = "other-player";
        Assert.Equal("ActorNotControlled", Registry().DispatchCommand(command, ActorId, Now).Error.Code);
    }

    [Theory]
    [InlineData("other-extension", "0.1.0")]
    [InlineData("actor.standard", "0.2.0")]
    public void ExtensionIdentityAndVersionMustMatchManifest(string extensionId, string version)
    {
        var command = Command(); command.ExtensionId = extensionId; command.ExtensionVersion = version;
        Assert.Equal("ExtensionVersionMismatch", Registry().DispatchCommand(command, ActorId, Now).Error.Code);
    }

    [Fact]
    public void DuplicateExtensionsAndCapabilitiesCannotPartiallyCorruptRegistry()
    {
        var registry = Registry();
        Assert.Throws<InvalidOperationException>(() => registry.RegisterExtension(
            new TestExtension(ActorStandardExtensionPackage.Create(), _ => { })));
        Assert.Throws<InvalidOperationException>(() => registry.RegisterExtension(
            new TestExtension(AgentsArenaExtensionPackage.Create(), scope =>
            {
                scope.RegisterObservation<RoomMembershipObservation>("agents-arena.room-membership", "Room", _ => new());
                scope.RegisterObservation<RoomMembershipObservation>("actor.standard.transform2d", "Conflict", _ => new());
            })));
        var manifest = registry.BuildManifest(new GameIdentity("test", "1"), ActorId);
        Assert.Single(manifest.Extensions);
        Assert.Equal(3, manifest.Capabilities.Count);
    }

    [Fact]
    public void TypedEventPublisherAutomaticallyBuildsGameEnvelope()
    {
        var registry = Registry();
        ActorEventPublisher<RoomChangedEvent>? publisher = null;
        registry.RegisterExtension(new TestExtension(AgentsArenaExtensionPackage.Create(), scope =>
            publisher = scope.RegisterEvent<RoomChangedEvent>("agents-arena.room-changed", "Room changed")));
        GameEventEnvelope? captured = null;
        registry.EventPublished += value => captured = value;
        publisher!.Publish(new RoomChangedEvent { PreviousRoomId = "main-hall", CurrentRoomId = "engineering" }, ActorId, 11);
        Assert.Equal("agents-arena", captured!.ExtensionId);
        Assert.Equal(11ul, captured.Revision);
        Assert.Equal(ActorId, captured.EntityId);
        Assert.Equal("engineering", captured.Payload.Unpack<RoomChangedEvent>().CurrentRoomId);
    }

    [Fact]
    public void ExtensionPackageRequiresMatchingSchemaFingerprintAndImmutableDescriptorBytes()
    {
        var original = ActorStandardExtensionPackage.Create();
        byte[] bytes = original.DescriptorSet.ToByteArray();
        var copy = new ExtensionPackage(original.Id, original.Version, original.SchemaHash, bytes);
        bytes[0] ^= 0xff;
        Assert.Equal(original.DescriptorSet, copy.DescriptorSet);
        Assert.Throws<ArgumentException>(() => new ExtensionPackage(original.Id, original.Version, original.SchemaHash, bytes));
        Assert.Throws<ArgumentException>(() => new ExtensionPackage(original.Id, original.Version, original.SchemaHash, Array.Empty<byte>()));
    }

    [Fact]
    public void RegisteringForeignMessageUnderStandardPackageCannotAdvertiseFalseSchema()
    {
        var registry = new ActorCapabilityRegistry();
        Assert.Throws<InvalidOperationException>(() => registry.RegisterExtension(new TestExtension(
            ActorStandardExtensionPackage.Create(), scoped => scoped.RegisterObservation<RoomMembershipObservation>(
                "not-standard.room", "Invalid package ownership", _ => new()))));
        Assert.Empty(registry.BuildManifest(new GameIdentity("test", "1"), ActorId).Extensions);
    }
}
