using UnityEngine;

namespace Actor.World
{
    [ExecuteAlways]
    public sealed class RoomWayfinding2D : MonoBehaviour
    {
        [System.Serializable] public struct Marker { public Vector2 world; public string text; }
        [SerializeField] private Marker[] markers;
        public void Configure(Marker[] values) => markers = values;
#if UNITY_EDITOR || !UNITY_SERVER
        private GUIStyle style;
        private void OnGUI()
        {
            if (Camera.main == null || markers == null) return;
            if (style == null) style = new GUIStyle(GUI.skin.box) { fontSize = 13, alignment = TextAnchor.MiddleCenter };
            foreach (var marker in markers)
            {
                Vector3 screen = Camera.main.WorldToScreenPoint(marker.world);
                if (screen.z <= 0 || screen.x < 0 || screen.x > Screen.width || screen.y < 0 || screen.y > Screen.height) continue;
                GUI.Label(new Rect(screen.x - 78,Screen.height-screen.y-11,156,22),marker.text,style);
            }
        }
#endif
    }
}
