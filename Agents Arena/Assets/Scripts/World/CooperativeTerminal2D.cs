using UnityEngine;

namespace Actor.World
{
    public enum CooperativeTerminalKind { Diagnostic, Calibration }
    public sealed class CooperativeTerminal2D : MonoBehaviour
    {
        [SerializeField] private CooperativeTerminalKind kind;
        public CooperativeTerminalKind Kind => kind;
        public Vector2 InteractionPoint => (Vector2)transform.position + (kind == CooperativeTerminalKind.Diagnostic ? new Vector2(-.3f, -.25f) : new Vector2(-.55f, -.54f));
        public string EntityId => kind == CooperativeTerminalKind.Diagnostic ? "main-hall-diagnostic-01" : "engineering-calibration-01";
        public float Radius => kind == CooperativeTerminalKind.Diagnostic ? 1.6f : 1.25f;
        public string DisplayName => kind == CooperativeTerminalKind.Diagnostic ? "Diagnostic Terminal" : "Calibration Console";
        public void Configure(CooperativeTerminalKind value) => kind = value;
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
