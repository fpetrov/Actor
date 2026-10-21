using System;
using UnityEngine;

namespace Actor.World
{
    public sealed class SocialTerminal2D : MonoBehaviour
    {
        [SerializeField] private string entityId, roomId, kind, taskId;
        [SerializeField] private Vector2 approachPoint;
        public string EntityId => entityId;
        public string RoomId => roomId;
        public string Kind => kind;
        public string TaskId => taskId ?? "";
        public Vector2 InteractionPoint => transform.position;
        public Vector2 ApproachPoint => approachPoint;
        public float Radius => 1.25f;

        public void Configure(string id, string room, string terminalKind, string task, Vector2 rootApproach)
        {
            if (string.IsNullOrWhiteSpace(id) || string.IsNullOrWhiteSpace(room)
                || terminalKind is not ("task" or "meeting" or "life-support")
                || terminalKind == "task" && string.IsNullOrWhiteSpace(task)) throw new ArgumentException("Invalid authored Social terminal.");
            entityId = id; roomId = room; kind = terminalKind;
            taskId = terminalKind == "task" ? task : ""; approachPoint = rootApproach;
        }
    }
}
