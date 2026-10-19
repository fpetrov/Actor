using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;

namespace Actor.Networking
{
    // Reference-game server state only. The caller supplies approved physical
    // poses and routes each delivery to its explicit audience. No Unity, NGO,
    // Brain, filesystem, wall clock, or hidden-cause getter belongs here.
    internal sealed class IncidentAuthority
    {
        internal sealed class ScenarioConfiguration
        {
            internal int Seed = 1;
            internal bool NaturalEnabled = true;
            internal long NaturalDelayMs = 15000;
            internal long IncidentTtlMs = 300000;
            internal long RepairDurationMs = 2000;
            internal string ActiveCircuit = "";
        }
        internal sealed class Participant
        {
            internal string Id = "", Name = "", Room = "";
            // Authoritative capsule center, not visual/predicted/root position.
            internal double X, Y;
        }
        internal sealed class Delivery
        {
            internal string Recipient;
            internal IMessage Payload;
            internal Delivery(string recipient, IMessage payload) { Recipient = recipient; Payload = payload; }
        }
        private sealed class Terminal { internal bool Available = true; internal double X, Y, Radius; }
        private sealed class Replay { internal byte[] Hash; internal IMessage Reply; internal long Expires; }
        private sealed class Bucket { internal double Tokens = 4; internal long At; }
        private enum HiddenOrigin { Mechanical, Manual }

        private readonly string match;
        private readonly bool naturalEnabled;
        private readonly long naturalDelay, lifetime, repairDuration;
        private readonly int seed;
        private readonly string activeCircuit;
        private readonly Dictionary<string, Participant> actors = new();
        private readonly Dictionary<string, CrewRole> roles = new();
        private readonly Dictionary<string, Replay> replays = new();
        private readonly Dictionary<string, Bucket> buckets = new();
        private readonly List<MaintenanceRecord> history = new();
        private readonly Terminal cooling = new() { X = -.25, Y = -6.54, Radius = 1.25 };
        private readonly Terminal security = new() { X = -1, Y = -.65, Radius = 1.6 };
        private readonly CoolingStateObservation state = new();
        private DiagnosticEvidence diagnostic;
        private SecurityEvidence securityEvidence;
        private HiddenOrigin hiddenOrigin;
        private string fixedEngineer = "", fixedSheriff = "", repairCorrelation = "";
        private string deliveredDiagnosticId = "";
        private bool pendingManual;
        private long readyAt = -1, faultWindowStart, faultWindowEnd, nextReport, nextMaintenance;
        private long lastNow, globalAt;
        private double globalTokens = 16;
        private int reportAttempts;
        private ulong revision;
        internal const int MaxReplayEntries = 512, MaxHistoryEntries = 64;
        private const string CoolingEntity = "engineering-cooling-01", SecurityEntity = "main-hall-security-01";

        internal IncidentAuthority(string matchId, ScenarioConfiguration configuration = null)
        {
            if (!Guid.TryParseExact(matchId, "N", out _)) throw new ArgumentException("Expected match GUID.");
            var c = configuration ?? new ScenarioConfiguration();
            if (c.NaturalDelayMs < 0 || c.NaturalDelayMs > 120000 || c.IncidentTtlMs < 1000 || c.IncidentTtlMs > 900000
                || c.RepairDurationMs < 100 || c.RepairDurationMs > 10000
                || c.ActiveCircuit != "" && c.ActiveCircuit != "alpha" && c.ActiveCircuit != "beta")
                throw new ArgumentException("Invalid trusted incident configuration.");
            match = matchId; seed = c.Seed; naturalEnabled = c.NaturalEnabled;
            naturalDelay = c.NaturalDelayMs; lifetime = c.IncidentTtlMs; repairDuration = c.RepairDurationMs;
            // Publicly observable revision/wear variation must not reveal the
            // selector. A deterministic trusted fixture may explicitly choose
            // a circuit; the ordinary game uses independent server entropy.
            activeCircuit = c.ActiveCircuit.Length > 0 ? c.ActiveCircuit : RandomCircuit();
            state.MatchId = match; state.CoolingStatus = CoolingStatus.Operational;
            state.IncidentStatus = IncidentStatus.None; state.Severity = CoolingSeverity.Normal;
            state.LocationId = "engineering"; state.ReasonCode = "operational";
        }
        internal bool Active => state.IncidentStatus == IncidentStatus.Active;
        internal string MatchId => match;
        internal string IncidentId => state.IncidentId;
        internal string EngineerActorId => Active || state.IncidentId.Length > 0 ? fixedEngineer : Holder(CrewRole.Engineer);
        internal string SheriffActorId => Active || state.IncidentId.Length > 0 ? fixedSheriff : Holder(CrewRole.Sheriff);
        internal CoolingStateObservation State() => state.Clone();
        internal OwnRoleObservation OwnRole(string actor) => new()
        { ActorId = Bound(actor), Role = Role(actor), SelectionLocked = Active, MatchId = match, ServerRevision = revision };
        internal DutyRosterObservation Roster()
        {
            var value = new DutyRosterObservation { MatchId = match, ServerRevision = revision };
            value.Entries.Add(actors.Values.OrderBy(p => p.Id).Select(p => new DutyRoleEntry
            { ActorId = p.Id, DisplayName = p.Name ?? "", Role = Role(p.Id) }));
            return value;
        }
        internal OwnInvestigationViewObservation OwnView(string actor)
        {
            var view = new OwnInvestigationViewObservation
            { RecipientActorId = Bound(actor), IncidentId = state.IncidentId, MatchId = match, ServerRevision = revision };
            if (!Active || !actors.ContainsKey(actor)) return view;
            if (actor == fixedEngineer && Role(actor) == CrewRole.Engineer && EvidenceLive(diagnostic?.Provenance, lastNow))
            { view.Diagnostic = diagnostic.Clone(); view.ExpiresAtUnixMs = diagnostic.Provenance.ExpiresAtUnixMs; }
            if (actor == fixedSheriff && Role(actor) == CrewRole.Sheriff && EvidenceLive(securityEvidence?.Provenance, lastNow))
            { view.Security = securityEvidence.Clone(); view.ExpiresAtUnixMs = securityEvidence.Provenance.ExpiresAtUnixMs; }
            return view;
        }
        internal void SetTerminals(bool coolingAvailable, double cx, double cy, double coolingRadius,
            bool securityAvailable, double sx, double sy, double securityRadius)
        {
            Set(cooling, coolingAvailable, cx, cy, coolingRadius); Set(security, securityAvailable, sx, sy, securityRadius);
        }
        private static void Set(Terminal t, bool available, double x, double y, double radius)
        { t.Available = available && Finite(x) && Finite(y) && Finite(radius) && radius > 0 && radius <= 5; t.X = x; t.Y = y; t.Radius = radius; }

