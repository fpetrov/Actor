using System;
using UnityEngine;

namespace Actor.Lobby
{
    public sealed class LobbyMenuView : MonoBehaviour
    {
        public event Action RefreshRequested;
        public event Action CreateRequested;
        public event Action<string> JoinRequested;
        public event Action DisconnectRequested;
        public string PlayerName = "Player";
        public string NewRoomName = "Main Hall";
        public string DirectRoomCode = "";
        public string Status = "Fetching rooms…";
        public string ConnectedRoom = "";
        public bool Busy;
        public bool Visible = true;
        public LobbyInfo[] Rooms = Array.Empty<LobbyInfo>();
#if !UNITY_SERVER
        private Vector2 scroll;
        private GUIStyle title, subtitle, label, button, input, status;
        private Texture2D backdrop, panel;

        private void InitializeStyles()
        {
            backdrop = Texture(new Color(0.035f, 0.045f, 0.075f));
            panel = Texture(new Color(0.075f, 0.09f, 0.145f));
            title = new GUIStyle(GUI.skin.label) { fontSize = 38, fontStyle = FontStyle.Bold };
            subtitle = new GUIStyle(GUI.skin.label) { fontSize = 17, normal = { textColor = new Color(0.65f, 0.73f, 0.9f) } };
            label = new GUIStyle(GUI.skin.label) { fontSize = 18 };
            button = new GUIStyle(GUI.skin.button) { fontSize = 17, padding = new RectOffset(16, 16, 10, 10) };
            input = new GUIStyle(GUI.skin.textField) { fontSize = 19, padding = new RectOffset(12, 12, 10, 10) };
            status = new GUIStyle(GUI.skin.label) { fontSize = 16, wordWrap = true, normal = { textColor = new Color(0.7f, 0.8f, 1f) } };
        }
        private static Texture2D Texture(Color color)
        {
            var texture = new Texture2D(1, 1);
            texture.SetPixel(0, 0, color); texture.Apply(); return texture;
        }

        private void OnGUI()
        {
            if (title == null) InitializeStyles();
            if (!Visible)
            {
                GUILayout.BeginArea(new Rect(18, 16, 330, 80));
                GUILayout.Label("Room " + ConnectedRoom + "  ·  " + PlayerName, subtitle);
                if (GUILayout.Button("Leave room", button)) DisconnectRequested?.Invoke();
                GUILayout.EndArea(); return;
            }
            GUI.DrawTexture(new Rect(0, 0, Screen.width, Screen.height), backdrop);
            float width = Mathf.Min(880, Screen.width - 40);
            float height = Mathf.Min(730, Screen.height - 40);
            var box = new Rect((Screen.width - width) / 2, (Screen.height - height) / 2, width, height);
            GUI.DrawTexture(box, panel);
            GUILayout.BeginArea(new Rect(box.x + 30, box.y + 22, box.width - 60, box.height - 44));
            GUILayout.Label("Agents Arena", title);
            GUILayout.Label("ACTOR  /  MAIN HALL", subtitle);
            GUILayout.Space(20);
            GUILayout.Label("Player name", label);
            PlayerName = GUILayout.TextField(PlayerName, 24, input, GUILayout.Height(44));
            GUILayout.Space(16);
            GUILayout.BeginHorizontal();
            GUILayout.Label("Available rooms", label);
            GUILayout.FlexibleSpace();
            GUI.enabled = !Busy;
            if (GUILayout.Button("Refresh", button, GUILayout.Width(130))) RefreshRequested?.Invoke();
            GUILayout.EndHorizontal();
            scroll = GUILayout.BeginScrollView(scroll, GUILayout.Height(Mathf.Max(80, height - 530)));
            if (Rooms.Length == 0) GUILayout.Label("No rooms yet. Create the first one below.", subtitle);
            foreach (LobbyInfo room in Rooms)
            {
                GUILayout.BeginHorizontal(GUI.skin.box);
                GUILayout.Label(room.roomCode, label, GUILayout.Width(110));
                GUILayout.Label(room.displayName, label, GUILayout.MinWidth(130));
                GUILayout.Label(room.currentPlayers + "/" + room.maxPlayers, label, GUILayout.Width(55));
                GUILayout.Label(room.status, subtitle, GUILayout.Width(90));
                GUI.enabled = !Busy && (room.status == "Ready" || room.status == "Running");
                if (GUILayout.Button("Join", button, GUILayout.Width(85))) JoinRequested?.Invoke(room.roomCode);
                GUI.enabled = !Busy;
                GUILayout.EndHorizontal();
            }
            GUILayout.EndScrollView();
            GUILayout.Space(14);
            GUILayout.Label("Join by room code", label);
            GUILayout.BeginHorizontal();
            DirectRoomCode = GUILayout.TextField(DirectRoomCode, 6, input, GUILayout.Height(44)).ToUpperInvariant();
            if (GUILayout.Button("Join", button, GUILayout.Width(130), GUILayout.Height(44))) JoinRequested?.Invoke(DirectRoomCode);
            GUILayout.EndHorizontal();
            GUILayout.Space(14);
            GUILayout.Label("Create a room", label);
            GUILayout.BeginHorizontal();
            NewRoomName = GUILayout.TextField(NewRoomName, 48, input, GUILayout.Height(44));
            if (GUILayout.Button("Create room", button, GUILayout.Width(160), GUILayout.Height(44))) CreateRequested?.Invoke();
            GUILayout.EndHorizontal();
            GUI.enabled = true;
            GUILayout.Space(14);
            GUILayout.Label(Status, status);
            GUILayout.EndArea();
        }
        private void OnDestroy()
        {
            if (backdrop != null) Destroy(backdrop);
            if (panel != null) Destroy(panel);
        }
#endif
    }
}
