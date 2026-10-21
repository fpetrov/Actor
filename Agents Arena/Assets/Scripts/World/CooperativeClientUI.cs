using System;
using System.Linq;
using System.Text;
using Actor.Characters;
using Actor.Networking;
using Actor.Protocol.Standard.Communication.V1;
using AgentsArena.ActorProtocol.V1;
using UnityEngine;
using UnityEngine.InputSystem;

namespace Actor.World
{
    // This UI only sees its local owner's targeted payloads. It never reads a
    // remote player's component state or the server-only Authority.
    public sealed class CooperativeClientUI : MonoBehaviour
    {
#if !UNITY_SERVER
        private NetworkCooperation owner;
        private PlayerInputSource keyboard;
        private CooperativeTerminal2D[] terminals = Array.Empty<CooperativeTerminal2D>();
        private CooperativeTerminal2D opened;
        private IncidentTerminal2D[] incidentTerminals = Array.Empty<IncidentTerminal2D>();
        private IncidentTerminal2D openedIncident;
        private IncidentClientUI investigation;
        private bool choosing, chat, station, sensitive;
        private bool clearFocus;
        private bool wasHuman;
        private string recipient = "", text = "", code = "", messageTaskScope = "";
        private GUIStyle heading, body;
        private Vector2 scroll;
        private void Awake() { owner = GetComponent<NetworkCooperation>(); keyboard = GetComponent<PlayerInputSource>(); investigation = GetComponent<IncidentClientUI>(); }
        public void PrepareMessage(string recipientActor, string content, bool privateContent, string taskScope)
        {
            recipient = recipientActor; text = content; sensitive = privateContent; station = false;
            messageTaskScope = taskScope; chat = true; clearFocus = true;
        }
        private void Update()
        {
            if (SocialVisibilitySystem.IsSocial(Unity.Netcode.NetworkManager.Singleton)) return;
            if (!owner.IsSpawned || !owner.IsOwner) return;
            if (terminals.Length == 0) terminals = FindObjectsByType<CooperativeTerminal2D>();
            if (incidentTerminals.Length == 0) incidentTerminals = FindObjectsByType<IncidentTerminal2D>();
            if (opened != null && !Near(opened)) opened = null;
            if (openedIncident != null && !Near(openedIncident)) openedIncident = null;
            if (!owner.Human)
            {
                if (wasHuman) { opened = null; openedIncident = null; choosing = chat = false; text = code = messageTaskScope = ""; clearFocus = true; }
                wasHuman = false; keyboard.TextEntryFocused = false; return;
            }
            wasHuman = true;
            if (Keyboard.current == null || !Application.isFocused) return;
            if (!keyboard.TextEntryFocused && Keyboard.current.tKey.wasPressedThisFrame) { chat = !chat; clearFocus = true; }
            if (Keyboard.current.escapeKey.wasPressedThisFrame) { chat = choosing = false; opened = null; openedIncident = null; SignalConsole.Current?.CloseFromFocus(); clearFocus = true; }
            if (!keyboard.TextEntryFocused && Keyboard.current.eKey.wasPressedThisFrame)
            {
                opened = null; openedIncident = null; SignalConsole.Current?.CloseFromFocus(); choosing = !choosing;
                var near = terminals.Where(Near).ToArray();
                var incidents = incidentTerminals.Where(Near).ToArray();
                int total = near.Length + incidents.Length + (NearSignal() ? 1 : 0);
                if (choosing && total == 1)
                {
                    if (near.Length == 1) opened = near[0];
                    else if (incidents.Length == 1) openedIncident = incidents[0];
                    else SignalConsole.Current.OpenFromFocus();
                    choosing = false;
                }
            }
        }
        private Vector2 Position => GetComponent<CapsuleCollider2D>().bounds.center;
        private bool Near(CooperativeTerminal2D terminal) => terminal != null && (Position - terminal.InteractionPoint).sqrMagnitude <= terminal.Radius * terminal.Radius;
        private bool Near(IncidentTerminal2D terminal) => terminal != null && (Position - terminal.InteractionPoint).sqrMagnitude <= terminal.Radius * terminal.Radius;
        private bool NearSignal() => SignalConsole.Current != null && (Position - SignalConsole.Current.InteractionPosition).sqrMagnitude <= SignalConsole.InteractionRadius * SignalConsole.InteractionRadius;
        private string Self => GetComponent<NetworkCharacter>().AuthoritativeActorId;
        private string Correlation() => Guid.NewGuid().ToString("N");
        private void OnGUI()
        {
            if (SocialVisibilitySystem.IsSocial(Unity.Netcode.NetworkManager.Singleton)) return;
            if (!owner.IsSpawned || !owner.IsOwner || !owner.IsClient) return;
            if (clearFocus) { GUI.FocusControl(null); clearFocus = false; }
            if (heading == null)
            { heading = new GUIStyle(GUI.skin.label) { fontSize = 16, fontStyle = FontStyle.Bold }; body = new GUIStyle(GUI.skin.label) { fontSize = 13, wordWrap = true }; }
            GUI.Box(new Rect(18, 205, 330, 78), GUIContent.none);
            GUI.Label(new Rect(28, 211, 310, 24), "Calibration · " + owner.Progress.Status, heading);
            string role = owner.TaskState.CoordinatorActorId == Self ? "Coordinator" : owner.TaskState.TechnicianActorId == Self ? "Technician" : "Observer";
            GUI.Label(new Rect(28, 237, 310, 40), role + "  ·  " + owner.LastAction, body);
            if (GUI.Button(new Rect(18, 290, 180, 28), chat ? "Close messages" : "T · Messages (" + owner.Inbox.Count + ")")) chat = !chat;
            if (owner.Human && opened == null && openedIncident == null && !choosing && (NearSignal() || terminals.Any(Near) || incidentTerminals.Any(Near)))
                if (GUI.Button(new Rect(Screen.width / 2f - 180, Screen.height - 54, 360, 36), "E · Nearby interactions")) choosing = true;
            if (choosing && owner.Human) DrawChooser();
            if (opened != null && owner.Human) DrawTerminal();
            if (openedIncident != null && owner.Human)
            {
                investigation.DrawTerminal(openedIncident);
                if (GUI.Button(new Rect(Screen.width / 2f + Mathf.Min(660, Screen.width - 36) / 2 - 46, Screen.height - Mathf.Min(580, Screen.height - 150) - 11, 30, 26), "×")) openedIncident = null;
            }
            if (chat) DrawChat();
            keyboard.TextEntryFocused = owner.Human && (GUI.GetNameOfFocusedControl() == "communication-text" || GUI.GetNameOfFocusedControl() == "calibration-code" || IncidentClientUI.TextFieldFocused);
        }
        private void DrawChooser()
        {
            var choices = terminals.Where(Near).ToArray();
            var incidents = incidentTerminals.Where(Near).ToArray();
            int total = choices.Length + incidents.Length + (NearSignal() ? 1 : 0);
            float height = 50 + Math.Max(1, total) * 40;
            var box = new Rect(Screen.width / 2f - 180, Screen.height - height - 20, 360, height);
            GUI.Box(box, GUIContent.none); GUI.Label(new Rect(box.x + 12, box.y + 8, 330, 25), "Choose interaction", heading);
            int row = 0;
            foreach (var terminal in choices)
            {
                if (GUI.Button(new Rect(box.x + 12, box.y + 40 + row++ * 40, 336, 34), terminal.DisplayName)) { opened = terminal; choosing = false; }
            }
            foreach (var terminal in incidents)
                if (GUI.Button(new Rect(box.x + 12, box.y + 40 + row++ * 40, 336, 34), terminal.DisplayName)) { openedIncident = terminal; choosing = false; }
            if (NearSignal() && GUI.Button(new Rect(box.x + 12, box.y + 40 + row * 40, 336, 34), "Signal Console"))
            { SignalConsole.Current.OpenFromFocus(); choosing = false; }
        }
        private void DrawTerminal()
        {
            var box = new Rect(Screen.width / 2f - 250, Screen.height - 285, 500, 265);
            GUI.Box(box, GUIContent.none); GUI.Label(new Rect(box.x + 12, box.y + 8, 440, 25), opened.DisplayName, heading);
            if (GUI.Button(new Rect(box.xMax - 40, box.y + 8, 28, 24), "×")) { opened = null; return; }
            var task = owner.TaskState;
            GUI.Label(new Rect(box.x + 12, box.y + 38, 476, 38), "State: " + (task.TaskId.Length > 0 ? task.Status : owner.Progress.Status), body);
            if (opened.Kind == CooperativeTerminalKind.Diagnostic)
            {
                int i = 0;
                foreach (var peer in owner.Peers.Peers.Where(p => p.ActorId != Self).Take(3))
                {
                    if (GUI.Button(new Rect(box.x + 12, box.y + 78 + 33 * i++, 476, 28), "Start with " + peer.DisplayName))
                        owner.Submit(new StartCalibrationCommand { TechnicianActorId = peer.ActorId, CorrelationId = Correlation(), MatchId = owner.Peers.MatchId });
                }
                if (task.CoordinatorActorId == Self)
                {
                    if (GUI.Button(new Rect(box.x + 12, box.y + 180, 220, 30), "Inspect private diagnostic"))
                        owner.Submit(new InspectDiagnosticCommand { TaskId = task.TaskId, CorrelationId = Correlation(), MatchId = owner.Peers.MatchId });
                    if (owner.PrivateDiagnostic != null && owner.PrivateDiagnostic.ExpiresAtUnixMs > DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())
                    {
                        GUI.Label(new Rect(box.x + 12, box.y + 215, 476, 36), "Private: " + owner.PrivateDiagnostic.AuthorizationCode, body);
                        if (GUI.Button(new Rect(box.x + 242, box.y + 180, 246, 30), "Prepare private message"))
                        PrepareMessage(task.TechnicianActorId, owner.PrivateDiagnostic.AuthorizationCode, true, task.TaskId);
                    }
                }
            }
            else
            {
                GUI.Label(new Rect(box.x + 12, box.y + 82, 476, 36), "The assigned Technician must receive the Coordinator's private authorization.", body);
                GUI.SetNextControlName("calibration-code");
                code = GUI.TextField(new Rect(box.x + 12, box.y + 132, 476, 32), code, 64);
                if (GUI.Button(new Rect(box.x + 12, box.y + 176, 476, 34), "Submit authorization"))
                    owner.Submit(new SubmitCalibrationCommand { TaskId = task.TaskId, AuthorizationCode = code, CorrelationId = Correlation(), MatchId = owner.Peers.MatchId });
                GUI.Label(new Rect(box.x + 12, box.y + 217, 476, 36), owner.LastAction, body);
            }
        }
        private void DrawChat()
        {
            float width = Mathf.Min(540, Screen.width - 32);
            var box = new Rect(Screen.width - width - 18, 132, width, Mathf.Min(500, Screen.height - 155));
            GUI.Box(box, GUIContent.none); GUI.Label(new Rect(box.x + 12, box.y + 8, width - 24, 25), "Station communications", heading);
            if (GUI.Button(new Rect(box.xMax - 40, box.y + 8, 28, 24), "×")) { chat = false; GUI.FocusControl(null); }
            var view = new Rect(box.x + 12, box.y + 40, width - 24, Math.Max(60, box.height - 220));
            var messages = owner.Inbox.Where(m => m.ExpiresAtUnixMs > DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()).ToArray();
            scroll = GUI.BeginScrollView(view, scroll, new Rect(0, 0, view.width - 20, messages.Length * 66));
            for (int i = 0; i < messages.Length; ++i)
            {
                var message = messages[i]; string content = message.Sensitive && !owner.Human ? "[Private, short-lived authorization]" : message.Text;
                GUI.Label(new Rect(0, i * 66, view.width - 22, 22), owner.DisplayName(message.SenderActorId) + " · " + message.ChannelId, body);
                GUI.Label(new Rect(0, i * 66 + 22, view.width - 22, 42), content, body);
            }
            GUI.EndScrollView();
            if (!owner.Human) return;
            float y = box.yMax - 174;
            station = GUI.Toggle(new Rect(box.x + 12, y, 120, 24), station, "Station-wide");
            sensitive = GUI.Toggle(new Rect(box.x + 145, y, 160, 24), sensitive, "Sensitive/private");
            var peers = owner.Peers.Peers.Where(p => p.ActorId != Self).ToArray();
            if (recipient.Length == 0 && peers.Length > 0) recipient = peers[0].ActorId;
            if (!station && GUI.Button(new Rect(box.x + 12, y + 28, width - 24, 27), "To: " + owner.DisplayName(recipient)))
            {
                int current = Array.FindIndex(peers, p => p.ActorId == recipient);
                if (peers.Length > 0) recipient = peers[(current + 1) % peers.Length].ActorId;
            }
            GUI.SetNextControlName("communication-text"); text = GUI.TextField(new Rect(box.x + 12, y + 62, width - 24, 42), text, 512);
            if (GUI.Button(new Rect(box.x + 12, y + 114, width - 24, 34), "Send · " + Encoding.UTF8.GetByteCount(text) + "/512 bytes"))
            {
                owner.Submit(new SendMessageCommand { RecipientActorId = station ? "" : recipient, ChannelId = station ? "station" : "direct",
                    Text = text, CorrelationId = Correlation(), TaskId = messageTaskScope.Length > 0 ? messageTaskScope : owner.TaskState.TaskId, Sensitive = sensitive, TtlMs = 300000, MatchId = owner.Peers.MatchId });
                text = messageTaskScope = ""; GUI.FocusControl(null);
            }
        }
        private void OnDisable() { if (keyboard != null) keyboard.TextEntryFocused = false; }
#endif
    }
}
