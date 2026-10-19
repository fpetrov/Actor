using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Actor.Protocol.Standard.Communication.V1;
using Actor.World;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    // The dedicated game server alone owns scenarios, truth and evidence. This
    // component adapts approved NGO identities/poses to the pure game authority.
    public sealed class IncidentSession : MonoBehaviour
    {
        public static IncidentSession Current { get; private set; }
        private IncidentAuthority authority;
        private readonly Dictionary<string, NetworkInvestigation> endpoints = new();
        private readonly Dictionary<string, MessageReceivedEvent> pending = new();
        private double nextRefresh;
        private bool invalidConfiguration;
        private long Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        [Serializable] private sealed class ConfigurationFile
        {
            public int seed = 1;
            public bool naturalEnabled = true;
            public long naturalDelayMs = 15000, incidentTtlMs = 300000, repairDurationMs = 2000;
            public string activeCircuit = "";
        }
        private void OnEnable() => Current = this;
        private void OnDisable()
        { if (Current == this) Current = null; authority = null; endpoints.Clear(); pending.Clear(); }
        private void Update()
        {
            if (NetworkManager.Singleton == null || !NetworkManager.Singleton.IsServer || Time.realtimeSinceStartupAsDouble < nextRefresh) return;
            nextRefresh = Time.realtimeSinceStartupAsDouble + .25; Refresh();
        }
        private void Initialize(string match)
        {
            if (authority != null || invalidConfiguration) return;
            try
            {
                if (!Guid.TryParseExact(match, "N", out _)) return;
                var entropy = new byte[4];
                using (var random = RandomNumberGenerator.Create()) random.GetBytes(entropy);
                var config = new IncidentAuthority.ScenarioConfiguration { Seed = BitConverter.ToInt32(entropy, 0) };
                // Trusted deployment fixtures are match-scoped files, never a
                // client command or public control-plane/debug endpoint. Lazy
                // initialization permits setup before the first role command.
                string directory = Environment.GetEnvironmentVariable("ACTOR_INCIDENT_CONFIG_DIRECTORY");
                if (string.IsNullOrEmpty(directory)) directory = "/etc/actor/incident-scenarios";
                string path = Path.Combine(directory, match + ".json");
                if (File.Exists(path))
                {
                    if (new FileInfo(path).Length > 4096) throw new InvalidDataException();
                    var file = JsonUtility.FromJson<ConfigurationFile>(File.ReadAllText(path));
                    if (file == null) throw new InvalidDataException();
                    config = new IncidentAuthority.ScenarioConfiguration { Seed = file.seed, NaturalEnabled = file.naturalEnabled,
                        NaturalDelayMs = file.naturalDelayMs, IncidentTtlMs = file.incidentTtlMs,
                        RepairDurationMs = file.repairDurationMs, ActiveCircuit = file.activeCircuit ?? "" };
                }
                authority = new IncidentAuthority(match, config);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
            { invalidConfiguration = true; Debug.LogError("ACTOR_INCIDENT configuration_invalid"); }
        }
        private void Refresh()
        {
            var manager = NetworkManager.Singleton;
            if (manager == null || !manager.IsServer) return;
            var session = manager.GetComponent<MatchSession>();
            if (session == null || string.IsNullOrEmpty(session.MatchId)) return;
            var participants = new List<IncidentAuthority.Participant>(); endpoints.Clear();
            foreach (var pair in manager.ConnectedClients)
            {
                if (!session.TryGetApprovedPlayer(pair.Key, out _) || pair.Value.PlayerObject == null) continue;
                var player = pair.Value.PlayerObject.GetComponent<NetworkCharacter>();
                var endpoint = pair.Value.PlayerObject.GetComponent<NetworkInvestigation>();
                if (player == null || !player.IsSpawned || endpoint == null || !endpoint.IsSpawned || string.IsNullOrEmpty(player.AuthoritativeActorId)) continue;
                var center = player.GetComponent<CapsuleCollider2D>().bounds.center;
                participants.Add(new IncidentAuthority.Participant { Id = player.AuthoritativeActorId, Name = player.PlayerName,
                    Room = player.GetComponent<NetworkRoomMembership>().CurrentRoomId, X = center.x, Y = center.y });
                endpoints[player.AuthoritativeActorId] = endpoint;
            }
            if (authority != null)
            {
                IncidentTerminal2D cooling = null, security = null;
                foreach (var terminal in FindObjectsByType<IncidentTerminal2D>())
                {
                    var entity = terminal.GetComponent<WorldEntity>();
                    if (!terminal.isActiveAndEnabled || entity == null || entity.EntityId != terminal.EntityId || entity.RoomId != terminal.RoomId) continue;
                    if (terminal.Kind == IncidentTerminalKind.Cooling) cooling = terminal; else security = terminal;
                }
                authority.SetTerminals(cooling != null, cooling != null ? cooling.InteractionPoint.x : 0, cooling != null ? cooling.InteractionPoint.y : 0, cooling != null ? cooling.Radius : 0,
                    security != null, security != null ? security.InteractionPoint.x : 0, security != null ? security.InteractionPoint.y : 0, security != null ? security.Radius : 0);
                Dispatch(authority.Refresh(participants, Now));
                // Fresh explicit views also clear merged private observations.
                foreach (var endpoint in endpoints)
                {
                    endpoint.Value.Deliver(authority.OwnRole(endpoint.Key)); endpoint.Value.Deliver(authority.Roster());
                    endpoint.Value.Deliver(authority.State()); endpoint.Value.Deliver(authority.OwnView(endpoint.Key));
                }
            }
            else
            {
                var roster = new DutyRosterObservation { MatchId = session.MatchId };
                roster.Entries.Add(participants.Select(p => new DutyRoleEntry { ActorId = p.Id, DisplayName = p.Name, Role = CrewRole.Crew }));
                foreach (var endpoint in endpoints)
                {
                    endpoint.Value.Deliver(new OwnRoleObservation { ActorId = endpoint.Key, Role = CrewRole.Crew, MatchId = session.MatchId });
                    endpoint.Value.Deliver(roster);
                    endpoint.Value.Deliver(new CoolingStateObservation { MatchId = session.MatchId, CoolingStatus = CoolingStatus.Operational,
                        IncidentStatus = IncidentStatus.None, Severity = CoolingSeverity.Normal, LocationId = "engineering", ReasonCode = "operational" });
                    endpoint.Value.Deliver(new OwnInvestigationViewObservation { MatchId = session.MatchId, RecipientActorId = endpoint.Key });
                }
            }
            foreach (var id in pending.Where(p => p.Value.ExpiresAtUnixMs <= Now || Now - p.Value.ServerUnixMs > 10000
                || authority == null || !authority.Active || p.Value.TaskId != authority.IncidentId).Select(p => p.Key).ToArray()) pending.Remove(id);
        }
        internal void Request(string actor, IMessage command)
        {
            var manager = NetworkManager.Singleton;
            if (manager == null || !manager.IsServer) return;
            Initialize(manager.GetComponent<MatchSession>().MatchId); Refresh();
            if (authority != null) { Dispatch(authority.Execute(actor, command, Now)); return; }
            if (command is SelectRoleCommand role && endpoints.TryGetValue(actor, out var endpoint))
                endpoint.Deliver(new RoleResultEvent { CorrelationId = role.CorrelationId, Accepted = false, ReasonCode = "configuration_unavailable",
                    MatchId = manager.GetComponent<MatchSession>().MatchId, State = new OwnRoleObservation { ActorId = actor, Role = CrewRole.Crew,
                        MatchId = manager.GetComponent<MatchSession>().MatchId } });
        }
        internal bool AllowsTaskMessage(string actor, string task) => authority?.AllowsTaskMessage(actor, task) ?? false;
        internal string ValidateEvidenceMessage(string actor, SendMessageCommand message)
            => authority?.ValidateEvidenceMessage(actor, message.RecipientActorId, message.ChannelId, message.TaskId, message.Text, message.Sensitive) ?? "";
        internal void MessageAccepted(MessageReceivedEvent message)
        {
            if (authority == null || !authority.Active || message.MatchId != authority.MatchId || message.TaskId != authority.IncidentId
                || message.ChannelId != "direct" || !message.Sensitive || message.SenderActorId != authority.EngineerActorId
                || message.RecipientActorId != authority.SheriffActorId || message.ExpiresAtUnixMs <= Now) return;
            if (pending.Count >= 64) pending.Remove(pending.OrderBy(p => p.Value.ServerUnixMs).First().Key);
            pending[message.MessageId] = message.Clone();
        }
        internal void MessageReceipt(MessageDeliveryResultEvent receipt)
        {
            if (receipt.Stage != MessageDeliveryStage.ClientReceived || !receipt.AcceptedByServer || authority == null
                || !pending.TryGetValue(receipt.MessageId, out var message)) return;
            pending.Remove(receipt.MessageId);
            if (receipt.MatchId != message.MatchId || receipt.TaskId != message.TaskId || receipt.RecipientActorId != message.RecipientActorId
                || message.ExpiresAtUnixMs <= Now || Now - message.ServerUnixMs > 10000) return;
            authority.OnDeliveredEvidence(message.SenderActorId, message.RecipientActorId, message.TaskId, message.Text, Now);
        }
        internal void ControlUnavailable(string actor)
        {
            if (authority == null) return;
            Dispatch(authority.ControlUnavailable(actor, Now));
            foreach (var id in pending.Where(p => !authority.Active || p.Value.SenderActorId == actor || p.Value.RecipientActorId == actor).Select(p => p.Key).ToArray()) pending.Remove(id);
        }
        private void Dispatch(IEnumerable<IncidentAuthority.Delivery> deliveries)
        {
            foreach (var delivery in deliveries)
            {
                if (!endpoints.TryGetValue(delivery.Recipient, out var target) || target == null || !target.IsSpawned) continue;
                target.Deliver(delivery.Payload);
                if (delivery.Payload is InvestigationResultEvent report)
                    Debug.Log("ACTOR_INCIDENT report actor=" + delivery.Recipient + " accepted=" + report.Accepted + " reason=" + report.ReasonCode + " status=" + report.IncidentStatus);
                else if (delivery.Payload is IncidentChangedEvent changed)
                    Debug.Log("ACTOR_INCIDENT changed status=" + changed.State.IncidentStatus + " cooling=" + changed.State.CoolingStatus + " revision=" + changed.State.ServerRevision);
            }
        }
    }
}
