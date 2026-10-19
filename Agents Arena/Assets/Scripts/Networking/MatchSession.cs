using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Actor.Contracts;
using Actor.Lobby;
using Unity.Netcode;
using Unity.Netcode.Transports.UTP;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.SceneManagement;

namespace Actor.Networking
{
    [RequireComponent(typeof(NetworkManager), typeof(UnityTransport))]
    public sealed class MatchSession : MonoBehaviour
    {
        [SerializeField] private LobbyClientSettings settings;
        [SerializeField] private Camera lobbyCamera;
        private NetworkManager manager;
        private UnityTransport transport;
        private string matchId, roomCode, gameVersion, protocolVersion, internalApi;
        private byte[] secret;
        private int maxPlayers;
        private bool gameplayLoaded, returningToLobby;
        private readonly Dictionary<ulong, JoinTicketClaims> approvals = new();
        private readonly Dictionary<ulong, int> spawnSlots = new();
        private readonly Dictionary<string, long> consumedTickets = new();
        public event Action Connected;
        public event Action<string> Disconnected;
        public string RoomCode => roomCode;
        public string MatchId => matchId;
        public bool IsConnected => manager != null && manager.IsConnectedClient;

        private void Awake()
        {
            manager = GetComponent<NetworkManager>();
            transport = GetComponent<UnityTransport>();
            manager.OnClientConnectedCallback += OnConnected;
            manager.OnClientDisconnectCallback += OnDisconnected;
            Application.runInBackground = true;
        }

        private void Start()
        {
#if UNITY_SERVER
            if (lobbyCamera != null) lobbyCamera.gameObject.SetActive(false);
            StartCoroutine(StartDedicatedServer());
#endif
        }

        public bool TryGetApprovedPlayer(ulong clientId, out JoinTicketClaims claims) => approvals.TryGetValue(clientId, out claims);

        public void Connect(JoinInfo admission)
        {
            if (manager.IsListening) throw new InvalidOperationException("A network session is already active.");
            roomCode = admission.roomCode;
            matchId = admission.lobbyId;
            transport.SetConnectionData(admission.host, (ushort)admission.port);
            manager.NetworkConfig.ConnectionData = Encoding.UTF8.GetBytes(admission.joinTicket);
            if (!manager.StartClient()) Disconnected?.Invoke("Could not start the network connection.");
        }

        public void Disconnect()
        {
            if (!returningToLobby) StartCoroutine(ReturnToLobby("Disconnected from the room."));
        }

