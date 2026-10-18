using System;
using AgentsArena.ActorProtocol.V1;

namespace Actor.Networking
{
    // Pure reference-game rules shared by the authoritative RPC and deterministic tests.
    // This is not an ACTOR command capability: only normal owned game interaction uses it.
    internal static class StationSignalRules
    {
        internal const double PlayerCooldownSeconds = 2;
        internal const double StationCooldownSeconds = 0.75;
        internal const double InteractionRadius = 1.6;
        internal const int MaxMessageUtf8Bytes = 256;
        internal const string MainHall = "main-hall";

        internal enum Rejection : byte { None, Unavailable, NotAuthorized, TooFar, Cooldown, InvalidAction }

        internal static Rejection Validate(bool consoleAvailable, bool approvedOwner, bool inMainHall,
            double squaredDistance, double now, double ownerReadyAt, double stationReadyAt, byte kind)
        {
            if (!consoleAvailable) return Rejection.Unavailable;
            if (!approvedOwner || !inMainHall) return Rejection.NotAuthorized;
            if (kind != (byte)StationSignalKind.RoutineNotice && kind != (byte)StationSignalKind.UrgentRecall)
                return Rejection.InvalidAction;
            if (!Finite(squaredDistance) || squaredDistance < 0 || squaredDistance > InteractionRadius * InteractionRadius)
                return Rejection.TooFar;
            if (!Finite(now) || !Finite(ownerReadyAt) || !Finite(stationReadyAt)
                || now < ownerReadyAt || now < stationReadyAt) return Rejection.Cooldown;
            return Rejection.None;
        }

        internal static string Message(StationSignalKind kind) => kind switch
        {
            StationSignalKind.RoutineNotice => "Routine station status update.",
            StationSignalKind.UrgentRecall => "All available personnel must return to Main Hall immediately.",
            _ => throw new ArgumentOutOfRangeException(nameof(kind))
        };

        internal static StationSignalEvent Create(StationSignalKind kind, string sourceActorId, long serverUnixMs, ulong revision)
        {
            if (string.IsNullOrWhiteSpace(sourceActorId) || sourceActorId.Length > 64 || serverUnixMs <= 0 || revision == 0)
                throw new ArgumentException("A station signal requires authoritative identity, time and revision.");
            return new StationSignalEvent
            {
                SignalId = Guid.NewGuid().ToString("N"), SignalKind = kind, SourceActorId = sourceActorId,
                TargetRoomId = MainHall, Message = Message(kind), ServerUnixMs = serverUnixMs,
                Audience = StationSignalAudience.StationWide, ServerRevision = revision
            };
        }

        private static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);
    }
}
