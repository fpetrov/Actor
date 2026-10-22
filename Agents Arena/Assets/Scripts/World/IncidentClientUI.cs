using System;
using System.Globalization;
using System.Linq;
using System.Text;
using Actor.Networking;
using Actor.UI;
using AgentsArena.ActorProtocol.V1;
using UnityEngine;
#if !UNITY_SERVER
using UnityEngine.UIElements;
#endif
namespace Actor.World
{
    public sealed class IncidentClientUI : MonoBehaviour
    {
#if !UNITY_SERVER
        private NetworkInvestigation owner;
        private CooperativeClientUI communications;
        private VisualElement hud, panel, content;
        private Label state, action;
        private IncidentTerminal2D opened;
        private string engineeringReference = "", explanation = "", selectedRecord = "", shownIncident = "", signature = "";
        private ProposedOrigin proposed = ProposedOrigin.InsufficientEvidence;
        public static bool TextFieldFocused => ArenaUI.InputCaptured;
        private void Awake() { owner = GetComponent<NetworkInvestigation>(); communications = GetComponent<CooperativeClientUI>(); }
        private string Correlation() => Guid.NewGuid().ToString("N");
        private void Build()
        {
            if (ArenaUI.HudLayer == null) return;
            hud = ArenaUI.Panel("Station duty", "hud legacy-duty", ArenaUI.HudLayer); hud.style.top = 220;
            state = ArenaUI.Text("", "body", hud); action = ArenaUI.Text("", "muted", hud);
            var roles = ArenaUI.Element("row wrap", hud);
            foreach (var role in new[] { CrewRole.Crew, CrewRole.Engineer, CrewRole.Sheriff })
                ArenaUI.Button(role.ToString(), () => owner.Submit(new SelectRoleCommand { Role = role, MatchId = owner.Role.MatchId, CorrelationId = Correlation() }), "grow", roles);
            panel = ArenaUI.Panel("Investigation terminal", "action-sheet", ArenaUI.ScreenLayer);
            ArenaUI.Button("Close terminal", CloseTerminal, "quiet", panel);
            var scroll = new ScrollView(); panel.Add(scroll); content = scroll.contentContainer;
        }
        public void ShowTerminal(IncidentTerminal2D terminal) { opened = terminal; signature = ""; }
        public void CloseTerminal() { opened = null; ArenaUI.Show(panel, false); }
        private void Update()
        {
            bool active = !SocialVisibilitySystem.IsSocial(Unity.Netcode.NetworkManager.Singleton) && owner.IsSpawned && owner.IsOwner && owner.IsClient;
            if (!active) { ArenaUI.Show(hud, false); ArenaUI.Show(panel, false); return; }
            if (hud == null) Build(); if (hud == null) return;
            ArenaUI.Show(hud, true);
            state.text = owner.Role.Role + " · " + owner.Cooling.CoolingStatus + " / " + owner.Cooling.IncidentStatus + (owner.Cooling.ReportValidated ? " · Report validated" : "");
            action.text = owner.LastAction;
            foreach (var button in hud.Query<Button>().ToList()) button.SetEnabled(owner.Human && owner.HasOwnState && !owner.Role.SelectionLocked);
            if (opened != null && ((Vector2)GetComponent<CapsuleCollider2D>().bounds.center - opened.InteractionPoint).sqrMagnitude > opened.Radius * opened.Radius) opened = null;
            ArenaUI.Show(panel, opened != null && owner.Human);
            if (opened == null || !owner.Human) return;
            if (shownIncident != owner.Cooling.IncidentId)
            { shownIncident = owner.Cooling.IncidentId; engineeringReference = explanation = selectedRecord = ""; proposed = ProposedOrigin.InsufficientEvidence; signature = ""; }
            var own = owner.CaptureOwnView();
            string next = opened.EntityId + owner.Role.Role + owner.Cooling.ToString() + own.Diagnostic?.ToString() + own.Security?.ToString();
            if (signature == next) return; signature = next; content.Clear();
            ArenaUI.Text(opened.DisplayName, "heading", content);
            ArenaUI.Text("Only your assigned private source is available. Messages from others remain claims.", "muted", content);
            if (opened.Kind == IncidentTerminalKind.Cooling) Cooling(); else Security();
        }
        private void Cooling()
        {
            bool active = owner.Cooling.IncidentStatus == IncidentStatus.Active;
            bool engineer = owner.Role.Role == CrewRole.Engineer;
            ArenaUI.Button("Inspect private cooling diagnostic", () => owner.Submit(new InspectCoolingCommand { IncidentId = shownIncident, MatchId = owner.Role.MatchId, CorrelationId = Correlation() }), "primary", content).SetEnabled(engineer && active);
            var evidence = owner.CaptureOwnView().Diagnostic;
            if (evidence != null)
            {
                ArenaUI.Text("Evidence " + evidence.Provenance.EvidenceId + "\nCircuit " + evidence.CircuitRef + " · wear " + evidence.EquipmentWear.ToString("0.00")
                    + "\nPressure " + string.Join(" / ", evidence.Samples.Select(s => s.Pressure.ToString("0.00")))
                    + "\nWindow " + evidence.FailureWindowStartUnixMs + " – " + evidence.FailureWindowEndUnixMs
                    + "\nController " + evidence.ControllerRevisionBefore + " → " + evidence.ControllerRevisionAfter, "body", content);
                ArenaUI.Button("Prepare private findings for Sheriff", () =>
                {
                    var sheriff = owner.Roster.Entries.SingleOrDefault(p => p.Role == CrewRole.Sheriff);
                    if (sheriff != null) communications.PrepareMessage(sheriff.ActorId, Share(evidence), true, shownIncident);
                }, "", content).SetEnabled(active);
                ArenaUI.Button("Repair cooling · remain near panel", () => owner.Submit(new RepairCoolingCommand { IncidentId = shownIncident, DiagnosticEvidenceId = evidence.Provenance.EvidenceId, MatchId = owner.Role.MatchId, CorrelationId = Correlation() }), "primary", content).SetEnabled(engineer && active);
            }
            ArenaUI.Button("Cancel repair", () => owner.Submit(new CancelCoolingRepairCommand { IncidentId = shownIncident, MatchId = owner.Role.MatchId, CorrelationId = Correlation() }), "quiet", content).SetEnabled(engineer && active);
            ArenaUI.Text("Maintenance changes are recorded by the station.", "muted", content);
            foreach (string circuit in new[] { "alpha", "beta" })
                ArenaUI.Button("Adjust circuit " + circuit, () => owner.Submit(new MaintenanceOverrideCommand { CircuitRef = circuit, MatchId = owner.Role.MatchId, CorrelationId = Correlation() }), "", content).SetEnabled(owner.Role.Role is CrewRole.Crew or CrewRole.Engineer);
            Cancel();
        }
        private void Security()
        {
            bool active = owner.Cooling.IncidentStatus == IncidentStatus.Active;
            ArenaUI.Button("Inspect private security records", () => owner.Submit(new InspectSecurityRecordsCommand { IncidentId = shownIncident, MatchId = owner.Role.MatchId, CorrelationId = Correlation() }), "primary", content).SetEnabled(owner.Role.Role == CrewRole.Sheriff && active);
            var evidence = owner.CaptureOwnView().Security; if (evidence == null) return;
            ArenaUI.Text("Evidence " + evidence.Provenance.EvidenceId + "\nCoverage " + evidence.CoverageWindowStartUnixMs + " – " + evidence.CoverageWindowEndUnixMs + "\nComplete for listed circuits: " + evidence.CompleteForListedCircuits, "body", content);
            foreach (var record in evidence.Records)
            {
                var button = ArenaUI.Button(record.CircuitRef + " · " + record.Action + "\n" + record.WindowStartUnixMs + " – " + record.WindowEndUnixMs + "\n" + (record.InteractionConfirmed ? "Observed interaction" : "Controller record") + " · " + record.SourceEntityId,
                    () => { selectedRecord = record.RecordId; foreach (var b in content.Query<Button>(className: "record").ToList()) b.EnableInClassList("selected", b.name == selectedRecord); }, "record", content);
                button.name = record.RecordId; button.EnableInClassList("selected", selectedRecord == record.RecordId);
            }
            ArenaUI.Field("Engineer evidence", engineeringReference, v => engineeringReference = v, 64, content);
            var choices = Enum.GetValues(typeof(ProposedOrigin)).Cast<ProposedOrigin>().Where(c => c != ProposedOrigin.Unspecified).ToArray();
            var dropdown = new DropdownField("Conclusion", choices.Select(c => c.ToString()).ToList(), Math.Max(0, Array.IndexOf(choices, proposed)));
            dropdown.RegisterValueChangedCallback(e => proposed = choices[dropdown.index]); content.Add(dropdown);
            ArenaUI.Field("Explanation", explanation, v => explanation = v, 256, content);
            ArenaUI.Button("Submit supported report", () =>
            {
                if (Encoding.UTF8.GetByteCount(explanation) > 512) { ArenaUI.Toast("Explanation exceeds 512 bytes."); return; }
                var command = new SubmitInvestigationReportCommand { IncidentId = shownIncident, MatchId = owner.Role.MatchId, CorrelationId = Correlation(), ProposedOrigin = proposed, Explanation = explanation };
                command.SupportingEvidenceIds.Add(new[] { engineeringReference.Trim(), evidence.Provenance.EvidenceId, selectedRecord }); owner.Submit(command);
            }, "primary", content).SetEnabled(active);
            Cancel();
        }
        private void Cancel() => ArenaUI.Button("Cancel unfinished investigation", () => owner.Submit(new CancelInvestigationCommand { IncidentId = shownIncident, MatchId = owner.Role.MatchId, CorrelationId = Correlation() }), "quiet", content).SetEnabled(owner.Cooling.IncidentStatus == IncidentStatus.Active && owner.Role.Role is CrewRole.Engineer or CrewRole.Sheriff);
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
        }        private void OnDisable() { hud?.RemoveFromHierarchy(); panel?.RemoveFromHierarchy(); hud = panel = null; }
        private void OnDestroy() => OnDisable();
#endif
    }
}