        private IEnumerator StartDedicatedServer()
        {
            string[] args = Environment.GetCommandLineArgs();
            matchId = Argument(args, "--match-id");
            roomCode = Argument(args, "--room-code");
            gameVersion = Argument(args, "--game-version");
            protocolVersion = Argument(args, "--protocol-version");
            internalApi = Environment.GetEnvironmentVariable("ACTOR_INTERNAL_API_URL");
            string encodedSecret = Environment.GetEnvironmentVariable("ACTOR_MATCH_SECRET");
            int port = 0;
            bool valid = Guid.TryParseExact(matchId, "N", out _)
                && int.TryParse(Argument(args, "--port"), out port) && port >= 1024 && port <= 65535
                && int.TryParse(Argument(args, "--max-players"), out maxPlayers) && maxPlayers >= 2 && maxPlayers <= 16
                && !string.IsNullOrEmpty(encodedSecret) && !string.IsNullOrEmpty(gameVersion) && !string.IsNullOrEmpty(protocolVersion)
                && Uri.TryCreate(internalApi, UriKind.Absolute, out var uri) && uri.IsLoopback;
            if (valid)
            {
                try { secret = Convert.FromBase64String(encodedSecret); valid = secret.Length == 32; }
                catch (FormatException) { valid = false; }
            }
            if (!valid)
            {
                Debug.LogError("ACTOR: invalid dedicated server configuration.");
                Application.Quit(2); yield break;
            }
            transport.SetConnectionData("0.0.0.0", (ushort)port, "0.0.0.0");
            manager.ConnectionApprovalCallback = Approve;
            if (!manager.StartServer())
            {
                Debug.LogError("ACTOR: could not bind the game server transport.");
                Application.Quit(3); yield break;
            }
            manager.SceneManager.SetClientSynchronizationMode(LoadSceneMode.Additive);
            manager.SceneManager.OnSceneEvent += OnSceneEvent;
            var result = manager.SceneManager.LoadScene(settings.gameplayScene, LoadSceneMode.Additive);
            if (result != SceneEventProgressStatus.Started)
            {
                Debug.LogError("ACTOR: could not load the gameplay scene.");
                Application.Quit(4); yield break;
            }
            float deadline = Time.realtimeSinceStartup + 20;
            while (!gameplayLoaded && Time.realtimeSinceStartup < deadline) yield return null;
            if (!gameplayLoaded) { Application.Quit(5); yield break; }
            bool ready = false;
            for (int attempt = 0; attempt < 3 && !ready; attempt++)
            {
                yield return Callback("ready", "{}", success => ready = success);
                if (!ready) yield return new WaitForSecondsRealtime(1);
            }
            if (!ready) { Application.Quit(6); yield break; }
            Debug.Log($"ACTOR_READY match={matchId} room={roomCode} port={port}");
            int failures = 0;
            while (manager.IsServer)
            {
                yield return new WaitForSecondsRealtime(2);
                string[] connectedNonces = manager.ConnectedClientsIds.Where(id => approvals.ContainsKey(id)).Select(id => approvals[id].nonce).ToArray();
                var heartbeat = new Heartbeat
                {
                    currentPlayers = manager.ConnectedClientsIds.Count,
                    serverState = manager.ConnectedClientsIds.Count > 0 ? "Running" : "Ready",
                    connectedNonces = connectedNonces
                };
                bool accepted = false;
                yield return Callback("heartbeat", JsonUtility.ToJson(heartbeat), success => accepted = success);
                failures = accepted ? 0 : failures + 1;
                if (failures >= 3) { manager.Shutdown(); Application.Quit(7); yield break; }
            }
        }

        private void OnSceneEvent(SceneEvent sceneEvent)
        {
            if (!manager.IsServer || sceneEvent.SceneEventType != SceneEventType.LoadEventCompleted || sceneEvent.SceneName != settings.gameplayScene) return;
            gameplayLoaded = true;
#if UNITY_SERVER
            foreach (Camera camera in FindObjectsByType<Camera>()) camera.enabled = false;
            foreach (AudioListener listener in FindObjectsByType<AudioListener>()) listener.enabled = false;
            foreach (var light in FindObjectsByType<UnityEngine.Rendering.Universal.Light2D>()) light.enabled = false;
#endif
        }

        private void Approve(NetworkManager.ConnectionApprovalRequest request, NetworkManager.ConnectionApprovalResponse response)
        {
            response.Approved = false;
            response.CreatePlayerObject = false;
            response.Pending = false;
            response.Reason = "Invalid or expired join ticket.";
            string ticket = Encoding.UTF8.GetString(request.Payload);
            if (!gameplayLoaded || !JoinTicketCodec.TryVerify(ticket, secret, out string payload)) return;
            JoinTicketClaims claims;
            try { claims = JsonUtility.FromJson<JoinTicketClaims>(payload); }
            catch (ArgumentException) { return; }
            long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            if (!JoinTicketCodec.ClaimsMatch(claims, matchId, gameVersion, protocolVersion, now)) return;
            foreach (string nonce in consumedTickets.Where(p => p.Value <= now).Select(p => p.Key).ToArray()) consumedTickets.Remove(nonce);
            if (consumedTickets.ContainsKey(claims.nonce)) { response.Reason = "Join ticket has already been used."; return; }
            if (manager.ConnectedClientsIds.Count >= maxPlayers || spawnSlots.Count >= maxPlayers)
            { response.Reason = "The room is full."; return; }
            int slot = Enumerable.Range(0, maxPlayers).First(i => !spawnSlots.ContainsValue(i));
            approvals[request.ClientNetworkId] = claims;
            spawnSlots[request.ClientNetworkId] = slot;
            consumedTickets[claims.nonce] = claims.expiresAt;
            response.Approved = true;
            response.CreatePlayerObject = true;
            response.Position = SpawnPosition(slot);
            response.Rotation = Quaternion.identity;
            response.Reason = "";
        }

