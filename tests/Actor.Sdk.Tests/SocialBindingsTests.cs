using System.Text.Json;
using Actor.GameAdapter;
using Actor.Protocol.Core.V1;
using Actor.Sdk;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Google.Protobuf.WellKnownTypes;

// Compile the actual production binding source, not a duplicate registration.
// Only the Unity/NGO owner endpoint is replaced by this small test double.
namespace Actor.GameAdapter
{
    internal sealed class SocialEndpointFixture
    {
        internal bool HasOwnState = true;
        internal SocialMatchStateObservation State = new() { MatchId = "match-own", PhaseRevision = 3 };
        internal SocialOwnStateObservation Own = new() { MatchId = "match-own", ActorId = "self", ConnectionGeneration = 9 };
        internal SocialVisibleWorldObservation World = new() { MatchId = "match-own", RecipientActorId = "self" };
        internal SocialMeetingObservation Meeting = new() { MatchId = "match-own" };
        internal SocialOwnTasksObservation Tasks = new() { MatchId = "match-own", RecipientActorId = "self" };
        internal SocialEmergencyStateObservation Emergency = new() { MatchId = "match-own" };
        internal SocialPostgameObservation Postgame = new() { MatchId = "match-own" };
        internal SocialWaitingPlayersObservation Waiting = new() { MatchId = "match-own", RecipientActorId = "self" };
        internal SocialOwnTaskWorkObservation TaskWork = new() { MatchId = "match-own", RecipientActorId = "self" };
        internal SocialVisibleWorldObservation CaptureWorld() => World.Clone();
    }
    internal sealed class AgentsArenaGameAdapter
    {
        internal bool HasFreshState = true;
        internal SocialEndpointFixture Social = new();
        internal string SelfActorId = "self";
        internal List<IMessage> Submitted = new();
        internal CommandOutcome SubmitSocial(IMessage command)
        { Submitted.Add(command); return CommandOutcome.Accepted(); }
    }
}
namespace Actor.Networking
{
    internal static class SocialPrivacyProbe
    {
        internal static void ObserveAdapterCapture(IMessage _) { }
    }
}
namespace Actor.Sdk.Tests
{
    public sealed class SocialBindingsTests
    {
        private sealed class Extension : IActorExtension
        {
            internal readonly AgentsArenaGameAdapter Game = new();
            internal readonly AgentsArenaSocialBindings Binding;
            public ExtensionPackage Package => AgentsArenaExtensionPackage.Create();
            internal Extension() => Binding = new(Game);
            public void Register(IActorExtensionRegistrar scope) => Binding.Register(scope);
        }
        private static (Extension Extension, ActorCapabilityRegistry Registry) Setup()
        {
            var extension = new Extension(); var registry = new ActorCapabilityRegistry();
            registry.RegisterExtension(extension); return (extension, registry);
        }
        private static ObservationEnvelope Observation(ObservationFrame frame, string suffix) =>
            Assert.Single(frame.Observations.Where(value => value.CapabilityId == "agents-arena.social-" + suffix));

