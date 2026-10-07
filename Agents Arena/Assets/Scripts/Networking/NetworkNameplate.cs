using UnityEngine;

namespace Actor.Networking
{
    [RequireComponent(typeof(NetworkCharacter))]
    public sealed class NetworkNameplate : MonoBehaviour
    {
#if !UNITY_SERVER
        private NetworkCharacter character;
        private GUIStyle style;
        private void Awake() => character = GetComponent<NetworkCharacter>();
        private void OnGUI()
        {
            if (!character.IsSpawned || Camera.main == null || string.IsNullOrEmpty(character.PlayerName)) return;
            Vector3 screen = Camera.main.WorldToScreenPoint(character.PresentationPosition + Vector3.up * 1.1f);
            if (screen.z < 0) return;
            if (style == null)
                style = new GUIStyle(GUI.skin.box) { fontSize = 15, alignment = TextAnchor.MiddleCenter };
            GUI.Label(new Rect(screen.x - 90, Screen.height - screen.y - 22, 180, 26), character.PlayerName, style);
        }
#endif
    }
}
