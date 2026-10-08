using System;
using UnityEngine;

namespace Actor.Presentation
{
    [RequireComponent(typeof(Camera))]
    public sealed class CameraFollow2D : MonoBehaviour
    {
        [SerializeField] private Transform target;
        [SerializeField, Min(0.01f)] private float smoothTime = 0.12f;
        [SerializeField] private Vector2 roomMin = new Vector2(-6.27f, -6.27f);
        [SerializeField] private Vector2 roomMax = new Vector2(6.27f, 6.27f);
        private Vector3 velocity;
        private Camera view;

        public void Follow(Transform character)
        {
            target = character;
            velocity = Vector3.zero;
        }

        public void SetWorldBounds(Vector2 minimum, Vector2 maximum)
        {
            if (!float.IsFinite(minimum.x) || !float.IsFinite(minimum.y)
                || !float.IsFinite(maximum.x) || !float.IsFinite(maximum.y)
                || minimum.x >= maximum.x || minimum.y >= maximum.y)
                throw new ArgumentException("Camera bounds must have finite, increasing coordinates.");
            roomMin = minimum;
            roomMax = maximum;
        }

        private void Awake() => view = GetComponent<Camera>();

        private void LateUpdate()
        {
            if (target == null) return;
            float halfHeight = view.orthographicSize;
            float halfWidth = halfHeight * view.aspect;
            Vector3 desired = new Vector3(
                ClampToRoom(target.position.x, roomMin.x, roomMax.x, halfWidth),
                ClampToRoom(target.position.y + 0.45f, roomMin.y, roomMax.y, halfHeight),
                transform.position.z);
            transform.position = Vector3.SmoothDamp(transform.position, desired, ref velocity,
                smoothTime, Mathf.Infinity, Time.deltaTime);
        }

        private static float ClampToRoom(float value, float min, float max, float halfView)
            => max - min <= halfView * 2f ? (min + max) * 0.5f
                : Mathf.Clamp(value, min + halfView, max - halfView);
    }
}
