using System;

namespace Actor.Presentation
{
    // Presentation envelopes only. This class cannot enlarge permitted sight.
    internal static class StationAtmosphereRules
    {
        internal const float FeatherPixels = 12;
        internal const int LampCount = 10;

        internal static bool SamePublicMatch(string current, string source)
            => !string.IsNullOrEmpty(current) && string.Equals(current, source, StringComparison.Ordinal);

        internal static float FeatherWorldWidth(float pixels, float orthographicSize, int pixelHeight)
        {
            if (!float.IsFinite(pixels) || !float.IsFinite(orthographicSize) || orthographicSize <= 0 || pixelHeight <= 0) return 0;
            float width = 2 * orthographicSize / pixelHeight * Math.Clamp(pixels, 1, 16);
            return float.IsFinite(width) ? width : 0;
        }

        internal static float EmergencyEnvelope(float previous, bool permitted, bool active, float seconds, bool reset=false)
        {
            // A lost presentation must not keep a stale emergency signal alive.
            if (!permitted) return 0;
            float target = active ? 1 : 0;
            if (reset) return target;
            if (!float.IsFinite(previous) || !float.IsFinite(seconds) || seconds < 0 || seconds > .5f) return target;
            previous = Math.Clamp(previous, 0, 1);
            float result = target + (previous - target) * (float)Math.Exp(-seconds / .3);
            return Math.Abs(result - target) < .001f ? target : Math.Clamp(result, 0, 1);
        }
    }

    internal readonly struct StationAtmosphereScope
    {
        internal readonly string Match;
        internal readonly string Session, ObserverId;
        internal readonly ulong Connection, Epoch;
        internal readonly bool Observer;
        internal StationAtmosphereScope(string match, ulong connection, ulong epoch, bool observer, string session="", string observerId="")
        { Match=match;Connection=connection;Epoch=epoch;Observer=observer;Session=session;ObserverId=observerId; }
        internal bool Valid=>!string.IsNullOrEmpty(Match)&&Connection>0&&Epoch>0
            &&(!Observer||!string.IsNullOrEmpty(Session)&&!string.IsNullOrEmpty(ObserverId));
        internal bool Matches(StationAtmosphereScope other)=>Valid&&other.Valid&&Connection==other.Connection
            &&Epoch==other.Epoch&&Observer==other.Observer&&StationAtmosphereRules.SamePublicMatch(Match,other.Match)
            &&(!Observer||string.Equals(Session,other.Session,StringComparison.Ordinal)&&string.Equals(ObserverId,other.ObserverId,StringComparison.Ordinal));
    }
}
