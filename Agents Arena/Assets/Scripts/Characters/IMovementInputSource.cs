using UnityEngine;

namespace Actor.Characters
{
    public interface IMovementInputSource
    {
        Vector2 ReadMovement();
    }
}