        internal List<Delivery> Refresh(IEnumerable<Participant> approved, long now)
        {
            var list = approved.Take(17).ToArray();
            if (list.Length > 16 || list.Any(p => !Identifier(p.Id)) || list.Select(p => p.Id).Distinct().Count() != list.Length)
                throw new ArgumentException("Expected bounded distinct approved actors.");
            var next = list.ToDictionary(p => p.Id);
            var output = new List<Delivery>();
            bool changed = actors.Count != next.Count || actors.Any(p => !next.TryGetValue(p.Key, out var n) || p.Value.Name != n.Name);
            foreach (string removed in actors.Keys.Where(id => !next.ContainsKey(id)).ToArray())
            {
                if (Active && (removed == fixedEngineer || removed == fixedSheriff)) Finish(IncidentStatus.Cancelled, "role_holder_disconnected");
                roles.Remove(removed); buckets.Remove(removed);
                foreach (string key in replays.Keys.Where(k => k.StartsWith(removed + ":", StringComparison.Ordinal)).ToArray()) replays.Remove(key);
            }
            actors.Clear(); foreach (var p in next) { actors.Add(p.Key, p.Value); if (!roles.ContainsKey(p.Key)) roles.Add(p.Key, CrewRole.Crew); }
            if (changed) { Advance(); output.AddRange(Broadcast(true)); }
            output.AddRange(Tick(now)); return output;
        }
        internal List<Delivery> Tick(long now)
        {
            lastNow = Math.Max(lastNow, now); now = lastNow;
            var output = new List<Delivery>();
            foreach (string key in replays.Where(p => p.Value.Expires <= now).Select(p => p.Key).ToArray()) replays.Remove(key);
            bool ready = Holder(CrewRole.Engineer).Length > 0 && Holder(CrewRole.Sheriff).Length > 0;
            if (state.IncidentStatus == IncidentStatus.None)
            {
                if (!ready) ResetCycle();
                else if (readyAt < 0) readyAt = now;
                long cycleDuration = Math.Max(1000, naturalDelay);
                if (ready && now - readyAt >= cycleDuration)
                {
                    long deadline = readyAt + cycleDuration;
                    if ((pendingManual || naturalEnabled) && cooling.Available && security.Available)
                    { Begin(pendingManual ? HiddenOrigin.Manual : HiddenOrigin.Mechanical, deadline); output.AddRange(Broadcast(true, true)); }
                    else
                    {
                        // No fault was prepared and natural generation is off.
                        // Start a new bounded controller cycle; no old operation
                        // is silently presented as part of its complete audit.
                        ResetCycle(); readyAt = now;
                    }
                }
            }
            if (!Active) return output;
            if (now >= state.ExpiresAtUnixMs)
            { Finish(IncidentStatus.Expired, "incident_expired"); output.AddRange(Broadcast(true)); return output; }
            if (repairCorrelation.Length > 0)
            {
                if (!actors.TryGetValue(fixedEngineer, out var engineer) || Role(fixedEngineer) != CrewRole.Engineer
                    || !At(engineer, cooling, "engineering") || !EvidenceLive(diagnostic?.Provenance, now))
                {
                    string correlation = repairCorrelation; ClearRepair(); Advance();
                    output.Add(new Delivery(fixedEngineer, Repair(correlation, false, "repair_interrupted", false)));
                    CacheReply(fixedEngineer, correlation, output.Last().Payload); output.AddRange(Broadcast(false));
                }
                else if (now >= state.RepairCompletesAtUnixMs)
                {
                    string correlation = repairCorrelation; repairCorrelation = ""; state.RepairCompletesAtUnixMs = 0;
                    state.CoolingStatus = CoolingStatus.Restored; state.Severity = CoolingSeverity.Normal; Advance(); ResolveIfReady();
                    var result = Repair(correlation, true, "cooling_restored", true);
                    CacheReply(fixedEngineer, correlation, result); output.Add(new Delivery(fixedEngineer, result)); output.AddRange(Broadcast(true));
                }
            }
            return output;
        }
        internal List<Delivery> Execute(string actor, IMessage command, long now)
        {
            var output = Tick(now); now = lastNow;
            if (!actors.TryGetValue(actor, out var caller) || command == null) return output;
            string correlation = Correlation(command);
            string invalid = Match(command) != match ? "match_mismatch" : !Identifier(correlation) ? "invalid_correlation" : Bounds(command);
            if (invalid.Length > 0) { output.Add(new Delivery(actor, Rejection(actor, command, invalid))); return output; }
            string key = actor + ":" + correlation;
            byte[] hash;
            using (var sha = SHA256.Create()) hash = sha.ComputeHash(Encoding.UTF8.GetBytes(command.Descriptor.FullName + "\0").Concat(command.ToByteArray()).ToArray());
            if (replays.TryGetValue(key, out var previous))
            {
                if (!previous.Hash.SequenceEqual(hash)) output.Add(new Delivery(actor, Rejection(actor, command, "correlation_conflict")));
                else if (PrivateReply(previous.Reply) && (!Active || !RoleCanRead(actor, command) || ReplyExpiry(previous.Reply) <= now))
                    output.Add(new Delivery(actor, Rejection(actor, command, "evidence_unavailable")));
                else output.Add(new Delivery(actor, Clone(previous.Reply)));
                return output;
            }
            if (replays.Count >= MaxReplayEntries || !Rate(actor, now))
            { output.Add(new Delivery(actor, Rejection(actor, command, replays.Count >= MaxReplayEntries ? "request_capacity" : "rate_limited"))); return output; }
            var action = command is SelectRoleCommand role ? Select(actor, role, now)
                : command is MaintenanceOverrideCommand change ? Override(caller, change, now)
                : Investigate(caller, command, now);
            output.AddRange(action);
            var reply = action.LastOrDefault(d => d.Recipient == actor && IsReply(d.Payload));
            if (reply != null) replays[key] = new Replay { Hash = hash, Reply = Clone(reply.Payload), Expires = now + lifetime };
            return output;
        }
        private List<Delivery> Select(string actor, SelectRoleCommand command, long now)
        {
            string reason = "";
            if (command.Role != CrewRole.Crew && command.Role != CrewRole.Engineer && command.Role != CrewRole.Sheriff) reason = "invalid_role";
            else if (Active && command.Role != Role(actor)) reason = "role_selection_locked";
            else if (command.Role != CrewRole.Crew && roles.Any(p => p.Key != actor && p.Value == command.Role)) reason = "role_unavailable";
            var output = new List<Delivery>();
            if (reason.Length == 0 && Role(actor) != command.Role)
            {
                roles[actor] = command.Role;
                if (state.IncidentStatus == IncidentStatus.None) ResetCycle();
                Advance(); output.AddRange(Broadcast(true));
            }
            if (state.IncidentStatus == IncidentStatus.None)
            { bool ready = Holder(CrewRole.Engineer).Length > 0 && Holder(CrewRole.Sheriff).Length > 0; if (!ready) readyAt = -1; else if (readyAt < 0) readyAt = now; }
            output.Add(new Delivery(actor, new RoleResultEvent { CorrelationId = command.CorrelationId, Accepted = reason.Length == 0,
                ReasonCode = reason.Length == 0 ? "role_selected" : reason, State = OwnRole(actor), MatchId = match, ServerRevision = revision }));
            return output;
        }
        private List<Delivery> Override(Participant caller, MaintenanceOverrideCommand command, long now)
        {
            string reason = "";
            if (Role(caller.Id) != CrewRole.Crew && Role(caller.Id) != CrewRole.Engineer) reason = "maintenance_permission_required";
            else if (!cooling.Available) reason = "terminal_unavailable";
            else if (!At(caller, cooling, "engineering")) reason = "cooling_out_of_range";
            else if (command.CircuitRef != "alpha" && command.CircuitRef != "beta") reason = "invalid_circuit";
            else if (state.IncidentStatus != IncidentStatus.None) reason = "incident_conflict";
            else if (now < nextMaintenance) reason = "maintenance_cooldown";
            else if (Holder(CrewRole.Engineer).Length == 0 || Holder(CrewRole.Sheriff).Length == 0) reason = "duty_roles_required";
            else if (history.Any(r => r.Action == AuditAction.FlowAdjustment && r.CircuitRef == command.CircuitRef)) reason = "maintenance_cycle_capacity";
            var output = new List<Delivery>();
            if (reason.Length == 0)
            {
                nextMaintenance = now + 2000;
                history.Add(Record(command.CircuitRef, now, AuditAction.FlowAdjustment, caller.Id, true)); TrimHistory();
                // The valve adjustment is real now. Its effect is evaluated at
                // the same controller-cycle boundary as a natural fault, so an
                // immediate alarm cannot disclose whether this was active or
                // standby. Both successful panel outcomes are identical.
                if (command.CircuitRef == activeCircuit) pendingManual = true;
            }
            // A successful action says only that the actual panel operation was
            // accepted. Public state reveals whether a failure was detected.
            output.Add(new Delivery(caller.Id, ActionResult(command, reason))); return output;
        }
        private List<Delivery> Investigate(Participant caller, IMessage command, long now)
        {
            var output = new List<Delivery>();
            string requested = RequestedIncident(command), reason = "";
            if (requested.Length == 0 || requested != state.IncidentId) reason = "incident_not_found";
            else if (command is CancelCoolingRepairCommand)
            {
                if (caller.Id != fixedEngineer || Role(caller.Id) != CrewRole.Engineer) reason = "engineer_required";
                else if (repairCorrelation.Length > 0)
                {
                    string previous = repairCorrelation; ClearRepair(); Advance();
                    var stopped = Repair(previous, false, "repair_cancelled", false);
                    CacheReply(caller.Id, previous, stopped); output.Add(new Delivery(caller.Id, stopped)); output.AddRange(Broadcast(false));
                }
                output.Add(new Delivery(caller.Id, ActionResult(command, reason))); return output;
            }
            else if (command is CancelInvestigationCommand)
            {
                if (caller.Id != fixedEngineer && caller.Id != fixedSheriff) reason = "incident_participant_required";
                else if (Active) { Finish(IncidentStatus.Cancelled, "participant_cancelled"); output.AddRange(Broadcast(true)); }
                output.Add(new Delivery(caller.Id, ActionResult(command, reason))); return output;
            }
            else if (!Active) reason = state.IncidentStatus == IncidentStatus.Expired ? "incident_expired" : "incident_not_active";
            if (reason.Length > 0) { output.Add(new Delivery(caller.Id, Rejection(caller.Id, command, reason))); return output; }
            if (command is InspectCoolingCommand inspect)
            {
                if (caller.Id != fixedEngineer || Role(caller.Id) != CrewRole.Engineer) reason = "engineer_required";
                else if (!cooling.Available) reason = "terminal_unavailable";
                else if (!At(caller, cooling, "engineering")) reason = "cooling_out_of_range";
                else if (!EvidenceLive(diagnostic?.Provenance, now)) { diagnostic = BuildDiagnostic(now); deliveredDiagnosticId = ""; }
                output.Add(new Delivery(caller.Id, DiagnosticReply(inspect.CorrelationId, caller.Id, reason)));
                if (reason.Length == 0) output.Add(new Delivery(caller.Id, OwnView(caller.Id)));
            }
            else if (command is InspectSecurityRecordsCommand inspectSecurity)
            {
                if (caller.Id != fixedSheriff || Role(caller.Id) != CrewRole.Sheriff) reason = "sheriff_required";
                else if (!security.Available) reason = "terminal_unavailable";
                else if (!At(caller, security, "main-hall")) reason = "security_out_of_range";
                else if (!EvidenceLive(securityEvidence?.Provenance, now)) securityEvidence = BuildSecurity(now);
                output.Add(new Delivery(caller.Id, SecurityReply(inspectSecurity.CorrelationId, caller.Id, reason)));
                if (reason.Length == 0) output.Add(new Delivery(caller.Id, OwnView(caller.Id)));
            }
            else if (command is RepairCoolingCommand repair)
            {
                if (caller.Id != fixedEngineer || Role(caller.Id) != CrewRole.Engineer) reason = "engineer_required";
                else if (!cooling.Available) reason = "terminal_unavailable";
                else if (!At(caller, cooling, "engineering")) reason = "cooling_out_of_range";
                else if (!EvidenceLive(diagnostic?.Provenance, now) || repair.DiagnosticEvidenceId != diagnostic.Provenance.EvidenceId) reason = "diagnostic_evidence_required";
                else if (state.CoolingStatus == CoolingStatus.Restored) { output.Add(new Delivery(caller.Id, Repair(repair.CorrelationId, true, "already_restored", true))); return output; }
                else if (repairCorrelation.Length > 0) reason = "repair_in_progress";
                if (reason.Length == 0)
                { repairCorrelation = repair.CorrelationId; state.CoolingStatus = CoolingStatus.Repairing; state.RepairCompletesAtUnixMs = now + repairDuration; Advance(); output.AddRange(Broadcast(false)); }
                output.Add(new Delivery(caller.Id, Repair(repair.CorrelationId, reason.Length == 0, reason.Length == 0 ? "repair_started" : reason, false)));
            }
            else if (command is SubmitInvestigationReportCommand report) output.AddRange(Report(caller, report, now));
            else output.Add(new Delivery(caller.Id, Rejection(caller.Id, command, "invalid_action")));
            return output;
        }
        private List<Delivery> Report(Participant caller, SubmitInvestigationReportCommand report, long now)
        {
            string reason = "";
            if (caller.Id != fixedSheriff || Role(caller.Id) != CrewRole.Sheriff) reason = "sheriff_required";
            else if (!security.Available) reason = "terminal_unavailable";
            else if (!At(caller, security, "main-hall")) reason = "security_out_of_range";
            else if (state.ReportValidated) reason = "report_already_validated";
            else if (now < nextReport) reason = "report_cooldown";
            else if (report.ProposedOrigin != ProposedOrigin.MechanicalFailure && report.ProposedOrigin != ProposedOrigin.ManualInterference && report.ProposedOrigin != ProposedOrigin.InsufficientEvidence) reason = "invalid_hypothesis";
            if (reason.Length == 0 && report.ProposedOrigin == ProposedOrigin.InsufficientEvidence)
            { nextReport = now + 2000; return new List<Delivery> { new(caller.Id, Investigation(report.CorrelationId, true, "insufficient_evidence")) }; }
            if (reason.Length == 0 && (!EvidenceLive(diagnostic?.Provenance, now) || !EvidenceLive(securityEvidence?.Provenance, now))) reason = "evidence_required";
            if (reason.Length == 0 && deliveredDiagnosticId != diagnostic.Provenance.EvidenceId) reason = "diagnostic_reference_not_delivered";
            if (reason.Length == 0 && (report.SupportingEvidenceIds.Count != 3 || report.SupportingEvidenceIds.Distinct().Count() != 3
                || !report.SupportingEvidenceIds.Contains(diagnostic.Provenance.EvidenceId) || !report.SupportingEvidenceIds.Contains(securityEvidence.Provenance.EvidenceId))) reason = "invalid_evidence_references";
            var output = new List<Delivery>();
            if (reason.Length == 0)
            {
                nextReport = now + 2000;
                string recordId = report.SupportingEvidenceIds.Single(id => id != diagnostic.Provenance.EvidenceId && id != securityEvidence.Provenance.EvidenceId);
                var candidate = securityEvidence.Records.SingleOrDefault(r => r.RecordId == recordId);
                bool correlated = candidate != null && Correlates(candidate, diagnostic)
                    && securityEvidence.CompleteForListedCircuits && securityEvidence.CircuitRefs.Contains(diagnostic.CircuitRef)
                    && securityEvidence.CoverageWindowStartUnixMs <= diagnostic.FailureWindowStartUnixMs
                    && securityEvidence.CoverageWindowEndUnixMs >= diagnostic.FailureWindowEndUnixMs;
                bool manual = correlated && candidate.Action == AuditAction.FlowAdjustment && candidate.ActorId.Length > 0;
                bool mechanical = correlated && (candidate.Action == AuditAction.ControllerCheck || candidate.Action == AuditAction.Inspect)
                    && !securityEvidence.Records.Any(r => Correlates(r, diagnostic) && r.Action == AuditAction.FlowAdjustment && r.ActorId.Length > 0);
                bool correct = correlated && (manual && hiddenOrigin == HiddenOrigin.Manual && report.ProposedOrigin == ProposedOrigin.ManualInterference
                    || mechanical && hiddenOrigin == HiddenOrigin.Mechanical && report.ProposedOrigin == ProposedOrigin.MechanicalFailure);
                if (correct) { state.ReportValidated = true; Advance(); ResolveIfReady(); output.AddRange(Broadcast(true)); }
                else { ++reportAttempts; reason = "report_not_supported"; if (reportAttempts >= 3) { Finish(IncidentStatus.Failed, "report_attempts_exhausted"); output.AddRange(Broadcast(true)); } }
            }
            output.Add(new Delivery(caller.Id, Investigation(report.CorrelationId, reason.Length == 0, reason.Length == 0 ? "report_validated" : reason))); return output;
        }

