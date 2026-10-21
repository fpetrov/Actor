using System;
using System.Collections.Generic;
using Actor.Characters;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    // Public status and private role evidence use distinct typed payloads. Every
    // delivery targets the chosen owner's player object, never a shared variable.
    [RequireComponent(typeof(NetworkCharacter), typeof(NetworkRoomMembership))]
    public sealed class NetworkInvestigation : NetworkBehaviour
    {
        private enum Kind : byte { Role, Roster, Cooling, View, RoleResult, Started, Changed, Diagnostic, Security, Repair, Report, Action }
        internal static NetworkInvestigation LocalOwner { get; private set; }
        public OwnRoleObservation Role { get; private set; } = new();
        public DutyRosterObservation Roster { get; private set; } = new();
        public CoolingStateObservation Cooling { get; private set; } = new();
        private OwnInvestigationViewObservation view = new();
        public event Action<IMessage> PermittedEvent;
        internal string LastAction { get; private set; } = "";
        internal long DiagnosticEvidenceCount { get; private set; }
        internal long SecurityEvidenceCount { get; private set; }
        internal long RepairCompletedCount { get; private set; }
        internal long ReportsValidatedCount { get; private set; }
        internal long ReportsRejectedCount { get; private set; }
        internal long RejectedActionsCount { get; private set; }
        internal long IncidentsStartedCount { get; private set; }
        private double tokens = 8, tokenAt, nextUnavailable, serverUnavailableAt;
        internal bool Human => IsSpawned && IsClient && IsOwner && GetComponent<PlayerInputSource>().isActiveAndEnabled;
        private string Self => GetComponent<NetworkCharacter>().AuthoritativeActorId;
        // NGO spawn and the first owner-targeted state RPC are independent.
        // Never advertise empty placeholder payloads as usable observations.
        internal bool HasOwnState => Role.ActorId == Self && Role.MatchId.Length > 0 && Role.Role != CrewRole.Unspecified
            && Roster.MatchId == Role.MatchId && Cooling.MatchId == Role.MatchId
            && view.MatchId == Role.MatchId && view.RecipientActorId == Self;

        public override void OnNetworkSpawn()
        {
            if (IsClient && IsOwner) LocalOwner = this;
            tokenAt = Time.realtimeSinceStartupAsDouble;
        }

        public OwnInvestigationViewObservation CaptureOwnView()
        {
            var copy = view.Clone();
            if (copy.RecipientActorId != Self || copy.ExpiresAtUnixMs <= DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                || (copy.Diagnostic != null && Role.Role != CrewRole.Engineer) || (copy.Security != null && Role.Role != CrewRole.Sheriff))
            { copy.ClearEvidence(); copy.ExpiresAtUnixMs = 0; }
            return copy;
        }

        public bool Submit(IMessage command)
        {
            if (SocialVisibilitySystem.IsSocial(NetworkManager) || !IsSpawned || !IsOwner || !IsClient || command == null || command.CalculateSize() > 2048) return false;
            byte kind = command switch
            {
                SelectRoleCommand _ => 1, InspectCoolingCommand _ => 2, InspectSecurityRecordsCommand _ => 3,
                RepairCoolingCommand _ => 4, CancelCoolingRepairCommand _ => 5, MaintenanceOverrideCommand _ => 6,
                SubmitInvestigationReportCommand _ => 7, CancelInvestigationCommand _ => 8, _ => (byte)0
            };
            if (kind == 0) return false;
            RequestRpc(kind, command.ToByteArray()); return true;
        }

        private bool Approved(RpcParams rpc)
        {
            if (SocialVisibilitySystem.IsSocial(NetworkManager) || !IsServer || !IsSpawned || rpc.Receive.SenderClientId != OwnerClientId) return false;
            var session = NetworkManager.GetComponent<MatchSession>();
            return session != null && session.TryGetApprovedPlayer(OwnerClientId, out _)
                && NetworkManager.ConnectedClients.TryGetValue(OwnerClientId, out var client) && client.PlayerObject == NetworkObject;
        }

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
                    1 => SelectRoleCommand.Parser.ParseFrom(bytes), 2 => InspectCoolingCommand.Parser.ParseFrom(bytes),
                    3 => InspectSecurityRecordsCommand.Parser.ParseFrom(bytes), 4 => RepairCoolingCommand.Parser.ParseFrom(bytes),
                    5 => CancelCoolingRepairCommand.Parser.ParseFrom(bytes), 6 => MaintenanceOverrideCommand.Parser.ParseFrom(bytes),
                    7 => SubmitInvestigationReportCommand.Parser.ParseFrom(bytes), 8 => CancelInvestigationCommand.Parser.ParseFrom(bytes), _ => null
                };
                if (command != null) IncidentSession.Current?.Request(Self, command);
            }
            catch (InvalidProtocolBufferException) { /* Invalid untrusted bytes never reach the authority. */ }
        }

        public void RuntimeUnavailable()
        {
            if (!IsSpawned || !IsOwner || !IsClient || Time.realtimeSinceStartupAsDouble < nextUnavailable) return;
            nextUnavailable = Time.realtimeSinceStartupAsDouble + 1;
            ControlUnavailableRpc(); view.ClearEvidence(); view.ExpiresAtUnixMs = 0;
        }

        [Rpc(SendTo.Server, Delivery = RpcDelivery.Reliable, InvokePermission = RpcInvokePermission.Owner)]
        private void ControlUnavailableRpc(RpcParams rpc = default)
        {
            if (!Approved(rpc) || Time.realtimeSinceStartupAsDouble < serverUnavailableAt) return;
            serverUnavailableAt = Time.realtimeSinceStartupAsDouble + 1;
            IncidentSession.Current?.ControlUnavailable(Self);
        }

        internal void Deliver(IMessage payload)
        {
            if (SocialVisibilitySystem.IsSocial(NetworkManager)) return;
            Kind kind = payload switch
            {
                OwnRoleObservation _ => Kind.Role, DutyRosterObservation _ => Kind.Roster,
                CoolingStateObservation _ => Kind.Cooling, OwnInvestigationViewObservation _ => Kind.View,
                RoleResultEvent _ => Kind.RoleResult, IncidentStartedEvent _ => Kind.Started,
                IncidentChangedEvent _ => Kind.Changed, DiagnosticEvidenceEvent _ => Kind.Diagnostic,
                SecurityEvidenceEvent _ => Kind.Security, RepairResultEvent _ => Kind.Repair,
                InvestigationResultEvent _ => Kind.Report, IncidentActionResultEvent _ => Kind.Action,
                _ => throw new ArgumentException("Unsupported incident delivery.")
            };
            ReceivePermittedRpc((byte)kind, payload.ToByteArray());
        }

        [Rpc(SendTo.Owner, Delivery = RpcDelivery.Reliable, InvokePermission = RpcInvokePermission.Server)]
        private void ReceivePermittedRpc(byte kind, byte[] bytes)
        {
            if (!IsOwner || !IsClient || bytes == null || bytes.Length > 16384) return;
            IMessage value = (Kind)kind switch
            {
                Kind.Role => OwnRoleObservation.Parser.ParseFrom(bytes), Kind.Roster => DutyRosterObservation.Parser.ParseFrom(bytes),
                Kind.Cooling => CoolingStateObservation.Parser.ParseFrom(bytes), Kind.View => OwnInvestigationViewObservation.Parser.ParseFrom(bytes),
                Kind.RoleResult => RoleResultEvent.Parser.ParseFrom(bytes), Kind.Started => IncidentStartedEvent.Parser.ParseFrom(bytes),
                Kind.Changed => IncidentChangedEvent.Parser.ParseFrom(bytes), Kind.Diagnostic => DiagnosticEvidenceEvent.Parser.ParseFrom(bytes),
                Kind.Security => SecurityEvidenceEvent.Parser.ParseFrom(bytes), Kind.Repair => RepairResultEvent.Parser.ParseFrom(bytes),
                Kind.Report => InvestigationResultEvent.Parser.ParseFrom(bytes), Kind.Action => IncidentActionResultEvent.Parser.ParseFrom(bytes), _ => null
            };
            if (value == null || MatchOf(value) != NetworkManager.GetComponent<MatchSession>().MatchId) return;
            switch (value)
            {
                case OwnRoleObservation role: ApplyRole(role); return;
                case DutyRosterObservation roster:
                    if (roster.ServerRevision >= Roster.ServerRevision) Roster = roster;
                    return;
                case CoolingStateObservation state: ApplyState(state); return;
                case OwnInvestigationViewObservation own:
                    if (own.RecipientActorId == Self && own.ServerRevision >= view.ServerRevision) view = own;
                    return;
                case RoleResultEvent result:
                    if (result.State?.ActorId != Self) return;
                    ApplyRole(result.State); LastAction = result.ReasonCode; CountRejected(result.Accepted); break;
                case IncidentStartedEvent started: ApplyState(started.State); ++IncidentsStartedCount; break;
                case IncidentChangedEvent changed: ApplyState(changed.State); break;
                case DiagnosticEvidenceEvent diagnostic:
                    if (diagnostic.RecipientActorId != Self) return;
                    if (diagnostic.Accepted) ++DiagnosticEvidenceCount;
                    LastAction = diagnostic.ReasonCode; CountRejected(diagnostic.Accepted); break;
                case SecurityEvidenceEvent security:
                    if (security.RecipientActorId != Self) return;
                    if (security.Accepted) ++SecurityEvidenceCount;
                    LastAction = security.ReasonCode; CountRejected(security.Accepted); break;
                case RepairResultEvent repair:
                    if (repair.Accepted && repair.Completed) ++RepairCompletedCount;
                    LastAction = repair.ReasonCode; CountRejected(repair.Accepted); break;
                case InvestigationResultEvent report:
                    if (report.Accepted && report.ReportValidated) ++ReportsValidatedCount;
                    if (!report.Accepted) ++ReportsRejectedCount;
                    LastAction = report.ReasonCode; CountRejected(report.Accepted); break;
                case IncidentActionResultEvent action:
                    LastAction = action.ReasonCode; CountRejected(action.Accepted); break;
            }
            PermittedEvent?.Invoke(value);
        }

        private void CountRejected(bool accepted) { if (!accepted) ++RejectedActionsCount; }
        private void ApplyRole(OwnRoleObservation role)
        {
            if (role == null || role.ActorId != Self || role.ServerRevision < Role.ServerRevision) return;
            if (role.Role != Role.Role) { view.ClearEvidence(); view.ExpiresAtUnixMs = 0; }
            Role = role;
        }
        private void ApplyState(CoolingStateObservation state)
        {
            if (state == null || state.ServerRevision < Cooling.ServerRevision) return;
            Cooling = state;
            if (view.IncidentId != state.IncidentId || state.IncidentStatus is IncidentStatus.Cancelled or IncidentStatus.Expired or IncidentStatus.Failed)
            { view.ClearEvidence(); view.ExpiresAtUnixMs = 0; }
        }
        private static string MatchOf(IMessage value) => value switch
        {
            OwnRoleObservation x => x.MatchId, DutyRosterObservation x => x.MatchId, CoolingStateObservation x => x.MatchId,
            OwnInvestigationViewObservation x => x.MatchId, RoleResultEvent x => x.MatchId,
            IncidentStartedEvent x => x.State?.MatchId, IncidentChangedEvent x => x.State?.MatchId,
            DiagnosticEvidenceEvent x => x.MatchId, SecurityEvidenceEvent x => x.MatchId,
            RepairResultEvent x => x.MatchId, InvestigationResultEvent x => x.MatchId, IncidentActionResultEvent x => x.MatchId, _ => ""
        };
        public override void OnNetworkDespawn()
        {
            if (LocalOwner == this) LocalOwner = null;
            Role = new(); Roster = new(); Cooling = new(); view = new();
        }
    }
}
