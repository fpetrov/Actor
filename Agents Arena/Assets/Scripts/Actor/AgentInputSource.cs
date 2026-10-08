using Actor.Characters;
using Actor.Protocol.Standard.V1;
using Actor.Sdk;
using UnityEngine;

namespace Actor.GameAdapter
{
    // Input intent only. The existing NetworkCharacter -> server motor owns movement.
    public sealed class AgentInputSource : MonoBehaviour, IMovementInputSource
    {
        private Vector2 movement;
        private float expiresAt = -100;
        private float commandTimeout = 0.25f;
        public void ConfigureTimeout(float seconds) => commandTimeout = Mathf.Clamp(seconds, 0.05f, 2f);
        public Vector2 ReadMovement() => isActiveAndEnabled && Time.unscaledTime < expiresAt ? movement : Vector2.zero;

        public CommandOutcome Apply(CommandContext context, Locomotion2DCommand command)
        {
            if (!float.IsFinite(command.DesiredMoveX) || !float.IsFinite(command.DesiredMoveY))
            { Clear(); return CommandOutcome.Rejected("InvalidMovement", "Movement intent must be finite."); }
            double remaining = (context.ExpiresAtUnixMs - System.DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()) / 1000d;
            if (remaining <= 0) { Clear(); return CommandOutcome.Rejected("CommandTimedOut", "Movement intent expired."); }
            movement = Vector2.ClampMagnitude(new Vector2(command.DesiredMoveX, command.DesiredMoveY), 1);
            expiresAt = Time.unscaledTime + Mathf.Min(commandTimeout, (float)remaining);
            return CommandOutcome.Accepted();
        }
        public void Clear() { movement = Vector2.zero; expiresAt = -100; }
        private void OnDisable() => Clear();
    }
}