        internal bool AllowsTaskMessage(string actor, string taskId)
            => Active && actors.ContainsKey(actor) && taskId == state.IncidentId
                && (actor == fixedEngineer && Role(actor) == CrewRole.Engineer
                    || actor == fixedSheriff && Role(actor) == CrewRole.Sheriff);
        internal string ValidateEvidenceMessage(string sender, string recipient, string channel, string taskId, string text, bool sensitive)
        {
            bool technical = ContainsReference(text, diagnostic?.Provenance.EvidenceId), audit = ContainsReference(text, securityEvidence?.Provenance.EvidenceId);
            if (!technical && !audit) return "";
            if (!Active || taskId != state.IncidentId) return "evidence_incident_unavailable";
            if (!sensitive || channel != "direct") return "evidence_requires_sensitive_direct";
            if (technical && (sender != fixedEngineer || recipient != fixedSheriff)
                || audit && (sender != fixedSheriff || recipient != fixedEngineer)) return "evidence_recipient_not_permitted";
            return "";
        }
        // Trusted integration invokes this only for a real, still-live direct
        // message after the intended recipient's actual acknowledgement.
        internal void OnDeliveredEvidence(string sender, string recipient, string incidentId, string text, long now)
        {
            if (!Active || incidentId != state.IncidentId || sender != fixedEngineer || recipient != fixedSheriff
                || Role(sender) != CrewRole.Engineer || Role(recipient) != CrewRole.Sheriff
                || !EvidenceLive(diagnostic?.Provenance, now) || !ContainsReference(text, diagnostic.Provenance.EvidenceId)) return;
            deliveredDiagnosticId = diagnostic.Provenance.EvidenceId;
        }
        internal List<Delivery> ControlUnavailable(string actor, long now)
        {
            var output = Tick(now);
            if (Active && (actor == fixedEngineer || actor == fixedSheriff))
            { Finish(IncidentStatus.Cancelled, "role_holder_control_unavailable"); roles[actor] = CrewRole.Crew; Advance(); output.AddRange(Broadcast(true)); }
            else if (state.IncidentStatus == IncidentStatus.None && (Role(actor) == CrewRole.Engineer || Role(actor) == CrewRole.Sheriff))
            { roles[actor] = CrewRole.Crew; ResetCycle(); Advance(); output.AddRange(Broadcast(true)); }
            return output;
        }

