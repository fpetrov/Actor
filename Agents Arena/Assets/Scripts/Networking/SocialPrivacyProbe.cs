using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Actor.World;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    // Opt-in passive acceptance auditor. It never changes visibility, position,
    // life, role, input, server policy or a Brain decision. Reports are counts.
    public sealed class SocialPrivacyProbe : MonoBehaviour
    {
        private static SocialPrivacyProbe active;
        [Serializable] private sealed class Check { public string name; public bool passed; }
        [Serializable] private sealed class FarInterval
        {
            public long incoming_first_unix_ms, incoming_last_unix_ms, adapter_first_unix_ms, adapter_last_unix_ms;
            public int incoming_frames, adapter_frames;
        }
        [Serializable] private sealed class Report
        {
            public string schema = "actor.social-privacy-probe.v1";
            public bool completed, passed, socialModeObserved, farSecurityCoverage;
            public long incomingHookCalls, adapterHookCalls, incomingWorldFrames, adapterWorldFrames, spawnAuditSamples,
                ownStateSamples, foreignComponentSamples, initialLegitimateHallActors, farWorldFrames, farAdapterFrames,
                endpointCheckedCalls, foreignEndpointIncoming,
                wrongRecipientIncoming, wrongRecipientAdapter, foreignPrivateComponentSamples, legacyInvestigationSamples,
                legacyCalibrationSamples, legacyAdapterPayloads, persistentOutOfSightActors, persistentOutOfSightBodies,
                farForbiddenIncomingEntries, farForbiddenAdapterEntries, farForbiddenAttackEvents, earlyPostgamePayloads, probeFailures;
            public double durationSeconds, farSecuritySeconds, maxFarSecurityContinuousSeconds;
            public long matchStartedAtUnixMs;
            public FarInterval[] stableFarIntervals;
            public Check[] checks;
        }
        private readonly Report report = new();
        private readonly HashSet<string> initialHallActors = new();
        private readonly Dictionary<string,double> badActorSight = new(), badBodySight = new();
        private readonly List<string> currentActors = new(), currentBodies = new(), removals = new();
        private readonly List<(string recipient, bool adapter)> pendingRecipients = new();
        private string output, self;
        private NetworkSocialMatch owner;
        private double started, nextAudit, nextSave, lastAudit, lastOwnWorldAt = -100, farSince = -1;
        private long farEnteredUnixMs;
        private readonly List<FarInterval> stableFarIntervals = new();
        private FarInterval currentFarInterval;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap()
        {
#if !UNITY_SERVER
            if (Argument("--social-privacy-report") == null) return;
            var root = new GameObject("Passive Social Privacy Auditor"); DontDestroyOnLoad(root);
            root.AddComponent<SocialPrivacyProbe>();
#endif
        }
        private static string Argument(string flag)
        {
            string[] args=Environment.GetCommandLineArgs();int index=Array.IndexOf(args,flag);
            return index>=0&&index+1<args.Length?args[index+1]:null;
        }
        private void Awake()
        {
            output=Argument("--social-privacy-report");
            if(string.IsNullOrEmpty(output)||active!=null){enabled=false;return;}
            active=this;started=lastAudit=Time.realtimeSinceStartupAsDouble;
        }

        // Call after bounded protobuf parsing, BEFORE endpoint IsOwner/recipient
        // filters. Receiving forbidden private bytes is itself a failed check.
        public static void ObserveIncoming(IMessage payload,bool? ownerEndpoint=null)
        {
            if(active==null||payload==null)return;
            try
            {
                if(ownerEndpoint.HasValue)
                {++active.report.endpointCheckedCalls;if(!ownerEndpoint.Value)++active.report.foreignEndpointIncoming;}
                active.Observe(payload,false);
            }
            catch(Exception) { ++active.report.probeFailures; }
        }
        // Call on actual adapter capture values / event publication, not by
        // resampling a parallel copy. This does not claim Python context audit.
        public static void ObserveAdapterCapture(IMessage payload)
        {
            if(active==null||payload==null)return;
            try { active.Observe(payload,true); } catch(Exception) { ++active.report.probeFailures; }
        }
        private void ResolveOwner()
        {
            owner=NetworkSocialMatch.LocalOwner;
            if(owner==null||owner.Self.Length==0)return;
            if(self!=null&&self!=owner.Self){++report.probeFailures;return;}
            self=owner.Self;
            foreach(var pending in pendingRecipients) CheckRecipient(pending.recipient,pending.adapter);
            pendingRecipients.Clear();
        }
        private void CheckRecipient(string recipient,bool adapter)
        {
            if(recipient==null||recipient.Length>128){++report.probeFailures;return;}
            if(self==null)
            {
                if(pendingRecipients.Count<128) pendingRecipients.Add((recipient,adapter));
                else ++report.probeFailures;
                return;
            }
            if(recipient!=self)
            {if(adapter)++report.wrongRecipientAdapter;else ++report.wrongRecipientIncoming;}
        }
        private void Observe(IMessage payload,bool adapter)
        {
            ResolveOwner();
            if(adapter)++report.adapterHookCalls;else ++report.incomingHookCalls;
            switch(payload)
            {
                case SocialOwnStateObservation own: CheckRecipient(own.ActorId,adapter); break;
                case SocialOwnStateChangedEvent own: CheckRecipient(own.State?.ActorId??"",adapter); break;
                case SocialOwnTasksObservation tasks: CheckRecipient(tasks.RecipientActorId,adapter); break;
                case SocialWaitingPlayersObservation waiting:
                    CheckRecipient(waiting.RecipientActorId,adapter);
                    if(waiting.Players.Count>6)++report.probeFailures;
                    break;
                case SocialOwnTaskWorkObservation work: CheckRecipient(work.RecipientActorId,adapter); break;
                case SocialVisibleWorldObservation world:
                    CheckRecipient(world.RecipientActorId,adapter);
                    if(adapter)++report.adapterWorldFrames;else {++report.incomingWorldFrames;lastOwnWorldAt=Time.realtimeSinceStartupAsDouble;}
                    if(world.Actors.Count>6||world.Bodies.Count>6||world.Interactables.Count>16){++report.probeFailures;break;}
                    if(owner!=null&&world.RecipientActorId==self)
                    {
                        if(!adapter&&OwnerRoom()=="main-hall")
                            foreach(var actor in world.Actors)
                                if(actor.ActorId!=self&&actor.ActorId.Length>0&&initialHallActors.Count<6)initialHallActors.Add(actor.ActorId);
                        if(FarReady()&&world.CapturedAtUnixMs>=farEnteredUnixMs)
                        {
                            RecordFarWorld(world.CapturedAtUnixMs,adapter);
                            if(adapter)++report.farAdapterFrames;else ++report.farWorldFrames;
                            long forbidden=world.Actors.Count(actor=>!PermittedPoint(new Vector2(actor.X,actor.Y),.35f))
                                +world.Bodies.Count(body=>!PermittedPoint(new Vector2(body.X,body.Y),.35f));
                            if(adapter)report.farForbiddenAdapterEntries+=forbidden;else report.farForbiddenIncomingEntries+=forbidden;
                        }
                    }
                    break;
                case SocialVisibleAttackEvent attack:
                    CheckRecipient(attack.ObserverActorId,adapter);
                    if(!adapter&&FarReady()&&attack.ObservedAtUnixMs>=farEnteredUnixMs
                        &&!PermittedPoint(new Vector2(attack.X,attack.Y),.35f))++report.farForbiddenAttackEvents;
                    break;
                case SocialVisibleActivityEvent activity: CheckRecipient(activity.ObserverActorId,adapter); break;
                case SocialKillPresentationEvent presentation:
                    CheckRecipient(presentation.RecipientActorId,adapter);
                    if(presentation.VictimActorId==self&&presentation.AttackerActorId.Length>0)++report.probeFailures;
                    if(FarReady()&&presentation.ServerUnixMs>=farEnteredUnixMs
                        &&!PermittedPoint(new Vector2(presentation.X,presentation.Y),.35f))++report.farForbiddenAttackEvents;
                    break;
                case SocialPostgameObservation postgame:
                    if(postgame.Summary!=null&&!TerminalObserved())++report.earlyPostgamePayloads;
                    break;
                case SocialMatchStateObservation state:
                    RecordMatchStart(state.StartedAtUnixMs);
                    if(state.Phase==SocialPhase.GameOver)report.completed=true;
                    break;
                case SocialMatchChangedEvent state:
                    if(state.State!=null)RecordMatchStart(state.State.StartedAtUnixMs);
                    if(state.State?.Phase==SocialPhase.GameOver)report.completed=true;
                    break;
                case SocialMatchEndedEvent _:
                    // This authoritative terminal event is itself a disclosure.
                    report.completed=true;break;
                case OwnRoleObservation _:
                case DutyRosterObservation _:
                case OwnInvestigationViewObservation _:
                case DiagnosticEvidenceEvent _:
                case SecurityEvidenceEvent _:
                    if(adapter)++report.legacyAdapterPayloads;
                    break;
            }
        }
        private bool TerminalObserved()=>report.completed||owner!=null&&owner.State.Phase==SocialPhase.GameOver;
        private void RecordMatchStart(long startedAt)
        {
            if(startedAt<=0)return;
            if(report.matchStartedAtUnixMs!=0&&report.matchStartedAtUnixMs!=startedAt)++report.probeFailures;
            else report.matchStartedAtUnixMs=startedAt;
        }
        private void RecordFarWorld(long capturedAt,bool adapter)
        {
            if(capturedAt<=0){++report.probeFailures;return;}
            if(currentFarInterval==null)
            {
                if(stableFarIntervals.Count>=16){++report.probeFailures;return;}
                currentFarInterval=new FarInterval();stableFarIntervals.Add(currentFarInterval);
            }
            if(adapter)
            {
                if(currentFarInterval.adapter_frames++==0)currentFarInterval.adapter_first_unix_ms=capturedAt;
                currentFarInterval.adapter_last_unix_ms=Math.Max(currentFarInterval.adapter_last_unix_ms,capturedAt);
            }
            else
            {
                if(currentFarInterval.incoming_frames++==0)currentFarInterval.incoming_first_unix_ms=capturedAt;
                currentFarInterval.incoming_last_unix_ms=Math.Max(currentFarInterval.incoming_last_unix_ms,capturedAt);
            }
        }
        private string OwnerRoom()=>owner!=null?owner.GetComponent<NetworkRoomMembership>().CurrentRoomId:"";
        private bool FarEligible()
        {
            if(owner==null||!owner.IsSpawned||!owner.HasOwnState||owner.Own.Life!=SocialLife.Alive
                ||owner.State.Phase!=SocialPhase.Exploration||OwnerRoom()!="security"
                ||Time.realtimeSinceStartupAsDouble-lastOwnWorldAt>.75)return false;
            var character=owner.GetComponent<NetworkCharacter>();
            return character.HasAuthoritativeSnapshot&&character.AuthoritativeSnapshotAge<=.5f&&character.AuthoritativeVelocity.sqrMagnitude<=.04f;
        }
        private bool FarReady()=>farSince>=0&&Time.realtimeSinceStartupAsDouble-farSince>=1&&FarEligible();
        private bool PermittedPoint(Vector2 point,float margin)
        {
            if(owner==null||!float.IsFinite(point.x)||!float.IsFinite(point.y))return false;
            Vector2 center=owner.GetComponent<CapsuleCollider2D>().bounds.center;
            float radius=owner.World.VisibilityRadius;
            return radius>0&&float.IsFinite(radius)&&(center-point).sqrMagnitude<=(radius+margin)*(radius+margin)
                &&SocialVisibilitySystem.LineOfSight(center,point);
        }
        private void Update()
        {
            double now=Time.realtimeSinceStartupAsDouble;if(now<nextAudit)return;nextAudit=now+.1;
            try
            {
                ResolveOwner();var manager=NetworkManager.Singleton;
                if(manager!=null&&SocialVisibilitySystem.IsSocial(manager))report.socialModeObserved=true;
                if(owner!=null&&owner.IsSpawned&&manager!=null&&manager.IsClient&&!manager.IsServer)
                {
                    ++report.ownStateSamples;
                    if(owner.State.Phase==SocialPhase.GameOver)report.completed=true;
                    if(FarEligible())
                    {
                        if(farSince<0){farSince=now;farEnteredUnixMs=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();}
                        report.farSecuritySeconds+=Math.Max(0,now-lastAudit);
                        report.maxFarSecurityContinuousSeconds=Math.Max(report.maxFarSecurityContinuousSeconds,now-farSince);
                    }
                    else {farSince=-1;currentFarInterval=null;}
                    AuditSpawned(manager,now);
                    AuditLegacy(owner.gameObject);
                }
                lastAudit=now;
                if(now>=nextSave){nextSave=now+.5;Save();}
            }
            catch(Exception){++report.probeFailures;}
        }
        private void AuditSpawned(NetworkManager manager,double now)
        {
            ++report.spawnAuditSamples;currentActors.Clear();currentBodies.Clear();
            foreach(var target in manager.SpawnManager.SpawnedObjectsList)
            {
                if(target.IsOwner)continue;
                var social=target.GetComponent<NetworkSocialMatch>();
                if(social!=null)
                {
                    ++report.foreignComponentSamples;
                    if(social.Own.Assigned||social.Own.Alignment!=SocialAlignment.Unspecified||social.Own.Duty!=CrewRole.Unspecified
                        ||social.Own.Life!=SocialLife.Unspecified||social.Own.ActorId.Length>0||social.Tasks.Tasks.Count>0||social.Tasks.RecipientActorId.Length>0
                        ||social.TaskWork.RecipientActorId.Length>0||social.TaskWork.WorkId.Length>0||social.Waiting.RecipientActorId.Length>0||social.Waiting.Players.Count>0)
                        ++report.foreignPrivateComponentSamples;
                    AuditLegacy(target.gameObject);
                    var character=target.GetComponent<NetworkCharacter>();
                    string id=character!=null?character.AuthoritativeActorId:"";
                    if(id.Length>0)
                    {
                        currentActors.Add(id);
                        TrackSight(badActorSight,id,PermittedPoint(target.GetComponent<CapsuleCollider2D>().bounds.center,.35f),now,false);
                    }
                }
                var body=target.GetComponent<SocialBody>();
                if(body!=null&&body.BodyId.Length>0)
                {
                    currentBodies.Add(body.BodyId);
                    TrackSight(badBodySight,body.BodyId,PermittedPoint(target.transform.position,.35f),now,true);
                }
            }
            Prune(badActorSight,currentActors);Prune(badBodySight,currentBodies);
        }
        private void TrackSight(Dictionary<string,double> starts,string id,bool permitted,double now,bool body)
        {
            if(permitted||owner.Own.Life!=SocialLife.Alive||owner.State.Phase!=SocialPhase.Exploration||now-lastOwnWorldAt>.75)
            {starts.Remove(id);return;}
            if(!starts.TryGetValue(id,out double since))
            {if(starts.Count<16)starts[id]=now;else++report.probeFailures;return;}
            // Reliable Hide and interpolation can trail the allowed server
            // snapshot briefly. Sustained replication outside sight is a failure.
            if(now-since>=.75){if(body)++report.persistentOutOfSightBodies;else++report.persistentOutOfSightActors;}
        }
        private void Prune(Dictionary<string,double> starts,List<string> current)
        {
            removals.Clear();foreach(string id in starts.Keys)if(!current.Contains(id))removals.Add(id);
            foreach(string id in removals)starts.Remove(id);
        }
        private void AuditLegacy(GameObject target)
        {
            var investigation=target.GetComponent<NetworkInvestigation>();
            if(investigation!=null)
            {
                var view=investigation.CaptureOwnView();
                if(investigation.Role.ActorId.Length>0||investigation.Role.Role!=CrewRole.Unspecified||investigation.Roster.Entries.Count>0
                    ||view.Diagnostic!=null||view.Security!=null||investigation.DiagnosticEvidenceCount>0||investigation.SecurityEvidenceCount>0)
                    ++report.legacyInvestigationSamples;
            }
            var cooperation=target.GetComponent<NetworkCooperation>();
            if(cooperation!=null&&(cooperation.TaskState.TaskId.Length>0||cooperation.PrivateDiagnostic!=null||cooperation.DiagnosticCount>0))
                ++report.legacyCalibrationSamples;
        }
        private void Save()
        {
            if(string.IsNullOrEmpty(output))return;
            report.initialLegitimateHallActors=initialHallActors.Count;
            report.stableFarIntervals=stableFarIntervals.ToArray();
            report.durationSeconds=Math.Max(0,Time.realtimeSinceStartupAsDouble-started);
            report.farSecurityCoverage=report.maxFarSecurityContinuousSeconds>=4&&initialHallActors.Count>0
                &&report.farWorldFrames>=10&&report.farAdapterFrames>=10;
            report.checks=new[]{
                new Check{name="actual_incoming_and_adapter_hooks_exercised",passed=report.incomingWorldFrames>0&&report.adapterWorldFrames>0&&report.endpointCheckedCalls>0},
                new Check{name="actual_ngo_spawn_audit_exercised",passed=report.spawnAuditSamples>0&&report.ownStateSamples>0},
                new Check{name="only_own_private_rpc_and_adapter_values",passed=report.wrongRecipientIncoming==0&&report.wrongRecipientAdapter==0&&report.foreignEndpointIncoming==0},
                new Check{name="remote_endpoints_never_contain_private_state",passed=report.foreignPrivateComponentSamples==0},
                new Check{name="social_has_no_legacy_private_or_roster_state",passed=report.legacyInvestigationSamples==0&&report.legacyCalibrationSamples==0&&report.legacyAdapterPayloads==0},
                new Check{name="no_sustained_out_of_sight_network_objects",passed=report.persistentOutOfSightActors==0&&report.persistentOutOfSightBodies==0},
                new Check{name="far_security_receives_no_out_of_sight_facts",passed=report.farForbiddenIncomingEntries==0&&report.farForbiddenAdapterEntries==0&&report.farForbiddenAttackEvents==0},
                new Check{name="postgame_disclosure_is_terminal",passed=report.earlyPostgamePayloads==0},
                new Check{name="auditor_is_bounded_and_operational",passed=report.probeFailures==0&&pendingRecipients.Count==0}};
            report.passed=report.completed&&report.socialModeObserved&&report.checks.All(check=>check.passed);
            string path=Path.GetFullPath(output);Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path,JsonUtility.ToJson(report,true)+"\n");
        }
        private void OnDestroy(){Save();if(active==this)active=null;}
    }
}
