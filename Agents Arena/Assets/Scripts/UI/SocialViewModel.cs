#if !UNITY_SERVER
using System;
using Actor.Networking;
using AgentsArena.ActorProtocol.V1;

namespace Actor.UI
{
    // Projection of a single authorised endpoint; no server or remote-avatar reads.
    public readonly struct SocialViewModel
    {
        public readonly SocialPhase Phase;
        public readonly bool Alive, Human, Meeting, Terminal;
        public readonly string Heading, Assignment, Progress, Countdown, Cooldown;
        public SocialViewModel(NetworkSocialMatch endpoint, long now)
        {
            var state = endpoint.State; var own = endpoint.Own;
            Phase = state.Phase; Alive = own.Life == SocialLife.Alive; Human = endpoint.Human;
            Meeting = Phase is SocialPhase.Discussion or SocialPhase.Voting or SocialPhase.VoteResolution;
            Terminal = Phase == SocialPhase.GameOver;
            Heading = Phase switch { SocialPhase.WaitingForPlayers => "Boarding the station", SocialPhase.Exploration => "Explore the station", SocialPhase.Discussion => "What did you observe?", SocialPhase.Voting => "Choose your ballot", SocialPhase.VoteResolution => "Ballot result", SocialPhase.GameOver => "Expedition complete", _ => "Preparing the expedition" };
            Assignment = !Human ? "ACTOR Runtime · " + own.Life : own.Assigned ? own.Alignment + " / " + own.Duty + " · " + own.Life : "Your assignment is private · waiting";
            Progress = "Crew work  " + state.CrewTasksCompleted + " / " + state.TaskThreshold;
            long end = state.PhaseEndsAtUnixMs > 0 ? state.PhaseEndsAtUnixMs : state.EndsAtUnixMs;
            Countdown = end > 0 ? Seconds(end, now) + "s remaining" : "4–6 players · everyone confirms readiness";
            Cooldown = own.Alignment == SocialAlignment.Impostor && Human ? "Eliminate · " + (own.KillReadyAtUnixMs <= now ? "ready" : Seconds(own.KillReadyAtUnixMs, now) + "s") : "Stay observant. Messages are claims.";
        }
        public static long Seconds(long end, long now) => Math.Max(0, (end - now + 999) / 1000);
    }
}
#endif
