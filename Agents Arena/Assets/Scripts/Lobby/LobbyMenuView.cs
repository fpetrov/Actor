using System;
using Actor.UI;
using UnityEngine;
#if !UNITY_SERVER
using UnityEngine.UIElements;
#endif

namespace Actor.Lobby
{
    // Stable controller-facing model; rendering is retained and never handles admission.
    public sealed class LobbyMenuView : MonoBehaviour
    {
        public event Action RefreshRequested;
        public event Action CreateRequested;
        public event Action<string> JoinRequested;
        public event Action DisconnectRequested;
        public event Action FreshRoundRequested;
        public event Action ResumeRequested;
        public string PlayerName = "Player";
        public string NewRoomName = "New expedition";
        public string GameMode = "SocialDeduction";
        public string DirectRoomCode = "";
        public string Status = "Fetching rooms…";
        public string ConnectedRoom = "";
        public bool ResumeAvailable, ResumeBusy;
        public long ResumeCredentialSeconds;
        public bool Busy;
        public bool Visible = true;
        public LobbyInfo[] Rooms = Array.Empty<LobbyInfo>();
        public void Leave() => DisconnectRequested?.Invoke();
        public void RequestFreshRound() => FreshRoundRequested?.Invoke();
#if !UNITY_SERVER
        private VisualElement root, roomList, connection;
        private Label status, connected;
        private TextField name, roomName, roomCode;
        private Button create, join, refresh, resume;
        private VisualElement recovery;
        private DropdownField mode;
        private string roomSignature = "";
        private bool tutorialOffered;
        private void Build()
        {
            var layer = ArenaUI.ScreenLayer; if (layer == null) return;
            root = ArenaUI.Element("lobby-backdrop", layer);
            var shell = ArenaUI.Panel("", "lobby-shell", root);
            ArenaUI.Text("ACTOR  /  AGENTS ARENA", "eyebrow", shell);
            ArenaUI.Text("A quiet station. Hidden intentions.", "title", shell);
            ArenaUI.Text("Meet, work, investigate — or deceive. Human and autonomous players share the same world.", "muted", shell);
            var scroll = new ScrollView(); shell.Add(scroll);
            var columns = ArenaUI.Element("lobby-columns", scroll);
            var browse = ArenaUI.Element("lobby-column", columns);
            var title = ArenaUI.Element("row spaced", browse);
            ArenaUI.Text("Find an expedition", "heading grow", title);
            refresh = ArenaUI.Button("Refresh", () => RefreshRequested?.Invoke(), "quiet", title);
            recovery = ArenaUI.Element("participant", browse);
            ArenaUI.Text("Connection interrupted", "body", recovery);
            ArenaUI.Text("Try reconnecting promptly. The server checks the grace period and your original identity.", "muted", recovery);
            resume = ArenaUI.Button("Reconnect", () => ResumeRequested?.Invoke(), "primary", recovery);
            var rooms = new ScrollView(); rooms.AddToClassList("room-list"); browse.Add(rooms); roomList = rooms.contentContainer;
            roomCode = ArenaUI.Field("Room code", DirectRoomCode, v => DirectRoomCode = v.ToUpperInvariant(), 6, browse);
            join = ArenaUI.Button("Join room", () => JoinRequested?.Invoke(DirectRoomCode), "primary", browse);
            var setup = ArenaUI.Element("lobby-column", columns);
            ArenaUI.Text("Your seat", "heading", setup);
            name = ArenaUI.Field("Player name", PlayerName, v => PlayerName = v, 24, setup);
            roomName = ArenaUI.Field("Room name", NewRoomName, v => NewRoomName = v, 48, setup);
            mode = new DropdownField("Mode", new System.Collections.Generic.List<string> { "Social Deduction · 4–6", "Sandbox / Investigation" }, GameMode == "SocialDeduction" ? 0 : 1);
            mode.AddToClassList("vertical-field");
            mode.RegisterValueChangedCallback(e => GameMode = e.newValue.StartsWith("Social") ? "SocialDeduction" : "Sandbox"); setup.Add(mode);
            create = ArenaUI.Button("Create room", () => CreateRequested?.Invoke(), "primary", setup);
            var tools = ArenaUI.Element("row", setup);
            ArenaUI.Button("How to play", ArenaUI.Tutorial, "quiet grow gap", tools);
            ArenaUI.Button("Settings", ArenaUI.Settings, "quiet grow", tools);
            status = ArenaUI.Text(Status, "muted", shell);
            connection = ArenaUI.Element("connection-bar", ArenaUI.HudLayer);
            connected = ArenaUI.Text("", "eyebrow", connection);
            var actions = ArenaUI.Element("connection-tools", connection);
            ArenaUI.Button("Help", ArenaUI.Tutorial, "quiet", actions);
            ArenaUI.Button("Menu", OpenMenu, "quiet", actions);
        }
        private void OpenMenu()
        {
            var card = ArenaUI.OpenModal("Expedition menu"); if (card == null) return;
            ArenaUI.Text("Room " + ConnectedRoom + " · " + PlayerName, "body", card);
            ArenaUI.Button("Settings", ArenaUI.Settings, "", card);
            ArenaUI.Button("How to play", ArenaUI.Tutorial, "", card);
            ArenaUI.Button("Leave room", () => { ArenaUI.CloseModal(); Leave(); }, "danger", card);
        }
        private void Update()
        {
            if (root == null) Build(); if (root == null) return;
            bool observer = GetComponent<global::Actor.Networking.MatchSession>()?.IsBroadcastObserver == true;
            ArenaUI.Show(root, Visible); ArenaUI.Show(connection, !Visible && !observer);
            status.text = Status; connected.text = "ROOM " + ConnectedRoom + "  ·  " + PlayerName;
            name.SetValueWithoutNotify(PlayerName); roomName.SetValueWithoutNotify(NewRoomName); roomCode.SetValueWithoutNotify(DirectRoomCode);
            mode.SetValueWithoutNotify(GameMode == "SocialDeduction" ? "Social Deduction · 4–6" : "Sandbox / Investigation");
            ArenaUI.Show(recovery, ResumeAvailable || ResumeBusy);
            resume.text = ResumeBusy ? "Reconnecting…" : "Try reconnect · credential " + ResumeCredentialSeconds + "s";
            resume.SetEnabled(ResumeAvailable && !ResumeBusy && !Busy);
            create.SetEnabled(!Busy && !ResumeBusy); join.SetEnabled(!Busy && !ResumeBusy); refresh.SetEnabled(!Busy);
            name.SetEnabled(!Busy && !ResumeBusy); roomCode.SetEnabled(!Busy && !ResumeBusy);
            roomName.SetEnabled(!Busy && !ResumeBusy); mode.SetEnabled(!Busy && !ResumeBusy);
            string signature = Busy + "|" + ResumeBusy + "|" + string.Join("|", Array.ConvertAll(Rooms ?? Array.Empty<LobbyInfo>(), r => r.roomCode + r.status + r.currentPlayers + r.displayName));
            if (signature != roomSignature)
            {
                roomSignature = signature; roomList.Clear();
                if (Rooms == null || Rooms.Length == 0) ArenaUI.Text("No expeditions yet. Create a room to begin.", "muted", roomList);
                foreach (var room in Rooms ?? Array.Empty<LobbyInfo>())
                {
                    var row = ArenaUI.Element("room-card row", roomList);
                    var description = ArenaUI.Element("grow", row);
                    ArenaUI.Text(room.displayName, "room-name", description);
                    ArenaUI.Text(room.roomCode + " · " + (room.gameMode == "SocialDeduction" ? "Social" : "Sandbox") + " · " + room.currentPlayers + "/" + room.maxPlayers + " · " + room.status, "muted", description);
                    var button = ArenaUI.Button("Join", () => JoinRequested?.Invoke(room.roomCode), "", row);
                    button.SetEnabled(!Busy && !ResumeBusy && (room.status == "Ready" || room.status == "Running"));
                }
            }
            if (!Visible && !observer && !tutorialOffered && PlayerPrefs.GetInt("Arena.TutorialSeen", 0) == 0)
            { tutorialOffered = true; ArenaUI.Tutorial(); }
        }
        private void OnDisable() { root?.RemoveFromHierarchy(); connection?.RemoveFromHierarchy(); root = connection = null; }
        private void OnDestroy() => OnDisable();
#endif
    }
}
