using UnityEngine;

namespace Actor.Characters
{
    public enum FacingDirection { Down, DownRight, Right, UpRight, Up, UpLeft, Left, DownLeft }
    public enum CharacterRole { Unassigned }
    public enum InteractionState { None }

    public readonly struct CharacterState
    {
        public string ActorId { get; }
        public Vector2 Position { get; }
        public Vector2 Velocity { get; }
        public string RoomId { get; }
        public FacingDirection Facing { get; }
        public bool IsMoving { get; }
        public CharacterRole Role { get; }
        public InteractionState Interaction { get; }

        public CharacterState(string actorId, Vector2 position, Vector2 velocity, string roomId,
            FacingDirection facing, bool isMoving, CharacterRole role, InteractionState interaction)
        {
            ActorId = actorId;
            Position = position;
            Velocity = velocity;
            RoomId = roomId;
            Facing = facing;
            IsMoving = isMoving;
            Role = role;
            Interaction = interaction;
        }
    }
}
