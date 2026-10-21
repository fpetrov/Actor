using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    public enum SocialVisibilityKind { Actor, Body, Interactable }

    // Every spatially private NetworkObject uses this component. Configuration
    // happens before NGO sets its initial observers, not after its first spawn.
    [DisallowMultipleComponent, RequireComponent(typeof(NetworkObject))]
    public sealed class SocialVisibilityMember : NetworkBehaviour
    {
        [SerializeField] private SocialVisibilityKind kind;
        private SocialVisibilitySystem visibility;
        public SocialVisibilityKind Kind => kind;
        public void Configure(SocialVisibilityKind value) => kind = value;

        protected override void OnNetworkPreSpawn(ref NetworkManager networkManager)
        {
            if (!SocialVisibilitySystem.IsSocial(networkManager)) return;
            var target = GetComponent<NetworkObject>();
            visibility = SocialVisibilitySystem.Ensure(networkManager);
            // NGO 2.13.3 AddPlayerObject bypasses CheckObjectVisibility when this
            // is true. In client/server mode false also omits the owner, whose
            // explicit grant is handled by scene sync or the next safe refresh.
            target.SpawnWithObservers = false;
            target.CheckObjectVisibility = client => visibility != null && visibility.CanObserve(client, target);
        }

        public override void OnNetworkSpawn()
        {
            if (IsServer && SocialVisibilitySystem.IsSocial(NetworkManager))
            {
                visibility ??= SocialVisibilitySystem.Ensure(NetworkManager);
                visibility.Register(this);
                // Do not Show here: manual Spawn() sends initial observers after
                // OnNetworkSpawn returns, which would duplicate a queued Show.
            }
        }

        public override void OnNetworkDespawn()
        {
            if (visibility != null) visibility.Unregister(this);
        }
    }
}
