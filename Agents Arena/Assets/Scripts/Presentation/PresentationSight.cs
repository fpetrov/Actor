using Actor.Networking;
using Unity.Netcode;
using UnityEngine;
namespace Actor.Presentation
{
    // Presentation-only permitted eye. Never queries another player's own state.
    internal static class PresentationSight
    {
        internal static bool Observer => NetworkManager.Singleton?.GetComponent<MatchSession>()?.IsBroadcastObserver == true;
        internal static bool TryGet(out Vector2 eye, out float radius, out ulong epoch, bool strict = false)
        {
            eye = Vector2.zero; radius = 0; epoch = 0;
            if (Observer)
            {
#if !UNITY_SERVER
                return BroadcastViewPresenter.Current != null && BroadcastViewPresenter.Current.Sight(out eye, out radius, out epoch);
#else
                return false;
#endif
            }
            var manager = NetworkManager.Singleton;
            var owner = manager != null && manager.IsConnectedClient ? manager.SpawnManager.GetLocalPlayerObject() : null;
            if (owner == null || !SocialVisibilitySystem.IsSocial(manager)) return false;
            var social = owner.GetComponent<NetworkSocialMatch>();
            if (strict && (social == null || !social.HasOwnState)) return false;
            var character = owner.GetComponent<NetworkCharacter>(); var capsule = owner.GetComponent<CapsuleCollider2D>();
            if (character == null || capsule == null) return false;
            eye = (Vector2)character.PresentationPosition + capsule.offset;
            radius = social != null && social.World.VisibilityRadius > 0 ? social.World.VisibilityRadius : 3.5f;
            epoch = owner.NetworkObjectId + 1;
            return float.IsFinite(eye.x) && float.IsFinite(eye.y) && float.IsFinite(radius) && radius > 0;
        }
    }
}
