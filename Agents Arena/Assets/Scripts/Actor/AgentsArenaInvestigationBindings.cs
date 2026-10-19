using Actor.Sdk;
using Actor.Protocol.Core.V1;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;

namespace Actor.GameAdapter
{
    // These bindings share the Arena extension package. No role or incident
    // concept enters Core or the standard communication/movement extensions.
    internal sealed class AgentsArenaInvestigationBindings
    {
        private readonly AgentsArenaGameAdapter game;
        private ActorEventPublisher<RoleResultEvent> role;
        private ActorEventPublisher<IncidentStartedEvent> started;
        private ActorEventPublisher<IncidentChangedEvent> changed;
        private ActorEventPublisher<DiagnosticEvidenceEvent> diagnostic;
        private ActorEventPublisher<SecurityEvidenceEvent> security;
        private ActorEventPublisher<RepairResultEvent> repair;
        private ActorEventPublisher<InvestigationResultEvent> report;
        private ActorEventPublisher<IncidentActionResultEvent> action;
        internal AgentsArenaInvestigationBindings(AgentsArenaGameAdapter game) => this.game = game;
        private bool Ready => game.HasFreshState && game.Investigation.HasOwnState;
        internal void Register(IActorExtensionRegistrar registry)
        {
            registry.RegisterObservation<OwnRoleObservation>("agents-arena.own-role", "Own server-assigned duty, independent of calibration assignment.",
                _ => Ready ? game.Investigation.Role.Clone() : null);
            registry.RegisterObservation<DutyRosterObservation>("agents-arena.duty-roster", "Public connected crew duties; no hidden alignment.",
                _ => Ready ? game.Investigation.Roster.Clone() : null);
            registry.RegisterObservation<CoolingStateObservation>("agents-arena.cooling-state", "Public alarm and repair/report progress; no cause or private evidence.",
                _ => Ready ? game.Investigation.Cooling.Clone() : null);
            registry.RegisterObservation<OwnInvestigationViewObservation>("agents-arena.own-investigation-view", "Only the current owner's role-permitted short-lived source evidence, or an explicit empty view.",
                _ => Ready ? game.Investigation.CaptureOwnView() : null);
            registry.RegisterCommand<SelectRoleCommand>("agents-arena.select-role", "Request a public unique duty; locked during active investigations.", (_, c) => game.SubmitIncident(c), CommandDelivery.Ordered);
            registry.RegisterCommand<InspectCoolingCommand>("agents-arena.inspect-cooling", "Assigned nearby Engineer requests private diagnostic evidence.", (_, c) => game.SubmitIncident(c), CommandDelivery.Ordered);
            registry.RegisterCommand<InspectSecurityRecordsCommand>("agents-arena.inspect-security-records", "Assigned nearby Sheriff requests private security records.", (_, c) => game.SubmitIncident(c), CommandDelivery.Ordered);
            registry.RegisterCommand<RepairCoolingCommand>("agents-arena.repair-cooling", "Begin a server-timed physical repair using own current diagnostic; completion is a separate event.", (_, c) => game.SubmitIncident(c), CommandDelivery.Ordered);
            registry.RegisterCommand<CancelCoolingRepairCommand>("agents-arena.cancel-cooling-repair", "Cancel the caller's active repair lease.", (_, c) => game.SubmitIncident(c), CommandDelivery.Ordered);
            registry.RegisterCommand<MaintenanceOverrideCommand>("agents-arena.maintenance-override", "Nearby authorized crew adjusts a public circuit at the maintenance panel.", (_, c) => game.SubmitIncident(c), CommandDelivery.Ordered);
            registry.RegisterCommand<SubmitInvestigationReportCommand>("agents-arena.submit-investigation-report", "Nearby Sheriff submits a supported origin report with permitted cross-role citations.", (_, c) => game.SubmitIncident(c), CommandDelivery.Ordered);
            registry.RegisterCommand<CancelInvestigationCommand>("agents-arena.cancel-investigation", "An assigned participant cancels the unfinished investigation.", (_, c) => game.SubmitIncident(c), CommandDelivery.Ordered);
            role = registry.RegisterEvent<RoleResultEvent>("agents-arena.role-result", "Authoritative role request outcome.");
            started = registry.RegisterEvent<IncidentStartedEvent>("agents-arena.incident-started", "Public alarm; contains no hidden cause.");
            changed = registry.RegisterEvent<IncidentChangedEvent>("agents-arena.incident-changed", "Public server lifecycle transition.");
            diagnostic = registry.RegisterEvent<DiagnosticEvidenceEvent>("agents-arena.diagnostic-evidence", "Private expiring Engineering evidence with source provenance.");
            security = registry.RegisterEvent<SecurityEvidenceEvent>("agents-arena.security-evidence", "Private expiring Sheriff evidence with explicit coverage provenance.");
            repair = registry.RegisterEvent<RepairResultEvent>("agents-arena.repair-result", "Server repair outcome; completed alone confirms physical restoration.");
            report = registry.RegisterEvent<InvestigationResultEvent>("agents-arena.investigation-result", "Server report validation outcome without revealing an answer on rejection.");
            action = registry.RegisterEvent<IncidentActionResultEvent>("agents-arena.incident-action-result", "Server maintenance/cancellation outcome.");
        }
        internal void Publish(IMessage value)
        {
            switch (value)
            {
                case RoleResultEvent x: role.Publish(x, game.SelfActorId, x.ServerRevision); break;
                case IncidentStartedEvent x: started.Publish(x, game.SelfActorId, x.State.ServerRevision); break;
                case IncidentChangedEvent x: changed.Publish(x, game.SelfActorId, x.State.ServerRevision); break;
                case DiagnosticEvidenceEvent x: diagnostic.Publish(x, game.SelfActorId, x.ServerRevision); break;
                case SecurityEvidenceEvent x: security.Publish(x, game.SelfActorId, x.ServerRevision); break;
                case RepairResultEvent x: repair.Publish(x, game.SelfActorId, x.ServerRevision); break;
                case InvestigationResultEvent x: report.Publish(x, game.SelfActorId, x.ServerRevision); break;
                case IncidentActionResultEvent x: action.Publish(x, game.SelfActorId, x.ServerRevision); break;
            }
        }
    }
}
