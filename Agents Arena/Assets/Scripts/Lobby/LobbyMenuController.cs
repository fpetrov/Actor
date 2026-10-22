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
            view.FreshRoundRequested += FreshRound;
            view.ResumeRequested += session.ResumeConnection;
            session.Connected += OnConnected;
            session.Disconnected += OnDisconnected;
#endif
        }

        private void Update()
        {
#if !UNITY_SERVER
            view.ResumeAvailable = session.CanResume;
            view.ResumeBusy = session.IsResuming;
            view.ResumeCredentialSeconds = Math.Max(0, (session.ResumeExpiresAtUnixMs - DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + 999) / 1000);
#endif
        }
        private IEnumerator Start()
        {
            while (true)
            {
                if (view.Visible && !view.Busy && !refreshing) StartCoroutine(RefreshRooms(false));
                yield return new WaitForSecondsRealtime(settings.refreshInterval);
            }
        }

        public void Refresh()
        {
            if (refreshing) return;
            StartCoroutine(RefreshRooms(true));
        }
        private IEnumerator RefreshRooms(bool showStatus)
        {
            refreshing = true;
            yield return api.List(list =>
            {
                view.Rooms = list.lobbies ?? Array.Empty<LobbyInfo>();
                if (!view.Busy && (showStatus || view.Status == "Fetching rooms…")) view.Status = "Choose an expedition or create one. Move with WASD or arrow keys in the game.";
            }, error => { if (!view.Busy && showStatus) view.Status = error; });
            refreshing = false;
        }
        public void Create()
        {
            if (view.Busy || rematching || session.IsResuming || session.IsReturningToLobby || !ValidatePlayerName()) return;
            StartCoroutine(CreateAndJoin());
        }
        private IEnumerator CreateAndJoin(bool waitForCapacity = false)
        {
            view.Busy = true;
            view.Status = "Creating room…";
            LobbyInfo lobby = null;
            string error = null;
            // An empty completed room can retain the last server slot during its
            // shutdown grace. Only this explicit rematch flow waits for capacity;
            // transport failures and other errors are never retried as POSTs.
            double capacityDeadline = Time.realtimeSinceStartupAsDouble + 75;
            do
            {
                string code = null; error = null;
                yield return api.Create(view.NewRoomName, result => lobby = result, message => error = message, view.GameMode, value => code = value);
                if (lobby != null || !waitForCapacity || code != "server_capacity_reached" || Time.realtimeSinceStartupAsDouble >= capacityDeadline) break;
                view.Status = "Waiting for a server slot for the next expedition…";
                // Stay below the public create limit (12/minute per address).
                yield return new WaitForSecondsRealtime(8);
            } while (true);
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
            if (view.Busy || rematching || session.IsResuming || session.IsReturningToLobby || !ValidatePlayerName()) return;
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
        private bool rematching;
        private void FreshRound()
        {
            if (rematching || session.IsResuming || session.IsReturningToLobby) return;
            StartCoroutine(CreateFreshRound());
        }
        private IEnumerator CreateFreshRound()
        {
            rematching = true;
            session.Disconnect();
            view.Status = "Leaving the completed expedition…";
            double deadline = Time.realtimeSinceStartupAsDouble + 15;
            while ((session.IsReturningToLobby || Unity.Netcode.NetworkManager.Singleton != null && Unity.Netcode.NetworkManager.Singleton.IsListening)
                && Time.realtimeSinceStartupAsDouble < deadline) yield return null;
            if (session.IsReturningToLobby || Unity.Netcode.NetworkManager.Singleton != null && Unity.Netcode.NetworkManager.Singleton.IsListening)
            { rematching = false; Fail("Could not finish disconnecting. Return to rooms before creating the next expedition."); yield break; }
            view.Busy = false; view.GameMode = "SocialDeduction"; view.NewRoomName = "Next expedition";
            yield return CreateAndJoin(true);
            rematching = false;
        }
        private bool ValidatePlayerName()
        {
            view.PlayerName = view.PlayerName.Trim();
            if (!JoinTicketCodec.IsPlayerNameValid(view.PlayerName))
            { view.Status = "Use 2–24 letters, numbers, spaces, dots, underscores or hyphens for your name."; return false; }
            // Several local agents share Unity's preferences. Launcher-owned
            // names belong to that launch and must not replace the Human's name.
            if (!Application.isBatchMode && Array.IndexOf(Environment.GetCommandLineArgs(), "--auto-room") < 0)
            {
                PlayerPrefs.SetString("Actor.PlayerName", view.PlayerName);
                PlayerPrefs.Save();
            }
            return true;
        }
        private void OnConnected()
        {
            view.Busy = false; view.Visible = false; view.ConnectedRoom = session.RoomCode;
            // A long expedition outlives the room browser snapshot. Return to
            // an empty list until the ordinary disconnect refresh completes.
            view.Rooms = Array.Empty<LobbyInfo>();
        }
        private void OnDisconnected(string reason)
        {
            view.Busy = rematching; view.Visible = true; view.Status = reason;
            if (!refreshing) StartCoroutine(RefreshRooms(false));
        }
        private void Fail(string error) { view.Busy = false; view.Status = error ?? "The request failed. Please try again."; }
        private void OnDestroy()
        {
            view.RefreshRequested -= Refresh; view.CreateRequested -= Create; view.JoinRequested -= JoinRoom;
            view.DisconnectRequested -= session.Disconnect; view.FreshRoundRequested -= FreshRound; view.ResumeRequested -= session.ResumeConnection;
            session.Connected -= OnConnected; session.Disconnected -= OnDisconnected;
        }
    }
}
