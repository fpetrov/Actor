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
        private NetworkSceneManager observedSceneManager;
        private string matchId, roomCode, gameVersion, protocolVersion, internalApi;
        private byte[] secret;
        private int maxPlayers;
        private bool gameplayLoaded, returningToLobby;
        private int diagnosticMessages;
        private int connectionAttempt;
        private const double ClientConnectionDeadlineSeconds = 20;
        private const int MaxDiagnosticMessages = 128;
        private readonly Dictionary<ulong, JoinTicketClaims> approvals = new();
        private readonly Dictionary<ulong, int> spawnSlots = new();
        private readonly Dictionary<string, long> consumedTickets = new();
        public event Action Connected;
        public event Action<string> Disconnected;
        public string RoomCode => roomCode;
        public string MatchId => matchId;
        public string GameMode { get; private set; } = "Sandbox";
        private string GameplayScene => GameMode == "SocialDeduction" ? "SocialDeduction" : settings.gameplayScene;
        internal int ApprovedJoinOrder(ulong clientId) => spawnSlots.TryGetValue(clientId, out int slot) ? slot : int.MaxValue;
        public bool IsConnected => manager != null && manager.IsConnectedClient;

        private void Awake()
        {
            string apiOverride = Argument(Environment.GetCommandLineArgs(), "--api-url");
            if (!string.IsNullOrEmpty(apiOverride))
            {
                if (!Uri.TryCreate(apiOverride, UriKind.Absolute, out var apiUri) || (apiUri.Scheme != "http" && apiUri.Scheme != "https")
                    || apiUri.UserInfo.Length > 0 || apiUri.Query.Length > 0 || apiUri.Fragment.Length > 0)
                    throw new ArgumentException("A plain absolute HTTP(S) API base URL is required.");
                settings.apiBaseUrl = apiOverride.TrimEnd('/');
            }
            manager = GetComponent<NetworkManager>();
            transport = GetComponent<UnityTransport>();
            manager.OnClientConnectedCallback += OnConnected;
            manager.OnClientDisconnectCallback += OnDisconnected;
            manager.OnTransportFailure += OnTransportFailure;
            transport.OnTransportEvent += OnTransportEvent;
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
            Diagnostic("admission-received", $"host={DiagnosticHost(admission.host)} port={admission.port}");
            roomCode = admission.roomCode;
            matchId = admission.lobbyId;
            GameMode = admission.gameMode == "SocialDeduction" ? "SocialDeduction" : "Sandbox";
            SocialVisibilitySystem.Ensure(manager);
            transport.SetConnectionData(admission.host, (ushort)admission.port);
            manager.NetworkConfig.ConnectionData = Encoding.UTF8.GetBytes(admission.joinTicket);
            bool started = manager.StartClient();
            Diagnostic("client-start", $"started={started} listening={manager.IsListening}");
            if (started)
            {
                ObserveSceneEvents();
                StartCoroutine(WatchClientConnection(++connectionAttempt));
            }
            else Disconnected?.Invoke("Could not start the network connection.");
        }

        private IEnumerator WatchClientConnection(int attempt)
        {
            double deadline = Time.realtimeSinceStartupAsDouble + ClientConnectionDeadlineSeconds;
            while (attempt == connectionAttempt && !returningToLobby && !manager.IsConnectedClient
                && manager.IsListening && Time.realtimeSinceStartupAsDouble < deadline) yield return null;
            if (attempt != connectionAttempt || returningToLobby || manager.IsConnectedClient) yield break;
            Diagnostic(manager.IsListening ? "client-connection-deadline" : "client-connection-stopped");
            yield return ReturnToLobby("The game server did not complete the connection. Please try again.");
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
            GameMode = Argument(args, "--game-mode") ?? "Sandbox";
            if (GameMode == "SocialDeduction") Application.targetFrameRate = 60;
            SocialVisibilitySystem.Ensure(manager);
            internalApi = Environment.GetEnvironmentVariable("ACTOR_INTERNAL_API_URL");
            string encodedSecret = Environment.GetEnvironmentVariable("ACTOR_MATCH_SECRET");
            int port = 0;
            bool valid = Guid.TryParseExact(matchId, "N", out _)
                && int.TryParse(Argument(args, "--port"), out port) && port >= 1024 && port <= 65535
                && int.TryParse(Argument(args, "--max-players"), out maxPlayers) && maxPlayers >= 2 && maxPlayers <= 16
                && !string.IsNullOrEmpty(encodedSecret) && !string.IsNullOrEmpty(gameVersion) && !string.IsNullOrEmpty(protocolVersion)
                && (GameMode == "Sandbox" || GameMode == "SocialDeduction")
                && (GameMode != "SocialDeduction" || maxPlayers >= 4 && maxPlayers <= 6)
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
            bool started = manager.StartServer();
            Diagnostic("server-start", $"started={started} port={port}");
            if (!started)
            {
                Debug.LogError("ACTOR: could not bind the game server transport.");
                Application.Quit(3); yield break;
            }
            manager.SceneManager.SetClientSynchronizationMode(LoadSceneMode.Additive);
            ObserveSceneEvents();
            var result = manager.SceneManager.LoadScene(GameplayScene, LoadSceneMode.Additive);
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
            Diagnostic("scene-event", $"event={sceneEvent.SceneEventType} client={sceneEvent.ClientId} scene={DiagnosticLabel(sceneEvent.SceneName)} players={manager.ConnectedClientsIds.Count}");
            if (!manager.IsServer || sceneEvent.SceneEventType != SceneEventType.LoadEventCompleted || sceneEvent.SceneName != GameplayScene) return;
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
            if (!gameplayLoaded) { ApprovalRejected(request.ClientNetworkId, "gameplay-not-ready"); return; }
            if (!JoinTicketCodec.TryVerify(ticket, secret, out string payload))
            { ApprovalRejected(request.ClientNetworkId, "ticket-invalid"); return; }
            JoinTicketClaims claims;
            try { claims = JsonUtility.FromJson<JoinTicketClaims>(payload); }
            catch (ArgumentException) { ApprovalRejected(request.ClientNetworkId, "ticket-payload-invalid"); return; }
            long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            if (!JoinTicketCodec.ClaimsMatch(claims, matchId, gameVersion, protocolVersion, now))
            { ApprovalRejected(request.ClientNetworkId, "ticket-claims-invalid"); return; }
            foreach (string nonce in consumedTickets.Where(p => p.Value <= now).Select(p => p.Key).ToArray()) consumedTickets.Remove(nonce);
            if (consumedTickets.ContainsKey(claims.nonce))
            { response.Reason = "Join ticket has already been used."; ApprovalRejected(request.ClientNetworkId, "ticket-replayed"); return; }
            if (manager.ConnectedClientsIds.Count >= maxPlayers || spawnSlots.Count >= maxPlayers)
            { response.Reason = "The room is full."; ApprovalRejected(request.ClientNetworkId, "room-full"); return; }
            int slot = Enumerable.Range(0, maxPlayers).First(i => !spawnSlots.ContainsValue(i));
            approvals[request.ClientNetworkId] = claims;
            spawnSlots[request.ClientNetworkId] = slot;
            consumedTickets[claims.nonce] = claims.expiresAt;
            response.Approved = true;
            response.CreatePlayerObject = true;
            response.Position = SpawnPosition(slot);
            response.Rotation = Quaternion.identity;
            response.Reason = "";
            Diagnostic("approval-accepted", $"client={request.ClientNetworkId} approved={approvals.Count} players={manager.ConnectedClientsIds.Count}");
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
            Diagnostic("ngo-connected", $"client={clientId} players={manager.ConnectedClientsIds.Count}");
            if (manager.IsServer) Debug.Log($"ACTOR_PLAYER_CONNECTED client={clientId} players={manager.ConnectedClientsIds.Count}");
            if (!manager.IsClient || clientId != manager.LocalClientId) return;
            if (lobbyCamera != null) lobbyCamera.gameObject.SetActive(false);
            Connected?.Invoke();
        }

        private void OnDisconnected(ulong clientId)
        {
            Diagnostic("ngo-disconnected", $"client={clientId} event={transport.DisconnectEvent} players={manager.ConnectedClientsIds.Count}");
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
            ++connectionAttempt;
            Diagnostic("return-to-lobby", $"connected={manager.IsConnectedClient} listening={manager.IsListening}");
            returningToLobby = true;
            manager.Shutdown();
            yield return null;
            while (manager.ShutdownInProgress) yield return null;
            ObserveSceneEvents();
            Scene gameplay = SceneManager.GetSceneByName(settings.gameplayScene);
            if (gameplay.IsValid() && gameplay.isLoaded) yield return SceneManager.UnloadSceneAsync(gameplay);
            if (lobbyCamera != null) lobbyCamera.gameObject.SetActive(true);
            returningToLobby = false;
            Diagnostic("lobby-restored");
            Disconnected?.Invoke(reason);
        }

        private void OnTransportEvent(NetworkEvent eventType, ulong clientId, ArraySegment<byte> payload, float receiveTime)
        {
            // Never inspect or retain payload: it can include admission tickets,
            // private communication, or short-lived diagnostic authorization.
            if (eventType == NetworkEvent.Connect || eventType == NetworkEvent.Disconnect || eventType == NetworkEvent.TransportFailure)
                Diagnostic("transport-event", $"event={eventType} client={clientId} disconnect={transport.DisconnectEvent}");
        }

        private void OnTransportFailure() => Diagnostic("transport-failure");

        private void ObserveSceneEvents()
        {
            var current = manager.SceneManager;
            if (ReferenceEquals(observedSceneManager, current)) return;
            if (observedSceneManager != null) observedSceneManager.OnSceneEvent -= OnSceneEvent;
            observedSceneManager = current;
            if (observedSceneManager != null) observedSceneManager.OnSceneEvent += OnSceneEvent;
        }

        private void ApprovalRejected(ulong clientId, string reason)
            => Diagnostic("approval-rejected", $"client={clientId} reason={reason} approved={approvals.Count} players={manager.ConnectedClientsIds.Count}");

        private void Diagnostic(string phase, string metadata = "")
        {
            // A fixed lifetime cap avoids packet/tick noise and bounds logging
            // even when unapproved peers repeatedly attempt to connect.
            if (diagnosticMessages > MaxDiagnosticMessages) return;
            if (diagnosticMessages++ == MaxDiagnosticMessages)
            { Debug.Log($"ACTOR_NETWORK phase=log-limit limit={MaxDiagnosticMessages}"); return; }
            Debug.Log($"ACTOR_NETWORK utc_ms={DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()} phase={phase} {metadata}");
        }

        private static string DiagnosticHost(string value)
        {
            if (string.IsNullOrEmpty(value) || value.Length > 253) return "invalid";
            foreach (char c in value)
                if (!(c >= 'a' && c <= 'z') && !(c >= 'A' && c <= 'Z') && !(c >= '0' && c <= '9')
                    && c != '.' && c != '-' && c != ':' && c != '[' && c != ']') return "invalid";
            return value;
        }

        private static string DiagnosticLabel(string value)
        {
            if (string.IsNullOrEmpty(value)) return "none";
            var result = new StringBuilder(Math.Min(value.Length, 128));
            foreach (char c in value.Take(128)) result.Append(char.IsLetterOrDigit(c) || c == '-' || c == '_' || c == '.' ? c : '_');
            return result.ToString();
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
            manager.OnTransportFailure -= OnTransportFailure;
            if (transport != null) transport.OnTransportEvent -= OnTransportEvent;
            if (observedSceneManager != null) observedSceneManager.OnSceneEvent -= OnSceneEvent;
        }
    }
}
