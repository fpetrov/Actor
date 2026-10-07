using System.Collections.Generic;
using Actor.Characters;
using UnityEngine;

namespace Actor.Networking
{
    // Presentation only: never writes to the network root or its Rigidbody2D.
    // Replays unacknowledged intent from a post-physics server snapshot.
    public sealed class OwnerMovementPrediction
    {
        private struct InputSample { public uint Sequence; public float Time; public Vector2 Input; }
        private const float MaxReplaySeconds = 0.5f;
        private const float SnapshotTimeout = 0.5f;
        private const float Skin = 0.003f;
        private readonly List<InputSample> history = new(64);
        private readonly RaycastHit2D[] hits = new RaycastHit2D[32];
        private readonly CharacterMotor2D motor;
        private readonly CapsuleCollider2D capsule;
        private readonly ContactFilter2D filter;
        private Vector2 position, correction;
        private float lastSnapshotAt, lastAdvancedAt;
        private uint lastSnapshot;
        private bool hasSnapshot;

        public Vector2 Velocity { get; private set; }
        public FacingDirection Facing { get; private set; } = FacingDirection.Down;
        public Vector2 Position => position;
        public Vector2 RenderedPosition
        {
            get
            {
                Vector2 unused = Vector2.zero;
                return Sweep(position, correction, ref unused);
            }
        }

        public OwnerMovementPrediction(CharacterMotor2D motor, CapsuleCollider2D capsule, Vector2 spawn)
        {
            this.motor = motor;
            this.capsule = capsule;
            position = spawn;
            lastSnapshotAt = lastAdvancedAt = Time.unscaledTime;
            filter = new ContactFilter2D { useTriggers = false };
            filter.SetLayerMask(Physics2D.GetLayerCollisionMask(capsule.gameObject.layer));
        }

        public void Record(uint sequence, Vector2 input, float now)
        {
            history.Add(new InputSample { Sequence = sequence, Input = input, Time = now });
            if (history.Count > 128) history.RemoveAt(0);
        }

        public void Advance(Vector2 input, float now)
        {
            float deltaTime = Mathf.Max(0, now - lastAdvancedAt);
            lastAdvancedAt = now;
            if (now - lastSnapshotAt > SnapshotTimeout)
            {
                Velocity = Vector2.zero;
                correction = Vector2.zero;
                return;
            }
            Simulate(input, Mathf.Min(deltaTime, 0.1f));
            correction *= Mathf.Exp(-30f * deltaTime);
        }

        public void Reconcile(uint snapshot, uint acknowledgedInput, float appliedSeconds,
            Vector2 serverPosition, Vector2 serverVelocity, FacingDirection serverFacing, float now)
        {
            if (hasSnapshot && unchecked((int)(snapshot - lastSnapshot)) <= 0) return;
            bool recovering = now - lastSnapshotAt > SnapshotTimeout;
            hasSnapshot = true;
            lastSnapshot = snapshot;
            lastSnapshotAt = now;
            lastAdvancedAt = now;
            Vector2 oldRendered = RenderedPosition;
            position = serverPosition;
            Velocity = serverVelocity;
            Facing = serverFacing;

            int acknowledged = history.FindIndex(sample => sample.Sequence == acknowledgedInput);
            // An initial idle snapshot may precede the first command.
            if (acknowledged >= 0 || acknowledgedInput == 0)
            {
                if (acknowledged > 0) history.RemoveRange(0, acknowledged);
                if (history.Count > 0)
                {
                    float start = acknowledged >= 0 ? history[0].Time + appliedSeconds : history[0].Time;
                    // Bound work and visual lead during stalls or packet loss.
                    // Retain the newest changes (especially release) when bounding replay.
                    start = Mathf.Max(start, now - MaxReplaySeconds);
                    float end = now;
                    Vector2 input = history[0].Input;
                    foreach (var sample in history)
                    {
                        if (sample.Time <= start) { input = sample.Input; continue; }
                        if (sample.Time >= end) break;
                        Simulate(input, sample.Time - start);
                        start = sample.Time;
                        input = sample.Input;
                    }
                    if (end > start) Simulate(input, end - start);
                }
            }

            Vector2 difference = oldRendered - position;
            correction = recovering || difference.sqrMagnitude > 0.75f * 0.75f ? Vector2.zero : difference;
        }

        private void Simulate(Vector2 input, float duration)
        {
            if (input.sqrMagnitude > 0.01f) Facing = CharacterMotor2D.FacingFor(input);
            while (duration > 0.00001f)
            {
                float step = Mathf.Min(duration, Time.fixedDeltaTime);
                Vector2 nextVelocity = motor.StepVelocity(Velocity, input, step);
                position = Sweep(position, nextVelocity * step, ref nextVelocity);
                Velocity = nextVelocity;
                duration -= step;
            }
        }

        private Vector2 Sweep(Vector2 from, Vector2 delta, ref Vector2 velocity)
        {
            Vector2 scale = capsule.transform.lossyScale;
            Vector2 size = Vector2.Scale(capsule.size, new Vector2(Mathf.Abs(scale.x), Mathf.Abs(scale.y)));
            Vector2 offset = Vector2.Scale(capsule.offset, scale);
            for (int iteration = 0; iteration < 3 && delta.sqrMagnitude > 0.00000001f; iteration++)
            {
                float distance = delta.magnitude;
                Vector2 direction = delta / distance;
                int count = Physics2D.CapsuleCast(from + offset, size, capsule.direction, 0,
                    direction, filter, hits, distance + Skin);
                int nearest = -1;
                float hitDistance = distance + Skin;
                for (int i = 0; i < count; i++)
                {
                    // Static room geometry is predictable. Other players remain server simulated.
                    var hit = hits[i];
                    if (hit.collider == capsule || (hit.rigidbody != null && hit.rigidbody.bodyType != RigidbodyType2D.Static)) continue;
                    // A capsule cast can report a nearby wall while travelling parallel to it.
                    // Such a contact must not consume all sweep iterations without moving.
                    if (Vector2.Dot(direction, hit.normal) >= -0.0001f) continue;
                    if (hit.distance < hitDistance) { nearest = i; hitDistance = hit.distance; }
                }
                if (nearest < 0) { from += delta; break; }
                float travel = Mathf.Max(0, hitDistance - Skin);
                from += direction * travel;
                Vector2 normal = hits[nearest].normal;
                delta -= direction * travel;
                delta -= normal * Mathf.Min(0, Vector2.Dot(delta, normal));
                velocity -= normal * Mathf.Min(0, Vector2.Dot(velocity, normal));
            }
            return from;
        }
    }
}
