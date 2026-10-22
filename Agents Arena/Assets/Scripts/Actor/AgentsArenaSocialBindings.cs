using Actor.Sdk;
using Actor.Networking;
using Actor.Protocol.Core.V1;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;

namespace Actor.GameAdapter
{
    internal sealed class AgentsArenaSocialBindings
    {
        private readonly AgentsArenaGameAdapter game;
        private ActorEventPublisher<SocialActionResultEvent> ActionResult;
        private ActorEventPublisher<SocialMatchChangedEvent> MatchChanged;
        private ActorEventPublisher<SocialOwnStateChangedEvent> OwnStateChanged;
        private ActorEventPublisher<SocialMeetingChangedEvent> MeetingChanged;
        private ActorEventPublisher<SocialEmergencyChangedEvent> EmergencyChanged;
        private ActorEventPublisher<SocialVisibleAttackEvent> VisibleAttack;
        private ActorEventPublisher<SocialVisibleActivityEvent> VisibleActivity;
        private ActorEventPublisher<SocialMatchEndedEvent> MatchEnded;
        private ActorEventPublisher<SocialKillPresentationEvent> KillPresentation;
        internal AgentsArenaSocialBindings(AgentsArenaGameAdapter game) => this.game = game;
        private bool Ready => game.HasFreshState && game.Social != null && game.Social.HasOwnState;
        private static T Capture<T>(T value) where T : class, IMessage
        { SocialPrivacyProbe.ObserveAdapterCapture(value); return value; }
        internal void Register(IActorExtensionRegistrar registry)
        {
            registry.RegisterObservation<SocialMatchStateObservation>("agents-arena.social-match", "Recipient-permitted authoritative social-match projection; private session data.",
                _ => Ready ? Capture(game.Social.State.Clone()) : null);
            registry.RegisterObservation<SocialOwnStateObservation>("agents-arena.social-own-state", "Recipient-permitted authoritative social-own-state projection; private session data.",
                _ => Ready ? Capture(game.Social.Own.Clone()) : null);
            registry.RegisterObservation<SocialVisibleWorldObservation>("agents-arena.social-visible-world", "Recipient-permitted authoritative social-visible-world projection; private session data.",
                _ => Ready ? Capture(game.Social.CaptureWorld()) : null);
            registry.RegisterObservation<SocialMeetingObservation>("agents-arena.social-meeting", "Recipient-permitted authoritative social-meeting projection; private session data.",
                _ => Ready ? Capture(game.Social.Meeting.Clone()) : null);
            registry.RegisterObservation<SocialOwnTasksObservation>("agents-arena.social-tasks", "Recipient-permitted authoritative social-tasks projection; private session data.",
                _ => Ready ? Capture(game.Social.Tasks.Clone()) : null);
            registry.RegisterObservation<SocialEmergencyStateObservation>("agents-arena.social-emergency", "Recipient-permitted authoritative social-emergency projection; private session data.",
                _ => Ready ? Capture(game.Social.Emergency.Clone()) : null);
            registry.RegisterObservation<SocialPostgameObservation>("agents-arena.social-postgame", "Recipient-permitted authoritative social-postgame projection; private session data.",
                _ => Ready ? Capture(game.Social.Postgame.Clone()) : null);
            registry.RegisterObservation<SocialWaitingPlayersObservation>("agents-arena.social-waiting-players", "Own-targeted public readiness roster; explicitly empty outside WaitingForPlayers.",
                _ => Ready && game.Social.Waiting.MatchId == game.Social.State.MatchId && game.Social.Waiting.RecipientActorId == game.SelfActorId
                    ? Capture(game.Social.Waiting.Clone()) : null);
            registry.RegisterObservation<SocialOwnTaskWorkObservation>("agents-arena.social-task-work", "Own authoritative work prompt and timing; an empty work ID explicitly clears the previous work.",
                _ => Ready && game.Social.TaskWork.MatchId == game.Social.State.MatchId && game.Social.TaskWork.RecipientActorId == game.SelfActorId
                    ? Capture(game.Social.TaskWork.Clone()) : null);
            registry.RegisterCommand<SocialReadyCommand>("agents-arena.social-ready", "Request social-ready through authoritative life, phase, permission and physical validation.", (_, command) => game.SubmitSocial(command), CommandDelivery.Ordered);
            registry.RegisterCommand<SocialKillCommand>("agents-arena.social-kill", "Request social-kill through authoritative life, phase, permission and physical validation.", (_, command) => game.SubmitSocial(command), CommandDelivery.Ordered);
            registry.RegisterCommand<SocialReportBodyCommand>("agents-arena.social-report-body", "Request social-report-body through authoritative life, phase, permission and physical validation.", (_, command) => game.SubmitSocial(command), CommandDelivery.Ordered);
            registry.RegisterCommand<SocialCallMeetingCommand>("agents-arena.social-call-meeting", "Request social-call-meeting through authoritative life, phase, permission and physical validation.", (_, command) => game.SubmitSocial(command), CommandDelivery.Ordered);
            registry.RegisterCommand<SocialCastVoteCommand>("agents-arena.social-cast-vote", "Request social-cast-vote through authoritative life, phase, permission and physical validation.", (_, command) => game.SubmitSocial(command), CommandDelivery.Ordered);
            registry.RegisterCommand<SocialWorkTaskCommand>("agents-arena.social-work-task", "Request social-work-task through authoritative life, phase, permission and physical validation.", (_, command) => game.SubmitSocial(command), CommandDelivery.Ordered);
            registry.RegisterCommand<SocialSabotageCommand>("agents-arena.social-sabotage", "Request social-sabotage through authoritative life, phase, permission and physical validation.", (_, command) => game.SubmitSocial(command), CommandDelivery.Ordered);
            registry.RegisterCommand<SocialRepairEmergencyCommand>("agents-arena.social-repair-emergency", "Request social-repair-emergency through authoritative life, phase, permission and physical validation.", (_, command) => game.SubmitSocial(command), CommandDelivery.Ordered);
            registry.RegisterCommand<SocialCancelWorkCommand>("agents-arena.social-cancel-work", "Request social-cancel-work through authoritative life, phase, permission and physical validation.", (_, command) => game.SubmitSocial(command), CommandDelivery.Ordered);
            registry.RegisterCommand<SocialTaskStepCommand>("agents-arena.social-task-step", "Submit the displayed own work step with its exact revision and current connection generation; await the authoritative receipt.", (_, command) => game.SubmitSocial(command), CommandDelivery.Ordered);
            ActionResult = registry.RegisterEvent<SocialActionResultEvent>("agents-arena.social-action-result", "Authoritative recipient-permitted social-action-result event.");
            MatchChanged = registry.RegisterEvent<SocialMatchChangedEvent>("agents-arena.social-match-changed", "Authoritative recipient-permitted social-match-changed event.");
            OwnStateChanged = registry.RegisterEvent<SocialOwnStateChangedEvent>("agents-arena.social-own-state-changed", "Authoritative recipient-permitted social-own-state-changed event.");
            MeetingChanged = registry.RegisterEvent<SocialMeetingChangedEvent>("agents-arena.social-meeting-changed", "Authoritative recipient-permitted social-meeting-changed event.");
            EmergencyChanged = registry.RegisterEvent<SocialEmergencyChangedEvent>("agents-arena.social-emergency-changed", "Authoritative recipient-permitted social-emergency-changed event.");
            VisibleAttack = registry.RegisterEvent<SocialVisibleAttackEvent>("agents-arena.social-visible-attack", "Authoritative recipient-permitted social-visible-attack event.");
            VisibleActivity = registry.RegisterEvent<SocialVisibleActivityEvent>("agents-arena.social-visible-activity", "Authoritative recipient-permitted social-visible-activity event.");
            MatchEnded = registry.RegisterEvent<SocialMatchEndedEvent>("agents-arena.social-match-ended", "Authoritative recipient-permitted social-match-ended event.");
            KillPresentation = registry.RegisterEvent<SocialKillPresentationEvent>("agents-arena.social-kill-presentation", "Own-targeted cosmetic presentation for an already-permitted attack; no additional witness or attacker disclosure.");
        }
        internal void Publish(IMessage value)
        {
            SocialPrivacyProbe.ObserveAdapterCapture(value);
            switch (value)
            {
                case SocialActionResultEvent x: ActionResult.Publish(x, game.SelfActorId, x.PhaseRevision); break;
                case SocialMatchChangedEvent x: MatchChanged.Publish(x, game.SelfActorId, x.State.ServerRevision); break;
                case SocialOwnStateChangedEvent x: OwnStateChanged.Publish(x, game.SelfActorId, x.State.ServerRevision); break;
                case SocialMeetingChangedEvent x: MeetingChanged.Publish(x, game.SelfActorId, x.State.PhaseRevision); break;
                case SocialEmergencyChangedEvent x: EmergencyChanged.Publish(x, game.SelfActorId, x.State.ServerRevision); break;
                case SocialVisibleAttackEvent x: VisibleAttack.Publish(x, game.SelfActorId, x.ObservedRevision); break;
                case SocialVisibleActivityEvent x: VisibleActivity.Publish(x, game.SelfActorId, x.ObservedRevision); break;
                case SocialMatchEndedEvent x: MatchEnded.Publish(x, game.SelfActorId, game.Social.State.PhaseRevision); break;
                case SocialKillPresentationEvent x when game.Social != null && x.MatchId == game.Social.State.MatchId && x.RecipientActorId == game.SelfActorId:
                    KillPresentation.Publish(x, game.SelfActorId, game.Social.State.PhaseRevision); break;
            }
        }
    }
}