        public static Vector3 SpawnPosition(int slot)
        {
            float[] x = { -1.8f, -0.85f, 0.85f, 1.8f };
            // The lower row sits inside the atlas hall's curved southern floor.
            // Keep the first row stable while avoiding walls for a full lobby.
            if ((slot / 4) % 4 == 1) x = new[] { -0.75f, -0.25f, 0.25f, 0.75f };
            float[] y = { -1.4f, -2.5f, -0.3f, 0.8f };
            return new Vector3(x[slot % 4], y[(slot / 4) % 4], 0);
        }

        private void OnConnected(ulong clientId)
        {
            if (manager.IsServer) Debug.Log($"ACTOR_PLAYER_CONNECTED client={clientId} players={manager.ConnectedClientsIds.Count}");
            if (!manager.IsClient || clientId != manager.LocalClientId) return;
            if (lobbyCamera != null) lobbyCamera.gameObject.SetActive(false);
            Connected?.Invoke();
        }

        private void OnDisconnected(ulong clientId)
        {
            if (manager.IsServer)
            {
                approvals.Remove(clientId); spawnSlots.Remove(clientId);
                Debug.Log($"ACTOR_PLAYER_DISCONNECTED client={clientId} players={manager.ConnectedClientsIds.Count}");
                return;
            }
            if (clientId != manager.LocalClientId || returningToLobby) return;
            string reason = manager.DisconnectReason;
            StartCoroutine(ReturnToLobby(string.IsNullOrEmpty(reason) ? "Connection was lost. You can reconnect from the lobby." : reason));
        }

        private IEnumerator ReturnToLobby(string reason)
        {
            returningToLobby = true;
            manager.Shutdown();
            yield return null;
            while (manager.ShutdownInProgress) yield return null;
            Scene gameplay = SceneManager.GetSceneByName(settings.gameplayScene);
            if (gameplay.IsValid() && gameplay.isLoaded) yield return SceneManager.UnloadSceneAsync(gameplay);
            if (lobbyCamera != null) lobbyCamera.gameObject.SetActive(true);
            returningToLobby = false;
            Disconnected?.Invoke(reason);
        }

        private IEnumerator Callback(string operation, string body, Action<bool> result)
        {
            using (var request = new UnityWebRequest(internalApi.TrimEnd('/') + "/internal/game-servers/" + matchId + "/" + operation, "POST"))
            {
                request.timeout = 5;
                request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
                request.downloadHandler = new DownloadHandlerBuffer();
                request.SetRequestHeader("Content-Type", "application/json");
                request.SetRequestHeader("X-Actor-Match-Secret", Convert.ToBase64String(secret));
                yield return request.SendWebRequest();
                result(request.result == UnityWebRequest.Result.Success);
            }
        }

        public void FinishMatch()
        {
            if (manager.IsServer) StartCoroutine(FinishMatchRoutine());
        }

        private IEnumerator FinishMatchRoutine()
        {
            yield return Callback("finished", "{}", _ => { });
            manager.Shutdown();
            Application.Quit(0);
        }

        private static string Argument(string[] args, string name)
        {
            int index = Array.IndexOf(args, name);
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
        }
        [Serializable] private sealed class Heartbeat { public int currentPlayers; public string serverState; public string[] connectedNonces; }

        private void OnDestroy()
        {
            if (manager == null) return;
            manager.OnClientConnectedCallback -= OnConnected;
            manager.OnClientDisconnectCallback -= OnDisconnected;
            if (manager.SceneManager != null) manager.SceneManager.OnSceneEvent -= OnSceneEvent;
        }
    }
}
