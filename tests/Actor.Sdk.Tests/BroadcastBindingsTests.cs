using Actor.GameAdapter;
using Actor.Networking;
using Actor.Sdk;
using Actor.Protocol.Core.V1;
using AgentsArena.ActorProtocol.V1;
using System.Text.Json;
namespace Actor.Networking
{
    internal sealed class BroadcastSession
    {
        internal bool PresentedFresh;
        internal BroadcastScope ExpectedScope = new() { MatchId = "00000000000000000000000000000001", BroadcastSessionId = "00000000000000000000000000000002", ObserverId = "00000000000000000000000000000003", ConnectionGeneration = 1, ViewEpoch = 1 };
        internal BroadcastPresentedViewObservation Presented;
        public event Action<BroadcastPresentedViewObservation> FramePresented;
        public event Action<BroadcastViewSelectionResultEvent> SelectionResult;
        internal bool Select(BroadcastSelectViewCommand _) => PresentedFresh;
        internal void Emit(BroadcastPresentedViewObservation value) => FramePresented?.Invoke(value);
    }
}
namespace Actor.Sdk.Tests
{
    public sealed class BroadcastBindingsTests
    {
        [Fact] public void ProductionBroadcastManifestHasExactlyBroadcastCapabilitiesAndNoPlayerActions()
        {
            var registry = new ActorCapabilityRegistry(); using var adapter = new BroadcastGameAdapter(new BroadcastSession(), "0.10.0"); adapter.Register(registry);
            var manifest = registry.BuildManifest(adapter.Identity, adapter.SelfActorId);
            using var metadata = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "ArenaProtocol/agents-arena.manifest.json")));
            var expected = metadata.RootElement.GetProperty("capabilities").EnumerateArray().Where(x => x.GetProperty("capability_id").GetString()!.StartsWith("agents-arena.broadcast-"));
            Assert.Equal(expected.Select(x => x.GetProperty("capability_id").GetString()).Order(), manifest.Capabilities.Select(x => x.CapabilityId).Order());
            Assert.DoesNotContain(manifest.Capabilities, c => c.CapabilityId.Contains("social-own") || c.CapabilityId.Contains("locomotion") || c.CapabilityId.Contains("communication"));
        }
        [Fact] public void NothingIsCapturedOrPublishedBeforePresentationAndDisposeUnsubscribes()
        {
            var session = new BroadcastSession(); var registry = new ActorCapabilityRegistry(); var adapter = new BroadcastGameAdapter(session, "0.10.0"); adapter.Register(registry);
            var events = new List<GameEventEnvelope>(); registry.EventPublished += events.Add;
            Assert.Empty(registry.CaptureObservations(adapter.SelfActorId, adapter.SelfActorId, 1).Observations);
            var view = new BroadcastView { Scope = session.ExpectedScope.Clone(), Sequence = 1, SemanticRevision = 1 };
            var ack = new BroadcastPresentationAck { Scope = view.Scope.Clone(), RenderedSequence = 1, FrameCounter = 1, Width = 800, Height = 600 };
            var frame = new BroadcastPresentedViewObservation { View = view, Presentation = ack };
            session.Emit(frame); Assert.Empty(events);
            session.Presented = frame; session.PresentedFresh = true; session.Emit(frame); Assert.Single(events);
            Assert.Single(registry.CaptureObservations(adapter.SelfActorId, adapter.SelfActorId, 2).Observations);
            var foreign = frame.Clone(); foreign.View.Scope.ObserverId = "foreign"; session.Emit(foreign); Assert.Single(events);
            adapter.Dispose(); session.Emit(frame); Assert.Single(events);
        }
    }
}
