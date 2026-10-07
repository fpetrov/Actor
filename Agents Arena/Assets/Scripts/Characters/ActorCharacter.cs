using System;
using UnityEngine;

namespace Actor.Characters
{
    [RequireComponent(typeof(CharacterMotor2D))]
    [DefaultExecutionOrder(-100)]
    public sealed class ActorCharacter : MonoBehaviour
    {
        [SerializeField] private string actorId;
        [SerializeField] private string initialRoomId;
        [SerializeField] private CharacterRole role;
        [SerializeField] private MonoBehaviour inputSource;
        private IMovementInputSource commands;
        private CharacterMotor2D motor;
        private ICharacterMotionState motionState;

        public string ActorId => actorId;
        public string RoomId { get; private set; }

        private void Awake()
        {
            // Scene instances may have authored IDs; fresh prefab instances receive a new ID.
            if (string.IsNullOrWhiteSpace(actorId)) actorId = Guid.NewGuid().ToString("N");
            RoomId = initialRoomId;
            motor = GetComponent<CharacterMotor2D>();
            SetInputSource(inputSource as IMovementInputSource);
        }

        private void Update() => motor.SetDesiredMovement(commands?.ReadMovement() ?? Vector2.zero);

        public void SetInputSource(IMovementInputSource source) => commands = source;
        public void SetMotionState(ICharacterMotionState state) => motionState = state;
        public void SetIdentity(string id)
        {
            if (string.IsNullOrWhiteSpace(id)) throw new ArgumentException("Actor ID cannot be empty.", nameof(id));
            actorId = id;
        }
        public void SetCurrentRoom(string roomId) => RoomId = roomId;
        public CharacterState CaptureState()
        {
            ICharacterMotionState state = motionState ?? motor;
            return new CharacterState(actorId, motor.Position, state.Velocity,
                RoomId, state.Facing, state.IsMoving, role, InteractionState.None);
        }
    }
}
