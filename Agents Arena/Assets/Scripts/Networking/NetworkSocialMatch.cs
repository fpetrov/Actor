using System;
using Actor.Characters;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    // A private owner endpoint. No alignment, task or global life count is ever
    // stored in a replicated Everyone variable on a visible remote avatar.
    [RequireComponent(typeof(NetworkCharacter))]
    public sealed class NetworkSocialMatch : NetworkBehaviour
    {
        private enum Kind : byte { Match, Own, World, Meeting, Tasks, Emergency, Postgame, ActionResult, MatchChanged, OwnStateChanged, MeetingChanged, EmergencyChanged, VisibleAttack, VisibleActivity, MatchEnded }
        public SocialMatchStateObservation State { get; private set; } = new();
        public SocialOwnStateObservation Own { get; private set; } = new();
        public SocialVisibleWorldObservation World { get; private set; } = new();
        public SocialMeetingObservation Meeting { get; private set; } = new();
        public SocialOwnTasksObservation Tasks { get; private set; } = new();
        public SocialEmergencyStateObservation Emergency { get; private set; } = new();
        public SocialPostgameObservation Postgame { get; private set; } = new();
        public static NetworkSocialMatch LocalOwner { get; private set; }
        public event Action<IMessage> PermittedEvent;
        public string LastAction { get; private set; } = "";
        public string ActiveWorkId { get; private set; } = "";
        private double tokens = 8, tokenAt, nextUnavailable, worldReceivedAt = -100;
        private readonly System.Collections.Generic.Dictionary<Kind, byte[]> sent = new();
        public string Self => GetComponent<NetworkCharacter>().AuthoritativeActorId;
        public bool Human => IsSpawned && IsClient && IsOwner && GetComponent<PlayerInputSource>().isActiveAndEnabled;
        public bool HasOwnState => IsSpawned && Own.ActorId == Self && Own.MatchId.Length > 0
            && State.MatchId == Own.MatchId && World.MatchId == Own.MatchId && World.RecipientActorId == Self;
        public SocialVisibleWorldObservation CaptureWorld()
        {
            if (Time.realtimeSinceStartupAsDouble - worldReceivedAt <= .5) return World.Clone();
            return new SocialVisibleWorldObservation { MatchId = World.MatchId, RecipientActorId = World.RecipientActorId,
                VisibilityRadius = World.VisibilityRadius, CapturedAtUnixMs = World.CapturedAtUnixMs, ServerRevision = World.ServerRevision };
        }
        public bool CanMove => HasOwnState && Own.CanMove;
        public SocialActionContext Context() => new() { MatchId = State.MatchId, PhaseRevision = State.PhaseRevision,
            CorrelationId = Guid.NewGuid().ToString("N"), ExpiresAtUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + 5000 };
        public override void OnNetworkSpawn()
        { if (IsClient && IsOwner) LocalOwner = this; tokenAt = Time.realtimeSinceStartupAsDouble; }
        public bool Submit(IMessage command)
        {
            if (!SocialVisibilitySystem.IsSocial(NetworkManager) || !IsSpawned || !IsOwner || !IsClient
                || command == null || command.CalculateSize() > 2048) return false;
            byte kind = command switch
            {
                SocialReadyCommand _ => 1,
                SocialKillCommand _ => 2,
                SocialReportBodyCommand _ => 3,
                SocialCallMeetingCommand _ => 4,
                SocialCastVoteCommand _ => 5,
                SocialWorkTaskCommand _ => 6,
                SocialSabotageCommand _ => 7,
                SocialRepairEmergencyCommand _ => 8,
                SocialCancelWorkCommand _ => 9, _ => (byte)0
            };
            if (kind == 0) return false;
            RequestRpc(kind, command.ToByteArray()); return true;
        }
        private bool Approved(RpcParams rpc) => IsServer && IsSpawned && SocialVisibilitySystem.IsSocial(NetworkManager)
            && rpc.Receive.SenderClientId == OwnerClientId
            && NetworkManager.GetComponent<MatchSession>().TryGetApprovedPlayer(OwnerClientId, out _)
            && NetworkManager.ConnectedClients.TryGetValue(OwnerClientId, out var client) && client.PlayerObject == NetworkObject;
        [Rpc(SendTo.Server, Delivery = RpcDelivery.Reliable, InvokePermission = RpcInvokePermission.Owner)]
        private void RequestRpc(byte kind, byte[] bytes, RpcParams rpc = default)
        {
            if (!Approved(rpc) || bytes == null || bytes.Length > 2048) return;
            double now = Time.realtimeSinceStartupAsDouble;
            tokens = Math.Min(8, tokens + Math.Max(0, now - tokenAt) * 4); tokenAt = now;
            if (tokens < 1) return; --tokens;
            try
            {
                IMessage command = kind switch
                {
                    1 => SocialReadyCommand.Parser.ParseFrom(bytes),
                    2 => SocialKillCommand.Parser.ParseFrom(bytes),
                    3 => SocialReportBodyCommand.Parser.ParseFrom(bytes),
                    4 => SocialCallMeetingCommand.Parser.ParseFrom(bytes),
                    5 => SocialCastVoteCommand.Parser.ParseFrom(bytes),
                    6 => SocialWorkTaskCommand.Parser.ParseFrom(bytes),
                    7 => SocialSabotageCommand.Parser.ParseFrom(bytes),
                    8 => SocialRepairEmergencyCommand.Parser.ParseFrom(bytes),
                    9 => SocialCancelWorkCommand.Parser.ParseFrom(bytes), _ => null
                };
                if (command != null) SocialMatchSession.Current?.Request(Self, command);
            }
            catch (InvalidProtocolBufferException) { }
        }
        public void RuntimeUnavailable()
        {
            if (!IsSpawned || !IsOwner || !IsClient || !SocialVisibilitySystem.IsSocial(NetworkManager)
                || Time.realtimeSinceStartupAsDouble < nextUnavailable) return;
            nextUnavailable = Time.realtimeSinceStartupAsDouble + 1;
            ControlUnavailableRpc();
        }
        [Rpc(SendTo.Server, Delivery = RpcDelivery.Reliable, InvokePermission = RpcInvokePermission.Owner)]
        private void ControlUnavailableRpc(RpcParams rpc = default)
        { if (Approved(rpc)) SocialMatchSession.Current?.ControlUnavailable(Self); }
        internal void Deliver(IMessage payload)
        {
            if (!IsServer || !SocialVisibilitySystem.IsSocial(NetworkManager)) return;
            Kind kind = payload switch
            {
                SocialMatchStateObservation _ => Kind.Match,
                SocialOwnStateObservation _ => Kind.Own,
                SocialVisibleWorldObservation _ => Kind.World,
                SocialMeetingObservation _ => Kind.Meeting,
                SocialOwnTasksObservation _ => Kind.Tasks,
                SocialEmergencyStateObservation _ => Kind.Emergency,
                SocialPostgameObservation _ => Kind.Postgame,
                SocialActionResultEvent _ => Kind.ActionResult,
                SocialMatchChangedEvent _ => Kind.MatchChanged,
                SocialOwnStateChangedEvent _ => Kind.OwnStateChanged,
                SocialMeetingChangedEvent _ => Kind.MeetingChanged,
                SocialEmergencyChangedEvent _ => Kind.EmergencyChanged,
                SocialVisibleAttackEvent _ => Kind.VisibleAttack,
                SocialVisibleActivityEvent _ => Kind.VisibleActivity,
                SocialMatchEndedEvent _ => Kind.MatchEnded, _ => throw new ArgumentException("Unsupported social delivery.")
            };
            byte[] bytes = payload.ToByteArray();
            if (bytes.Length > 65536) throw new InvalidOperationException("Bounded social payload exceeded.");
            if (kind == Kind.World)
            {
                SocialMatchMetrics.RecordPayload(Self, bytes.Length);
                SocialMatchMetrics.RecordWorld(Self, ((SocialVisibleWorldObservation)payload).Actors.Count);
                ReceiveWorldRpc(bytes); return;
            }
            if ((int)kind <= (int)Kind.Postgame)
            {
                if (sent.TryGetValue(kind, out var previous) && previous.AsSpan().SequenceEqual(bytes)) return;
                sent[kind] = bytes;
            }
            SocialMatchMetrics.RecordPayload(Self, bytes.Length);
            ReceivePermittedRpc((byte)kind, bytes);
        }
        [Rpc(SendTo.Owner, Delivery = RpcDelivery.Unreliable, InvokePermission = RpcInvokePermission.Server)]
        private void ReceiveWorldRpc(byte[] bytes)
        {
            if (!IsClient || bytes == null || bytes.Length > 16384) return;
            try
            {
                var world = SocialVisibleWorldObservation.Parser.ParseFrom(bytes);
                SocialPrivacyProbe.ObserveIncoming(world, IsOwner);
                if (!IsOwner || world.MatchId != NetworkManager.GetComponent<MatchSession>().MatchId || world.RecipientActorId != Self
                    || world.ServerRevision <= World.ServerRevision || world.CapturedAtUnixMs < World.CapturedAtUnixMs) return;
                worldReceivedAt = Time.realtimeSinceStartupAsDouble;
                World = world; // Replacement, including empty: never preserve a hidden target as current.
            }
            catch (InvalidProtocolBufferException) { }
        }
        [Rpc(SendTo.Owner, Delivery = RpcDelivery.Reliable, InvokePermission = RpcInvokePermission.Server)]
        private void ReceivePermittedRpc(byte kind, byte[] bytes)
        {
            if (!IsClient || bytes == null || bytes.Length > 65536) return;
            IMessage value;
            try
            {
                value = (Kind)kind switch
                {
                    Kind.Match => SocialMatchStateObservation.Parser.ParseFrom(bytes),
                    Kind.Own => SocialOwnStateObservation.Parser.ParseFrom(bytes),
                    Kind.World => SocialVisibleWorldObservation.Parser.ParseFrom(bytes),
                    Kind.Meeting => SocialMeetingObservation.Parser.ParseFrom(bytes),
                    Kind.Tasks => SocialOwnTasksObservation.Parser.ParseFrom(bytes),
                    Kind.Emergency => SocialEmergencyStateObservation.Parser.ParseFrom(bytes),
                    Kind.Postgame => SocialPostgameObservation.Parser.ParseFrom(bytes),
                    Kind.ActionResult => SocialActionResultEvent.Parser.ParseFrom(bytes),
                    Kind.MatchChanged => SocialMatchChangedEvent.Parser.ParseFrom(bytes),
                    Kind.OwnStateChanged => SocialOwnStateChangedEvent.Parser.ParseFrom(bytes),
                    Kind.MeetingChanged => SocialMeetingChangedEvent.Parser.ParseFrom(bytes),
                    Kind.EmergencyChanged => SocialEmergencyChangedEvent.Parser.ParseFrom(bytes),
                    Kind.VisibleAttack => SocialVisibleAttackEvent.Parser.ParseFrom(bytes),
                    Kind.VisibleActivity => SocialVisibleActivityEvent.Parser.ParseFrom(bytes),
                    Kind.MatchEnded => SocialMatchEndedEvent.Parser.ParseFrom(bytes), _ => null
                };
            }
            catch (InvalidProtocolBufferException) { return; }
            SocialPrivacyProbe.ObserveIncoming(value, IsOwner);
            if (!IsOwner) return;
            string match = value switch
            {
                SocialMatchStateObservation x => x.MatchId,
                SocialOwnStateObservation x => x.MatchId,
                SocialVisibleWorldObservation x => x.MatchId,
                SocialMeetingObservation x => x.MatchId,
                SocialOwnTasksObservation x => x.MatchId,
                SocialEmergencyStateObservation x => x.MatchId,
                SocialPostgameObservation x => x.MatchId,
                SocialActionResultEvent x => x.MatchId,
                SocialMatchChangedEvent x => x.State?.MatchId,
                SocialOwnStateChangedEvent x => x.State?.MatchId,
                SocialMeetingChangedEvent x => x.State?.MatchId,
                SocialEmergencyChangedEvent x => x.State?.MatchId,
                SocialVisibleAttackEvent x => x.MatchId,
                SocialVisibleActivityEvent x => x.MatchId,
                SocialMatchEndedEvent x => x.Summary?.MatchId, _ => ""
            };
            if (match != NetworkManager.GetComponent<MatchSession>().MatchId) return;
            switch (value)
            {
                case SocialMatchStateObservation x: State = x; return;
                case SocialOwnStateObservation x: if (x.ActorId != Self) return; Own = x; return;
                case SocialVisibleWorldObservation x: if (x.RecipientActorId != Self) return; World = x; return;
                case SocialMeetingObservation x: Meeting = x; return;
                case SocialOwnTasksObservation x: if (x.RecipientActorId != Self) return; Tasks = x; return;
                case SocialEmergencyStateObservation x: Emergency = x; return;
                case SocialPostgameObservation x: Postgame = x; return;
                case SocialActionResultEvent x:
                    LastAction = x.ReasonCode;
                    if (x.WorkId == ActiveWorkId && (!x.Accepted || x.Completed)) ActiveWorkId = "";
                    else if (x.Accepted && !x.Completed && x.WorkId.Length > 0) ActiveWorkId = x.WorkId;
                    break;
                case SocialMatchChangedEvent x: State = x.State; break;
                case SocialOwnStateChangedEvent x: if (x.State.ActorId != Self) return; Own = x.State; break;
                case SocialMeetingChangedEvent x: Meeting = x.State; break;
                case SocialEmergencyChangedEvent x: Emergency = x.State; break;
                case SocialVisibleAttackEvent x: if (x.ObserverActorId != Self) return; break;
                case SocialVisibleActivityEvent x: if (x.ObserverActorId != Self) return; break;
                case SocialMatchEndedEvent x: Postgame = new SocialPostgameObservation { MatchId = match, Summary = x.Summary }; break;
            }
            PermittedEvent?.Invoke(value);
        }
        public override void OnNetworkDespawn()
        {
            if (LocalOwner == this) LocalOwner = null;
            State = new(); Own = new(); World = new(); Meeting = new(); Tasks = new(); Emergency = new(); Postgame = new(); sent.Clear(); worldReceivedAt = -100; ActiveWorkId = "";
        }
    }
}
