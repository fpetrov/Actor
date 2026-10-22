using System;
using System.Collections;
using System.IO;
using Actor.Characters;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using UnityEngine;

namespace Actor.Networking
{
    // Explicit headless fixture. Never reads authority, a remote role or a
    // resume bearer. Actions use the normal own endpoint and server receipts.
    public sealed class SocialResumeProbe : MonoBehaviour
    {
        [Serializable] private sealed class Report
        {
            public string schema = "actor.social-resume-client.v1", stage = "starting", reason_code = "";
            public bool completed, passed, credential_memory_ready, connected_resume_rejected, disconnect_observed,
                same_actor, same_match, same_private_assignment, alive_preserved, higher_generation, hydrated,
                stale_generation_rejected, own_client_stopped, process_kept_alive, resumed_during_exploration;
            public long created_at_unix_ms, ready_at_unix_ms, disconnected_at_unix_ms, resumed_at_unix_ms;
            public uint public_kill_cooldown_ms;
            public int reconnect_attempts, stationary_samples;
            public double stationary_seconds, duration_seconds;
        }
        private readonly Report report = new();
        private string output, actor, match;
        private SocialAlignment alignment;
        private CrewRole duty;
        private ulong generation;
        private SocialReadyCommand oldIntent;
        private MatchSession session;
        private NetworkSocialMatch subscribed;
        private double started, nextSave, nextReady, nextResume, stableSince;
        private bool busyCheckStarted, staleSent;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap()
        {
#if !UNITY_SERVER
            if (!Application.isBatchMode || FindFirstObjectByType<SocialResumeProbe>() != null) return;
            string[] args = Environment.GetCommandLineArgs(); int index = Array.IndexOf(args, "--social-resume-report");
            if (index < 0 || index + 1 >= args.Length) return;
            var root = new GameObject("Opt-in Social Resume Probe"); DontDestroyOnLoad(root);
            root.AddComponent<SocialResumeProbe>().output = Path.GetFullPath(args[index + 1]);
#endif
        }
        private void Awake() => started = Time.realtimeSinceStartupAsDouble;
        private void Update()
        {
            if (report.completed) return;
            try
            {
                double now = Time.realtimeSinceStartupAsDouble;
                if (now - started > 220) { Finish(false, "bounded_prerequisite_or_reconnect_timeout"); return; }
                if (session == null)
                { session = FindAnyObjectByType<MatchSession>(); if (session != null) session.Disconnected += Disconnected; }
                var owner = NetworkSocialMatch.LocalOwner;
                if (owner != subscribed)
                { if (subscribed != null) subscribed.PermittedEvent -= Observe; subscribed = owner; if (subscribed != null) subscribed.PermittedEvent += Observe; }
                if (session == null) return;
                if (!report.disconnect_observed)
                {
                    if (owner == null || !owner.HasOwnState) return;
                    if (owner.State.Phase == SocialPhase.WaitingForPlayers && now >= nextReady)
                    { nextReady = now + 2; oldIntent = new SocialReadyCommand { Context = owner.Context(), Ready = true }; owner.Submit(oldIntent); }
                    if (owner.State.Phase == SocialPhase.Exploration && owner.Own.Life == SocialLife.Alive
                        && session.HasPrivateResumeCredential && now >= nextReady)
                    {
                        report.credential_memory_ready = true; report.public_kill_cooldown_ms = owner.State.KillCooldownMs;
                        if (report.public_kill_cooldown_ms < 120000) { Finish(false, "public_cooldown_profile_too_short"); return; }
                        actor = owner.Self; match = owner.State.MatchId; alignment = owner.Own.Alignment; duty = owner.Own.Duty; generation = owner.Own.ConnectionGeneration;
                        if (!busyCheckStarted) { busyCheckStarted = true; StartCoroutine(CheckConnected()); }
                    }
                }
                else if (session.IsConnected && owner != null && owner.HasOwnState)
                {
                    report.same_actor = owner.Self == actor; report.same_match = owner.State.MatchId == match;
                    report.same_private_assignment = owner.Own.Alignment == alignment && owner.Own.Duty == duty;
                    report.alive_preserved = owner.Own.Life == SocialLife.Alive;
                    report.higher_generation = owner.Own.ConnectionGeneration > generation;
                    report.resumed_during_exploration = owner.State.Phase == SocialPhase.Exploration;
                    report.hydrated = owner.Tasks.RecipientActorId == actor && owner.Tasks.MatchId == match
                        && owner.TaskWork.RecipientActorId == actor && owner.TaskWork.MatchId == match
                        && owner.Waiting.RecipientActorId == actor && owner.Waiting.MatchId == match
                        && owner.Meeting.MatchId == match && owner.Emergency.MatchId == match;
                    if (!report.same_actor || !report.same_match || !report.same_private_assignment || !report.alive_preserved || !report.higher_generation || !report.resumed_during_exploration)
                    { Finish(false, "resumed_identity_life_or_generation_mismatch"); return; }
                    if (report.hydrated)
                    {
                        report.stage = "resumed_hydrated"; if (report.resumed_at_unix_ms == 0) report.resumed_at_unix_ms = Utc;
                        if (!staleSent)
                        {
                            staleSent = true;
                            if (oldIntent == null) oldIntent = new SocialReadyCommand { Context = owner.Context(), Ready = true };
                            oldIntent.Context.ConnectionGeneration = generation; owner.Submit(oldIntent);
                        }
                        var character = owner.GetComponent<NetworkCharacter>();
                        if (character.HasAuthoritativeSnapshot && character.AuthoritativeSnapshotAge <= .5f && character.AuthoritativeVelocity.sqrMagnitude <= .0025f)
                        {
                            if (stableSince <= 0) stableSince = now;
                            ++report.stationary_samples; report.stationary_seconds = now - stableSince;
                            report.own_client_stopped = report.stationary_samples >= 4 && report.stationary_seconds >= 2;
                        }
                        else { stableSince = 0; report.stationary_samples = 0; report.stationary_seconds = 0; }
                        if (report.own_client_stopped && report.stale_generation_rejected) Finish(true, "authoritative_resume_verified");
                    }
                }
                else if (report.disconnect_observed && session.CanResume && now >= nextResume)
                { nextResume = now + 2; ++report.reconnect_attempts; session.ResumeConnection(); }
                if (now >= nextSave) { nextSave = now + .25; Save(); }
            }
            catch (Exception) { Finish(false, "probe_failed"); }
        }
        private IEnumerator CheckConnected()
        {
            // Give the authenticated two-second server heartbeat time to bind
            // the connected actor at CP before testing a duplicate attempt.
            yield return new WaitForSecondsRealtime(3);
            yield return session.ProbeConnectedResumeRejected(value => report.connected_resume_rejected = value);
            if (!report.connected_resume_rejected) { Finish(false, "connected_resume_not_rejected"); yield break; }
            report.stage = "ready_for_udp_blackhole"; report.ready_at_unix_ms = Utc; Save();
        }
        private void Disconnected(string _)
        {
            if (report.ready_at_unix_ms == 0 || report.completed) return;
            report.disconnect_observed = true; report.disconnected_at_unix_ms = Utc; report.stage = "transport_disconnected"; Save();
        }
        private void Observe(IMessage payload)
        {
            if (!staleSent || payload is not SocialActionResultEvent result || oldIntent == null) return;
            if (result.CorrelationId == oldIntent.Context.CorrelationId && result.Action == SocialAction.Ready
                && !result.Accepted && !result.Completed && result.ReasonCode == "connection_changed") report.stale_generation_rejected = true;
        }
        private void Finish(bool passed, string reason)
        { report.completed = true; report.passed = passed; report.reason_code = reason; report.stage = "completed"; report.process_kept_alive = true; Save(); }
        private static long Utc => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        private void Save()
        {
            if (string.IsNullOrEmpty(output)) return;
            report.created_at_unix_ms = Utc; report.duration_seconds = Time.realtimeSinceStartupAsDouble - started;
            Directory.CreateDirectory(Path.GetDirectoryName(output)); string temp = output + ".tmp";
            File.WriteAllText(temp, JsonUtility.ToJson(report, true));
            if (File.Exists(output)) File.Replace(temp, output, null); else File.Move(temp, output);
        }
        private void OnDestroy()
        { if (session != null) session.Disconnected -= Disconnected; if (subscribed != null) subscribed.PermittedEvent -= Observe; }
    }
}
