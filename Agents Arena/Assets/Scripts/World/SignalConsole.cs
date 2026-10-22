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
        private UnityEngine.UIElements.VisualElement panel, notice;
        private UnityEngine.UIElements.Label worldLabel, noticeTitle, noticeBody, actionStatus;
        private UnityEngine.UIElements.Button routine, urgent;
        internal void OpenFromFocus() => opened = true;
        internal void CloseFromFocus() => opened = false;
#endif
        private void OnEnable() { if (Current == null) Current = this; }
        private void OnDisable()
        {
            if (Current == this) Current = null;
#if !UNITY_SERVER
            panel?.RemoveFromHierarchy(); notice?.RemoveFromHierarchy(); worldLabel?.RemoveFromHierarchy(); panel = notice = null; worldLabel = null;
#endif
        }

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
            if (NetworkCooperation.LocalOwner == null && !Actor.UI.ArenaUI.InputCaptured && Application.isFocused && Keyboard.current != null && Keyboard.current.eKey.wasPressedThisFrame) opened = !opened;
        }
        private void LateUpdate()
        {
            if (NetworkStationSignals.LocalOwner == null || Actor.Networking.SocialVisibilitySystem.IsSocial(Unity.Netcode.NetworkManager.Singleton))
            { Actor.UI.ArenaUI.Show(panel, false); Actor.UI.ArenaUI.Show(notice, false); Actor.UI.ArenaUI.Show(worldLabel, false); return; }
            if (Actor.UI.ArenaUI.HudLayer == null) return;
            if (panel == null)
            {
                worldLabel = Actor.UI.ArenaUI.Text("Signal Console", "world-label", Actor.UI.ArenaUI.WorldLayer);
                panel = Actor.UI.ArenaUI.Panel("Signal Console · training", "action-sheet", Actor.UI.ArenaUI.ScreenLayer);
                Actor.UI.ArenaUI.Button("Close", () => opened = false, "quiet", panel);
                Actor.UI.ArenaUI.Text("Broadcast to all connected station personnel. No emergency is simulated.", "body", panel);
                routine = Actor.UI.ArenaUI.Button("Routine notice", () => Send(NetworkStationSignals.LocalOwner, StationSignalKind.RoutineNotice), "primary", panel);
                urgent = Actor.UI.ArenaUI.Button("Urgent recall", () => Send(NetworkStationSignals.LocalOwner, StationSignalKind.UrgentRecall), "", panel);
                actionStatus = Actor.UI.ArenaUI.Text("", "muted", panel);
                notice = Actor.UI.ArenaUI.Panel("", "death", Actor.UI.ArenaUI.ScreenLayer);
                noticeTitle = Actor.UI.ArenaUI.Text("", "heading", notice); noticeBody = Actor.UI.ArenaUI.Text("", "body", notice);
            }
            Actor.UI.ArenaUI.WorldLabel(worldLabel, transform.position + Vector3.up * .55f);
            Actor.UI.ArenaUI.Show(panel, opened && NearHuman(out _));
            routine.SetEnabled(Time.realtimeSinceStartupAsDouble >= localReadyAt);
            urgent.SetEnabled(Time.realtimeSinceStartupAsDouble >= localReadyAt);
            actionStatus.text = NetworkStationSignals.LocalOwner.InteractionStatus;
            bool showing = lastSignal != null && Time.realtimeSinceStartupAsDouble < noticeUntil;
            Actor.UI.ArenaUI.Show(notice, showing);
            if (showing)
            {
                noticeTitle.text = lastSignal.SignalKind == StationSignalKind.UrgentRecall ? "Station · urgent recall" : "Station · routine notice";
                noticeBody.text = lastSignal.Message;
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
