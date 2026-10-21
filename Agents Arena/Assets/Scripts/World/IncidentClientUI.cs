using System;
using System.Globalization;
using System.Linq;
using System.Text;
using Actor.Networking;
using AgentsArena.ActorProtocol.V1;
using UnityEngine;

namespace Actor.World
{
    // Owner-only presentation. This component has no reference to the server
    // authority, hidden scenario configuration, or another player's evidence.
    public sealed class IncidentClientUI : MonoBehaviour
    {
#if !UNITY_SERVER
        private NetworkInvestigation owner;
        private CooperativeClientUI communications;
        private string engineeringReference = "", explanation = "", selectedRecord = "", shownIncident = "";
        private ProposedOrigin proposed = ProposedOrigin.InsufficientEvidence;
        private GUIStyle body, heading;
        private Vector2 scroll;
        public static bool TextFieldFocused => GUI.GetNameOfFocusedControl() is "investigation-reference" or "investigation-explanation";
        private void Awake() { owner = GetComponent<NetworkInvestigation>(); communications = GetComponent<CooperativeClientUI>(); }
        private string Correlation() => Guid.NewGuid().ToString("N");
        private void Styles()
        {
            if (body != null) return;
            body = new GUIStyle(GUI.skin.label) { fontSize = 13, wordWrap = true };
            heading = new GUIStyle(GUI.skin.label) { fontSize = 16, fontStyle = FontStyle.Bold };
        }
        private void OnGUI()
        {
            if (SocialVisibilitySystem.IsSocial(Unity.Netcode.NetworkManager.Singleton)) return;
            if (!owner.IsSpawned || !owner.IsOwner || !owner.IsClient) return;
            Styles();
            GUI.Box(new Rect(18, 328, 330, 148), GUIContent.none);
            GUI.Label(new Rect(28, 335, 310, 25), "Duty · " + owner.Role.Role, heading);
            var state = owner.Cooling;
            GUI.Label(new Rect(28, 362, 310, 40), "Cooling: " + state.CoolingStatus + " / " + state.IncidentStatus
                + (state.ReportValidated ? " · Report validated" : ""), body);
            GUI.Label(new Rect(28, 403, 310, 25), owner.LastAction, body);
            if (owner.Human)
            {
                GUI.enabled = owner.HasOwnState && !owner.Role.SelectionLocked;
                var roles = new[] { CrewRole.Crew, CrewRole.Engineer, CrewRole.Sheriff };
                for (int i = 0; i < roles.Length; ++i)
                    if (GUI.Button(new Rect(28 + i * 102, 435, 98, 29), roles[i].ToString()))
                        owner.Submit(new SelectRoleCommand { Role = roles[i], MatchId = owner.Role.MatchId, CorrelationId = Correlation() });
                GUI.enabled = true;
            }
        }
        public void DrawTerminal(IncidentTerminal2D terminal)
        {
            if (!owner.Human || terminal == null) return;
            Styles();
            if (shownIncident != owner.Cooling.IncidentId)
            { shownIncident = owner.Cooling.IncidentId; engineeringReference = explanation = selectedRecord = ""; proposed = ProposedOrigin.InsufficientEvidence; }
            float width = Mathf.Min(660, Screen.width - 36), height = Mathf.Min(580, Screen.height - 150);
            var box = new Rect((Screen.width - width) / 2, Screen.height - height - 20, width, height);
            GUI.Box(box, GUIContent.none);
            GUI.Label(new Rect(box.x + 14, box.y + 9, width - 28, 26), terminal.DisplayName + " · " + owner.Role.Role, heading);
            GUI.Label(new Rect(box.x + 14, box.y + 39, width - 28, 35), "Public state: " + owner.Cooling.CoolingStatus + " / " + owner.Cooling.IncidentStatus + "   ·   " + owner.LastAction, body);
            var viewport = new Rect(box.x + 12, box.y + 78, width - 24, height - 90);
            scroll = GUI.BeginScrollView(viewport, scroll, new Rect(0, 0, viewport.width - 20, terminal.Kind == IncidentTerminalKind.Security ? 750 : 475));
            if (terminal.Kind == IncidentTerminalKind.Cooling) DrawCooling(viewport.width - 20); else DrawSecurity(viewport.width - 20);
            GUI.EndScrollView();
        }
        private void DrawCooling(float width)
        {
            bool engineer = owner.Role.Role == CrewRole.Engineer, active = owner.Cooling.IncidentStatus == IncidentStatus.Active;
            GUI.enabled = engineer && active;
            if (GUI.Button(new Rect(0, 0, width, 32), "Inspect cooling · Engineer private diagnostic"))
                owner.Submit(new InspectCoolingCommand { IncidentId = shownIncident, MatchId = owner.Role.MatchId, CorrelationId = Correlation() });
            GUI.enabled = true;
            var evidence = owner.CaptureOwnView().Diagnostic;
            if (evidence != null)
            {
                GUI.Label(new Rect(0, 42, width, 105), "Private source: " + evidence.Provenance.SourceEntityId + "\nEvidence: " + evidence.Provenance.EvidenceId
                    + "\nCircuit: " + evidence.CircuitRef + " · samples: " + evidence.Samples.Count
                    + " · wear: " + evidence.EquipmentWear.ToString("0.00")
                    + " · pressure: " + string.Join(" / ", evidence.Samples.Select(s => s.Pressure.ToString("0.00")))
                    + "\nWindow: " + evidence.FailureWindowStartUnixMs + " – " + evidence.FailureWindowEndUnixMs
                    + "\nController: " + evidence.ControllerRevisionBefore + " → " + evidence.ControllerRevisionAfter, body);
                GUI.enabled = active;
                if (GUI.Button(new Rect(0, 155, width, 32), "Prepare private findings for Sheriff"))
                {
                    var sheriff = owner.Roster.Entries.SingleOrDefault(p => p.Role == CrewRole.Sheriff);
                    if (sheriff != null) communications.PrepareMessage(sheriff.ActorId, Share(evidence), true, shownIncident);
                }
                if (GUI.Button(new Rect(0, 195, width, 32), "Repair cooling · remain near panel"))
                    owner.Submit(new RepairCoolingCommand { IncidentId = shownIncident, DiagnosticEvidenceId = evidence.Provenance.EvidenceId,
                        MatchId = owner.Role.MatchId, CorrelationId = Correlation() });
                GUI.enabled = true;
            }
            else GUI.Label(new Rect(0, 44, width, 75), "Engineering evidence is visible only to the assigned Engineer after inspecting this terminal. Share findings through private station communications.", body);
            GUI.enabled = engineer && active;
            if (GUI.Button(new Rect(0, 240, width, 30), "Cancel current repair"))
                owner.Submit(new CancelCoolingRepairCommand { IncidentId = shownIncident, MatchId = owner.Role.MatchId, CorrelationId = Correlation() });
            GUI.enabled = true;
            GUI.Label(new Rect(0, 290, width, 48), "Maintenance · authorized Crew / Engineer circuit adjustment. Changes are recorded by the station.", body);
            GUI.enabled = owner.Role.Role is CrewRole.Crew or CrewRole.Engineer;
            foreach (var item in new[] { ("alpha", 0f), ("beta", width / 2 + 4) })
                if (GUI.Button(new Rect(item.Item2, 346, width / 2 - 4, 34), "Adjust circuit " + item.Item1))
                    owner.Submit(new MaintenanceOverrideCommand { CircuitRef = item.Item1, MatchId = owner.Role.MatchId, CorrelationId = Correlation() });
            GUI.enabled = true;
            DrawCancel(width, 416);
        }
        private void DrawSecurity(float width)
        {
            bool active = owner.Cooling.IncidentStatus == IncidentStatus.Active;
            GUI.enabled = owner.Role.Role == CrewRole.Sheriff && active;
            if (GUI.Button(new Rect(0, 0, width, 32), "Inspect private security records"))
                owner.Submit(new InspectSecurityRecordsCommand { IncidentId = shownIncident, MatchId = owner.Role.MatchId, CorrelationId = Correlation() });
            GUI.enabled = true;
            var evidence = owner.CaptureOwnView().Security;
            if (evidence == null)
            { GUI.Label(new Rect(0, 45, width, 80), "Only the assigned Sheriff can inspect this source. Compare records with findings received from the Engineer; player messages remain claims.", body); return; }
            GUI.Label(new Rect(0, 42, width, 75), "Private source: " + evidence.Provenance.SourceEntityId + "\nEvidence: " + evidence.Provenance.EvidenceId
                + "\nCoverage: " + evidence.CoverageWindowStartUnixMs + " – " + evidence.CoverageWindowEndUnixMs
                + " · complete for listed circuits: " + evidence.CompleteForListedCircuits, body);
            float y = 125;
            foreach (var record in evidence.Records)
            {
                string label = (selectedRecord == record.RecordId ? "● " : "○ ") + record.CircuitRef + " · " + record.Action
                    + " · " + (record.InteractionConfirmed ? "observed interaction" : "controller record")
                    + "\n" + record.WindowStartUnixMs + " – " + record.WindowEndUnixMs + " · revision " + record.PriorControllerRevision + " → " + record.ResultingControllerRevision
                    + "\nSource: " + record.SourceEntityId + (record.ActorId.Length > 0 ? " · actor: " + record.ActorId : "");
                if (GUI.Button(new Rect(0, y, width, 66), label)) selectedRecord = record.RecordId;
                y += 72;
            }
            GUI.Label(new Rect(0, y + 4, width, 25), "Engineer evidence reference received in private chat", body);
            GUI.SetNextControlName("investigation-reference"); engineeringReference = GUI.TextField(new Rect(0, y + 31, width, 29), engineeringReference, 64);
            var choices = new[] { ProposedOrigin.MechanicalFailure, ProposedOrigin.ManualInterference, ProposedOrigin.InsufficientEvidence };
            for (int i = 0; i < choices.Length; ++i)
                if (GUI.Toggle(new Rect(i * width / 3, y + 68, width / 3, 30), proposed == choices[i], choices[i].ToString())) proposed = choices[i];
            GUI.SetNextControlName("investigation-explanation"); explanation = GUI.TextField(new Rect(0, y + 106, width, 32), explanation, 256);
            GUI.enabled = active && Encoding.UTF8.GetByteCount(explanation) <= 512;
            if (GUI.Button(new Rect(0, y + 147, width, 34), "Submit supported report"))
            {
                var command = new SubmitInvestigationReportCommand { IncidentId = shownIncident, MatchId = owner.Role.MatchId,
                    CorrelationId = Correlation(), ProposedOrigin = proposed, Explanation = explanation };
                command.SupportingEvidenceIds.Add(new[] { engineeringReference.Trim(), evidence.Provenance.EvidenceId, selectedRecord });
                owner.Submit(command);
            }
            GUI.enabled = true; DrawCancel(width, y + 193);
        }
        private void DrawCancel(float width, float y)
        {
            GUI.enabled = owner.Cooling.IncidentStatus == IncidentStatus.Active && owner.Role.Role is CrewRole.Engineer or CrewRole.Sheriff;
            if (GUI.Button(new Rect(0, y, width, 30), "Cancel unfinished investigation"))
                owner.Submit(new CancelInvestigationCommand { IncidentId = shownIncident, MatchId = owner.Role.MatchId, CorrelationId = Correlation() });
            GUI.enabled = true;
        }
        private static string Share(DiagnosticEvidence evidence)
        {
            // All strings are server-issued opaque IDs or a bounded circuit ID.
            var p = evidence.Provenance;
            double delta = evidence.Samples.Count > 1 ? evidence.Samples.Last().Pressure - evidence.Samples.First().Pressure : 0;
            return "{\"kind\":\"diagnostic_claim\",\"evidence_id\":\"" + p.EvidenceId + "\",\"incident_id\":\"" + p.IncidentId
                + "\",\"match_id\":\"" + p.MatchId + "\",\"circuit_ref\":\"" + evidence.CircuitRef + "\",\"window\":["
                + evidence.FailureWindowStartUnixMs + "," + evidence.FailureWindowEndUnixMs + "],\"controller_revision\":["
                + evidence.ControllerRevisionBefore + "," + evidence.ControllerRevisionAfter + "],\"pressure_delta\":"
                + Math.Round(delta, 4).ToString(CultureInfo.InvariantCulture) + "}";
        }
#endif
    }
}
