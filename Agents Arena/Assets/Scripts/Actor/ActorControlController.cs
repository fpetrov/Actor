using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using Actor.Characters;
using Actor.Lobby;
using Actor.Networking;
using Actor.World;
using Actor.Sdk;
using Actor.Transport.Grpc;
using Unity.Netcode;
using UnityEngine;

namespace Actor.GameAdapter
{
    public enum ActorControlMode { Human, Runtime }

    [DefaultExecutionOrder(-80)]
    public sealed class ActorControlController : MonoBehaviour
    {
#if !UNITY_SERVER
        [Serializable] private sealed class Report
        {
            public string mode, state, actorId, roomId, sessionId, errorCode, error;
            public string[] errors, warnings;
            public bool keyboardEnabled;
            public Vector2 position, velocity;
            public long observations, messagesSent, messagesReceived, commands, bytesSent, bytesReceived;
            public double meanFps, maxPumpMs, elapsedSeconds;
            public long managedHeapStart, managedHeapCurrent;
            public ulong stationSignals, lastSignalRevision;
            public string lastSignalId, lastSignalKind, lastSignalSourceActorId;
            public long commReceivedCount, receiptsServerAcceptedCount, receiptsClientReceivedCount, receiptsRejectedCount, diagnosticResultsCount;
            public string lastMessageId, lastMessageSenderActorId, lastMessageTaskId, calibrationTaskId, calibrationStatus, publicCalibrationStatus;
            public ulong calibrationRevision;
            public bool ownCalibrationAssignment;
            public double meanMessageLatencyMs;
        }
        private NetworkCharacter owner;
        private PlayerInputSource keyboard;
        private AgentInputSource agent;
        private AgentsArenaGameAdapter adapter;
        private ActorBridgeClient bridge;
        private ActorControlMode mode;
        private BridgeEndpoint endpoint;
        private string configurationError, reportPath;
        private float commandTimeout = 0.25f;
        private double observationRate = 15, reconnectAt, nextReport, startedAt;
        private int reconnectAttempt;
        private bool retryScheduled, shuttingDown;
        private long frames, heapStart;
        private readonly List<string> errors = new(), warnings = new();
        public ActorControlMode Mode => mode;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap()
        {
            if (FindAnyObjectByType<ActorControlController>() != null) return;
            var root = new GameObject("ACTOR Client Control");
            DontDestroyOnLoad(root);
            root.AddComponent<ActorControlController>();
        }
        private void Awake()
        {
            mode = string.Equals(Argument("--control-mode"), "actor", StringComparison.OrdinalIgnoreCase)
                ? ActorControlMode.Runtime : ActorControlMode.Human;
            if (float.TryParse(Argument("--actor-command-timeout"), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out float timeout))
            {
                if (float.IsFinite(timeout)) commandTimeout = Mathf.Clamp(timeout, 0.05f, 2);
                else configurationError = "Movement timeout must be finite.";
            }
            if (double.TryParse(Argument("--actor-observation-rate"), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out double rate))
            {
                if (!double.IsNaN(rate) && !double.IsInfinity(rate)) observationRate = Math.Clamp(rate, 1, 60);
                else configurationError = "Observation rate must be finite.";
            }
            string token = Environment.GetEnvironmentVariable("ACTOR_BRIDGE_TOKEN");
            try
            {
                endpoint = new BridgeEndpoint(Argument("--actor-endpoint", "http://127.0.0.1:50051"), token);
            }
            catch (ArgumentException) { configurationError = "Start the local Runtime launcher to configure a loopback endpoint and session token."; }
            catch (UriFormatException) { configurationError = "The ACTOR Runtime endpoint is not a valid loopback HTTP address."; }
            reportPath = Argument("--actor-report");
            if (reportPath != null) Application.logMessageReceived += OnLog;
            startedAt = Time.realtimeSinceStartupAsDouble;
            heapStart = GC.GetTotalMemory(false);
            NetworkCharacter.LocalOwnerSpawned += Attach;
            StartCoroutine(AutoJoin());
        }
        private IEnumerator AutoJoin()
        {
            string code = Argument("--auto-room");
            // Explicit probes own their lobby workflow; ordinary clients can auto-join independently.
            if (code == null || Argument("--probe-role") != null) yield break;
            yield return new WaitForSecondsRealtime(0.5f);
            var controller = FindAnyObjectByType<LobbyMenuController>();
            var view = FindAnyObjectByType<LobbyMenuView>();
            if (controller == null || view == null) yield break;
            view.PlayerName = Argument("--player-name", mode == ActorControlMode.Runtime ? "Agent Pilot" : "Human Player");
            view.DirectRoomCode = code;
            controller.JoinRoom(code);
        }
        private void Update()
        {
            if (shuttingDown) return;
            ++frames;
            if (owner == null || !owner.IsSpawned)
            {
                if (bridge != null || adapter != null) Detach();
                var manager = NetworkManager.Singleton;
                var player = manager != null && manager.IsConnectedClient ? manager.SpawnManager.GetLocalPlayerObject() : null;
                var character = player != null ? player.GetComponent<NetworkCharacter>() : null;
                if (character != null && character.IsOwner)
                    Attach(character);
            }
            if (owner != null && owner.IsSpawned && mode == ActorControlMode.Runtime)
            {
                if (bridge == null && endpoint != null && configurationError == null && owner.HasAuthoritativeSnapshot
                    && !string.IsNullOrEmpty(owner.AuthoritativeActorId)) BeginBridge();
                bridge?.Pump(Time.realtimeSinceStartupAsDouble);
                if (bridge != null && bridge.State == ActorConnectionState.Error)
                {
                    agent.Clear();
                    if (bridge.CanRetry && !retryScheduled)
                    { retryScheduled = true; reconnectAt = Time.realtimeSinceStartupAsDouble + Math.Min(5, 1 << Math.Min(reconnectAttempt++, 2)); }
                    if (retryScheduled && Time.realtimeSinceStartupAsDouble >= reconnectAt)
                    { retryScheduled = false; bridge.Abort(); _ = bridge.StartAsync(endpoint); }
                }
                else if (bridge?.State == ActorConnectionState.Connected) { reconnectAttempt = 0; retryScheduled = false; }
            }
            if (reportPath != null && Time.realtimeSinceStartupAsDouble >= nextReport)
            { nextReport = Time.realtimeSinceStartupAsDouble + 0.5; SaveReport(); }
        }
        private void Attach(NetworkCharacter character)
        {
            if (shuttingDown || owner == character) return;
            if (owner != null) Detach();
            owner = character;
            keyboard = owner.GetComponent<PlayerInputSource>();
            agent = owner.GetComponent<AgentInputSource>() ?? owner.gameObject.AddComponent<AgentInputSource>();
            agent.ConfigureTimeout(commandTimeout);
            ApplyInputMode(false);
        }
        private void ApplyInputMode(bool forceHumanSource = true)
        {
            if (owner == null) return;
            agent.Clear();
            keyboard.enabled = mode == ActorControlMode.Human;
            agent.enabled = mode == ActorControlMode.Runtime;
            if (mode == ActorControlMode.Runtime) owner.SetLocalInputSource(agent);
            else if (forceHumanSource) owner.SetLocalInputSource(keyboard);
        }
        public void SetMode(ActorControlMode next)
        {
            if (mode == next) return;
            CloseBridge();
            mode = next;
            reconnectAttempt = 0;
            retryScheduled = false;
            ApplyInputMode();
        }
        private void BeginBridge()
        {
            try
            {
                adapter = new AgentsArenaGameAdapter(owner, agent, Application.version, () => mode == ActorControlMode.Runtime && owner != null && owner.IsSpawned);
                bridge = new ActorBridgeClient(adapter, () => new GrpcActorTransport(), new ActorBridgeOptions { ObservationRateHz = observationRate });
                _ = bridge.StartAsync(endpoint);
            }
            catch (Exception exception)
            {
                CloseBridge();
                configurationError = "Could not initialise the ACTOR bridge (" + exception.GetType().Name + ").";
            }
        }
        private void CloseBridge()
        {
            bridge?.Abort();
            bridge?.Dispose();
            bridge = null;
            adapter?.Dispose();
            adapter = null;
            if (agent != null) agent.Clear();
        }
        private void Detach()
        { CloseBridge(); owner = null; keyboard = null; agent = null; retryScheduled = false; }
        private void OnGUI()
        {
            var box = new Rect(Mathf.Max(12, Screen.width - 335), 12, 323, 108);
            GUILayout.BeginArea(box, GUI.skin.box);
            GUILayout.Label("Control mode");
            GUILayout.BeginHorizontal();
            if (GUILayout.Toggle(mode == ActorControlMode.Human, "Human", GUI.skin.button)) SetMode(ActorControlMode.Human);
            if (GUILayout.Toggle(mode == ActorControlMode.Runtime, "ACTOR Runtime", GUI.skin.button)) SetMode(ActorControlMode.Runtime);
            GUILayout.EndHorizontal();
            string state = mode == ActorControlMode.Human ? "Disconnected" : configurationError != null ? "Error" : bridge?.State.ToString() ?? "Connecting";
            GUILayout.Label("ACTOR Runtime: " + state);
            string detail = mode == ActorControlMode.Runtime ? configurationError ?? bridge?.LastErrorMessage : "WASD / arrow keys";
            // Keep the IMGUI control topology identical during Layout/Repaint when async state changes.
            GUILayout.Label(detail ?? "");
            GUILayout.EndArea();
        }
        private void OnLog(string message, string stack, LogType type)
        {
            if (type == LogType.Error || type == LogType.Exception || type == LogType.Assert) { if (errors.Count < 32) errors.Add(message); }
            if (type == LogType.Warning && warnings.Count < 32) warnings.Add(message);
        }
        private void SaveReport()
        {
            var diagnostic = bridge?.Diagnostics;
            var console = SignalConsole.Current;
            var cooperation = owner != null ? owner.GetComponent<NetworkCooperation>() : null;
            var signal = console != null ? console.LastReceivedSignal : null;
            double elapsed = Time.realtimeSinceStartupAsDouble - startedAt;
            var report = new Report
            {
                mode = mode.ToString(), state = bridge?.State.ToString() ?? "Disconnected", actorId = owner?.AuthoritativeActorId,
                roomId = owner != null ? owner.GetComponent<NetworkRoomMembership>().CurrentRoomId : null,
                sessionId = bridge?.SessionId, errorCode = bridge?.LastErrorCode, error = bridge?.LastErrorMessage,
                keyboardEnabled = keyboard != null && keyboard.enabled,
                position = owner != null ? owner.AuthoritativePosition : Vector2.zero,
                velocity = owner != null ? owner.AuthoritativeVelocity : Vector2.zero,
                observations = diagnostic?.ObservationsSent ?? 0, messagesSent = diagnostic?.MessagesSent ?? 0,
                messagesReceived = diagnostic?.MessagesReceived ?? 0, commands = diagnostic?.CommandsDispatched ?? 0,
                bytesSent = diagnostic?.BytesSent ?? 0, bytesReceived = diagnostic?.BytesReceived ?? 0,
                maxPumpMs = diagnostic?.MaxPumpMilliseconds ?? 0, elapsedSeconds = elapsed, meanFps = elapsed > 0 ? frames / elapsed : 0,
                managedHeapStart = heapStart, managedHeapCurrent = GC.GetTotalMemory(false), errors = errors.ToArray(), warnings = warnings.ToArray(),
                stationSignals = console != null ? console.ReceivedSignalCount : 0, lastSignalId = signal?.SignalId,
                lastSignalKind = signal?.SignalKind.ToString(), lastSignalSourceActorId = signal?.SourceActorId,
                lastSignalRevision = signal?.ServerRevision ?? 0,
                commReceivedCount = cooperation?.MessageCount ?? 0, receiptsServerAcceptedCount = cooperation?.ServerAcceptedCount ?? 0,
                receiptsClientReceivedCount = cooperation?.ClientReceivedCount ?? 0, receiptsRejectedCount = cooperation?.RejectedCount ?? 0,
                diagnosticResultsCount = cooperation?.DiagnosticCount ?? 0, lastMessageId = cooperation?.LastMessageId,
                lastMessageSenderActorId = cooperation?.LastMessageSender, lastMessageTaskId = cooperation?.LastMessageTask,
                meanMessageLatencyMs = cooperation?.MeanMessageLatencyMs ?? 0, calibrationTaskId = cooperation?.TaskState.TaskId,
                calibrationStatus = cooperation?.TaskState.Status.ToString(), publicCalibrationStatus = cooperation?.Progress.Status.ToString(),
                calibrationRevision = cooperation?.TaskState.ServerRevision ?? 0, ownCalibrationAssignment = cooperation != null && cooperation.TaskState.TaskId.Length > 0
            };
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(reportPath)));
            File.WriteAllText(reportPath, JsonUtility.ToJson(report, true));
        }
        private void OnDisable() => Shutdown();
        private void OnDestroy() => Shutdown();
        private void OnApplicationQuit() => Shutdown();
        private void Shutdown()
        {
            if (shuttingDown) return;
            shuttingDown = true;
            NetworkCharacter.LocalOwnerSpawned -= Attach;
            if (reportPath != null) { SaveReport(); Application.logMessageReceived -= OnLog; }
            Detach();
        }
        private static string Argument(string name, string fallback = null)
        {
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, name);
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : fallback;
        }
#endif
    }
}
