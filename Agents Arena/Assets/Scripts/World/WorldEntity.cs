using System;
using UnityEngine;

namespace Actor.World
{
    public sealed class WorldEntity : MonoBehaviour
    {
        [SerializeField] private string entityId;
        [SerializeField] private string roomId;
        public string EntityId => entityId;
        public string RoomId => roomId;
        public Vector2 Position => transform.position;

        private void Awake()
        {
            if (string.IsNullOrWhiteSpace(entityId)) entityId = Guid.NewGuid().ToString("N");
        }
    }
}
