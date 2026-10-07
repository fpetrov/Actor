using UnityEngine;

namespace Actor.Characters
{
    public interface ICharacterMotionState
    {
        Vector2 Velocity { get; }
        FacingDirection Facing { get; }
        bool IsMoving { get; }
        float MaxSpeed { get; }
    }
}
