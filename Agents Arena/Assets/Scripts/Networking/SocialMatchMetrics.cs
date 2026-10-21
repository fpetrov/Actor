using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    // Bounded server instrumentation. It is saved only after GameOver and is
    // never an observation or a live global activity/role feed.
    internal sealed class SocialMatchMetrics : MonoBehaviour
    {
        private sealed class Samples
        {
            private readonly double[] values = new double[1024];
            internal long Count;
            internal double Maximum;
            internal void Add(double value)
            { if (!double.IsFinite(value) || value < 0) return; values[Count++ % values.Length] = value; Maximum = Math.Max(Maximum, value); }
            internal double P95()
            {
                int count = (int)Math.Min(Count, values.Length);
                if (count == 0) return 0;
                var sorted = values.Take(count).OrderBy(x => x).ToArray();
                return sorted[Math.Min(count - 1, (int)Math.Ceiling(count * .95) - 1)];
            }
        }
        [Serializable] private sealed class Payload
        {
            public string actor_id;
            public long bytes, messages, world_samples, visible_actor_total;
            public int visible_actor_max;
        }
        [Serializable] private sealed class Report
        {
            public string schema = "actor.social-performance.v1", match_id;
            public string scope = "server session initialization through GameOver";
            public string percentile_window = "latest 1024 samples; maximum covers all samples";
            public string payload_scope = "social protobuf payloads only; excludes RPC/NGO/UDP headers, transforms, spawn and Standard communication";
            public double elapsed_seconds, physics_ticks_per_second, mean_update_rate;
            public double server_process_cpu_seconds, server_cpu_percent_one_core;
            public long server_peak_working_set_bytes;
            public long physics_ticks, update_frames, visibility_samples, refresh_samples;
            public double visibility_p95_ms, visibility_max_ms, refresh_p95_ms, refresh_max_ms, mean_observer_pairs;
            public long observer_pair_samples;
            public Payload[] social_payloads;
        }
        private static SocialMatchMetrics active;
        private readonly Samples visibility = new(), refresh = new();
        private readonly Dictionary<string, Payload> payloads = new();
        private double started, observerPairs, cpuStarted;
        private long ticks, frames, observerSamples;
        internal static void Ensure(GameObject root)
        { if (active == null) root.AddComponent<SocialMatchMetrics>(); }
        private void Awake()
        {
            active = this; started = Time.realtimeSinceStartupAsDouble;
            using var process = System.Diagnostics.Process.GetCurrentProcess();
            cpuStarted = process.TotalProcessorTime.TotalSeconds;
        }
        private void OnDestroy() { if (active == this) active = null; }
        private void FixedUpdate() { if (NetworkManager.Singleton != null && NetworkManager.Singleton.IsServer) ++ticks; }
        private void Update() { if (NetworkManager.Singleton != null && NetworkManager.Singleton.IsServer) ++frames; }
        public static void RecordVisibility(double milliseconds, int visibleObserverPairs)
        {
            if (active == null) return;
            active.visibility.Add(milliseconds); active.observerPairs += Math.Max(0, visibleObserverPairs); ++active.observerSamples;
        }
        internal static void RecordRefresh(double milliseconds) { if (active != null) active.refresh.Add(milliseconds); }
        internal static void RecordPayload(string actor, int bytes)
        {
            if (active == null || string.IsNullOrEmpty(actor) || bytes < 0) return;
            if (!active.payloads.TryGetValue(actor, out var value))
            {
                if (active.payloads.Count >= 32) return;
                active.payloads[actor] = value = new Payload { actor_id = actor };
            }
            value.bytes += bytes; ++value.messages;
        }
        internal static void RecordWorld(string actor, int visibleActors)
        {
            if (active == null || !active.payloads.TryGetValue(actor, out var value)) return;
            ++value.world_samples; value.visible_actor_total += Math.Max(0, visibleActors);
            value.visible_actor_max = Math.Max(value.visible_actor_max, visibleActors);
        }
        internal static void SavePostgame(string directory, string match)
        {
            if (active == null || string.IsNullOrEmpty(directory)) return;
            var a = active; double elapsed = Time.realtimeSinceStartupAsDouble - a.started;
            using var process = System.Diagnostics.Process.GetCurrentProcess();
            double cpu = Math.Max(0, process.TotalProcessorTime.TotalSeconds - a.cpuStarted);
            var value = new Report { match_id = match, elapsed_seconds = elapsed, physics_ticks = a.ticks, update_frames = a.frames,
                server_process_cpu_seconds = cpu, server_cpu_percent_one_core = elapsed > 0 ? cpu / elapsed * 100 : 0,
                server_peak_working_set_bytes = process.PeakWorkingSet64,
                physics_ticks_per_second = elapsed > 0 ? a.ticks / elapsed : 0, mean_update_rate = elapsed > 0 ? a.frames / elapsed : 0,
                visibility_samples = a.visibility.Count, refresh_samples = a.refresh.Count,
                visibility_p95_ms = a.visibility.P95(), visibility_max_ms = a.visibility.Maximum,
                refresh_p95_ms = a.refresh.P95(), refresh_max_ms = a.refresh.Maximum,
                observer_pair_samples = a.observerSamples, mean_observer_pairs = a.observerSamples > 0 ? a.observerPairs / a.observerSamples : 0,
                social_payloads = a.payloads.Values.OrderBy(p => p.actor_id).ToArray() };
            File.WriteAllText(Path.Combine(directory, match + ".performance.json"), JsonUtility.ToJson(value, true) + "\n");
        }
    }
}
