using System;
using System.Collections.Generic;
using Actor.Protocol.Standard.Communication.V1;
using Actor.World;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    // Scene lifetime matches the one NGO gameplay world. Only the dedicated
    // server creates Authority; clients retain solely their owner-targeted data.
    public sealed class CooperativeSession : MonoBehaviour
    {
        public static CooperativeSession Current { get; private set; }
        private CooperativeAuthority authority;
        private double nextRefresh;
        private readonly Dictionary<string, NetworkCooperation> endpoints = new();
        private void OnEnable() => Current = this;
        private void OnDisable() { if (Current == this) Current = null; authority = null; endpoints.Clear(); }
        private long Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        private void Update()
        {
            var manager = NetworkManager.Singleton;
            if (manager == null || !manager.IsServer || Time.realtimeSinceStartupAsDouble < nextRefresh) return;
            nextRefresh = Time.realtimeSinceStartupAsDouble + .25;
            Refresh();
        }
        private void Refresh()
        {
            var manager = NetworkManager.Singleton;
            if (manager == null || !manager.IsServer) return;
            var session = manager.GetComponent<MatchSession>();
            if (string.IsNullOrEmpty(session.MatchId)) return;
            authority ??= new CooperativeAuthority(session.MatchId);
            CooperativeTerminal2D diagnostic = null, calibration = null;
            foreach (var terminal in FindObjectsByType<CooperativeTerminal2D>())
            {
                var entity = terminal.GetComponent<WorldEntity>();
                if (!terminal.isActiveAndEnabled || entity == null || entity.EntityId != terminal.EntityId) continue;
                if (terminal.Kind == CooperativeTerminalKind.Diagnostic) diagnostic = terminal; else calibration = terminal;
            }
            authority.SetTerminals(diagnostic != null, diagnostic != null ? diagnostic.InteractionPoint.x : 0, diagnostic != null ? diagnostic.InteractionPoint.y : 0,
                calibration != null, calibration != null ? calibration.InteractionPoint.x : 0, calibration != null ? calibration.InteractionPoint.y : 0);
            var participants = new List<CooperativeAuthority.Participant>(); endpoints.Clear();
            foreach (var pair in manager.ConnectedClients)
            {
                if (!session.TryGetApprovedPlayer(pair.Key, out _) || pair.Value.PlayerObject == null) continue;
                var player = pair.Value.PlayerObject.GetComponent<NetworkCharacter>();
                var endpoint = pair.Value.PlayerObject.GetComponent<NetworkCooperation>();
                if (player == null || !player.IsSpawned || endpoint == null || !endpoint.IsSpawned || string.IsNullOrEmpty(player.AuthoritativeActorId)) continue;
                var center = player.GetComponent<CapsuleCollider2D>().bounds.center;
                participants.Add(new CooperativeAuthority.Participant { Id = player.AuthoritativeActorId, Name = player.PlayerName,
                    Room = player.GetComponent<NetworkRoomMembership>().CurrentRoomId, X = center.x, Y = center.y });
                endpoints[player.AuthoritativeActorId] = endpoint;
            }
            Dispatch(authority.Refresh(participants, Now));
        }
        internal void Request(string actorId, IMessage command)
        { Refresh(); if (authority != null) Dispatch(authority.Execute(actorId, command, Now)); }
        internal void Acknowledge(string actorId, string messageId, string matchId)
        { if (authority != null) Dispatch(authority.Acknowledge(actorId, messageId, matchId, Now)); }
        internal void ControlUnavailable(string actorId)
        { if (authority != null) Dispatch(authority.ControlUnavailable(actorId)); }
        private void Dispatch(IEnumerable<CooperativeAuthority.Delivery> deliveries)
        {
            foreach (var delivery in deliveries)
            {
                if (!endpoints.TryGetValue(delivery.Recipient, out var target) || target == null || !target.IsSpawned) continue;
                target.Deliver(delivery.Payload);
                // Privacy-safe evidence: no chat text, secret, diagnostic object
                // dump or arbitrary exception contents enter server logs.
                if (delivery.Payload is MessageReceivedEvent m)
                    Debug.Log("ACTOR_COMM message=" + m.MessageId + " sender=" + m.SenderActorId + " recipient=" + m.RecipientActorId + " channel=" + m.ChannelId + " sensitive=" + m.Sensitive);
                else if (delivery.Payload is DiagnosticResultEvent d)
                    Debug.Log("ACTOR_DIAGNOSTIC actor=" + delivery.Recipient + " accepted=" + d.Accepted + " revision=" + d.ServerRevision);
                else if (delivery.Payload is CalibrationResultEvent r)
                    Debug.Log("ACTOR_CALIBRATION actor=" + delivery.Recipient + " action=" + r.Action + " accepted=" + r.Accepted + " reason=" + r.ReasonCode + " status=" + r.Status + " revision=" + r.ServerRevision);
            }
        }
    }
}
