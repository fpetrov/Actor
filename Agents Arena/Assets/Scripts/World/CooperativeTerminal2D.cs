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
        private void OnGUI()
        {
            if (Camera.main == null || NetworkOwnerAbsent()) return;
            Vector3 p = Camera.main.WorldToScreenPoint(transform.position + Vector3.up * .55f);
            if (p.z > 0 && p.x >= 0 && p.x <= Screen.width && p.y >= 0 && p.y <= Screen.height)
                GUI.Label(new Rect(p.x - 100, Screen.height - p.y - 14, 200, 26), DisplayName, GUI.skin.box);
        }
        private static bool NetworkOwnerAbsent() => Actor.Networking.NetworkCooperation.LocalOwner == null;
#endif
    }
}
