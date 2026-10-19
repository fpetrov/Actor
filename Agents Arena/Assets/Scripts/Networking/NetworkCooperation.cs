using System;
using System.Collections.Generic;
using Actor.Characters;
using Actor.Protocol.Standard.Communication.V1;
using Actor.World;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    [RequireComponent(typeof(NetworkCharacter), typeof(NetworkRoomMembership))]
    public sealed class NetworkCooperation : NetworkBehaviour
    {
        internal enum PayloadKind : byte { Peers, Message, Receipt, State, Progress, Assignment, Changed, Diagnostic, Result }
        internal static NetworkCooperation LocalOwner { get; private set; }
        public CommunicationPeersObservation Peers { get; private set; } = new();
        public CalibrationTaskStateObservation TaskState { get; private set; } = new();
        public CalibrationProgressObservation Progress { get; private set; } = new();
        public event Action<IMessage> PermittedEvent;
        internal readonly Queue<MessageReceivedEvent> Inbox = new();
        internal DiagnosticResultEvent PrivateDiagnostic { get; private set; }
        internal string LastAction { get; private set; } = "";
        internal long MessageCount { get; private set; }
        internal long DiagnosticCount { get; private set; }
        internal long ServerAcceptedCount { get; private set; }
        internal long ClientReceivedCount { get; private set; }
        internal long RejectedCount { get; private set; }
        internal string LastMessageId { get; private set; } = "";
        internal string LastMessageSender { get; private set; } = "";
        internal string LastMessageTask { get; private set; } = "";
        internal double MeanMessageLatencyMs => MessageCount == 0 ? 0 : latencyTotal / MessageCount;
        private double latencyTotal;
        private ulong taskStateRevision;
        internal bool Human => IsSpawned && IsClient && IsOwner && GetComponent<PlayerInputSource>().isActiveAndEnabled;
        private double tokens = 8, tokenAt, nextInvalidReply, nextUnavailable;
        private double ackTokens = 32, ackAt, serverUnavailableAt;
        private readonly HashSet<string> seen = new();
        private readonly Queue<string> seenOrder = new();

        public override void OnNetworkSpawn()
        {
            if (IsClient && IsOwner) LocalOwner = this;
            tokens = 8; tokenAt = Time.realtimeSinceStartupAsDouble;
            ackTokens = 32; ackAt = tokenAt;
        }
        public bool Submit(IMessage command)
        {
            if (!IsSpawned || !IsOwner || !IsClient || command == null || command.CalculateSize() > 2048) return false;
            byte kind = command switch { SendMessageCommand _ => 1, StartCalibrationCommand _ => 2,
                InspectDiagnosticCommand _ => 3, SubmitCalibrationCommand _ => 4, CancelCalibrationCommand _ => 5, _ => (byte)0 };
            if (kind == 0) return false;
            RequestRpc(kind, command.ToByteArray()); return true;
        }
        public void RuntimeUnavailable()
        {
            if (!IsSpawned || !IsOwner || !IsClient || Time.realtimeSinceStartupAsDouble < nextUnavailable) return;
            nextUnavailable = Time.realtimeSinceStartupAsDouble + 1;
            ControlUnavailableRpc();
            PrivateDiagnostic = null;
        }

        private bool Approved(RpcParams rpc)
        {
            if (!IsServer || !IsSpawned || rpc.Receive.SenderClientId != OwnerClientId) return false;
            var session = NetworkManager.GetComponent<MatchSession>();
            return session != null && session.TryGetApprovedPlayer(OwnerClientId, out _)
                && NetworkManager.ConnectedClients.TryGetValue(OwnerClientId, out var client) && client.PlayerObject == NetworkObject;
        }
        [Rpc(SendTo.Server, Delivery = RpcDelivery.Reliable, InvokePermission = RpcInvokePermission.Owner)]
        private void RequestRpc(byte kind, byte[] bytes, RpcParams rpc = default)
        {
            if (!Approved(rpc)) return;
            double now = Time.realtimeSinceStartupAsDouble;
            tokens = Math.Min(8, tokens + Math.Max(0, now - tokenAt) * 4); tokenAt = now;
            // Drop excessive packets before producing reliable response traffic.
            if (tokens < 1) return; --tokens;
            if (bytes == null || bytes.Length > 2048) return;
            try
            {
                IMessage command = kind switch { 1 => SendMessageCommand.Parser.ParseFrom(bytes), 2 => StartCalibrationCommand.Parser.ParseFrom(bytes),
                    3 => InspectDiagnosticCommand.Parser.ParseFrom(bytes), 4 => SubmitCalibrationCommand.Parser.ParseFrom(bytes),
                    5 => CancelCalibrationCommand.Parser.ParseFrom(bytes), _ => null };
                if (command != null) CooperativeSession.Current?.Request(GetComponent<NetworkCharacter>().AuthoritativeActorId, command);
            }
            catch (InvalidProtocolBufferException)
            {
                if (now >= nextInvalidReply) { nextInvalidReply = now + 1; LastAction = "invalid_payload"; }
            }
        }
        [Rpc(SendTo.Server, Delivery = RpcDelivery.Reliable, InvokePermission = RpcInvokePermission.Owner)]
        private void AcknowledgeRpc(string messageId, string matchId, RpcParams rpc = default)
        {
            if (!Approved(rpc) || messageId == null || messageId.Length > 64 || matchId == null || matchId.Length > 64) return;
            double now = Time.realtimeSinceStartupAsDouble;
            ackTokens = Math.Min(32, ackTokens + Math.Max(0, now - ackAt) * 16); ackAt = now;
            if (ackTokens < 1) return; --ackTokens;
            CooperativeSession.Current?.Acknowledge(GetComponent<NetworkCharacter>().AuthoritativeActorId, messageId, matchId);
        }
        [Rpc(SendTo.Server, Delivery = RpcDelivery.Reliable, InvokePermission = RpcInvokePermission.Owner)]
        private void ControlUnavailableRpc(RpcParams rpc = default)
        {
            if (!Approved(rpc) || Time.realtimeSinceStartupAsDouble < serverUnavailableAt) return;
            serverUnavailableAt = Time.realtimeSinceStartupAsDouble + 1;
            CooperativeSession.Current?.ControlUnavailable(GetComponent<NetworkCharacter>().AuthoritativeActorId);
        }
        internal void Deliver(IMessage payload)
        {
            PayloadKind kind = payload switch { CommunicationPeersObservation _ => PayloadKind.Peers, MessageReceivedEvent _ => PayloadKind.Message,
                MessageDeliveryResultEvent _ => PayloadKind.Receipt, CalibrationTaskStateObservation _ => PayloadKind.State,
                CalibrationProgressObservation _ => PayloadKind.Progress, CalibrationAssignmentEvent _ => PayloadKind.Assignment,
                CalibrationTaskChangedEvent _ => PayloadKind.Changed, DiagnosticResultEvent _ => PayloadKind.Diagnostic,
                CalibrationResultEvent _ => PayloadKind.Result, _ => throw new ArgumentException("Unsupported game delivery.") };
            // This method is only used by the server, on the chosen recipient's
            // own player NetworkObject. Private bytes never use ClientsAndHost.
            ReceivePermittedRpc((byte)kind, payload.ToByteArray());
        }
        [Rpc(SendTo.Owner, Delivery = RpcDelivery.Reliable, InvokePermission = RpcInvokePermission.Server)]
        private void ReceivePermittedRpc(byte kind, byte[] bytes)
        {
            if (!IsOwner || !IsClient || bytes == null || bytes.Length > 8192) return;
            IMessage value = (PayloadKind)kind switch
            {
                PayloadKind.Peers => CommunicationPeersObservation.Parser.ParseFrom(bytes), PayloadKind.Message => MessageReceivedEvent.Parser.ParseFrom(bytes),
                PayloadKind.Receipt => MessageDeliveryResultEvent.Parser.ParseFrom(bytes), PayloadKind.State => CalibrationTaskStateObservation.Parser.ParseFrom(bytes),
                PayloadKind.Progress => CalibrationProgressObservation.Parser.ParseFrom(bytes), PayloadKind.Assignment => CalibrationAssignmentEvent.Parser.ParseFrom(bytes),
                PayloadKind.Changed => CalibrationTaskChangedEvent.Parser.ParseFrom(bytes), PayloadKind.Diagnostic => DiagnosticResultEvent.Parser.ParseFrom(bytes),
                PayloadKind.Result => CalibrationResultEvent.Parser.ParseFrom(bytes), _ => null
            };
            var session = NetworkManager.GetComponent<MatchSession>();
            if (value == null || MatchOf(value) != session.MatchId) return;
            switch (value)
            {
                case CommunicationPeersObservation peers: Peers = peers; return;
                case CalibrationTaskStateObservation state:
                    ApplyTaskState(state);
                    return;
                case CalibrationProgressObservation progress:
                    if (progress.ServerRevision >= Progress.ServerRevision) Progress = progress;
                    return;
                case CalibrationTaskChangedEvent changed:
                    // Update presentation/report state before forwarding the
                    // same authoritative event to the Runtime adapter.
                    ApplyTaskState(changed.State);
                    break;
                case MessageReceivedEvent message:
                    if (message.RecipientActorId != GetComponent<NetworkCharacter>().AuthoritativeActorId) return;
                    AcknowledgeRpc(message.MessageId, message.MatchId);
                    if (!seen.Add(message.MessageId)) return;
                    seenOrder.Enqueue(message.MessageId); while (seenOrder.Count > 256) seen.Remove(seenOrder.Dequeue());
                    if (message.ExpiresAtUnixMs <= DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()) return;
                    ++MessageCount; Inbox.Enqueue(message); while (Inbox.Count > 16) Inbox.Dequeue();
                    LastMessageId = message.MessageId; LastMessageSender = message.SenderActorId; LastMessageTask = message.TaskId;
                    latencyTotal += Math.Clamp(DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - message.ServerUnixMs, 0, 300000);
                    LastAction = "Message from " + DisplayName(message.SenderActorId); break;
                case MessageDeliveryResultEvent receipt:
                    if (receipt.Stage == MessageDeliveryStage.ServerAccepted) ++ServerAcceptedCount;
                    if (receipt.Stage == MessageDeliveryStage.ClientReceived) ++ClientReceivedCount;
                    if (receipt.Stage == MessageDeliveryStage.Rejected) ++RejectedCount;
                    LastAction = receipt.ReasonCode + " · " + receipt.Stage; break;
                case DiagnosticResultEvent diagnostic:
                    ++DiagnosticCount;
                    if (diagnostic.Accepted && diagnostic.TaskId == TaskState.TaskId && diagnostic.ServerRevision >= taskStateRevision
                        && (TaskState.Status == CalibrationStatus.AwaitingAuthorization || TaskState.Status == CalibrationStatus.AwaitingDiagnostic)
                        && diagnostic.ExpiresAtUnixMs > DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()) PrivateDiagnostic = diagnostic;
                    LastAction = diagnostic.Accepted ? "Private diagnostic authorization received" : diagnostic.ReasonCode; break;
                case CalibrationResultEvent result: LastAction = result.Action + " · " + result.ReasonCode; break;
                case CalibrationAssignmentEvent assignment: LastAction = "Assigned " + assignment.Role; break;
            }
            PermittedEvent?.Invoke(value);
        }

        private void ApplyTaskState(CalibrationTaskStateObservation state)
        {
            if (state == null) return;
            // Unassigned private state deliberately carries no task details and
            // revision zero. Its preceding public Progress supplies the match's
            // revision, allowing assignment clearing without rolling back the
            // watermark when a terminal Changed event arrived first.
            ulong revision = state.TaskId.Length == 0 ? Math.Max(state.ServerRevision, Progress.ServerRevision) : state.ServerRevision;
            if (revision < taskStateRevision) return;
            taskStateRevision = revision;
            TaskState = state;
            if (state.Status != CalibrationStatus.AwaitingAuthorization && state.Status != CalibrationStatus.AwaitingDiagnostic)
                PrivateDiagnostic = null;
        }
        public string DisplayName(string actorId)
        {
            foreach (var p in Peers.Peers) if (p.ActorId == actorId) return p.DisplayName;
            return actorId;
        }
        private static string MatchOf(IMessage m) => m switch { CommunicationPeersObservation x => x.MatchId, MessageReceivedEvent x => x.MatchId,
            MessageDeliveryResultEvent x => x.MatchId, CalibrationTaskStateObservation x => x.MatchId, CalibrationProgressObservation x => x.MatchId,
            CalibrationAssignmentEvent x => x.MatchId, CalibrationTaskChangedEvent x => x.State?.MatchId,
            DiagnosticResultEvent x => x.MatchId, CalibrationResultEvent x => x.MatchId, _ => "" };
        public override void OnNetworkDespawn()
        {
            if (LocalOwner == this) LocalOwner = null;
            PrivateDiagnostic = null; Inbox.Clear(); seen.Clear(); seenOrder.Clear();
            taskStateRevision = 0;
            Peers = new(); TaskState = new(); Progress = new();
        }
    }
}
