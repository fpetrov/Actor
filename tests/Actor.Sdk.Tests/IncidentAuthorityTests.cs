using Actor.Networking;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;

namespace Actor.Sdk.Tests;

public sealed class IncidentAuthorityTests
{
    private sealed class Fixture
    {
        internal const long Start = 1790000000123;
        internal readonly string Match = new('a', 32);
        internal readonly IncidentAuthority Authority;
        internal readonly IncidentAuthority.Participant Engineer = new() { Id = "engineer", Name = "Engineer", Room = "engineering", X = -.25, Y = -6.54 };
        internal readonly IncidentAuthority.Participant Sheriff = new() { Id = "sheriff", Name = "Sheriff", Room = "main-hall", X = -1, Y = -.65 };
        internal readonly IncidentAuthority.Participant Crew = new() { Id = "crew", Name = "Crew", Room = "engineering", X = -.25, Y = -6.54 };
        internal long Now = Start;
        private int correlation;
        internal DiagnosticEvidence? Diagnostic;
        internal SecurityEvidence? Security;
        internal Fixture(bool natural = false, string active = "alpha", long ttl = 300000)
        {
            Authority = new IncidentAuthority(Match, new IncidentAuthority.ScenarioConfiguration
            { Seed = 4, ActiveCircuit = active, NaturalEnabled = natural, NaturalDelayMs = 10010, IncidentTtlMs = ttl });
            Refresh();
            Do(Engineer.Id, Role(CrewRole.Engineer));
            Do(Sheriff.Id, Role(CrewRole.Sheriff));
        }
        internal string Next() => "request-" + ++correlation;
        internal SelectRoleCommand Role(CrewRole role) => new() { Role = role, MatchId = Match, CorrelationId = Next() };
        internal List<IncidentAuthority.Delivery> Refresh() => Authority.Refresh(new[] { Engineer, Sheriff, Crew }, Now);
        internal List<IncidentAuthority.Delivery> Do(string actor, IMessage command) => Authority.Execute(actor, command, Now);
        internal void Advance(long milliseconds) { Now += milliseconds; Authority.Tick(Now); }
        internal void Manual(string circuit = "alpha", long actionOffset = 1000)
        {
            Now = Start + actionOffset;
            Assert.True(Payload<IncidentActionResultEvent>(Do(Crew.Id, Override(circuit))).Accepted);
            Assert.False(Authority.Active);
            Now = Start + 10010; Authority.Tick(Now);
            Assert.True(Authority.Active);
        }
        internal void Mechanical(string? standby = null)
        {
            Now = Start + 10000;
            if (standby != null) Assert.True(Payload<IncidentActionResultEvent>(Do(Crew.Id, Override(standby))).Accepted);
            Now = Start + 10010; Authority.Tick(Now); Assert.True(Authority.Active);
        }
        internal MaintenanceOverrideCommand Override(string circuit) => new() { CircuitRef = circuit, MatchId = Match, CorrelationId = Next() };
        internal DiagnosticEvidence InspectCooling()
        {
            Advance(100);
            var result = Payload<DiagnosticEvidenceEvent>(Do(Engineer.Id, Inspect()));
            Assert.True(result.Accepted, result.ReasonCode); return Diagnostic = result.Evidence;
        }
        internal InspectCoolingCommand Inspect(string? correlation = null) => new()
        { IncidentId = Authority.IncidentId, MatchId = Match, CorrelationId = correlation ?? Next() };
        internal SecurityEvidence InspectSecurity()
        {
            Advance(100);
            var result = Payload<SecurityEvidenceEvent>(Do(Sheriff.Id, new InspectSecurityRecordsCommand
            { IncidentId = Authority.IncidentId, MatchId = Match, CorrelationId = Next() }));
            Assert.True(result.Accepted, result.ReasonCode); return Security = result.Evidence;
        }
        internal void Evidence(bool share = true)
        {
            InspectCooling(); InspectSecurity();
            if (share) Share();
        }
        internal void Share() => Authority.OnDeliveredEvidence(Engineer.Id, Sheriff.Id, Authority.IncidentId, Diagnostic!.Provenance.EvidenceId, Now);
        internal RepairCoolingCommand RepairCommand(string? id = null) => new()
        { IncidentId = Authority.IncidentId, DiagnosticEvidenceId = id ?? Diagnostic!.Provenance.EvidenceId, MatchId = Match, CorrelationId = Next() };
        internal RepairResultEvent Repair()
            => Payload<RepairResultEvent>(Do(Engineer.Id, RepairCommand()));
        internal SubmitInvestigationReportCommand ReportCommand(ProposedOrigin origin, string? record = null)
        {
            var result = new SubmitInvestigationReportCommand { IncidentId = Authority.IncidentId, ProposedOrigin = origin, MatchId = Match, CorrelationId = Next() };
            result.SupportingEvidenceIds.Add(new[] { Diagnostic!.Provenance.EvidenceId, Security!.Provenance.EvidenceId,
                record ?? Matching(Security, Diagnostic).Single().RecordId });
            return result;
        }
        internal InvestigationResultEvent Report(ProposedOrigin origin, string? record = null)
            => Payload<InvestigationResultEvent>(Do(Sheriff.Id, ReportCommand(origin, record)));
    }
    private static T Payload<T>(IEnumerable<IncidentAuthority.Delivery> deliveries) where T : IMessage
        => deliveries.Select(d => d.Payload).OfType<T>().Single();
    private static IEnumerable<MaintenanceRecord> Matching(SecurityEvidence security, DiagnosticEvidence diagnostic)
        => security.Records.Where(r => r.InteractionConfirmed && r.CircuitRef == diagnostic.CircuitRef
            && r.WindowStartUnixMs <= diagnostic.FailureWindowEndUnixMs && r.WindowEndUnixMs >= diagnostic.FailureWindowStartUnixMs
            && r.PriorControllerRevision == diagnostic.ControllerRevisionBefore && r.ResultingControllerRevision == diagnostic.ControllerRevisionAfter);
    private static DiagnosticEvidence Marginal(DiagnosticEvidence evidence)
    { var result = evidence.Clone(); NormalizeOpaqueIds(result.Provenance); return result; }
    private static SecurityEvidence Marginal(SecurityEvidence evidence)
    { var result = evidence.Clone(); NormalizeOpaqueIds(result.Provenance); foreach (var record in result.Records) record.RecordId = ""; return result; }
    private static void NormalizeOpaqueIds(EvidenceProvenance provenance)
    { provenance.EvidenceId = ""; provenance.IncidentId = ""; }