        private void Begin(HiddenOrigin origin, long now)
        {
            hiddenOrigin = origin; fixedEngineer = Holder(CrewRole.Engineer); fixedSheriff = Holder(CrewRole.Sheriff);
            faultWindowStart = readyAt; faultWindowEnd = now;
            diagnostic = null; securityEvidence = null; deliveredDiagnosticId = ""; reportAttempts = 0; nextReport = 0;
            state.IncidentId = Guid.NewGuid().ToString("N"); state.IncidentStatus = IncidentStatus.Active;
            state.CoolingStatus = CoolingStatus.Failure; state.Severity = CoolingSeverity.Elevated;
            state.DetectedAtUnixMs = now / 10000 * 10000; state.ExpiresAtUnixMs = now + lifetime;
            state.ReportValidated = false; state.RepairCompletesAtUnixMs = 0; state.ReasonCode = "cooling_failure";
            // Actual automatic controller checkpoints are neutral facts, not
            // fabricated player actions. Physical valve changes do not rewrite
            // controller configuration: both paths retain revision R -> R.
            foreach (string circuit in new[] { "alpha", "beta" })
                if (!history.Any(r => r.CircuitRef == circuit && r.Action == AuditAction.FlowAdjustment))
                    history.Add(Record(circuit, now, AuditAction.ControllerCheck, "", true));
            TrimHistory(); Advance();
        }
        private DiagnosticEvidence BuildDiagnostic(long now)
        {
            var d = new DiagnosticEvidence { Provenance = Provenance(fixedEngineer, CrewRole.Engineer, CoolingEntity, now), CircuitRef = activeCircuit,
                FailureWindowStartUnixMs = faultWindowStart, FailureWindowEndUnixMs = faultWindowEnd,
                EquipmentWear = .55 + (unchecked((uint)seed) % 4) * .1,
                ControllerRevisionBefore = ControllerRevision(activeCircuit), ControllerRevisionAfter = ControllerRevision(activeCircuit) };
            // Post-cycle pressure response. Every timestamp is an actual past
            // sample point, never a fabricated future pressure observation.
            long elapsed = Math.Max(0, now - faultWindowEnd);
            for (int i = 0; i < 6; ++i)
            {
                long offset = elapsed * i / 5;
                d.Samples.Add(new PressureSample { AtUnixMs = faultWindowEnd + offset, Pressure = .51 + .49 * Math.Exp(-offset / 300.0) });
            }
            return d;
        }
        private SecurityEvidence BuildSecurity(long now)
        {
            var s = new SecurityEvidence { Provenance = Provenance(fixedSheriff, CrewRole.Sheriff, SecurityEntity, now),
                CoverageWindowStartUnixMs = faultWindowStart, CoverageWindowEndUnixMs = faultWindowEnd, CompleteForListedCircuits = true };
            s.CircuitRefs.Add(new[] { "alpha", "beta" });
            foreach (string circuit in new[] { "alpha", "beta" })
            {
                var record = history.Single(r => r.CircuitRef == circuit).Clone();
                // The source exposes its completed controller-cycle interval,
                // not precise actor timing. It truthfully contains the actual
                // recorded interaction and is identical for both causes.
                record.WindowStartUnixMs = faultWindowStart; record.WindowEndUnixMs = faultWindowEnd;
                s.Records.Add(record);
            }
            // Bounded neutral prior checkpoints are labelled as controller
            // history, with no invented actor identity or intent accusation.
            s.Records.Add(Record("alpha", faultWindowStart - 8000, AuditAction.Inspect, "", true));
            var stale = Record("beta", faultWindowStart, AuditAction.ControllerCheck, "", true);
            stale.WindowEndUnixMs = faultWindowEnd;
            stale.PriorControllerRevision += 20; stale.ResultingControllerRevision += 20;
            stale.SourceEntityId = "cooling-controller-archive-01"; s.Records.Add(stale);
            s.Records.Add(Record("alpha", faultWindowStart, AuditAction.Access, "", false));
            return s;
        }
        private MaintenanceRecord Record(string circuit, long now, AuditAction action, string actor, bool confirmed) => new()
        {
            RecordId = Guid.NewGuid().ToString("N"), SourceEntityId = actor.Length > 0 ? CoolingEntity : "engineering-controller-01",
            CircuitRef = circuit, WindowStartUnixMs = now, WindowEndUnixMs = now,
            Action = action, ActorId = actor, InteractionConfirmed = confirmed,
            PriorControllerRevision = ControllerRevision(circuit), ResultingControllerRevision = ControllerRevision(circuit)
        };
        private ulong ControllerRevision(string circuit) => (ulong)(100 + unchecked((uint)seed) % 10 + (circuit == "beta" ? 10 : 0));
        private void TrimHistory() { if (history.Count > MaxHistoryEntries) history.RemoveRange(0, history.Count - MaxHistoryEntries); }
        private void ResetCycle() { readyAt = -1; pendingManual = false; history.Clear(); nextMaintenance = 0; }
        private static string RandomCircuit()
        { using var random = RandomNumberGenerator.Create(); var bytes = new byte[1]; random.GetBytes(bytes); return (bytes[0] & 1) == 0 ? "alpha" : "beta"; }
        private EvidenceProvenance Provenance(string actor, CrewRole role, string source, long now) => new()
        { EvidenceId = Guid.NewGuid().ToString("N"), IncidentId = state.IncidentId, SourceEntityId = source, ObservedAtUnixMs = now,
            ExpiresAtUnixMs = state.ExpiresAtUnixMs, ServerRevision = revision, MatchId = match, RecipientActorId = actor,
            RecipientRole = role, Visibility = EvidenceVisibility.RolePrivate };
        private bool EvidenceLive(EvidenceProvenance p, long now) => p != null && p.MatchId == match && p.IncidentId == state.IncidentId && p.ExpiresAtUnixMs > now;
        private static bool Correlates(MaintenanceRecord r, DiagnosticEvidence d) => r.InteractionConfirmed && r.CircuitRef == d.CircuitRef
            && r.WindowStartUnixMs <= d.FailureWindowEndUnixMs && r.WindowEndUnixMs >= d.FailureWindowStartUnixMs
            && r.PriorControllerRevision == d.ControllerRevisionBefore && r.ResultingControllerRevision == d.ControllerRevisionAfter;
        private void ResolveIfReady()
        { if (Active && state.ReportValidated && state.CoolingStatus == CoolingStatus.Restored) Finish(IncidentStatus.Resolved, "incident_resolved"); }
        private void Finish(IncidentStatus status, string reason)
        {
            string interrupted = repairCorrelation;
            ClearRepair(); state.IncidentStatus = status; state.ReasonCode = reason; Advance();
            if (interrupted.Length > 0) CacheReply(fixedEngineer, interrupted, Repair(interrupted, false, reason, false));
            diagnostic = null; securityEvidence = null; deliveredDiagnosticId = "";
            foreach (string key in replays.Where(p => PrivateReply(p.Value.Reply)).Select(p => p.Key).ToArray()) replays.Remove(key);
        }
        private void ClearRepair()
        { repairCorrelation = ""; state.RepairCompletesAtUnixMs = 0; if (state.CoolingStatus == CoolingStatus.Repairing) state.CoolingStatus = CoolingStatus.Failure; }
        private void Advance() { ++revision; state.ServerRevision = revision; }
        private List<Delivery> Broadcast(bool includeRoles, bool started = false)
        {
            var output = new List<Delivery>();
            foreach (string id in actors.Keys)
            {
                output.Add(new Delivery(id, state.Clone())); output.Add(new Delivery(id, OwnView(id)));
                output.Add(new Delivery(id, started ? new IncidentStartedEvent { State = state.Clone() } : new IncidentChangedEvent { State = state.Clone() }));
                if (includeRoles) { output.Add(new Delivery(id, OwnRole(id))); output.Add(new Delivery(id, Roster())); }
            }
            return output;
        }
        private CrewRole Role(string actor) => roles.TryGetValue(actor, out var role) ? role : CrewRole.Crew;
        private string Holder(CrewRole role) => roles.FirstOrDefault(p => p.Value == role).Key ?? "";
        private static bool At(Participant p, Terminal t, string room) => t.Available && p.Room == room && Finite(p.X) && Finite(p.Y)
            && (p.X - t.X) * (p.X - t.X) + (p.Y - t.Y) * (p.Y - t.Y) <= t.Radius * t.Radius;
        private static bool Finite(double x) => !double.IsNaN(x) && !double.IsInfinity(x);
        private static bool Identifier(string s) => !string.IsNullOrWhiteSpace(s) && s.Length <= 64;
        private static string Bound(string s) => s == null ? "" : s.Length <= 64 ? s : s.Substring(0, 64);
        private static bool ContainsReference(string text, string id) => text != null && id != null && id.Length == 32 && text.IndexOf(id, StringComparison.OrdinalIgnoreCase) >= 0;
        private bool Rate(string actor, long now)
        {
            if (!buckets.TryGetValue(actor, out var b)) buckets[actor] = b = new Bucket { At = now };
            b.Tokens = Math.Min(4, b.Tokens + Math.Max(0, now - b.At) * .002); b.At = now;
            globalTokens = Math.Min(16, globalTokens + Math.Max(0, now - globalAt) * .008); globalAt = now;
            if (b.Tokens < 1 || globalTokens < 1) return false; --b.Tokens; --globalTokens; return true;
        }
        private bool RoleCanRead(string actor, IMessage command) => command is InspectCoolingCommand
            ? actor == fixedEngineer && Role(actor) == CrewRole.Engineer : actor == fixedSheriff && Role(actor) == CrewRole.Sheriff;
        private static bool PrivateReply(IMessage m) => m is DiagnosticEvidenceEvent || m is SecurityEvidenceEvent;
        private static long ReplyExpiry(IMessage m) => m is DiagnosticEvidenceEvent d ? d.ExpiresAtUnixMs : m is SecurityEvidenceEvent s ? s.ExpiresAtUnixMs : long.MaxValue;
        private static bool IsReply(IMessage m) => m is RoleResultEvent || PrivateReply(m) || m is RepairResultEvent || m is InvestigationResultEvent || m is IncidentActionResultEvent;
        private static IMessage Clone(IMessage m) => m.Descriptor.Parser.ParseFrom(m.ToByteArray());
        private void CacheReply(string actor, string correlation, IMessage reply)
        { if (replays.TryGetValue(actor + ":" + correlation, out var r)) r.Reply = Clone(reply); }
        private DiagnosticEvidenceEvent DiagnosticReply(string correlation, string actor, string reason) => new()
        { CorrelationId = correlation, Accepted = reason.Length == 0, ReasonCode = reason.Length == 0 ? "evidence_observed" : reason,
            Evidence = reason.Length == 0 ? diagnostic.Clone() : null, MatchId = match, RecipientActorId = actor,
            ExpiresAtUnixMs = reason.Length == 0 ? diagnostic.Provenance.ExpiresAtUnixMs : 0, ServerRevision = revision };
        private SecurityEvidenceEvent SecurityReply(string correlation, string actor, string reason) => new()
        { CorrelationId = correlation, Accepted = reason.Length == 0, ReasonCode = reason.Length == 0 ? "evidence_observed" : reason,
            Evidence = reason.Length == 0 ? securityEvidence.Clone() : null, MatchId = match, RecipientActorId = actor,
            ExpiresAtUnixMs = reason.Length == 0 ? securityEvidence.Provenance.ExpiresAtUnixMs : 0, ServerRevision = revision };
        private RepairResultEvent Repair(string correlation, bool accepted, string reason, bool completed) => new()
        { CorrelationId = correlation, Accepted = accepted, ReasonCode = reason, IncidentId = state.IncidentId, Completed = completed,
            CoolingStatus = state.CoolingStatus, IncidentStatus = state.IncidentStatus, ServerRevision = revision, MatchId = match };
        private InvestigationResultEvent Investigation(string correlation, bool accepted, string reason) => new()
        { CorrelationId = correlation, Accepted = accepted, ReasonCode = reason, IncidentId = state.IncidentId,
            ReportValidated = state.ReportValidated, IncidentStatus = state.IncidentStatus, ServerRevision = revision, MatchId = match };
        private IncidentActionResultEvent ActionResult(IMessage command, string reason) => new()
        { CorrelationId = Bound(Correlation(command)), Action = command is MaintenanceOverrideCommand ? IncidentAction.MaintenanceOverride
            : command is CancelCoolingRepairCommand ? IncidentAction.CancelRepair : IncidentAction.CancelInvestigation,
            Accepted = reason.Length == 0, ReasonCode = reason.Length == 0 ? "accepted" : reason, IncidentId = state.IncidentId,
            CoolingStatus = state.CoolingStatus, IncidentStatus = state.IncidentStatus, MatchId = match, ServerRevision = revision };
        private IMessage Rejection(string actor, IMessage command, string reason)
            => command is SelectRoleCommand ? new RoleResultEvent { CorrelationId = Bound(Correlation(command)), Accepted = false, ReasonCode = reason, State = OwnRole(actor), MatchId = match, ServerRevision = revision }
            : command is InspectCoolingCommand ? DiagnosticReply(Bound(Correlation(command)), actor, reason)
            : command is InspectSecurityRecordsCommand ? SecurityReply(Bound(Correlation(command)), actor, reason)
            : command is RepairCoolingCommand ? Repair(Bound(Correlation(command)), false, reason, false)
            : command is SubmitInvestigationReportCommand ? Investigation(Bound(Correlation(command)), false, reason)
            : ActionResult(command, reason);
        private static string Bounds(IMessage command)
        {
            if (command.CalculateSize() > 4096 || RequestedIncident(command).Length > 64) return "invalid_identifier";
            if (command is RepairCoolingCommand repair && repair.DiagnosticEvidenceId.Length > 64) return "invalid_identifier";
            if (command is MaintenanceOverrideCommand panel && panel.CircuitRef.Length > 32) return "invalid_identifier";
            if (command is SubmitInvestigationReportCommand report && (report.SupportingEvidenceIds.Count > 8
                || report.SupportingEvidenceIds.Any(id => !Identifier(id)) || Encoding.UTF8.GetByteCount(report.Explanation) > 512)) return "invalid_report_payload";
            return "";
        }
        private static string Correlation(IMessage m) => m switch { SelectRoleCommand c => c.CorrelationId, InspectCoolingCommand c => c.CorrelationId,
            InspectSecurityRecordsCommand c => c.CorrelationId, RepairCoolingCommand c => c.CorrelationId, CancelCoolingRepairCommand c => c.CorrelationId,
            CancelInvestigationCommand c => c.CorrelationId, MaintenanceOverrideCommand c => c.CorrelationId, SubmitInvestigationReportCommand c => c.CorrelationId, _ => "" };
        private static string Match(IMessage m) => m switch { SelectRoleCommand c => c.MatchId, InspectCoolingCommand c => c.MatchId,
            InspectSecurityRecordsCommand c => c.MatchId, RepairCoolingCommand c => c.MatchId, CancelCoolingRepairCommand c => c.MatchId,
            CancelInvestigationCommand c => c.MatchId, MaintenanceOverrideCommand c => c.MatchId, SubmitInvestigationReportCommand c => c.MatchId, _ => "" };
        private static string RequestedIncident(IMessage m) => m switch { InspectCoolingCommand c => c.IncidentId, InspectSecurityRecordsCommand c => c.IncidentId,
            RepairCoolingCommand c => c.IncidentId, CancelCoolingRepairCommand c => c.IncidentId, CancelInvestigationCommand c => c.IncidentId,
            SubmitInvestigationReportCommand c => c.IncidentId, _ => "" };
    }
}
