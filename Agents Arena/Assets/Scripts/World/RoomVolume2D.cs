using System;
using System.Collections.Generic;
using UnityEngine;

namespace Actor.World
{
    [RequireComponent(typeof(PolygonCollider2D))]
    public sealed class RoomVolume2D : MonoBehaviour
    {
        [SerializeField] private string roomId = RoomId.MainHall;
        private static readonly List<RoomVolume2D> activeVolumes = new();
        private PolygonCollider2D volume;

        public string Id => roomId;

        public void Configure(string id, Vector2[] localFloorContour)
        {
            if (string.IsNullOrWhiteSpace(id)) throw new ArgumentException("Room ID cannot be empty.", nameof(id));
            if (localFloorContour == null || localFloorContour.Length < 3)
                throw new ArgumentException("A room volume requires a floor polygon.", nameof(localFloorContour));
            roomId = id;
            volume = GetComponent<PolygonCollider2D>();
            volume.isTrigger = true;
            volume.pathCount = 1;
            volume.SetPath(0, localFloorContour);
        }

        private void OnEnable()
        {
            volume = GetComponent<PolygonCollider2D>();
            volume.isTrigger = true;
            if (!activeVolumes.Contains(this)) activeVolumes.Add(this);
        }

        private void OnDisable() => activeVolumes.Remove(this);

        public bool Contains(Vector2 physicalCenter)
        {
            if (volume == null) volume = GetComponent<PolygonCollider2D>();
            return isActiveAndEnabled && volume != null && volume.enabled && volume.OverlapPoint(physicalCenter);
        }

        public static bool TryResolve(Vector2 physicalCenter, string currentRoom, out string resolvedRoom)
        {
            // Retain the current identity on a shared polygon edge, avoiding a transition
            // caused only by enumeration order when a character stops in the doorway.
            foreach (var room in activeVolumes)
                if (room.Id == currentRoom && room.Contains(physicalCenter))
                { resolvedRoom = currentRoom; return true; }

            resolvedRoom = null;
            foreach (var room in activeVolumes)
                if (room.Contains(physicalCenter) && (resolvedRoom == null || string.CompareOrdinal(room.Id, resolvedRoom) < 0))
                    resolvedRoom = room.Id;
            return resolvedRoom != null;
        }
    }
}
