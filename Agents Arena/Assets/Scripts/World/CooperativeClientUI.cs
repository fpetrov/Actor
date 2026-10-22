using System;
using System.Linq;
using Actor.Networking;
using Actor.UI;
using AgentsArena.ActorProtocol.V1;
using UnityEngine;
#if !UNITY_SERVER
using UnityEngine.InputSystem;
using UnityEngine.UIElements;
#endif
namespace Actor.World
{
    public sealed class CooperativeClientUI : MonoBehaviour
    {
#if !UNITY_SERVER
        private NetworkCooperation owner;
        private IncidentClientUI investigation;
        private VisualElement hud, toolbar, sheet, content, chatSheet;
        private Label status;
        private CommunicationPanel chat;
        private CooperativeTerminal2D opened;
        private CooperativeTerminal2D[] terminals = Array.Empty<CooperativeTerminal2D>();
        private IncidentTerminal2D[] incidentTerminals = Array.Empty<IncidentTerminal2D>();
        private bool choosing, chatOpen, wasHuman;
        private string code = "", signature = "";
        private void Awake() { owner = GetComponent<NetworkCooperation>(); investigation = GetComponent<IncidentClientUI>(); }
        public void PrepareMessage(string recipientActor, string text, bool privateContent, string taskScope)
        {
            if (chat == null) Build(); if (chat == null) return;
            choosing = false; opened = null; investigation.CloseTerminal(); chatOpen = true;
            ArenaUI.Show(chatSheet, true); chat.Prepare(recipientActor, text, privateContent, taskScope);
        }
        private void Build()
        {
            if (ArenaUI.HudLayer == null) return;
            hud = ArenaUI.Panel("Station calibration", "hud", ArenaUI.HudLayer);
            status = ArenaUI.Text("", "body", hud);
            toolbar = ArenaUI.Element("hud-actions", ArenaUI.HudLayer);
            ArenaUI.Button("E · Interact", ToggleActions, "primary", toolbar);
            ArenaUI.Button("T · Messages", () => { chatOpen = !chatOpen; choosing = false; opened = null; investigation.CloseTerminal(); }, "", toolbar);
            sheet = ArenaUI.Panel("Station interaction", "action-sheet", ArenaUI.ScreenLayer);
            ArenaUI.Button("Close", () => { choosing = false; opened = null; }, "quiet", sheet);
            var scroll = new ScrollView(); sheet.Add(scroll); content = scroll.contentContainer;
            chatSheet = ArenaUI.Panel("Station communications", "action-sheet", ArenaUI.ScreenLayer);
            chat = new CommunicationPanel(owner, chatSheet, false);
            ArenaUI.EscapeRequested += Escape;
        }
        private void ToggleActions()
        {
            choosing = !choosing; opened = null; chatOpen = false; investigation.CloseTerminal(); SignalConsole.Current?.CloseFromFocus(); signature = "";
        }
        private Vector2 Position => GetComponent<CapsuleCollider2D>().bounds.center;
        private bool Near(CooperativeTerminal2D t) => t != null && (Position - t.InteractionPoint).sqrMagnitude <= t.Radius * t.Radius;
        private bool Near(IncidentTerminal2D t) => t != null && (Position - t.InteractionPoint).sqrMagnitude <= t.Radius * t.Radius;
        private bool NearSignal() => SignalConsole.Current != null && (Position - SignalConsole.Current.InteractionPosition).sqrMagnitude <= SignalConsole.InteractionRadius * SignalConsole.InteractionRadius;
        private string Self => GetComponent<NetworkCharacter>().AuthoritativeActorId;
        private string Correlation() => Guid.NewGuid().ToString("N");
        private void Update()
        {
            bool active = !SocialVisibilitySystem.IsSocial(Unity.Netcode.NetworkManager.Singleton) && owner.IsSpawned && owner.IsOwner && owner.IsClient;
            if (!active) { foreach (var p in new[] { hud, toolbar, sheet, chatSheet }) ArenaUI.Show(p, false); return; }
            if (hud == null) Build(); if (hud == null) return;
            if (terminals.Length == 0) terminals = FindObjectsByType<CooperativeTerminal2D>();
            if (incidentTerminals.Length == 0) incidentTerminals = FindObjectsByType<IncidentTerminal2D>();
            if (opened != null && !Near(opened)) opened = null;
            if (!owner.Human && wasHuman) { choosing = chatOpen = false; opened = null; code = ""; investigation.CloseTerminal(); }
            wasHuman = owner.Human;
            ArenaUI.Show(hud, true); ArenaUI.Show(toolbar, owner.Human);
            ArenaUI.Show(sheet, owner.Human && (choosing || opened != null)); ArenaUI.Show(chatSheet, chatOpen);
            string role = owner.TaskState.CoordinatorActorId == Self ? "Coordinator" : owner.TaskState.TechnicianActorId == Self ? "Technician" : "Observer";
            status.text = owner.Progress.Status + " · " + role + "\n" + owner.LastAction;
            chat.Tick(chatOpen, owner.Human);
            if (choosing) RenderChooser(); else if (opened != null) RenderTerminal();
            if (!owner.Human || ArenaUI.InputCaptured || Keyboard.current == null || !Application.isFocused) return;
            if (Keyboard.current.eKey.wasPressedThisFrame) ToggleActions();
            if (Keyboard.current.tKey.wasPressedThisFrame) { chatOpen = !chatOpen; choosing = false; opened = null; investigation.CloseTerminal(); }
            if (Keyboard.current.escapeKey.wasPressedThisFrame) { chatOpen = choosing = false; opened = null; investigation.CloseTerminal(); SignalConsole.Current?.CloseFromFocus(); }
        }
        private void RenderChooser()
        {
            var choices = terminals.Where(Near).ToArray(); var incidents = incidentTerminals.Where(Near).ToArray();
            string next = "choose|" + string.Join("|", choices.Select(t => t.EntityId)) + "|" + string.Join("|", incidents.Select(t => t.EntityId)) + "|" + NearSignal();
            if (next == signature) return; signature = next; content.Clear();
            ArenaUI.Text("Nearby interactions", "heading", content);
            foreach (var t in choices) ArenaUI.Button(t.DisplayName, () => { opened = t; choosing = false; signature = ""; }, "", content);
            foreach (var t in incidents) ArenaUI.Button(t.DisplayName, () => { investigation.ShowTerminal(t); choosing = false; }, "", content);
            if (NearSignal()) ArenaUI.Button("Signal Console", () => { SignalConsole.Current.OpenFromFocus(); choosing = false; }, "", content);
            if (choices.Length + incidents.Length == 0 && !NearSignal()) ArenaUI.Text("Approach a station terminal to interact.", "muted", content);
        }
        private void RenderTerminal()
        {
            bool diagnosticValid = owner.PrivateDiagnostic != null && owner.PrivateDiagnostic.ExpiresAtUnixMs > DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            string next = opened.EntityId + owner.TaskState.ToString() + (diagnosticValid ? owner.PrivateDiagnostic.ToString() : "expired") + owner.Human;
            if (next == signature) return; signature = next; content.Clear();
            ArenaUI.Text(opened.DisplayName, "heading", content);
            var task = owner.TaskState;
            ArenaUI.Text("State · " + (task.TaskId.Length > 0 ? task.Status : owner.Progress.Status), "body", content);
            if (opened.Kind == CooperativeTerminalKind.Diagnostic)
            {
                foreach (var peer in owner.Peers.Peers.Where(p => p.ActorId != Self))
                    ArenaUI.Button("Start with " + peer.DisplayName, () => owner.Submit(new StartCalibrationCommand { TechnicianActorId = peer.ActorId, CorrelationId = Correlation(), MatchId = owner.Peers.MatchId }), "", content);
                if (task.CoordinatorActorId != Self) return;
                ArenaUI.Button("Inspect private diagnostic", () => owner.Submit(new InspectDiagnosticCommand { TaskId = task.TaskId, CorrelationId = Correlation(), MatchId = owner.Peers.MatchId }), "primary", content);
                if (diagnosticValid)
                {
                    ArenaUI.Text("Your private authorization: " + owner.PrivateDiagnostic.AuthorizationCode, "body", content);
                    ArenaUI.Button("Prepare private message", () => PrepareMessage(task.TechnicianActorId, owner.PrivateDiagnostic.AuthorizationCode, true, task.TaskId), "", content);
                }
            }
            else
            {
                ArenaUI.Text("The assigned Technician must receive the Coordinator's private authorization.", "body", content);
                ArenaUI.Field("Authorization", code, value => code = value, 64, content);
                ArenaUI.Button("Submit authorization", () => owner.Submit(new SubmitCalibrationCommand { TaskId = task.TaskId, AuthorizationCode = code, CorrelationId = Correlation(), MatchId = owner.Peers.MatchId }), "primary", content);
            }
        }
        private void Escape() { chatOpen = choosing = false; opened = null; investigation.CloseTerminal(); SignalConsole.Current?.CloseFromFocus(); }
        private void OnDisable()
        {
            ArenaUI.EscapeRequested -= Escape;
            chat?.Dispose(); chat = null;
            foreach (var p in new[] { hud, toolbar, sheet, chatSheet }) p?.RemoveFromHierarchy();
            hud = toolbar = sheet = chatSheet = null;
        }
        private void OnDestroy() => OnDisable();
#endif
    }
}
