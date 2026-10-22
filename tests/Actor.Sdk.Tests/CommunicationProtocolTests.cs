using Actor.Protocol.Core.V1;
using Actor.Protocol.Standard.V1;
using Actor.Protocol.Standard.Communication.V1;
using Actor.Sdk;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Google.Protobuf.WellKnownTypes;

namespace Actor.Sdk.Tests;

public sealed class CommunicationProtocolTests
{
    private sealed class CommunicationExtension : IActorExtension
    {
        public ExtensionPackage Package => ActorCommunicationExtensionPackage.Create(required: false);
        public SendMessageCommand? Received { get; private set; }
        public void Register(IActorExtensionRegistrar scope)
        {
            scope.RegisterCommand<SendMessageCommand>("actor.standard.communication.send-message", "Send", (_, value) =>
            {
                Received = value;
                return CommandOutcome.Accepted();
            }, CommandDelivery.Ordered);
            scope.RegisterEvent<MessageReceivedEvent>("actor.standard.communication.message-received", "Message");
            scope.RegisterEvent<MessageDeliveryResultEvent>("actor.standard.communication.delivery-result", "Receipt");
            scope.RegisterObservation<CommunicationPeersObservation>("actor.standard.communication.peers", "Peers",
                _ => new() { MatchId = "match-1", Peers = { new CommunicationPeer { ActorId = "peer-b", DisplayName = "Partner" } } });
        }
    }

    [Fact]
    public void OptionalCommunicationRegistersIndependentlyWithoutMotionOrGameExtension()
    {
        var registry = new ActorCapabilityRegistry();
        registry.RegisterExtension(new CommunicationExtension());
        var manifest = registry.BuildManifest(new GameIdentity("another-game", "1.0.0"), "actor-a");
        Assert.Single(manifest.Extensions);
        Assert.Equal("actor.standard.communication", manifest.Extensions[0].ExtensionId);
        Assert.Equal("0.1.0", manifest.Extensions[0].ExtensionVersion);
        Assert.False(manifest.Extensions[0].Required);
        Assert.Equal(4, manifest.Capabilities.Count);
        var command = manifest.Capabilities.Single(item => item.Kind == CapabilityKind.Command);
        Assert.Equal(CommandDelivery.Ordered, command.CommandDelivery);
        Assert.Equal(ActorProtocol.TypeUrl<SendMessageCommand>(), command.InputTypeUrl);
        Assert.Equal("type.googleapis.com/actor.standard.communication.v1.SendMessageCommand", command.InputTypeUrl);
        Assert.Equal(1u, manifest.ProtocolVersion.Major);
    }

    [Fact]
    public void TypedAnyAndRegistryKeepMessageTtlSeparateFromBridgeCommandDeadline()
    {
        var extension = new CommunicationExtension();
        var registry = new ActorCapabilityRegistry();
        registry.RegisterExtension(extension);
        var message = new SendMessageCommand { RecipientActorId = "actor-b", ChannelId = "direct", Text = "Hello",
            CorrelationId = "correlation-1", TaskId = "task-1", MatchId = "match-1", Sensitive = true, TtlMs = 60000 };
        var packed = Any.Pack(message);
        Assert.Equal(message, Any.Parser.ParseFrom(packed.ToByteArray()).Unpack<SendMessageCommand>());
        var command = new CommandEnvelope { CommandId = 1, CapabilityId = "actor.standard.communication.send-message",
            ExtensionId = ActorCommunicationExtensionPackage.Id, ExtensionVersion = ActorCommunicationExtensionPackage.Version,
            TargetActorId = "actor-a", IssuedAtUnixMs = 1000, TtlMs = 250, Payload = packed };
        Assert.Equal(CommandStatus.Accepted, registry.DispatchCommand(command, "actor-a", 1010).Status);
        Assert.Equal(60000u, extension.Received!.TtlMs);
        Assert.Equal("actor-b", extension.Received.RecipientActorId);
        Assert.DoesNotContain(SendMessageCommand.Descriptor.Fields.InFieldNumberOrder(), field => field.Name == "sender_actor_id");
    }

    [Fact]
    public void ServerAcceptedClientReceivedAndBrainReadAreDifferentClaims()
    {
        var receipt = new MessageDeliveryResultEvent { CorrelationId = "correlation-1", MessageId = "server-message-1",
            AcceptedByServer = true, Stage = MessageDeliveryStage.ServerAccepted, MatchId = "match-1" };
        Assert.Equal(receipt, Any.Pack(receipt).Unpack<MessageDeliveryResultEvent>());
        Assert.NotEqual(MessageDeliveryStage.ClientReceived, receipt.Stage);
        Assert.DoesNotContain(MessageDeliveryResultEvent.Descriptor.Fields.InFieldNumberOrder(),
            field => field.Name.Contains("brain") || field.Name.Contains("read"));
    }

    [Fact]
    public void CommunicationPackageCannotContainAnArenaDiagnosticType()
    {
        var registry = new ActorCapabilityRegistry();
        Assert.Throws<InvalidOperationException>(() => registry.RegisterExtension(new InvalidExtension()));
        Assert.Equal("0.1.0", ActorStandardExtensionPackage.Version);
        Assert.Equal("5188081ff451868991d0ba8cda56dc18d0a422794251c43216586996f7d9fb21", ActorStandardExtensionPackage.SchemaHash);
        Assert.Equal(AgentsArenaExtensionPackage.Version, AgentsArenaExtensionPackage.Version);
    }

    private sealed class InvalidExtension : IActorExtension
    {
        public ExtensionPackage Package => ActorCommunicationExtensionPackage.Create();
        public void Register(IActorExtensionRegistrar scope) =>
            scope.RegisterEvent<DiagnosticResultEvent>("invalid.secret", "Wrong package");
    }

    [Fact]
    public void PublicCalibrationSchemasExcludePrivateAuthorization()
    {
        Assert.DoesNotContain(CalibrationProgressObservation.Descriptor.Fields.InFieldNumberOrder(),
            field => field.Name.Contains("authorization") || field.Name.Contains("coordinator") || field.Name.Contains("technician"));
        Assert.DoesNotContain(CalibrationTaskStateObservation.Descriptor.Fields.InFieldNumberOrder(),
            field => field.Name.Contains("authorization"));
        Assert.Contains(DiagnosticResultEvent.Descriptor.Fields.InFieldNumberOrder(), field => field.Name == "authorization_code");
        Assert.DoesNotContain(StartCalibrationCommand.Descriptor.Fields.InFieldNumberOrder(), field => field.Name == "coordinator_actor_id");
    }
}