    [Fact]
    public void RolesAreUniqueCrewDefaultAndLockedDuringActiveIncident()
    {
        var f = new Fixture(); Assert.Equal(CrewRole.Crew, f.Authority.OwnRole(f.Crew.Id).Role);
        Assert.Equal(3, f.Authority.Roster().Entries.Count);
        Assert.Equal("role_unavailable", Payload<RoleResultEvent>(f.Do(f.Crew.Id, f.Role(CrewRole.Engineer))).ReasonCode);
        f.Manual(); Assert.True(f.Authority.OwnRole(f.Engineer.Id).SelectionLocked);
        Assert.Equal("role_selection_locked", Payload<RoleResultEvent>(f.Do(f.Engineer.Id, f.Role(CrewRole.Sheriff))).ReasonCode);
        Assert.Equal(CrewRole.Engineer, f.Authority.OwnRole(f.Engineer.Id).Role);
    }
    [Theory]
    [InlineData(CrewRole.Engineer)]
    [InlineData(CrewRole.Sheriff)]
    public void NoAvailableRequiredRolePreventsNaturalGeneration(CrewRole role)
    {
        var f = new Fixture(natural: true); string id = role == CrewRole.Engineer ? f.Engineer.Id : f.Sheriff.Id;
        Assert.True(Payload<RoleResultEvent>(f.Do(id, f.Role(CrewRole.Crew))).Accepted);
        f.Advance(100000); Assert.Equal(IncidentStatus.None, f.Authority.State().IncidentStatus);
        Assert.Equal("duty_roles_required", Payload<IncidentActionResultEvent>(f.Do(f.Crew.Id, f.Override("alpha"))).ReasonCode);
    }
    [Fact]
    public void NaturalMechanicalIsClockDrivenAndManualUsesAnActualValidatedActorAction()
    {
        var natural = new Fixture(natural: true); natural.Advance(10009); Assert.False(natural.Authority.Active);
        natural.Advance(1); Assert.True(natural.Authority.Active); natural.Evidence();
        Assert.DoesNotContain(natural.Security!.Records, r => r.ActorId.Length > 0);
        Assert.True(natural.Report(ProposedOrigin.MechanicalFailure).ReportValidated);
        var manual = new Fixture(); manual.Manual(); manual.Evidence();
        Assert.Contains(manual.Security!.Records, r => r.Action == AuditAction.FlowAdjustment && r.ActorId == manual.Crew.Id && r.InteractionConfirmed);
        Assert.True(manual.Report(ProposedOrigin.ManualInterference).ReportValidated);
    }
    [Fact]
    public void StandbyOverrideIsBenignAndDoesNotReplaceNaturalMechanicalOrigin()
    {
        var f = new Fixture(natural: true); f.Mechanical("beta"); f.Evidence();
        Assert.Contains(f.Security!.Records, r => r.ActorId == f.Crew.Id && r.CircuitRef == "beta" && r.Action == AuditAction.FlowAdjustment);
        Assert.True(f.Report(ProposedOrigin.MechanicalFailure).Accepted);
        Assert.False(f.Authority.State().CoolingStatus == CoolingStatus.Restored);
    }
    [Fact]
    public void IdenticalEngineerMarginalSupportsOppositeOriginsOnlyJointEvidenceSeparatesThem()
    {
        var manual = new Fixture(); manual.Manual(); manual.Evidence();
        var mechanical = new Fixture(natural: true); mechanical.Mechanical("beta"); mechanical.Evidence();
        Assert.Equal(Marginal(manual.Diagnostic!).ToByteArray(), Marginal(mechanical.Diagnostic!).ToByteArray());
        Assert.NotEqual(Marginal(manual.Security!).ToByteArray(), Marginal(mechanical.Security!).ToByteArray());
        Assert.True(manual.Report(ProposedOrigin.ManualInterference).Accepted);
        Assert.True(mechanical.Report(ProposedOrigin.MechanicalFailure).Accepted);
        Assert.All(manual.Diagnostic!.Samples, sample => Assert.InRange(sample.AtUnixMs,
            manual.Diagnostic.FailureWindowEndUnixMs, manual.Diagnostic.Provenance.ObservedAtUnixMs));
        Assert.True(manual.Security!.CoverageWindowEndUnixMs <= manual.Security.Provenance.ObservedAtUnixMs);
    }
    [Fact]
    public void IdenticalSheriffMarginalSupportsOppositeOriginsWhenHiddenFaultSelectorChanges()
    {
        var manual = new Fixture(); manual.Manual(); manual.Evidence();
        var mechanical = new Fixture(natural: true, active: "beta"); mechanical.Mechanical("alpha"); mechanical.Evidence();
        Assert.Equal(Marginal(manual.Security!).ToByteArray(), Marginal(mechanical.Security!).ToByteArray());
        Assert.NotEqual(manual.Diagnostic!.CircuitRef, mechanical.Diagnostic!.CircuitRef);
        Assert.True(manual.Report(ProposedOrigin.ManualInterference).Accepted);
        Assert.True(mechanical.Report(ProposedOrigin.MechanicalFailure).Accepted);
    }
    [Fact]
    public void ObservableControllerRevisionsAndWearDoNotConstrainThePrivateCircuitSelector()
    {
        // Same public seed variation, opposite trusted selector. This is a
        // deterministic contract test, not a probabilistic RNG distribution test.
        var alpha = new Fixture(natural: true, active: "alpha");
        var beta = new Fixture(natural: true, active: "beta");
        alpha.Mechanical(); beta.Mechanical(); alpha.Evidence(); beta.Evidence();
        Assert.Equal(Marginal(alpha.Security!).ToByteArray(), Marginal(beta.Security!).ToByteArray());
        Assert.Equal(alpha.Diagnostic!.EquipmentWear, beta.Diagnostic!.EquipmentWear);
        Assert.Equal("alpha", alpha.Diagnostic.CircuitRef); Assert.Equal("beta", beta.Diagnostic.CircuitRef);
        Assert.True(alpha.Report(ProposedOrigin.MechanicalFailure).Accepted);
        Assert.True(beta.Report(ProposedOrigin.MechanicalFailure).Accepted);
    }
    [Fact]
    public void RolePrivateEvidenceNeverAppearsInOtherRecipientsViewsOrPublicState()
    {
        var f = new Fixture(); f.Manual(); f.Evidence();
        Assert.Equal(OwnInvestigationViewObservation.EvidenceOneofCase.Diagnostic, f.Authority.OwnView(f.Engineer.Id).EvidenceCase);
        Assert.Equal(OwnInvestigationViewObservation.EvidenceOneofCase.Security, f.Authority.OwnView(f.Sheriff.Id).EvidenceCase);
        Assert.Equal(OwnInvestigationViewObservation.EvidenceOneofCase.None, f.Authority.OwnView(f.Crew.Id).EvidenceCase);
        Assert.DoesNotContain(f.Diagnostic!.Provenance.EvidenceId, f.Authority.State().ToString());
        Assert.DoesNotContain(f.Security!.Provenance.EvidenceId, f.Authority.Roster().ToString());
        Assert.DoesNotContain(CoolingStateObservation.Descriptor.Fields.InDeclarationOrder(), field => field.Name.Contains("origin") || field.Name.Contains("cause") || field.Name.Contains("circuit"));
    }
    [Theory]
    [InlineData("sheriff")]
    [InlineData("crew")]
    public void UnauthorizedCoolingInspectionAndRepairCannotReadOrAct(string actor)
    {
        var f = new Fixture(); f.Manual(); f.Evidence();
        var denied = Payload<DiagnosticEvidenceEvent>(f.Do(actor, f.Inspect()));
        Assert.False(denied.Accepted); Assert.Null(denied.Evidence);
        Assert.Equal("engineer_required", Payload<RepairResultEvent>(f.Do(actor, f.RepairCommand())).ReasonCode);
    }
    [Theory]
    [InlineData("engineer")]
    [InlineData("crew")]
    public void UnauthorizedSecurityInspectionAndReportCannotReadOrAct(string actor)
    {
        var f = new Fixture(); f.Manual(); f.Evidence();
        var denied = Payload<SecurityEvidenceEvent>(f.Do(actor, new InspectSecurityRecordsCommand
        { IncidentId = f.Authority.IncidentId, MatchId = f.Match, CorrelationId = f.Next() }));
        Assert.False(denied.Accepted); Assert.Null(denied.Evidence);
        Assert.Equal("sheriff_required", Payload<InvestigationResultEvent>(f.Do(actor, f.ReportCommand(ProposedOrigin.ManualInterference))).ReasonCode);
    }
    [Fact]
    public void DiagnosticReferenceRequiresActualPermittedSenderRecipientIncidentAndAcknowledgedDelivery()
    {
        var f = new Fixture(); f.Manual(); f.Evidence(false);
        Assert.Equal("diagnostic_reference_not_delivered", f.Report(ProposedOrigin.ManualInterference).ReasonCode);
        foreach (var bad in new[] { ("crew", "sheriff", f.Authority.IncidentId), ("engineer", "crew", f.Authority.IncidentId), ("engineer", "sheriff", "old-incident") })
            f.Authority.OnDeliveredEvidence(bad.Item1, bad.Item2, bad.Item3, f.Diagnostic!.Provenance.EvidenceId, f.Now);
        Assert.Equal("diagnostic_reference_not_delivered", f.Report(ProposedOrigin.ManualInterference).ReasonCode);
        f.Share(); Assert.True(f.Report(ProposedOrigin.ManualInterference).Accepted);
        // Delivery permits citation, not an authoritative foreign role view.
        Assert.Equal(OwnInvestigationViewObservation.EvidenceOneofCase.Security, f.Authority.OwnView(f.Sheriff.Id).EvidenceCase);
    }
    [Fact]
    public void SensitiveEvidenceIdsCannotBeBroadcastOrSentToUnassignedActors()
    {
        var f = new Fixture(); f.Manual(); f.Evidence(); string id = f.Diagnostic!.Provenance.EvidenceId;
        Assert.Equal("evidence_requires_sensitive_direct", f.Authority.ValidateEvidenceMessage("engineer", "sheriff", "station", f.Authority.IncidentId, id, true));
        Assert.Equal("evidence_requires_sensitive_direct", f.Authority.ValidateEvidenceMessage("engineer", "sheriff", "direct", f.Authority.IncidentId, id, false));
        Assert.Equal("evidence_recipient_not_permitted", f.Authority.ValidateEvidenceMessage("engineer", "crew", "direct", f.Authority.IncidentId, id, true));
        Assert.Empty(f.Authority.ValidateEvidenceMessage("engineer", "sheriff", "direct", f.Authority.IncidentId, id, true));
        Assert.Empty(f.Authority.ValidateEvidenceMessage("crew", "sheriff", "direct", "", "I accuse the engineer", false));
    }
    [Fact]
    public void IncidentMessageScopeIsOnlyForFixedActiveParticipants()
    {
        var f = new Fixture();
        Assert.False(f.Authority.AllowsTaskMessage(f.Engineer.Id, ""));
        f.Manual(); string id = f.Authority.IncidentId;
        Assert.True(f.Authority.AllowsTaskMessage(f.Engineer.Id, id));
        Assert.True(f.Authority.AllowsTaskMessage(f.Sheriff.Id, id));
        Assert.False(f.Authority.AllowsTaskMessage(f.Crew.Id, id));
        Assert.False(f.Authority.AllowsTaskMessage("absent", id));
        Assert.False(f.Authority.AllowsTaskMessage(f.Engineer.Id, "old-incident"));
        f.Authority.ControlUnavailable(f.Engineer.Id, f.Now);
        Assert.False(f.Authority.AllowsTaskMessage(f.Sheriff.Id, id));
    }
    [Fact]
    public void ExactlyThreeScopedReferencesAndCorrectJointRecordAreRequired()
    {
        var f = new Fixture(); f.Manual(); f.Evidence();
        var missing = f.ReportCommand(ProposedOrigin.ManualInterference); missing.SupportingEvidenceIds.RemoveAt(2);
        Assert.Equal("invalid_evidence_references", Payload<InvestigationResultEvent>(f.Do(f.Sheriff.Id, missing)).ReasonCode);
        var shotgun = f.ReportCommand(ProposedOrigin.ManualInterference); shotgun.SupportingEvidenceIds.Add(f.Security!.Records.Where(r => r.RecordId != shotgun.SupportingEvidenceIds[2]).First().RecordId);
        Assert.Equal("invalid_evidence_references", Payload<InvestigationResultEvent>(f.Do(f.Sheriff.Id, shotgun)).ReasonCode);
        Assert.Equal("report_not_supported", f.Report(ProposedOrigin.ManualInterference, f.Security.Records.Last().RecordId).ReasonCode);
        f.Advance(2000); Assert.True(f.Report(ProposedOrigin.ManualInterference).Accepted);
    }
    [Fact]
    public void ThreeIncorrectSemanticReportsFailWithoutRevealingCorrectOrigin()
    {
        var f = new Fixture(); f.Manual(); f.Evidence();
        for (int i = 0; i < 3; i++)
        {
            var result = f.Report(ProposedOrigin.MechanicalFailure);
            Assert.False(result.Accepted); Assert.Equal("report_not_supported", result.ReasonCode);
            Assert.DoesNotContain("ManualInterference", result.ToString());
            if (i < 2) { Assert.True(f.Authority.Active); f.Advance(2000); }
        }
        Assert.Equal(IncidentStatus.Failed, f.Authority.State().IncidentStatus);
        Assert.False(f.Authority.OwnRole(f.Engineer.Id).SelectionLocked);
    }
    [Fact]
    public void InsufficientEvidenceDoesNotValidateOrCompleteAndHasCooldown()
    {
        var f = new Fixture(); f.Manual();
        var request = new SubmitInvestigationReportCommand { IncidentId = f.Authority.IncidentId, ProposedOrigin = ProposedOrigin.InsufficientEvidence, MatchId = f.Match, CorrelationId = f.Next() };
        var result = Payload<InvestigationResultEvent>(f.Do(f.Sheriff.Id, request));
        Assert.True(result.Accepted); Assert.False(result.ReportValidated); Assert.True(f.Authority.Active);
        request.CorrelationId = f.Next(); Assert.Equal("report_cooldown", Payload<InvestigationResultEvent>(f.Do(f.Sheriff.Id, request)).ReasonCode);
    }
    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void BothIndependentBranchesAreRequiredInEitherOrder(bool repairFirst)
    {
        var f = new Fixture(); f.Manual(); f.Evidence();
        if (!repairFirst) { Assert.True(f.Report(ProposedOrigin.ManualInterference).ReportValidated); Assert.True(f.Authority.Active); }
        Assert.True(f.Repair().Accepted); Assert.Equal(CoolingStatus.Repairing, f.Authority.State().CoolingStatus);
        f.Advance(1999); Assert.Equal(CoolingStatus.Repairing, f.Authority.State().CoolingStatus);
        f.Advance(1); Assert.Equal(CoolingStatus.Restored, f.Authority.State().CoolingStatus);
        if (repairFirst) { Assert.True(f.Authority.Active); Assert.True(f.Report(ProposedOrigin.ManualInterference).ReportValidated); }
        Assert.Equal(IncidentStatus.Resolved, f.Authority.State().IncidentStatus);
        Assert.Equal(OwnInvestigationViewObservation.EvidenceOneofCase.None, f.Authority.OwnView(f.Engineer.Id).EvidenceCase);
    }
    [Fact]
    public void RepairCompletionRequiresContinuousFinitePositionAndLiveActualTerminal()
    {
        var f = new Fixture(); f.Manual(); f.Evidence(); var command = f.RepairCommand(); Assert.True(Payload<RepairResultEvent>(f.Do(f.Engineer.Id, command)).Accepted);
        f.Engineer.X = double.NaN; f.Advance(100); Assert.Equal(CoolingStatus.Failure, f.Authority.State().CoolingStatus);
        var replay = Payload<RepairResultEvent>(f.Do(f.Engineer.Id, command)); Assert.False(replay.Accepted); Assert.Equal("repair_interrupted", replay.ReasonCode);
        f.Engineer.X = -.25; f.Advance(1000); Assert.True(f.Repair().Accepted);
        f.Authority.SetTerminals(false, 0, 0, 1, true, -1, -.65, 1.6); f.Advance(2000);
        Assert.Equal(CoolingStatus.Failure, f.Authority.State().CoolingStatus);
    }
    [Fact]
    public void CancelRepairClearsLeaseAndUpdatesOriginalCorrelationReplay()
    {
        var f = new Fixture(); f.Manual(); f.Evidence(); var command = f.RepairCommand(); f.Do(f.Engineer.Id, command);
        var outputs = f.Do(f.Engineer.Id, new CancelCoolingRepairCommand { IncidentId = f.Authority.IncidentId, MatchId = f.Match, CorrelationId = f.Next() });
        Assert.True(Payload<IncidentActionResultEvent>(outputs).Accepted);
        Assert.False(Payload<RepairResultEvent>(outputs).Accepted);
        f.Advance(3000); Assert.Equal(CoolingStatus.Failure, f.Authority.State().CoolingStatus);
        Assert.Equal("repair_cancelled", Payload<RepairResultEvent>(f.Do(f.Engineer.Id, command)).ReasonCode);
    }
    [Theory]
    [InlineData("runtime")]
    [InlineData("disconnect")]
    [InlineData("cancel")]
    [InlineData("expiry")]
    public void TerminalCleanupRevokesRepairPrivateEvidenceAndRoleLock(string cause)
    {
        var f = new Fixture(); f.Manual(); f.Evidence(); var inspect = f.Inspect("cached-inspection"); f.Do(f.Engineer.Id, inspect); f.Repair();
        switch (cause)
        {
            case "runtime": f.Authority.ControlUnavailable(f.Engineer.Id, f.Now); break;
            case "disconnect": f.Authority.Refresh(new[] { f.Sheriff, f.Crew }, f.Now); break;
            case "cancel": f.Do(f.Sheriff.Id, new CancelInvestigationCommand { IncidentId = f.Authority.IncidentId, MatchId = f.Match, CorrelationId = f.Next() }); break;
            default: f.Advance(300001); break;
        }
        Assert.False(f.Authority.Active); Assert.Equal(0, f.Authority.State().RepairCompletesAtUnixMs);
        Assert.False(f.Authority.OwnRole(f.Sheriff.Id).SelectionLocked);
        Assert.Equal(OwnInvestigationViewObservation.EvidenceOneofCase.None, f.Authority.OwnView(f.Sheriff.Id).EvidenceCase);
        if (cause != "disconnect") Assert.Null(Payload<DiagnosticEvidenceEvent>(f.Do(f.Engineer.Id, inspect)).Evidence);
    }
    [Fact]
    public void ResolvedSurvivesRuntimeAndPlayerDisconnectAndDutyCanBeReassignedAfterCleanup()
    {
        var f = new Fixture(); f.Manual(); f.Evidence(); f.Report(ProposedOrigin.ManualInterference); f.Repair(); f.Advance(2000);
        f.Authority.ControlUnavailable(f.Engineer.Id, f.Now);
        f.Authority.Refresh(new[] { f.Sheriff, f.Crew }, f.Now);
        Assert.Equal(IncidentStatus.Resolved, f.Authority.State().IncidentStatus);
        Assert.True(Payload<RoleResultEvent>(f.Do(f.Crew.Id, f.Role(CrewRole.Engineer))).Accepted);
        Assert.Equal(IncidentStatus.Resolved, f.Authority.State().IncidentStatus);
    }
    [Fact]
    public void CrewDepartureDoesNotCancelAndCrewCannotCancelSomeoneElsesInvestigation()
    {
        var f = new Fixture(); f.Manual();
        Assert.Equal("incident_participant_required", Payload<IncidentActionResultEvent>(f.Do(f.Crew.Id,
            new CancelInvestigationCommand { IncidentId = f.Authority.IncidentId, MatchId = f.Match, CorrelationId = f.Next() })).ReasonCode);
        f.Authority.Refresh(new[] { f.Engineer, f.Sheriff }, f.Now); Assert.True(f.Authority.Active);
    }
    [Fact]
    public void TypedReplayCannotAliasCommandsWithIdenticalWireFieldsOrCreateNewEvidence()
    {
        var f = new Fixture(); f.Manual(); var request = f.Inspect("same-correlation");
        var first = Payload<DiagnosticEvidenceEvent>(f.Do(f.Engineer.Id, request));
        Assert.Equal(first.ToByteArray(), Payload<DiagnosticEvidenceEvent>(f.Do(f.Engineer.Id, request)).ToByteArray());
        var conflict = f.Do(f.Engineer.Id, new CancelInvestigationCommand { IncidentId = request.IncidentId, MatchId = request.MatchId, CorrelationId = request.CorrelationId });
        Assert.Equal("correlation_conflict", Payload<IncidentActionResultEvent>(conflict).ReasonCode); Assert.True(f.Authority.Active);
        f.Advance(1000); var fresh = Payload<DiagnosticEvidenceEvent>(f.Do(f.Engineer.Id, f.Inspect()));
        Assert.Equal(first.Evidence.Provenance.EvidenceId, fresh.Evidence.Provenance.EvidenceId);
    }
    [Theory]
    [InlineData(double.NaN, -6.54)]
    [InlineData(double.PositiveInfinity, -6.54)]
    [InlineData(10, -6.54)]
    public void ClaimedRoomDoesNotBypassActualInteractionDistanceOrFinitePose(double x, double y)
    {
        var f = new Fixture(); f.Manual(); f.Engineer.X = x; f.Engineer.Y = y; f.Refresh();
        Assert.Equal("cooling_out_of_range", Payload<DiagnosticEvidenceEvent>(f.Do(f.Engineer.Id, f.Inspect())).ReasonCode);
    }
    [Fact]
    public void CommandsAndCitationsCannotCrossIncidentOrLobbyIdentity()
    {
        var f = new Fixture(); f.Manual(); f.Evidence();
        var stale = f.Inspect(); stale.IncidentId = new string('b', 32);
        Assert.Equal("incident_not_found", Payload<DiagnosticEvidenceEvent>(f.Do(f.Engineer.Id, stale)).ReasonCode);
        stale = f.Inspect(); stale.MatchId = new string('b', 32);
        Assert.Equal("match_mismatch", Payload<DiagnosticEvidenceEvent>(f.Do(f.Engineer.Id, stale)).ReasonCode);
        var wrong = f.ReportCommand(ProposedOrigin.ManualInterference); wrong.SupportingEvidenceIds[0] = new string('b', 32);
        Assert.Equal("invalid_evidence_references", Payload<InvestigationResultEvent>(f.Do(f.Sheriff.Id, wrong)).ReasonCode);
    }
    [Fact]
    public void MissingAndMovedTerminalsAreAuthoritativeAndPrivateResultsStayBounded()
    {
        var f = new Fixture(); f.Manual();
        f.Authority.SetTerminals(false, -.25, -6.54, 1.25, true, -1, -.65, 1.6);
        Assert.Equal("terminal_unavailable", Payload<DiagnosticEvidenceEvent>(f.Do(f.Engineer.Id, f.Inspect())).ReasonCode);
        f.Authority.SetTerminals(true, 10, 10, 1.25, true, -1, -.65, 1.6);
        Assert.Equal("cooling_out_of_range", Payload<DiagnosticEvidenceEvent>(f.Do(f.Engineer.Id, f.Inspect())).ReasonCode);
        f.Authority.SetTerminals(true, -.25, -6.54, 1.25, true, -1, -.65, 1.6); f.Evidence();
        Assert.InRange(f.Diagnostic!.Samples.Count, 1, 8); Assert.InRange(f.Diagnostic.EquipmentWear, 0, 1);
        Assert.InRange(f.Security!.Records.Count, 2, 6); Assert.True(f.Security.CompleteForListedCircuits);
        Assert.Equal(2, f.Security.CircuitRefs.Count);
    }
    [Fact]
    public void MalformedInputsAndActorSpamHaveStructuredBoundedOutcomes()
    {
        var f = new Fixture(); f.Manual(); f.Evidence();
        var invalid = f.ReportCommand(ProposedOrigin.ManualInterference); invalid.Explanation = new string('Ж', 300);
        Assert.Equal("invalid_report_payload", Payload<InvestigationResultEvent>(f.Do(f.Sheriff.Id, invalid)).ReasonCode);
        var correlation = f.Inspect(); correlation.CorrelationId = new string('x', 300);
        Assert.InRange(Payload<DiagnosticEvidenceEvent>(f.Do(f.Engineer.Id, correlation)).CorrelationId.Length, 0, 64);
        var reasons = Enumerable.Range(0, 8).Select(_ => Payload<RoleResultEvent>(f.Do(f.Crew.Id, f.Role(CrewRole.Crew))).ReasonCode).ToArray();
        Assert.Contains("rate_limited", reasons);
    }
    [Fact]
    public void ActiveAndStandbyAdjustmentsHaveTheSamePreAlarmOutcomeAndBoundedCompleteCycle()
    {
        var active = new Fixture(); var standby = new Fixture(natural: true);
        active.Now = standby.Now = Fixture.Start + 1000;
        var activeReply = Payload<IncidentActionResultEvent>(active.Do(active.Crew.Id, active.Override("alpha")));
        var standbyReply = Payload<IncidentActionResultEvent>(standby.Do(standby.Crew.Id, standby.Override("beta")));
        Assert.Equal(activeReply.ToByteArray(), standbyReply.ToByteArray());
        Assert.Equal(active.Authority.State().ToByteArray(), standby.Authority.State().ToByteArray());
        Assert.False(active.Authority.OwnRole(active.Engineer.Id).SelectionLocked);
        active.Advance(2000); standby.Advance(2000);
        Assert.Equal("maintenance_cycle_capacity", Payload<IncidentActionResultEvent>(active.Do(active.Crew.Id, active.Override("alpha"))).ReasonCode);
        Assert.Equal("maintenance_cycle_capacity", Payload<IncidentActionResultEvent>(standby.Do(standby.Crew.Id, standby.Override("beta"))).ReasonCode);
        active.Now = standby.Now = Fixture.Start + 10010;
        active.Authority.Tick(active.Now); standby.Authority.Tick(standby.Now);
        active.Evidence(); standby.Evidence();
        Assert.Equal(Fixture.Start, active.Security!.CoverageWindowStartUnixMs);
        Assert.Equal(Fixture.Start + 10010, active.Security.CoverageWindowEndUnixMs);
        Assert.Equal(5, active.Security.Records.Count); Assert.True(active.Security.CompleteForListedCircuits);
        Assert.True(active.Report(ProposedOrigin.ManualInterference).Accepted);
        Assert.True(standby.Report(ProposedOrigin.MechanicalFailure).Accepted);
    }
    [Theory]
    [InlineData("role-change")]
    [InlineData("control")]
    [InlineData("disconnect")]
    public void PreAlarmDutyLossInterlocksPendingMaintenanceAndCannotLeakIntoANewCycle(string loss)
    {
        var f = new Fixture(); f.Now = Fixture.Start + 1000;
        Assert.True(Payload<IncidentActionResultEvent>(f.Do(f.Crew.Id, f.Override("alpha"))).Accepted);
        if (loss == "role-change") f.Do(f.Engineer.Id, f.Role(CrewRole.Crew));
        else if (loss == "control") f.Authority.ControlUnavailable(f.Engineer.Id, f.Now);
        else f.Authority.Refresh(new[] { f.Sheriff, f.Crew }, f.Now);
        f.Now = Fixture.Start + 11000; f.Authority.Tick(f.Now);
        Assert.Equal(IncidentStatus.None, f.Authority.State().IncidentStatus);
        if (loss == "disconnect") f.Refresh();
        Assert.True(Payload<RoleResultEvent>(f.Do(f.Engineer.Id, f.Role(CrewRole.Engineer))).Accepted);
        f.Advance(10010); Assert.Equal(IncidentStatus.None, f.Authority.State().IncidentStatus);
    }
}