        [Fact]
        public void ActualProductionSocialCatalogMatchesEveryGeneratedSocialCapability()
        {
            var (_, registry) = Setup();
            var manifest = registry.BuildManifest(new GameIdentity("agents-arena", "0.9.0"), "self");
            using var metadata = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "ArenaProtocol", "agents-arena.manifest.json")));
            var expected = metadata.RootElement.GetProperty("capabilities").EnumerateArray()
                .Where(value => value.GetProperty("capability_id").GetString()!.StartsWith("agents-arena.social-", StringComparison.Ordinal))
                .ToDictionary(value => value.GetProperty("capability_id").GetString()!);
            Assert.Equal(expected.Keys.Order(), manifest.Capabilities.Select(value => value.CapabilityId).Order());
            Assert.Equal(AgentsArenaExtensionPackage.Version, Assert.Single(manifest.Extensions).ExtensionVersion);
            foreach (var actual in manifest.Capabilities)
            {
                var row = expected[actual.CapabilityId];
                Assert.Equal(row.GetProperty("extension_id").GetString(), actual.ExtensionId);
                Assert.Equal(row.GetProperty("input_type_url").GetString(), actual.InputTypeUrl);
                Assert.Equal(row.GetProperty("output_type_url").GetString(), actual.OutputTypeUrl);
                Assert.Equal(row.GetProperty("kind").GetString(), actual.Kind.ToString().ToLowerInvariant());
                if (actual.Kind == CapabilityKind.Command)
                    Assert.Equal(CommandDelivery.Ordered, actual.CommandDelivery);
            }
            Assert.DoesNotContain(manifest.Capabilities, value => value.CapabilityId.Contains("calibration") || value.CapabilityId.Contains("investigation"));
        }

        [Fact]
        public void OwnReadinessAndWorkCaptureIncludeExplicitClearedReplacement()
        {
            var (extension, registry) = Setup(); var endpoint = extension.Game.Social;
            endpoint.Waiting.Players.Add(new SocialWaitingPlayer { ActorId = "peer", DisplayName = "Peer", Ready = true, Connected = true });
            endpoint.TaskWork.WorkId = "work-1"; endpoint.TaskWork.TaskId = "task-1";
            endpoint.TaskWork.Mechanic = SocialTaskMechanic.CircuitCalibration;
            endpoint.TaskWork.StepRevision = 7; endpoint.TaskWork.RequiredChoice = SocialTaskChoice.Alpha;
            var first = registry.CaptureObservations("self", "self", 1);
            Assert.Single(Observation(first, "waiting-players").Payload.Unpack<SocialWaitingPlayersObservation>().Players);
            Assert.Equal("work-1", Observation(first, "task-work").Payload.Unpack<SocialOwnTaskWorkObservation>().WorkId);
            endpoint.Waiting.Players.Clear(); endpoint.Waiting.ServerRevision = 0;
            endpoint.TaskWork = new() { MatchId = "match-own", RecipientActorId = "self" };
            var cleared = registry.CaptureObservations("self", "self", 2);
            var waiting = Observation(cleared, "waiting-players").Payload.Unpack<SocialWaitingPlayersObservation>();
            var work = Observation(cleared, "task-work").Payload.Unpack<SocialOwnTaskWorkObservation>();
            Assert.Empty(waiting.Players); Assert.Equal("self", waiting.RecipientActorId);
            Assert.Equal("", work.WorkId); Assert.Equal("self", work.RecipientActorId); Assert.Equal("match-own", work.MatchId);
            Assert.All(cleared.Observations, value => Assert.Equal("self", value.EntityId));
            Assert.Equal("work-1", Observation(first, "task-work").Payload.Unpack<SocialOwnTaskWorkObservation>().WorkId);
        }

        [Theory]
        [InlineData(true, false)]
        [InlineData(false, true)]
        [InlineData(true, true)]
        public void NewOwnBundlesRejectForeignRecipientOrDifferentMatch(bool foreign, bool differentMatch)
        {
            var (extension, registry) = Setup();
            if (foreign) { extension.Game.Social.Waiting.RecipientActorId = "foreign"; extension.Game.Social.TaskWork.RecipientActorId = "foreign"; }
            if (differentMatch) { extension.Game.Social.Waiting.MatchId = "different"; extension.Game.Social.TaskWork.MatchId = "different"; }
            var frame = registry.CaptureObservations("self", "self", 1);
            Assert.DoesNotContain(frame.Observations, value => value.CapabilityId.EndsWith("waiting-players") || value.CapabilityId.EndsWith("task-work"));
        }

        [Theory]
        [InlineData(true)]
        [InlineData(false)]
        public void StalePhysicsOrUnhydratedOwnerCannotBecomeFreshSdkObservations(bool stalePhysics)
        {
            var (extension, registry) = Setup();
            if (stalePhysics) extension.Game.HasFreshState = false;
            else extension.Game.Social.HasOwnState = false;
            Assert.Empty(registry.CaptureObservations("self", "self", 1).Observations);
        }

        [Fact]
        public void TaskStepDispatchPreservesExactOwnGenerationAndStageRatherThanRewritingStaleIntent()
        {
            var (extension, registry) = Setup();
            var payload = new SocialTaskStepCommand { Context = new() { MatchId = "match-own", PhaseRevision = 3,
                ConnectionGeneration = 8, CorrelationId = "step-correlation", ExpiresAtUnixMs = 1500 },
                WorkId = "work-1", StepRevision = 42, Choice = SocialTaskChoice.Beta };
            var envelope = new CommandEnvelope { CommandId = 1, CapabilityId = "agents-arena.social-task-step",
                ExtensionId = AgentsArenaExtensionPackage.Id, ExtensionVersion = AgentsArenaExtensionPackage.Version,
                TargetActorId = "self", IssuedAtUnixMs = 1000, TtlMs = 1000, Payload = Any.Pack(payload) };
            Assert.Equal(CommandStatus.Accepted, registry.DispatchCommand(envelope, "self", 1010).Status);
            var forwarded = Assert.IsType<SocialTaskStepCommand>(Assert.Single(extension.Game.Submitted));
            Assert.Equal(payload, forwarded); Assert.Equal(8ul, forwarded.Context.ConnectionGeneration); // Own endpoint is already generation 9.
            // Local Accepted establishes only submission. The authoritative server
            // generation fence and original WorkTask completion remain separate.
            envelope.TargetActorId = "foreign";
            Assert.Equal(CommandStatus.Rejected, registry.DispatchCommand(envelope, "self", 1010).Status);
            Assert.Single(extension.Game.Submitted);
        }

        [Fact]
        public void PresentationPublishesOnlyExactOwnMatchRecipientAndKeepsVictimAttackerEmpty()
        {
            var (extension, registry) = Setup(); var events = new List<GameEventEnvelope>(); registry.EventPublished += events.Add;
            var value = new SocialKillPresentationEvent { MatchId = "match-own", RecipientActorId = "self", VictimActorId = "self",
                PresentationId = Guid.NewGuid().ToString("N"), BodyId = Guid.NewGuid().ToString("N"), ServerUnixMs = 1000 };
            extension.Binding.Publish(value);
            var item = Assert.Single(events);
            Assert.Equal("agents-arena.social-kill-presentation", item.CapabilityId); Assert.Equal("self", item.EntityId);
            var captured = item.Payload.Unpack<SocialKillPresentationEvent>(); Assert.Equal(value, captured); Assert.Equal("", captured.AttackerActorId);
            value.RecipientActorId = "foreign"; extension.Binding.Publish(value);
            value.RecipientActorId = "self"; value.MatchId = "different"; extension.Binding.Publish(value);
            Assert.Single(events);
        }
    }
}
