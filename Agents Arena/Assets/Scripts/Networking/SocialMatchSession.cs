using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Actor.Protocol.Standard.Communication.V1;
using Actor.World;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    // The only owner of hidden game truth. Adapter/NGO/UI retain individually
    // permitted projections; they cannot query another player's alignment.
    public sealed class SocialMatchSession : MonoBehaviour
    {
        public static SocialMatchSession Current { get; private set; }
        [SerializeField] private GameObject bodyPrefab;
        private SocialMatchAuthority authority;
        private CooperativeAuthority communication;
        private SocialVisibilitySystem visibility;
        private readonly Dictionary<string, NetworkSocialMatch> endpoints = new();
        private readonly Dictionary<ulong, string> actors = new();
        private readonly Dictionary<string, SocialBody> bodyObjects = new();
        private SocialMatchAuthority.Configuration rules;
        private double nextRefresh;
        private bool refreshing, invalidConfiguration, summaryWritten;
        private long Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        [Serializable] private sealed class ConfigurationFile
        {
            public int MinPlayers = 4;
            public int MaxPlayers = 6;
            public int ImpostorCount = 1;
            public long MatchDurationMs = 900000;
            public long DiscussionDurationMs = 90000;
            public long VotingDurationMs = 60000;
            public long KillCooldownMs = 30000;
            public long EmergencyCooldownMs = 90000;
            public long SabotageTimeoutMs = 150000;
            public long WorkDurationMs = 12000;
            public long ReconnectGraceMs = 30000;
            public long CalibrationStepMs = 2500;
            public long SensorSampleDelayMs = 8000;
            public long TaskDecisionTimeoutMs = 30000;
            public long SabotageCooldownMs = 90000;
            public long WaitingTimeoutMs = 300000;
            public double KillDistance = .9;
            public double VisibilityRadius = 3.5;
            public double BodyReportDistance = 1.2;
            public double InteractionRadius = 1.25;
            public int TaskThreshold = 8;
            public int MeetingLimit = 3;
            public int PlayerMeetingLimit = 1;
            public SocialMatchAuthority.Configuration Rules() => new()
            {
                MinPlayers = MinPlayers,
                MaxPlayers = MaxPlayers,
                ImpostorCount = ImpostorCount,
                MatchDurationMs = MatchDurationMs,
                DiscussionDurationMs = DiscussionDurationMs,
                VotingDurationMs = VotingDurationMs,
                KillCooldownMs = KillCooldownMs,
                EmergencyCooldownMs = EmergencyCooldownMs,
                SabotageTimeoutMs = SabotageTimeoutMs,
                WorkDurationMs = WorkDurationMs,
                ReconnectGraceMs = ReconnectGraceMs,
                CalibrationStepMs = CalibrationStepMs,
                SensorSampleDelayMs = SensorSampleDelayMs,
                TaskDecisionTimeoutMs = TaskDecisionTimeoutMs,
                SabotageCooldownMs = SabotageCooldownMs,
                WaitingTimeoutMs = WaitingTimeoutMs,
                KillDistance = KillDistance,
                VisibilityRadius = VisibilityRadius,
                BodyReportDistance = BodyReportDistance,
                InteractionRadius = InteractionRadius,
                TaskThreshold = TaskThreshold,
                MeetingLimit = MeetingLimit,
                PlayerMeetingLimit = PlayerMeetingLimit
            };
        }
        [Serializable] private sealed class Fixture { public int seed; }
        private void OnEnable() => Current = this;
        private void OnDisable()
        {
            if (Current == this) Current = null;
            if (visibility != null) visibility.ObserverPolicy = null;
            authority = null; communication = null; endpoints.Clear(); actors.Clear(); bodyObjects.Clear();
        }
        private void Update()
        {
            var manager = NetworkManager.Singleton;
            if (manager == null || !manager.IsServer || !SocialVisibilitySystem.IsSocial(manager)
                || Time.realtimeSinceStartupAsDouble < nextRefresh) return;
            nextRefresh = Time.realtimeSinceStartupAsDouble + .1;
            Refresh();
        }
        private void Initialize(MatchSession session)
        {
            if (authority != null || invalidConfiguration) return;
            try
            {
                string json = Environment.GetEnvironmentVariable("ACTOR_SOCIAL_CONFIG");
                if (json != null && json.Length > 8192) throw new InvalidDataException();
                rules = (string.IsNullOrEmpty(json) ? new ConfigurationFile() : JsonUtility.FromJson<ConfigurationFile>(json)).Rules();
                int? seed = null;
                // Fixture files are trusted, server-private and scoped to an
                // unguessable match ID. No action or public API accepts a seed.
                string directory = Environment.GetEnvironmentVariable("ACTOR_SOCIAL_FIXTURE_DIRECTORY") ?? "/etc/actor/social-scenarios";
                string path = Path.Combine(directory, session.MatchId + ".json");
                if (File.Exists(path))
                {
                    if (new FileInfo(path).Length > 1024) throw new InvalidDataException();
                    seed = JsonUtility.FromJson<Fixture>(File.ReadAllText(path)).seed;
                }
                SocialMatchMetrics.Ensure(gameObject);
                authority = new SocialMatchAuthority(session.MatchId, rules, seed, LiveCanSee);
                authority.BroadcastActivity += (actor, terminal, activity, x, y, at) =>
                    NetworkManager.Singleton?.GetComponent<BroadcastSession>()?.VisibleActivity(x, y, actor, terminal, activity, at);
                communication = new CooperativeAuthority(session.MatchId, null,
                    (actor, message) => authority.CanCommunicate(actor, message.RecipientActorId, message.ChannelId) ? "" : "communication_unavailable",
                    (sender, recipient, channel) => authority.CanCommunicate(sender, recipient, channel));
                visibility = SocialVisibilitySystem.Ensure(NetworkManager.Singleton);
                visibility.ObserverPolicy = CanObserve;
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException or NullReferenceException)
            {
                invalidConfiguration = true;
                Debug.LogError("ACTOR_SOCIAL configuration_invalid");
                Application.Quit(8);
            }
        }
        private bool LiveCanSee(string observer, float x, float y)
        {
            if (!endpoints.TryGetValue(observer, out var endpoint) || endpoint == null || !endpoint.IsSpawned) return false;
            Vector2 center = endpoint.GetComponent<CapsuleCollider2D>().bounds.center;
            var target = new Vector2(x, y);
            return (center - target).sqrMagnitude <= rules.VisibilityRadius * rules.VisibilityRadius
                && SocialVisibilitySystem.LineOfSight(center, target);
        }
        private bool CanObserve(ulong clientId, NetworkObject target)
        {
            if (authority == null || !actors.TryGetValue(clientId, out string observer)
                || !authority.OwnState(observer).Assigned || authority.OwnState(observer).Life != SocialLife.Alive || authority.IsTerminal) return false;
            var character = target.GetComponent<NetworkCharacter>();
            if (character != null)
            {
                if (authority.OwnState(character.AuthoritativeActorId).Life != SocialLife.Alive) return false;
                // Use current physics poses here, not the last 10 Hz observation.
                var point = character.GetComponent<CapsuleCollider2D>().bounds.center;
                return LiveCanSee(observer, point.x, point.y);
            }
            var body = target.GetComponent<SocialBody>();
            return body != null && bodyObjects.ContainsKey(body.BodyId) && LiveCanSee(observer, target.transform.position.x, target.transform.position.y);
        }
        private void Refresh()
        {
            var manager = NetworkManager.Singleton;
            if (refreshing || manager == null || !manager.IsServer || !SocialVisibilitySystem.IsSocial(manager)) return;
            var session = manager.GetComponent<MatchSession>();
            if (session == null || string.IsNullOrEmpty(session.MatchId)) return;
            refreshing = true;
            long refreshStarted = System.Diagnostics.Stopwatch.GetTimestamp();
            try
            {
                var participants = new List<SocialMatchAuthority.Participant>(); endpoints.Clear(); actors.Clear();
                foreach (var pair in manager.ConnectedClients)
                {
                    if (!session.TryGetApprovedPlayer(pair.Key, out var admission) || pair.Value.PlayerObject == null) continue;
                    var player = pair.Value.PlayerObject.GetComponent<NetworkCharacter>();
                    var endpoint = pair.Value.PlayerObject.GetComponent<NetworkSocialMatch>();
                    if (player == null || !player.IsSpawned || endpoint == null || !endpoint.IsSpawned || player.AuthoritativeActorId.Length == 0) continue;
                    var center = player.GetComponent<CapsuleCollider2D>().bounds.center;
                    var velocity = player.AuthoritativeVelocity;
                    participants.Add(new SocialMatchAuthority.Participant { Id = player.AuthoritativeActorId, Name = player.PlayerName,
                        Room = player.GetComponent<NetworkRoomMembership>().CurrentRoomId, X = center.x, Y = center.y,
                        VelocityX = velocity.x, VelocityY = velocity.y, PoseFresh = true, ConnectionEpoch = pair.Key + 1,
                        JoinOrder = session.ApprovedJoinOrder(pair.Key), ConnectionGeneration = admission.connectionGeneration,
                        ResumeEligible = true, AuthenticatedResume = admission.resume });
                    endpoints[player.AuthoritativeActorId] = endpoint; actors[pair.Key] = player.AuthoritativeActorId;
                }
                Initialize(session); if (authority == null) return;
                foreach (var retained in session.RetainedSocialPlayers)
                {
                    var center = retained.GetComponent<CapsuleCollider2D>().bounds.center;
                    authority.RefreshSuspendedPose(new SocialMatchAuthority.Participant { Id = retained.AuthoritativeActorId,
                        X = center.x, Y = center.y, Room = retained.GetComponent<NetworkRoomMembership>().CurrentRoomId });
                }
                var terminals = FindObjectsByType<SocialTerminal2D>();
                var world = terminals.Select(t => new SocialMatchAuthority.Terminal { Id = t.EntityId, Room = t.RoomId, Kind = t.Kind,
                    X = t.InteractionPoint.x, Y = t.InteractionPoint.y, ApproachX = t.ApproachPoint.x, ApproachY = t.ApproachPoint.y,
                    Radius = Math.Min(t.Radius, rules.InteractionRadius), Available = t.isActiveAndEnabled }).ToArray();
                var tasks = terminals.Where(t => t.Kind == "task").Select(t => new SocialMatchAuthority.TaskDefinition { Id = t.TaskId, TerminalId = t.EntityId,
                    Mechanic = t.RoomId == "engineering" ? SocialTaskMechanic.MaintenanceHold : t.RoomId == "security" ? SocialTaskMechanic.SensorObservation
                        : SocialTaskMechanic.CircuitCalibration }).ToArray();
                authority.SetWorld(world, tasks);
                var deliveries = authority.Refresh(participants, Now);
                SynchronizeBodies(); visibility.RefreshNow(); Dispatch(deliveries);
                DispatchCommunication(communication.Refresh(participants.Select(p => new CooperativeAuthority.Participant
                    { Id = p.Id, Name = p.Name, Room = p.Room, X = p.X, Y = p.Y }), Now));
                SynchronizeBodies(); visibility.RefreshNow(); PublishViews();
                WriteSummary();
            }
            finally
            {
                SocialMatchMetrics.RecordRefresh((System.Diagnostics.Stopwatch.GetTimestamp() - refreshStarted) * 1000.0 / System.Diagnostics.Stopwatch.Frequency);
                refreshing = false;
            }
        }
        public bool CanMove(string actor) => authority?.CanMove(actor) ?? false;
        internal bool Eliminated(string actor) => authority != null && authority.OwnState(actor).Life is SocialLife.Dead or SocialLife.Ejected or SocialLife.Disconnected or SocialLife.Spectator;
        internal void Request(string actor, IMessage command)
        {
            Refresh(); if (authority == null) return;
            var broadcast = NetworkManager.Singleton.GetComponent<BroadcastSession>();
            ulong[] witnesses = Array.Empty<ulong>();
            string previousBody = null;
            if (command is SocialKillCommand kill && endpoints.TryGetValue(actor, out var attacker))
            {
                var victim = endpoints.TryGetValue(kill.TargetActorId, out var liveVictim) ? liveVictim.GetComponent<NetworkCharacter>()
                    : NetworkManager.Singleton.GetComponent<MatchSession>().RetainedSocialPlayers.FirstOrDefault(c => c.AuthoritativeActorId == kill.TargetActorId);
                previousBody = authority.BodyStates().FirstOrDefault(b => b.VictimActorId == kill.TargetActorId)?.BodyId;
                if (victim != null)
                {
                var a = attacker.GetComponent<CapsuleCollider2D>().bounds.center; var v = victim.GetComponent<CapsuleCollider2D>().bounds.center;
                witnesses = broadcast?.AttackWitnesses(a.x, a.y, v.x, v.y, actor, kill.TargetActorId, Now) ?? Array.Empty<ulong>();
                }
            }
            var deliveries = authority.Execute(actor, command, Now);
            if (command is SocialKillCommand acceptedKill && deliveries.Any(d => d.Payload is SocialActionResultEvent result
                && result.Action == SocialAction.Kill && result.Accepted && result.Completed))
            {
                var body = authority.BodyStates().FirstOrDefault(b => b.VictimActorId == acceptedKill.TargetActorId);
                if (body != null && body.BodyId != previousBody) broadcast?.AttackCompleted(witnesses, actor, acceptedKill.TargetActorId, body.BodyId, Now);
            }
            SynchronizeBodies(); visibility.RefreshNow(); Dispatch(deliveries); PublishViews(); WriteSummary();
        }
        internal void ControlUnavailable(string actor)
        {
            if (authority == null) return;
            var deliveries = authority.ControlUnavailable(actor, Now);
            SynchronizeBodies(); visibility.RefreshNow(); Dispatch(deliveries); PublishViews(); WriteSummary();
        }
        internal void RequestCommunication(string actor, SendMessageCommand command)
        {
            Refresh();
            if (communication == null || authority == null) return;
            if (!authority.CanCommunicate(actor, command.RecipientActorId, command.ChannelId))
            {
                if (endpoints.TryGetValue(actor, out var endpoint)) endpoint.GetComponent<NetworkCooperation>().Deliver(new MessageDeliveryResultEvent
                { MatchId = authority.MatchId, CorrelationId = command.CorrelationId.Length <= 64 ? command.CorrelationId : "",
                    Stage = MessageDeliveryStage.Rejected, ReasonCode = "communication_unavailable", ServerUnixMs = Now });
                return;
            }
            DispatchCommunication(communication.Execute(actor, command, Now));
        }
        internal void Acknowledge(string actor, string messageId, string match)
        { if (communication != null) DispatchCommunication(communication.Acknowledge(actor, messageId, match, Now)); }
        private void DispatchCommunication(IEnumerable<CooperativeAuthority.Delivery> deliveries)
        {
            foreach (var delivery in deliveries)
            {
                // Never reuse legacy roster/progress/assignment views in Social.
                if (delivery.Payload is not (MessageReceivedEvent or MessageDeliveryResultEvent)
                    || !endpoints.TryGetValue(delivery.Recipient, out var endpoint) || endpoint == null || !endpoint.IsSpawned) continue;
                if (delivery.Payload is MessageReceivedEvent message
                    && !authority.CanCommunicate(message.SenderActorId, delivery.Recipient, message.ChannelId)) continue;
                if (delivery.Payload is MessageReceivedEvent publicMessage && publicMessage.ChannelId == "discussion" && !publicMessage.Sensitive)
                    NetworkManager.Singleton.GetComponent<BroadcastSession>()?.PublicDiscussion(publicMessage.MessageId, publicMessage.SenderActorId, endpoints.TryGetValue(publicMessage.SenderActorId, out var sender) ? sender.GetComponent<NetworkCharacter>().PlayerName : "Player", publicMessage.Text, publicMessage.ServerUnixMs);
                endpoint.GetComponent<NetworkCooperation>().Deliver(delivery.Payload);
            }
        }
        private void Dispatch(IEnumerable<SocialMatchAuthority.Delivery> deliveries)
        {
            foreach (var delivery in deliveries)
                if (endpoints.TryGetValue(delivery.Recipient, out var target) && target != null && target.IsSpawned) target.Deliver(delivery.Payload);
        }
        private void PublishViews()
        {
            foreach (var pair in endpoints)
            {
                string actor = pair.Key; var endpoint = pair.Value;
                endpoint.Deliver(authority.State()); endpoint.Deliver(authority.OwnState(actor));
                endpoint.Deliver(authority.VisibleWorld(actor, Now)); endpoint.Deliver(authority.Meeting(actor));
                endpoint.Deliver(authority.Tasks(actor)); endpoint.Deliver(authority.Emergency());
                endpoint.Deliver(authority.Waiting(actor)); endpoint.Deliver(authority.TaskWork(actor));
                var postgame = new SocialPostgameObservation { MatchId = authority.MatchId };
                if (authority.IsTerminal) postgame.Summary = authority.Summary();
                endpoint.Deliver(postgame);
                var peers = new CommunicationPeersObservation { MatchId = authority.MatchId, ServerRevision = authority.State().PhaseRevision };
                if (authority.Phase == SocialPhase.Exploration) peers.AllowedChannels.Add(new[] { "direct", "proximity" });
                else if (authority.Phase is SocialPhase.Discussion or SocialPhase.Voting) peers.AllowedChannels.Add("discussion");
                if (authority.OwnState(actor).Life != SocialLife.Alive) peers.AllowedChannels.Clear();
                foreach (var other in endpoints)
                    if (other.Key != actor && (authority.CanCommunicate(actor, other.Key, "direct") || authority.CanCommunicate(actor, other.Key, "discussion")))
                        peers.Peers.Add(new CommunicationPeer { ActorId = other.Key, DisplayName = other.Value.GetComponent<NetworkCharacter>().PlayerName });
                endpoint.GetComponent<NetworkCooperation>().Deliver(peers);
            }
            var session = NetworkManager.Singleton.GetComponent<MatchSession>();
            var broadcastActors = endpoints.Values.Select(p => p.GetComponent<NetworkCharacter>()).Concat(session.RetainedSocialPlayers)
                .GroupBy(c => c.AuthoritativeActorId).Select(g => g.First()).Select(character =>
            {
                var center = character.GetComponent<CapsuleCollider2D>().bounds.center;
                var velocity = character.AuthoritativeVelocity;
                return new BroadcastViewProjector.Actor { Id = character.AuthoritativeActorId, Name = character.PlayerName, Room = character.GetComponent<NetworkRoomMembership>().CurrentRoomId,
                    X = center.x, Y = center.y, VelocityX = velocity.x, VelocityY = velocity.y, Facing = (uint)character.AuthoritativeFacing,
                    Cosmetic = (uint)character.CosmeticIndex, Alive = authority.OwnState(character.AuthoritativeActorId).Life == SocialLife.Alive };
            }).ToArray();
            var broadcastBodies = authority.BodyStates().Select(b => new BroadcastViewProjector.Body { Id = b.BodyId, Victim = b.VictimActorId,
                Name = b.VictimDisplayName, X = b.X, Y = b.Y, Cosmetic = (uint)session.CosmeticIndexForActor(b.VictimActorId) }).ToArray();
            NetworkManager.Singleton.GetComponent<BroadcastSession>()?.Publish(authority.State(), authority.Emergency(), authority.Meeting(""),
                broadcastActors, broadcastBodies, authority.IsTerminal ? authority.Summary() : null, Now);
        }
        private void SynchronizeBodies()
        {
            if (authority == null || bodyPrefab == null) return;
            var states = authority.BodyStates().ToDictionary(b => b.BodyId);
            foreach (var key in bodyObjects.Keys.Where(key => !states.ContainsKey(key)).ToArray())
            { var body = bodyObjects[key]; bodyObjects.Remove(key); if (body != null && body.IsSpawned) body.NetworkObject.Despawn(); }
            foreach (var state in states.Values)
            {
                if (bodyObjects.ContainsKey(state.BodyId)) continue;
                var body = Instantiate(bodyPrefab, new Vector3(state.X, state.Y, 0), Quaternion.identity).GetComponent<SocialBody>();
                int cosmetic = NetworkManager.Singleton.GetComponent<MatchSession>()?.CosmeticIndexForActor(state.VictimActorId) ?? 0;
                body.Configure(state, cosmetic); bodyObjects[state.BodyId] = body;
                body.GetComponent<SocialVisibilityMember>().Configure(SocialVisibilityKind.Body);
                body.NetworkObject.Spawn(); // No observers until this returns.
            }
        }
        private void WriteSummary()
        {
            if (summaryWritten || authority == null || !authority.IsTerminal) return;
            summaryWritten = true;
            var summary = authority.Summary();
            // Alignment and ordered replay are permitted only after termination.
            string directory = Environment.GetEnvironmentVariable("ACTOR_SOCIAL_SUMMARY_DIRECTORY");
            if (!string.IsNullOrEmpty(directory))
            {
                try { Directory.CreateDirectory(directory); File.WriteAllText(Path.Combine(directory, authority.MatchId + ".json"), JsonFormatter.Default.Format(summary));
                    SocialMatchMetrics.SavePostgame(directory, authority.MatchId); }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException) { Debug.LogWarning("ACTOR_SOCIAL summary_write_failed"); }
            }
            Debug.Log("ACTOR_SOCIAL game_over winner=" + summary.WinningSide + " reason=" + summary.WinReason + " duration_ms=" + summary.DurationMs);
        }
    }
}
