using AgentsArena.ActorProtocol.V1;
using Unity.Collections;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    // A body contains visible identification only. It never carries a killer,
    // alignment, timestamp, global sequence, or an owner-private component.
    [RequireComponent(typeof(NetworkObject), typeof(SocialVisibilityMember))]
    public sealed class SocialBody : NetworkBehaviour
    {
        private readonly NetworkVariable<FixedString64Bytes> bodyId = new();
        private readonly NetworkVariable<FixedString64Bytes> victimId = new();
        private readonly NetworkVariable<FixedString128Bytes> victimName = new();
        private readonly NetworkVariable<byte> cosmeticIndex = new();
        private SocialVisibleBody pending;
        private byte pendingCosmeticIndex;
        public string BodyId => pending?.BodyId ?? bodyId.Value.ToString();
        public string VictimId => victimId.Value.ToString();
        public string VictimName => victimName.Value.ToString();
        public int CosmeticIndex => IsServer && pending != null ? pendingCosmeticIndex : cosmeticIndex.Value;
        internal void Configure(SocialVisibleBody state, int approvedCosmeticIndex)
        { pending = state.Clone(); pendingCosmeticIndex = (byte)Mathf.Clamp(approvedCosmeticIndex, 0, 5); }
        public override void OnNetworkSpawn()
        {
            if (IsServer && pending != null)
            {
                bodyId.Value = new FixedString64Bytes(pending.BodyId);
                victimId.Value = new FixedString64Bytes(pending.VictimActorId);
                victimName.Value = new FixedString128Bytes(pending.VictimDisplayName);
                cosmeticIndex.Value = pendingCosmeticIndex;
            }
        }
    }
}
