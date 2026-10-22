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
        private readonly BroadcastObserverRules broadcastObservers = new();
        public bool IsBroadcastObserver { get; private set; }
        internal LobbyClientSettings ClientSettings => settings;
        internal bool IsObserver(ulong client) => broadcastObservers.Contains(client);
        internal IEnumerable<BroadcastObserverRules.Binding> ObserverBindings => broadcastObservers.Active;
        private ResumeAdmissionRegistry resumeRegistry;
        private readonly Dictionary<string, NetworkObject> retainedActors = new();
        private readonly Dictionary<string, Vector3> lastRoots = new();
        private double nextCredentialRenewal;
        // Memory only, transport/controller-private. Never PlayerPrefs, report,
        // protobuf observations, Runtime, Brain context or diagnostic logging.
        private string resumeCredential, resumeRoom, resumeMatch;
        private long resumeExpires;
        private ulong resumeGeneration;
        private int resumeAttemptEpoch;
        public bool IsResuming { get; private set; }
        public bool IsReturningToLobby => returningToLobby;
        public long ResumeExpiresAtUnixMs => resumeExpires;
        public bool CanResume => !IsConnected && !IsResuming && !returningToLobby && GameMode == "SocialDeduction"
            && !string.IsNullOrEmpty(resumeCredential) && resumeRoom == roomCode && resumeMatch == matchId
            && resumeExpires > DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        internal IEnumerable<NetworkCharacter> RetainedSocialPlayers => retainedActors.Values.Where(p => p != null && p.IsSpawned)
            .Select(p => p.GetComponent<NetworkCharacter>()).Where(p => p != null
                && resumeRegistry?.Find(p.AuthoritativeActorId) is { Connected: false } binding
                && binding.GraceUntil > DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
        public event Action Connected;
        public event Action<string> Disconnected;
        public string RoomCode => roomCode;
        public string MatchId => matchId;
        public string GameMode { get; private set; } = "Sandbox";
        private string GameplayScene => GameMode == "SocialDeduction" ? "SocialDeduction" : settings.gameplayScene;
        internal int ApprovedJoinOrder(ulong clientId) => spawnSlots.TryGetValue(clientId, out int slot) ? slot : int.MaxValue;
        internal int CosmeticIndexForActor(string actorId) => (resumeRegistry?.Find(actorId)?.Slot ?? 0) % 6;
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
            BroadcastSession.Ensure(manager);
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
            if (IsBroadcastObserver && !admission.joinTicket.StartsWith(ObserverTicketCodec.WirePrefix, StringComparison.Ordinal))
                throw new InvalidOperationException("An observer cannot use player admission.");
            if (manager.IsListening || returningToLobby) throw new InvalidOperationException("A network session is already active or closing.");
            if (!admission.resume) ForgetResume();
            Diagnostic("admission-received", $"host={DiagnosticHost(admission.host)} port={admission.port}");
            roomCode = admission.roomCode;
            matchId = admission.lobbyId;
            GameMode = admission.gameMode == "SocialDeduction" ? "SocialDeduction" : "Sandbox";
            SocialVisibilitySystem.Ensure(manager);
            // Explicit headless QA only: a loopback UDP relay can drop this
            // client's transport while its private in-memory credential lives.
            string[] localArgs = Environment.GetCommandLineArgs();
            int proxyPort = 0;
            bool proxyProbe = Application.isBatchMode && GameMode == "SocialDeduction" && Argument(localArgs, "--social-resume-report") != null
                && int.TryParse(Argument(localArgs, "--resume-probe-proxy-port"), out proxyPort) && proxyPort >= 1024 && proxyPort <= 65535;
            transport.SetConnectionData(proxyProbe ? "127.0.0.1" : admission.host, proxyProbe ? (ushort)proxyPort : (ushort)admission.port);
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

        internal void ConnectObserver(JoinInfo admission, ObserverTicketClaims identity)
        {
            if (admission.gameMode != "SocialDeduction" || identity == null || identity.lobbyId != admission.lobbyId)
                throw new ArgumentException("Observer admission does not match this Social match.");
            IsBroadcastObserver = true;
            BroadcastSession.Ensure(manager).Expect(new AgentsArena.ActorProtocol.V1.BroadcastScope
            { BroadcastSessionId = identity.broadcastSessionId, MatchId = identity.lobbyId, ObserverId = identity.observerId,
                ConnectionGeneration = identity.connectionGeneration, ViewEpoch = 1 });
            Connect(admission);
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
            ForgetResume();
            if (!returningToLobby) StartCoroutine(LeaveAndReturn());
        }
        public void ForgetResume()
        { ++resumeAttemptEpoch; IsResuming = false; resumeCredential = resumeRoom = resumeMatch = null; resumeExpires = 0; resumeGeneration = 0; }
        public void ResumeConnection()
        {
            if (!CanResume) return;
            StartCoroutine(ResumeRoutine());
        }
        private IEnumerator ResumeRoutine()
        {
            int epoch = resumeAttemptEpoch; IsResuming = true; JoinInfo admission = null; string error = null;
            yield return new LobbyApiClient(settings).Resume(resumeRoom, resumeCredential, value => admission = value, message => error = message);
            if (epoch != resumeAttemptEpoch || returningToLobby) yield break;
            if (admission == null) { IsResuming = false; Disconnected?.Invoke(error ?? "Could not resume this match."); yield break; }
            try { admission.resume = true; Connect(admission); }
            catch (InvalidOperationException) { IsResuming = false; Disconnected?.Invoke("The previous connection is still closing. Please retry shortly."); }
        }
        private IEnumerator LeaveAndReturn()
        {
            var owner = NetworkSocialMatch.LocalOwner;
            if (IsConnected && owner != null && GameMode == "SocialDeduction")
            {
                owner.RequestExplicitLeave();
                double until = Time.realtimeSinceStartupAsDouble + 1;
                while (IsConnected && !owner.ExplicitLeaveAcknowledged && Time.realtimeSinceStartupAsDouble < until) yield return null;
            }
            yield return ReturnToLobby("Disconnected from the room.");
        }
        internal void AcceptResumeCredential(string actor, ulong generation, string credential, long expires)
        {
            var owner = NetworkSocialMatch.LocalOwner;
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            if (!IsConnected || owner == null || !owner.IsOwner || owner.Self != actor || credential == null
                || credential.Length > ResumeCredentialCodec.MaximumLength || !credential.StartsWith("ar1.", StringComparison.Ordinal)
                || generation == 0 || generation < resumeGeneration || generation < owner.Own.ConnectionGeneration
                || expires <= now || expires > now + 180000) return;
            resumeCredential = credential; resumeExpires = expires; resumeGeneration = generation; resumeRoom = roomCode; resumeMatch = matchId;
            owner.AcknowledgeResumeCredential(generation);
        }
        internal void BindSocialPlayer(ulong clientId, NetworkObject player)
        {
            if (!manager.IsServer || GameMode != "SocialDeduction" || !approvals.TryGetValue(clientId, out var claims)) return;
            player.DontDestroyWithOwner = true; retainedActors[claims.actorId] = player;
            lastRoots[claims.actorId] = player.transform.position;
            nextCredentialRenewal = 0;
        }
        internal void AcknowledgeResume(string actor, ulong generation, ulong clientId) => resumeRegistry?.Acknowledge(actor, generation, clientId);
        internal void RevokeResume(string actor) => resumeRegistry?.Revoke(actor);
        internal bool HasPrivateResumeCredential => !string.IsNullOrEmpty(resumeCredential) && resumeExpires > DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        internal IEnumerator ProbeConnectedResumeRejected(Action<bool> completed)
        {
            if (!Application.isBatchMode || Argument(Environment.GetCommandLineArgs(), "--social-resume-report") == null
                || !IsConnected || !HasPrivateResumeCredential) { completed(false); yield break; }
            JoinInfo admission = null; string code = null;
            yield return new LobbyApiClient(settings).Resume(resumeRoom, resumeCredential, value => admission = value,
                _ => { }, value => code = value);
            completed(admission == null && code == "resume_busy");
        }
        private void Update()
        {
            if (manager == null || !manager.IsServer || resumeRegistry == null) return;
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            foreach (var pair in retainedActors.ToArray())
            {
                var binding = resumeRegistry.Find(pair.Key);
                if (pair.Value == null || !pair.Value.IsSpawned) { retainedActors.Remove(pair.Key); continue; }
                lastRoots[pair.Key] = pair.Value.GetComponent<NetworkCharacter>().AuthoritativePosition;
                if (binding != null && !binding.Connected && binding.GraceUntil <= now)
                { pair.Value.Despawn(); retainedActors.Remove(pair.Key); }
            }
            if (Time.realtimeSinceStartupAsDouble < nextCredentialRenewal) return;
            nextCredentialRenewal = Time.realtimeSinceStartupAsDouble + 20;
            foreach (var binding in resumeRegistry.Bindings.Where(b => b.Connected).ToArray())
            {
                if (!retainedActors.TryGetValue(binding.Actor, out var player) || player == null || !player.IsSpawned) continue;
                var credential = resumeRegistry.Credential(binding.Actor, matchId, gameVersion, protocolVersion, now);
                if (credential != null) player.GetComponent<NetworkSocialMatch>().SendResumeCredential(
                    ResumeCredentialCodec.Sign(JsonUtility.ToJson(credential), secret), credential.expiresAt * 1000, binding.Generation);
            }
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
            if (GameMode == "SocialDeduction")
            {
                try
                {
                    var json = Environment.GetEnvironmentVariable("ACTOR_SOCIAL_CONFIG");
                    var config = string.IsNullOrEmpty(json) ? new ResumeConfiguration() : JsonUtility.FromJson<ResumeConfiguration>(json);
                    resumeRegistry = new ResumeAdmissionRegistry(maxPlayers, config.ReconnectGraceMs);
                }
                catch (ArgumentException) { Debug.LogError("ACTOR: invalid resume configuration."); Application.Quit(2); yield break; }
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
                    currentPlayers = resumeRegistry != null ? resumeRegistry.Bindings.Count(b => b.Connected || b.GraceUntil > DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()) : approvals.Count,
                    serverState = approvals.Count > 0 ? "Running" : "Ready",
                    connectedNonces = connectedNonces,
                    connectedActorIds = approvals.Values.Select(c => c.actorId).Where(id => Guid.TryParseExact(id, "N", out _)).ToArray(),
                    connectedObserverSessions = broadcastObservers.Active.Select(b => new HeartbeatObserver
                    { broadcastSessionId = b.Scope.BroadcastSessionId, observerId = b.Scope.ObserverId, connectionGeneration = b.Scope.ConnectionGeneration }).ToArray()
                };
                bool accepted = false;
                yield return Callback("heartbeat", JsonUtility.ToJson(heartbeat), success => accepted = success,
                    payload => accepted = accepted && ApplyObserverRevocations(payload));
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
            if (ticket.StartsWith(ObserverTicketCodec.WirePrefix, StringComparison.Ordinal))
            {
                if (GameMode != "SocialDeduction" || !ObserverTicketCodec.TryVerify(ticket, secret, out string observerPayload)) return;
                ObserverTicketClaims observer;
                try { observer = JsonUtility.FromJson<ObserverTicketClaims>(observerPayload); }
                catch (ArgumentException) { return; }
                long observerNow = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                if (!ObserverTicketCodec.ClaimsMatch(observer, matchId, gameVersion, protocolVersion, observerNow)) return;
                var scope = new AgentsArena.ActorProtocol.V1.BroadcastScope { BroadcastSessionId = observer.broadcastSessionId,
                    MatchId = observer.lobbyId, ObserverId = observer.observerId, ConnectionGeneration = observer.connectionGeneration, ViewEpoch = 1 };
                if (!broadcastObservers.Admit(request.ClientNetworkId, scope, observer.nonce, observer.expiresAt, observerNow)) return;
                response.Approved = true; response.CreatePlayerObject = false; response.Reason = "";
                return; // Never touch player approvals, capacity, slots, resume or RNG.
            }
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
            ResumeAdmissionRegistry.Binding binding = null;
            if (resumeRegistry != null && !resumeRegistry.TryAdmit(claims, request.ClientNetworkId, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), out binding, out string resumeReason))
            { response.Reason = "This connection cannot be admitted. Retry resume or return to the lobby."; ApprovalRejected(request.ClientNetworkId, resumeReason); return; }
            if (resumeRegistry == null && (manager.ConnectedClientsIds.Count >= maxPlayers || spawnSlots.Count >= maxPlayers))
            { response.Reason = "The room is full."; ApprovalRejected(request.ClientNetworkId, "room-full"); return; }
            int slot = binding?.Slot ?? Enumerable.Range(0, maxPlayers).First(i => !spawnSlots.ContainsValue(i));
            Vector3 position = SpawnPosition(slot);
            if (claims.resume)
            {
                if (retainedActors.TryGetValue(claims.actorId, out var previous) && previous != null && previous.IsSpawned)
                { position = previous.GetComponent<NetworkCharacter>().AuthoritativePosition; lastRoots[claims.actorId] = position; previous.Despawn(); retainedActors.Remove(claims.actorId); }
                else if (lastRoots.TryGetValue(claims.actorId, out var last)) position = last;
            }
            approvals[request.ClientNetworkId] = claims;
            spawnSlots[request.ClientNetworkId] = slot;
            consumedTickets[claims.nonce] = claims.expiresAt;
            response.Approved = true;
            response.CreatePlayerObject = true;
            response.Position = position;
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
            if (manager.IsServer && IsObserver(clientId)) Debug.Log("ACTOR_OBSERVER_CONNECTED");
            else
            {
                Diagnostic("ngo-connected", $"client={clientId} players={approvals.Count}");
                if (manager.IsServer) Debug.Log($"ACTOR_PLAYER_CONNECTED client={clientId} players={approvals.Count}");
            }
            if (!manager.IsClient || clientId != manager.LocalClientId) return;
            IsResuming = false;
            if (lobbyCamera != null) lobbyCamera.gameObject.SetActive(false);
            Connected?.Invoke();
        }

        private void OnDisconnected(ulong clientId)
        {
            if (manager.IsServer)
            {
                if (IsObserver(clientId)) { broadcastObservers.Remove(clientId); Debug.Log("ACTOR_OBSERVER_DISCONNECTED"); return; }
                if (approvals.TryGetValue(clientId, out var lost) && retainedActors.TryGetValue(lost.actorId ?? "", out var player)
                    && player != null && player.IsSpawned) player.GetComponent<NetworkCharacter>().HaltForTransportLoss();
                resumeRegistry?.Disconnected(clientId, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
                approvals.Remove(clientId); spawnSlots.Remove(clientId);
                Diagnostic("ngo-disconnected", $"client={clientId} event={transport.DisconnectEvent} players={approvals.Count}");
                Debug.Log($"ACTOR_PLAYER_DISCONNECTED client={clientId} players={approvals.Count}");
                return;
            }
            Diagnostic("ngo-disconnected", $"client={clientId} event={transport.DisconnectEvent}");
            if (clientId != manager.LocalClientId || returningToLobby) return;
            string reason = manager.DisconnectReason;
            StartCoroutine(ReturnToLobby(string.IsNullOrEmpty(reason) ? "Connection was lost. You can reconnect from the lobby." : reason));
        }

        private IEnumerator ReturnToLobby(string reason)
        {
            if (returningToLobby) yield break;
            ++connectionAttempt;
            Diagnostic("return-to-lobby", $"connected={manager.IsConnectedClient} listening={manager.IsListening}");
            returningToLobby = true;
            IsResuming = false;
            manager.Shutdown();
            yield return null;
            while (manager.ShutdownInProgress) yield return null;
            ObserveSceneEvents();
            Scene gameplay = SceneManager.GetSceneByName(GameplayScene);
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

        private IEnumerator Callback(string operation, string body, Action<bool> result, Action<string> response = null)
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
                if (request.result == UnityWebRequest.Result.Success) response?.Invoke(request.downloadHandler.text);
            }
        }

        private bool ApplyObserverRevocations(string payload)
        {
            if (string.IsNullOrEmpty(payload)) return true; // compatibility with a pre-observer control plane.
            if (payload.Length > 16384) return false;
            HeartbeatReply reply;
            try { reply = JsonUtility.FromJson<HeartbeatReply>(payload); }
            catch (ArgumentException) { return false; }
            if (reply == null || reply.revokedObserverSessions == null || reply.revokedObserverSessions.Length > 128
                || reply.revokedObserverSessions.Any(s => !BroadcastObserverRules.Id(s))) return false;
            foreach (string session in reply.revokedObserverSessions)
                foreach (ulong client in broadcastObservers.Revoke(session))
                    if (manager.ConnectedClients.ContainsKey(client)) manager.DisconnectClient(client, "Observer session ended.");
            return true;
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
        [Serializable] private sealed class Heartbeat { public int currentPlayers; public string serverState; public string[] connectedNonces, connectedActorIds; public HeartbeatObserver[] connectedObserverSessions; }
        [Serializable] private sealed class HeartbeatObserver { public string broadcastSessionId, observerId; public ulong connectionGeneration; }
        [Serializable] private sealed class HeartbeatReply { public string[] revokedObserverSessions; }
        [Serializable] private sealed class ResumeConfiguration { public long ReconnectGraceMs = 30000; }

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
