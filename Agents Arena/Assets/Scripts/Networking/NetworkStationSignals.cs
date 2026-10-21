using System;
using Actor.Characters;
using Actor.World;
using AgentsArena.ActorProtocol.V1;
using Unity.Collections;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    [RequireComponent(typeof(NetworkCharacter), typeof(CapsuleCollider2D))]
    public sealed class NetworkStationSignals : NetworkBehaviour
    {
        private NetworkCharacter character;
        private CapsuleCollider2D capsule;
        private PlayerInputSource keyboard;
        private double ownerReadyAt, nextRejectionAt;
        internal static NetworkStationSignals LocalOwner { get; private set; }
        internal string InteractionStatus { get; private set; } = "";
        internal bool IsHumanOwner => IsSpawned && IsOwner && IsClient && keyboard != null && keyboard.isActiveAndEnabled;
        internal Vector2 InteractionPosition => capsule.bounds.center;

        private void Awake()
        { character = GetComponent<NetworkCharacter>(); capsule = GetComponent<CapsuleCollider2D>(); keyboard = GetComponent<PlayerInputSource>(); }

        public override void OnNetworkSpawn()
        {
            ownerReadyAt = nextRejectionAt = 0;
            if (IsOwner && IsClient) LocalOwner = this;
        }

        // Invoked only by the ordinary local Human console UI. No arbitrary text or
        // event identity is accepted, and there is no diagnostic interaction bypass.
        internal void Request(StationSignalKind kind)
        {
            if (SocialVisibilitySystem.IsSocial(NetworkManager) || !IsHumanOwner || !Application.isFocused) return;
            InteractionStatus = "Waiting for station confirmation…";
            RequestSignalRpc((byte)kind);
        }

        [Rpc(SendTo.Server, Delivery = RpcDelivery.Reliable, InvokePermission = RpcInvokePermission.Owner)]
        private void RequestSignalRpc(byte requestedKind, RpcParams rpc = default)
        {
            if (!IsServer || !IsSpawned || SocialVisibilitySystem.IsSocial(NetworkManager)) return;
            ulong sender = rpc.Receive.SenderClientId;
            var session = NetworkManager.GetComponent<MatchSession>();
            bool approved = sender == OwnerClientId && NetworkManager.ConnectedClients.TryGetValue(sender, out var client)
                && client.PlayerObject == NetworkObject && session != null && session.TryGetApprovedPlayer(sender, out _)
                && !string.IsNullOrEmpty(character.AuthoritativeActorId);
            var console = SignalConsole.Current;
            bool available = console != null && console.isActiveAndEnabled;
            double now = Time.realtimeSinceStartupAsDouble;
            var rejection = StationSignalRules.Validate(available, approved,
                GetComponent<NetworkRoomMembership>().CurrentRoomId == RoomId.MainHall,
                available ? ((Vector2)capsule.bounds.center - console.InteractionPosition).sqrMagnitude : double.PositiveInfinity,
                now, ownerReadyAt, available ? console.ReadyAt : 0, requestedKind);
            if (rejection != StationSignalRules.Rejection.None)
            {
                // Bound response amplification from repeated invalid requests.
                if (approved && now >= nextRejectionAt)
                { nextRejectionAt = now + .5; RejectSignalRpc((byte)rejection); }
                return;
            }
            ownerReadyAt = now + StationSignalRules.PlayerCooldownSeconds;
            StationSignalEvent signal = console.CreateAuthoritativeSignal((StationSignalKind)requestedKind,
                character.AuthoritativeActorId, now);
            // NGO reliable delivery to the existing visible player observers. The
            // current game makes every player station-visible; no private events
            // should ever reuse this explicitly station-wide broadcast path.
            ReceiveSignalRpc(new FixedString64Bytes(signal.SignalId), (byte)signal.SignalKind,
                new FixedString64Bytes(signal.SourceActorId), new FixedString64Bytes(signal.TargetRoomId),
                new FixedString512Bytes(signal.Message), signal.ServerUnixMs, signal.ServerRevision, (byte)signal.Audience);
        }

        [Rpc(SendTo.Owner, Delivery = RpcDelivery.Reliable, InvokePermission = RpcInvokePermission.Server)]
        private void RejectSignalRpc(byte reason)
        {
            InteractionStatus = (StationSignalRules.Rejection)reason switch
            {
                StationSignalRules.Rejection.Cooldown => "Please wait two seconds between signals.",
                StationSignalRules.Rejection.TooFar => "Move closer to the Signal Console.",
                _ => "The station could not accept this interaction."
            };
        }

        [Rpc(SendTo.ClientsAndHost, Delivery = RpcDelivery.Reliable, InvokePermission = RpcInvokePermission.Server)]
        private void ReceiveSignalRpc(FixedString64Bytes signalId, byte kind, FixedString64Bytes sourceActor,
            FixedString64Bytes targetRoom, FixedString512Bytes message, long serverUnixMs, ulong revision, byte audience)
        {
            var signal = new StationSignalEvent
            {
                SignalId = signalId.ToString(), SignalKind = (StationSignalKind)kind, SourceActorId = sourceActor.ToString(),
                TargetRoomId = targetRoom.ToString(), Message = message.ToString(), ServerUnixMs = serverUnixMs,
                ServerRevision = revision, Audience = (StationSignalAudience)audience
            };
            SignalConsole.Current?.ReceiveAuthoritativeSignal(signal);
            if (IsOwner) InteractionStatus = "Station confirmed: " + (signal.SignalKind == StationSignalKind.UrgentRecall ? "Urgent Recall" : "Routine Notice");
        }

        public override void OnNetworkDespawn()
        { if (LocalOwner == this) LocalOwner = null; InteractionStatus = ""; }
    }
}
