using System;
using Actor.Networking;
using Actor.Sdk;
using Actor.Protocol.Core.V1;
using AgentsArena.ActorProtocol.V1;

namespace Actor.GameAdapter
{
    // A different manifest: no locomotion, player own-state, communication or game actions.
    internal sealed class BroadcastGameAdapter : IActorGameAdapter, IActorExtension, IDisposable
    {
        private readonly BroadcastSession session;
        private ActorEventPublisher<BroadcastPresentedViewObservation> presented;
        private ActorEventPublisher<BroadcastViewSelectionResultEvent> selection;
        public GameIdentity Identity { get; }
        public string SelfActorId { get; }
        public ExtensionPackage Package => AgentsArenaExtensionPackage.Create();
        internal BroadcastGameAdapter(BroadcastSession source, string version)
        { session = source; Identity = new GameIdentity("agents-arena", version); SelfActorId = source.ExpectedScope.ObserverId; }
        public void Register(ActorCapabilityRegistry registry) => registry.RegisterExtension(this);
        public void Register(IActorExtensionRegistrar registry)
        {
            registry.RegisterObservation<BroadcastPresentedViewObservation>("agents-arena.broadcast-presented-view", "Only the native observer's actually presented coherent permitted view.", _ => session.PresentedFresh && BroadcastPresentationRules.SameIdentity(session.Presented?.View?.Scope, session.ExpectedScope) ? session.Presented.Clone() : null);
            presented = registry.RegisterEvent<BroadcastPresentedViewObservation>("agents-arena.broadcast-presented", "Native presentation confirmation; not OBS/audio acknowledgement.");
            selection = registry.RegisterEvent<BroadcastViewSelectionResultEvent>("agents-arena.broadcast-view-selection-result", "Server-authorized camera result; never hidden target data.");
            registry.RegisterCommand<BroadcastSelectViewCommand>("agents-arena.broadcast-select-view", "Request public anchor/currently presented focus subject; server dwell/scope/LOS policy.",
                (_, command) => session.Select(command) ? CommandOutcome.Accepted() : CommandOutcome.Rejected("ObserverViewUnavailable", "A fresh permitted observer view is required."), CommandDelivery.Ordered);
            session.FramePresented += OnPresented; session.SelectionResult += OnSelection;
        }
        private void OnPresented(BroadcastPresentedViewObservation value)
        { if (session.PresentedFresh && BroadcastPresentationRules.SameIdentity(value?.View?.Scope, session.ExpectedScope)
                && BroadcastPresentationRules.CanAcknowledge(value.View, value.Presentation, false)) presented.Publish(value, SelfActorId, value.View.SemanticRevision); }
        private void OnSelection(BroadcastViewSelectionResultEvent value)
        { if (BroadcastPresentationRules.SameIdentity(value?.Scope, session.ExpectedScope)) selection.Publish(value, SelfActorId, value.Scope.ViewEpoch); }
        public void OnDisconnected() { } // Never mutate a player's control/life.
        public void Dispose() { session.FramePresented -= OnPresented; session.SelectionResult -= OnSelection; }
    }
}
