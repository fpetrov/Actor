using System;
using Actor.Networking;
using AgentsArena.ActorProtocol.V1;
using UnityEngine;
using UnityEngine.InputSystem;

namespace Actor.World
{
    // An authored game object with a fixed station-wide audience. It is not a
    // public service or an ACTOR actuator, and never accepts free-form text.
    public sealed class SignalConsole : MonoBehaviour
    {
        public const string EntityId = "main-hall-signal-console";
        public static SignalConsole Current { get; private set; }
        public event Action<StationSignalEvent> SignalReceived;
        public StationSignalEvent LastReceivedSignal => lastSignal?.Clone();
        public ulong ReceivedSignalCount { get; private set; }
        public Vector2 InteractionPosition => (Vector2)transform.position + new Vector2(.3f, -.25f);
        public static double InteractionRadius => StationSignalRules.InteractionRadius;
        internal double ReadyAt { get; private set; }
        private ulong serverRevision, receivedRevision;
        private StationSignalEvent lastSignal;
#if !UNITY_SERVER
        private bool opened;
        private double localReadyAt, noticeUntil;
        private GUIStyle title, body;
        internal void OpenFromFocus() => opened = true;
        internal void CloseFromFocus() => opened = false;
#endif
        private void OnEnable() { if (Current == null) Current = this; }
        private void OnDisable() { if (Current == this) Current = null; }

        internal StationSignalEvent CreateAuthoritativeSignal(StationSignalKind kind, string sourceActor, double now)
        {
            if (Unity.Netcode.NetworkManager.Singleton == null || !Unity.Netcode.NetworkManager.Singleton.IsServer)
                throw new InvalidOperationException("Only the game server can create station signals.");
            ReadyAt = now + StationSignalRules.StationCooldownSeconds;
            return StationSignalRules.Create(kind, sourceActor, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), ++serverRevision);
        }

        internal void ReceiveAuthoritativeSignal(StationSignalEvent signal)
        {
            if (signal.ServerRevision <= receivedRevision || signal.Audience != StationSignalAudience.StationWide) return;
            receivedRevision = signal.ServerRevision;
            ++ReceivedSignalCount;
            lastSignal = signal.Clone();
#if !UNITY_SERVER
            noticeUntil = Time.realtimeSinceStartupAsDouble + 8;
#endif
            SignalReceived?.Invoke(signal);
        }

#if !UNITY_SERVER
        private bool NearHuman(out NetworkStationSignals owner)
        {
            owner = NetworkStationSignals.LocalOwner;
            return owner != null && owner.IsHumanOwner
                && (owner.InteractionPosition - InteractionPosition).sqrMagnitude <= StationSignalRules.InteractionRadius * StationSignalRules.InteractionRadius;
        }
        private void Update()
        {
            if (!NearHuman(out _)) { opened = false; return; }
            if (NetworkCooperation.LocalOwner == null && Application.isFocused && Keyboard.current != null && Keyboard.current.eKey.wasPressedThisFrame) opened = !opened;
        }
        private void OnGUI()
        {
            if (NetworkStationSignals.LocalOwner == null || Camera.main == null) return;
            if (title == null)
            {
                title = new GUIStyle(GUI.skin.label) { fontSize = 16, fontStyle = FontStyle.Bold };
                body = new GUIStyle(GUI.skin.label) { fontSize = 14, wordWrap = true };
            }
            Vector3 screen = Camera.main.WorldToScreenPoint(transform.position + Vector3.up * .55f);
            if (screen.z > 0 && screen.x > 0 && screen.x < Screen.width && screen.y > 0 && screen.y < Screen.height)
                GUI.Label(new Rect(screen.x - 80, Screen.height - screen.y - 14, 160, 26), "SIGNAL CONSOLE", GUI.skin.box);
            if (NearHuman(out var owner) && (opened || NetworkCooperation.LocalOwner == null))
            {
                float width = Mathf.Min(460, Screen.width - 32);
                var area = new Rect((Screen.width - width) * .5f, Screen.height - (opened ? 198 : 54), width, opened ? 182 : 38);
                GUI.Box(area, GUIContent.none);
                if (!opened)
                {
                    if (GUI.Button(new Rect(area.x + 8, area.y + 6, width - 16, 26), "E  ·  Use Signal Console")) opened = true;
                }
                else
                {
                    GUI.Label(new Rect(area.x + 14, area.y + 8, width - 70, 26), "Signal Console  ·  Training channel", title);
                    if (GUI.Button(new Rect(area.xMax - 44, area.y + 8, 30, 24), "×")) opened = false;
                    GUI.Label(new Rect(area.x + 14, area.y + 38, width - 28, 38), "Broadcast to all connected station personnel. No emergency is simulated.", body);
                    bool oldEnabled = GUI.enabled;
                    GUI.enabled = Time.realtimeSinceStartupAsDouble >= localReadyAt;
                    if (GUI.Button(new Rect(area.x + 14, area.y + 82, (width - 38) / 2, 40), "Routine Notice")) Send(owner, StationSignalKind.RoutineNotice);
                    if (GUI.Button(new Rect(area.x + 24 + (width - 38) / 2, area.y + 82, (width - 38) / 2, 40), "Urgent Recall")) Send(owner, StationSignalKind.UrgentRecall);
                    GUI.enabled = oldEnabled;
                    GUI.Label(new Rect(area.x + 14, area.y + 132, width - 28, 42), owner.InteractionStatus, body);
                }
            }
            if (lastSignal != null && Time.realtimeSinceStartupAsDouble < noticeUntil)
            {
                float width = Mathf.Min(440, Screen.width - 32);
                var area = new Rect(18, 112, width, 88);
                GUI.Box(area, GUIContent.none);
                GUI.Label(new Rect(area.x + 10, area.y + 8, width - 20, 24),
                    lastSignal.SignalKind == StationSignalKind.UrgentRecall ? "Station · URGENT RECALL" : "Station · Routine notice", title);
                GUI.Label(new Rect(area.x + 10, area.y + 34, width - 20, 48), lastSignal.Message, body);
            }
        }
        private void Send(NetworkStationSignals owner, StationSignalKind kind)
        {
            owner.Request(kind);
            localReadyAt = Time.realtimeSinceStartupAsDouble + StationSignalRules.PlayerCooldownSeconds;
        }
#endif
    }
}
