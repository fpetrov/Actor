using System;

namespace Actor.Presentation
{
    // Timing only: an outward mesh change must still obtain fresh current-eye LOS.
    internal static class FogRefreshRules
    {
        internal const double IdleInterval = 1.0 / 30;

        internal static bool Refresh(bool observer, bool legacyTiming, bool initialized,
            bool sightChanged, bool opening, double now, double deadline)
            => now >= deadline || !observer && !legacyTiming && (!initialized || sightChanged || opening);

        internal static double NextIdleDeadline(double now, double deadline)
        {
            if (!double.IsFinite(now)) throw new ArgumentOutOfRangeException(nameof(now));
            if (!double.IsFinite(deadline) || deadline <= 0 || now - deadline > .2)
                return now + IdleInterval;
            if (deadline > now) return deadline;
            double next = deadline + (Math.Floor((now - deadline) / IdleInterval) + 1) * IdleInterval;
            return next > now ? next : now + IdleInterval;
        }

        internal static float FinishOpening(float allowed, float displayed)
        {
            if (!float.IsFinite(allowed) || allowed <= 0 || !float.IsFinite(displayed) || displayed < 0) return 0;
            // Float exponentials can stall a few ULPs short forever. This snap
            // is to the freshly checked current limit, never beyond it.
            return allowed - displayed <= .0001f ? allowed : Math.Min(allowed, displayed);
        }
    }

    // Optional, bounded numeric diagnostics. No actor, view, position or evidence data.
    internal sealed class FogPerformanceCounters
    {
        internal const int Capacity = 4096;
        private readonly double[] frame = new double[Capacity], fog = new double[Capacity],
            geometry = new double[Capacity], upload = new double[Capacity], lag = new double[Capacity];
        private int count, cursor;
        private long frames, updates, rays, moving, frameDeadlineMisses;
        private double first, last, maxFog, maxFrame;
        internal void Record(double at, double fogMs, double geometryMs, double uploadMs,
            int rayCount, bool refreshed, bool sightChanged, double lagPixels)
        {
            if (!double.IsFinite(at) || at < 0 || !Finite(fogMs) || !Finite(geometryMs)
                || !Finite(uploadMs) || !Finite(lagPixels) || rayCount < 0 || rayCount > 193) return;
            if (frames > 0 && at < last) return;
            double frameMs = frames == 0 ? double.NaN : (at - last) * 1000;
            if (frames == 0) first = at;
            else
            {
                maxFrame = Math.Max(maxFrame, frameMs);
                if (frameMs > 1000.0 / 60 + .05) frameDeadlineMisses++;
            }
            frame[cursor] = frameMs; fog[cursor] = fogMs; geometry[cursor] = geometryMs;
            upload[cursor] = uploadMs; lag[cursor] = lagPixels;
            cursor = (cursor + 1) % Capacity; count = Math.Min(count + 1, Capacity);
            frames++; if (refreshed) updates++; if (sightChanged) moving++;
            rays += rayCount; last = at; maxFog = Math.Max(maxFog, fogMs);
        }
        private static bool Finite(double value) => double.IsFinite(value) && value >= 0;
        [Serializable] internal sealed class Distribution
        {
            public int count;
            public double p50, p95, p99, max;
        }
        [Serializable] internal sealed class Report
        {
            public string schema = "actor.fog-performance.v1", algorithm = "human-current-eye-v1", timing = "";
            public string scope = "active Human fog LateUpdate; percentiles from most recent 4096 frames; no GPU or gameplay acceptance";
            public long active_frames, geometry_updates, actual_sight_queries, moving_frames, frames_over_16_72_ms;
            public double duration_seconds, geometry_updates_per_second, sight_queries_per_second,
                max_fog_cpu_ms_all_frames, max_frame_interval_ms_all_frames;
            public Distribution frame_interval_ms = new(), fog_cpu_ms = new(), geometry_cpu_ms = new(),
                mesh_upload_cpu_ms = new(), eye_lag_pixels = new();
        }
        internal Report Snapshot(bool legacyTiming)
        {
            double elapsed = Math.Max(0, last - first);
            return new Report { timing = legacyTiming ? "legacy30" : "per-render-moving",
                active_frames = frames, geometry_updates = updates, actual_sight_queries = rays,
                moving_frames = moving, frames_over_16_72_ms = frameDeadlineMisses,
                duration_seconds = elapsed,
                geometry_updates_per_second = elapsed > 0 ? updates / elapsed : 0,
                sight_queries_per_second = elapsed > 0 ? rays / elapsed : 0,
                max_fog_cpu_ms_all_frames = maxFog, max_frame_interval_ms_all_frames = maxFrame,
                frame_interval_ms = Summarize(frame), fog_cpu_ms = Summarize(fog),
                geometry_cpu_ms = Summarize(geometry), mesh_upload_cpu_ms = Summarize(upload), eye_lag_pixels = Summarize(lag) };
        }
        private Distribution Summarize(double[] values)
        {
            var sorted = new double[count]; int valid = 0;
            for (int i = 0; i < count; i++) if (Finite(values[i])) sorted[valid++] = values[i];
            Array.Sort(sorted, 0, valid);
            if (valid == 0) return new Distribution();
            double At(double q) => sorted[Math.Max(0, (int)Math.Ceiling(valid * q) - 1)];
            return new Distribution { count = valid, p50 = At(.5), p95 = At(.95), p99 = At(.99), max = sorted[valid - 1] };
        }
    }
}
