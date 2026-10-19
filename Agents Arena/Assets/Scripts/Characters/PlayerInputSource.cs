using UnityEngine;
using UnityEngine.InputSystem;

namespace Actor.Characters
{
    public sealed class PlayerInputSource : MonoBehaviour, IMovementInputSource
    {
        [SerializeField] private InputActionAsset actions;
        private InputAction move;
        internal bool TextEntryFocused { get; set; }

        private void Awake()
        {
            // Each character owns its action lifetime, even when sharing the same asset.
            move = actions.FindAction("Player/Move", true).Clone();
        }

        private void OnEnable() => move?.Enable();
        private void OnDisable() => move?.Disable();
        private void OnDestroy() => move?.Dispose();
        public Vector2 ReadMovement() => isActiveAndEnabled && Application.isFocused && !TextEntryFocused
            ? move.ReadValue<Vector2>() : Vector2.zero;
    }
}
