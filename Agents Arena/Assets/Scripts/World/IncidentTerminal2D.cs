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
        private UnityEngine.UIElements.Label label;
        private void LateUpdate()
        {
            if (Actor.Networking.NetworkCooperation.LocalOwner == null) { if (label != null) Actor.UI.ArenaUI.Show(label, false); return; }
            if (label == null && Actor.UI.ArenaUI.WorldLayer != null) label = Actor.UI.ArenaUI.Text(DisplayName, "world-label", Actor.UI.ArenaUI.WorldLayer);
            if (label != null) Actor.UI.ArenaUI.WorldLabel(label, transform.position + Vector3.up * .55f, 200);
        }
        private void OnDisable() { label?.RemoveFromHierarchy(); label = null; }
#endif
    }
}
