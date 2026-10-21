using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using UnityEngine;

namespace Actor.Networking
{
    // Explicit ACTIVE audit, separate from the passive privacy observer. It
    // sends one ordinary owned request and never queries server/foreign truth.
    public sealed class SocialOutOfSightKillProbe : MonoBehaviour
    {
        [Serializable] private sealed class Report
        {
            public string schema="actor.social-out-of-sight-kill-probe.v1";
            public bool completed, passed, eligible, candidate_was_not_visible;
            public int attempts, authoritative_rejections, accepted, probe_failures, initial_legitimate_candidates;
            public long match_started_at_unix_ms, requested_at_unix_ms, request_world_captured_at_unix_ms, received_at_unix_ms;
            public double duration_seconds;
            public string outcome="awaiting_own_state";
        }
        private readonly Report report=new();
        private readonly HashSet<string> initialHallActors=new();
        private NetworkSocialMatch owner;
        private string output, correlation="";
        private double started, sentAt, nextSave;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap()
        {
#if !UNITY_SERVER
            string[] args=Environment.GetCommandLineArgs();int index=Array.IndexOf(args,"--social-out-of-sight-kill-report");
            if(index<0||index+1>=args.Length||FindFirstObjectByType<SocialOutOfSightKillProbe>()!=null)return;
            var root=new GameObject("Opt-in Active Out-of-Sight Kill Audit");DontDestroyOnLoad(root);
            root.AddComponent<SocialOutOfSightKillProbe>().output=Path.GetFullPath(args[index+1]);
#endif
        }
        private void Awake()=>started=Time.realtimeSinceStartupAsDouble;
        private void Update()
        {
            if(report.completed)return;
            double now=Time.realtimeSinceStartupAsDouble;
            try
            {
                var current=NetworkSocialMatch.LocalOwner;
                if(owner!=current)
                {
                    if(owner!=null)owner.PermittedEvent-=Observe;
                    owner=current;if(owner!=null)owner.PermittedEvent+=Observe;
                }
                if(now-started>300){Finish(false,"bounded_prerequisite_timeout");return;}
                if(report.attempts>0)
                {
                    if(now-sentAt>10)Finish(false,"authoritative_receipt_timeout");
                    return;
                }
                if(owner==null||!owner.HasOwnState||!owner.Own.Assigned)return;
                if(owner.Own.Alignment!=SocialAlignment.Impostor)
                {Finish(true,"own_participant_ineligible");return;}
                report.eligible=true;
                var state=owner.State;
                if(state.StartedAtUnixMs>0)report.match_started_at_unix_ms=state.StartedAtUnixMs;
                if(state.Phase==SocialPhase.GameOver){Finish(false,"terminal_before_attempt");return;}
                long utc=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                var world=owner.CaptureWorld();
                if(owner.GetComponent<NetworkRoomMembership>().CurrentRoomId=="main-hall"
                    &&(state.StartedAtUnixMs==0||utc-state.StartedAtUnixMs<20000))
                    foreach(var actor in world.Actors)
                        if(actor.ActorId!=owner.Self&&actor.ActorId.Length>0&&actor.ActorId.Length<=128&&initialHallActors.Count<6)
                            initialHallActors.Add(actor.ActorId);
                report.initial_legitimate_candidates=initialHallActors.Count;
                if(state.StartedAtUnixMs<=0||utc-state.StartedAtUnixMs<90000||owner.Own.KillReadyAtUnixMs>utc
                    ||owner.Own.Life!=SocialLife.Alive||state.Phase!=SocialPhase.Exploration)return;
                var character=owner.GetComponent<NetworkCharacter>();
                if(!character.HasAuthoritativeSnapshot||character.AuthoritativeSnapshotAge>.5f
                    ||world.CapturedAtUnixMs<=0||Math.Abs(utc-world.CapturedAtUnixMs)>400)return;
                string candidate=initialHallActors.OrderBy(id=>id,StringComparer.Ordinal)
                    .FirstOrDefault(id=>!world.Actors.Any(actor=>actor.ActorId==id));
                if(candidate==null)return;
                report.candidate_was_not_visible=true;
                report.requested_at_unix_ms=utc;report.request_world_captured_at_unix_ms=world.CapturedAtUnixMs;
                var context=owner.Context();correlation=context.CorrelationId;sentAt=now;
                report.attempts=1;
                if(!owner.Submit(new SocialKillCommand{Context=context,TargetActorId=candidate}))
                {report.attempts=0;Finish(false,"local_submit_rejected");return;}
                if(!report.completed)report.outcome="awaiting_authoritative_rejection";Save();
            }
            catch(Exception){++report.probe_failures;Finish(false,"probe_failed");}
            if(now>=nextSave){nextSave=now+.5;Save();}
        }
        private void Observe(IMessage value)
        {
            if(value is not SocialActionResultEvent result||correlation.Length==0||result.CorrelationId!=correlation)return;
            report.received_at_unix_ms=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            if(result.Accepted)++report.accepted;
            bool rejected=result.Action==SocialAction.Kill&&!result.Accepted&&!result.Completed&&result.ReasonCode=="target_unavailable";
            if(rejected)++report.authoritative_rejections;
            Finish(rejected&&report.attempts==1&&report.accepted==0,rejected?"authoritative_rejection_verified":"authoritative_outcome_unexpected");
        }
        private void Finish(bool passed,string outcome)
        {report.completed=true;report.passed=passed&&report.probe_failures==0;report.outcome=outcome;Save();}
        private void Save()
        {
            if(string.IsNullOrEmpty(output))return;
            report.duration_seconds=Math.Max(0,Time.realtimeSinceStartupAsDouble-started);
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(output));
                string temporary=output+".tmp";File.WriteAllText(temporary,JsonUtility.ToJson(report,true)+"\n");
                if(File.Exists(output))File.Replace(temporary,output,null);else File.Move(temporary,output);
            }
            catch(Exception error)when(error is IOException or UnauthorizedAccessException or ArgumentException)
            {++report.probe_failures;report.passed=false;}
        }
        private void OnDestroy(){if(owner!=null)owner.PermittedEvent-=Observe;Save();}
    }
}
