using UnityEngine;

namespace Actor.Characters
{
    [RequireComponent(typeof(Animator), typeof(SpriteRenderer))]
    public sealed class CharacterAnimationView : MonoBehaviour
    {
        [SerializeField] private CharacterMotor2D motor;
        private Animator animator;
        private SpriteRenderer spriteRenderer;
        private int currentState;
        private FacingDirection currentFacing = (FacingDirection)(-1);
        private bool currentWalking;
        private ICharacterMotionState motionState;

        public void SetMotionState(ICharacterMotionState state) => motionState = state;

        private void Awake()
        {
            animator = GetComponent<Animator>();
            spriteRenderer = GetComponent<SpriteRenderer>();
        }

        private void Update()
        {
            ICharacterMotionState motion = motionState ?? motor;
            FacingDirection facing = motion.Facing;
            spriteRenderer.flipX = facing == FacingDirection.UpLeft || facing == FacingDirection.DownLeft;
            if (facing == FacingDirection.UpLeft) facing = FacingDirection.UpRight;
            if (facing == FacingDirection.DownLeft) facing = FacingDirection.DownRight;
            bool walking = motion.IsMoving;
            if (facing != currentFacing || walking != currentWalking)
            {
                int state = Animator.StringToHash("Base Layer." + (walking ? "Walk_" : "Idle_") + facing);
                float phase = walking && currentWalking && currentState != 0
                    ? animator.GetCurrentAnimatorStateInfo(0).normalizedTime % 1f : 0f;
                animator.Play(state, 0, phase);
                currentState = state;
                currentFacing = facing;
                currentWalking = walking;
            }
            animator.speed = walking ? Mathf.Clamp(motion.Velocity.magnitude / motion.MaxSpeed, 0.25f, 1f) : 1f;
        }
    }
}
