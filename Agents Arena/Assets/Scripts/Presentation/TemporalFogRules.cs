using System;
namespace Actor.Presentation
{
    // Per current ray, not old world-space vertices. Closing is instantaneous.
    internal static class TemporalFogRules
    {
        internal static float Step(float allowed, float previous, float seconds, bool reset)
        {
            if (!float.IsFinite(allowed) || allowed <= 0) return 0;
            if (reset || !float.IsFinite(previous) || previous < 0 || !float.IsFinite(seconds) || seconds > .2f) return allowed;
            if (previous >= allowed) return allowed;
            if (seconds <= 0) return previous;
            return Math.Min(allowed, previous + (allowed - previous) * (float)(1 - Math.Exp(-seconds / .075)));
        }
    }
}
