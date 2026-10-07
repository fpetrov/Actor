using UnityEngine;

namespace Actor.Characters
{
    [RequireComponent(typeof(Rigidbody2D))]
    public sealed class CharacterMotor2D : MonoBehaviour, ICharacterMotionState
    {
        [SerializeField, Min(0.1f)] private float maxSpeed = 2.6f;
        [SerializeField, Min(0.1f)] private float acceleration = 22f;
        [SerializeField, Min(0.1f)] private float deceleration = 30f;
        private Rigidbody2D body;
        private Vector2 desiredMovement;

        public float MaxSpeed => maxSpeed;
        public Vector2 Position => body.position;
        public Vector2 Velocity => body.linearVelocity;
        public bool IsMoving => Velocity.sqrMagnitude > 0.0025f;
        public FacingDirection Facing { get; private set; } = FacingDirection.Down;

        private void Awake() => body = GetComponent<Rigidbody2D>();

        public void SetDesiredMovement(Vector2 movement)
        {
            if (!float.IsFinite(movement.x) || !float.IsFinite(movement.y)) movement = Vector2.zero;
            desiredMovement = Vector2.ClampMagnitude(movement, 1f);
            if (desiredMovement.sqrMagnitude > 0.01f)
            {
                Facing = FacingFor(desiredMovement);
            }
        }

        private void FixedUpdate()
        {
            body.linearVelocity = StepVelocity(body.linearVelocity, desiredMovement, Time.fixedDeltaTime);
        }

        // Shared by authoritative physics and the owner's presentation prediction.
        public Vector2 StepVelocity(Vector2 current, Vector2 input, float deltaTime)
        {
            float rate = input.sqrMagnitude > 0.0001f ? acceleration : deceleration;
            return Vector2.MoveTowards(current, input * maxSpeed, rate * deltaTime);
        }

        public static FacingDirection FacingFor(Vector2 input)
        {
            float angle = Mathf.Atan2(input.x, -input.y) * Mathf.Rad2Deg;
            return (FacingDirection)((Mathf.RoundToInt(angle / 45f) + 8) % 8);
        }

        private void OnDisable()
        {
            desiredMovement = Vector2.zero;
            if (body != null) body.linearVelocity = Vector2.zero;
        }
    }
}
