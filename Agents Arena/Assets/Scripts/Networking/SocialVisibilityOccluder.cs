using UnityEngine;

namespace Actor.Networking
{
    // Marker on authored physical wall/cabinet colliders, not on player bodies.
    [DisallowMultipleComponent, RequireComponent(typeof(Collider2D))]
    public sealed class SocialVisibilityOccluder : MonoBehaviour { }
}
