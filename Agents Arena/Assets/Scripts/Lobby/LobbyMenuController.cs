using System;
using System.Collections;
using Actor.Contracts;
using Actor.Networking;
using UnityEngine;

namespace Actor.Lobby
{
    public sealed class LobbyMenuController : MonoBehaviour
    {
        [SerializeField] private LobbyClientSettings settings;
        [SerializeField] private LobbyMenuView view;
        [SerializeField] private MatchSession session;
        private LobbyApiClient api;
        private bool refreshing;

        private void Awake()
        {
#if UNITY_SERVER
            view.enabled = false;
            enabled = false;
#else
            api = new LobbyApiClient(settings);
            view.PlayerName = PlayerPrefs.GetString("Actor.PlayerName", "Player");
            view.RefreshRequested += Refresh;
            view.CreateRequested += Create;
            view.JoinRequested += JoinRoom;
            view.DisconnectRequested += session.Disconnect;
            session.Connected += OnConnected;
            session.Disconnected += OnDisconnected;
#endif
        }

        private IEnumerator Start()
        {
            while (true)
            {
                if (view.Visible && !view.Busy) Refresh();
                yield return new WaitForSecondsRealtime(settings.refreshInterval);
            }
        }

        public void Refresh()
        {
            if (refreshing) return;
            StartCoroutine(RefreshRooms());
        }
        private IEnumerator RefreshRooms()
        {
            refreshing = true;
            yield return api.List(list =>
            {
                view.Rooms = list.lobbies ?? Array.Empty<LobbyInfo>();
                if (!view.Busy) view.Status = view.Rooms.Length + " rooms available. Move with WASD or arrow keys in the game.";
            }, error => { if (!view.Busy) view.Status = error; });
            refreshing = false;
        }
        public void Create()
        {
            if (view.Busy || !ValidatePlayerName()) return;
            StartCoroutine(CreateAndJoin());
        }
        private IEnumerator CreateAndJoin()
        {
            view.Busy = true;
            view.Status = "Creating room…";
            LobbyInfo lobby = null;
            string error = null;
            yield return api.Create(view.NewRoomName, result => lobby = result, message => error = message, view.GameMode);
            if (lobby == null) { Fail(error); yield break; }
            view.DirectRoomCode = lobby.roomCode;
            float deadline = Time.realtimeSinceStartup + 55;
            while (lobby.status == "Starting" && Time.realtimeSinceStartup < deadline)
            {
                view.Status = "Room " + lobby.roomCode + " is starting…";
                yield return new WaitForSecondsRealtime(1);
                yield return api.Get(lobby.roomCode, result => lobby = result, message => error = message);
                if (error != null) { Fail(error); yield break; }
            }
            if (lobby.status != "Ready" && lobby.status != "Running") { Fail("The game server did not become ready. Please try again."); yield break; }
            yield return Join(lobby.roomCode);
        }

        public void JoinRoom(string roomCode)
        {
            if (view.Busy || !ValidatePlayerName()) return;
            if (string.IsNullOrWhiteSpace(roomCode) || roomCode.Trim().Length != 6) { view.Status = "Enter a six-character room code."; return; }
            StartCoroutine(Join(roomCode));
        }
        private IEnumerator Join(string roomCode)
        {
            view.Busy = true;
            view.Status = "Connecting to " + roomCode + "…";
            JoinInfo admission = null;
            string error = null;
            yield return api.Join(roomCode, view.PlayerName, result => admission = result, message => error = message);
            if (admission == null) { Fail(error); yield break; }
            try { session.Connect(admission); }
            catch (InvalidOperationException exception) { Fail(exception.Message); }
        }
        private bool ValidatePlayerName()
        {
            view.PlayerName = view.PlayerName.Trim();
            if (!JoinTicketCodec.IsPlayerNameValid(view.PlayerName))
            { view.Status = "Use 2–24 letters, numbers, spaces, dots, underscores or hyphens for your name."; return false; }
            PlayerPrefs.SetString("Actor.PlayerName", view.PlayerName);
            PlayerPrefs.Save(); return true;
        }
        private void OnConnected()
        {
            view.Busy = false; view.Visible = false; view.ConnectedRoom = session.RoomCode;
        }
        private void OnDisconnected(string reason)
        {
            view.Busy = false; view.Visible = true; view.Status = reason; Refresh();
        }
        private void Fail(string error) { view.Busy = false; view.Status = error ?? "The request failed. Please try again."; }
        private void OnDestroy()
        {
            view.RefreshRequested -= Refresh; view.CreateRequested -= Create; view.JoinRequested -= JoinRoom;
            view.DisconnectRequested -= session.Disconnect;
            session.Connected -= OnConnected; session.Disconnected -= OnDisconnected;
        }
    }
}
