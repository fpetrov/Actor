using UnityEngine;

namespace Actor.World
{
    public enum IncidentTerminalKind { Cooling, Security }
    public sealed class IncidentTerminal2D : MonoBehaviour
    {
        [SerializeField] private IncidentTerminalKind kind;
        public IncidentTerminalKind Kind => kind;
        public Vector2 InteractionPoint => (Vector2)transform.position + (kind == IncidentTerminalKind.Cooling
            ? new Vector2(.55f, -.54f) : new Vector2(.3f, -.25f));
        public float Radius => kind == IncidentTerminalKind.Cooling ? 1.25f : 1.6f;
        public string EntityId => kind == IncidentTerminalKind.Cooling ? "engineering-cooling-01" : "main-hall-security-01";
        public string RoomId => kind == IncidentTerminalKind.Cooling ? "engineering" : "main-hall";
        public string DisplayName => kind == IncidentTerminalKind.Cooling ? "Cooling Control / Maintenance" : "Security Records";
        public void Configure(IncidentTerminalKind value) => kind = value;
#if !UNITY_SERVER
        private void OnGUI()
        {
            if (Camera.main == null || Actor.Networking.NetworkInvestigation.LocalOwner == null) return;
            Vector3 p = Camera.main.WorldToScreenPoint(transform.position + Vector3.up * .55f);
            if (p.z > 0 && p.x >= 0 && p.x <= Screen.width && p.y >= 0 && p.y <= Screen.height)
                GUI.Label(new Rect(p.x - 110, Screen.height - p.y - 14, 220, 26), DisplayName, GUI.skin.box);
        }
#endif
    }
}
