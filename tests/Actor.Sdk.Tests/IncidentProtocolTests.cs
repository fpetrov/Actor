using Actor.Protocol.Core.V1;
using Actor.Sdk;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Google.Protobuf.Reflection;
using Google.Protobuf.WellKnownTypes;

namespace Actor.Sdk.Tests;

public sealed class IncidentProtocolTests
{
    private sealed class InvestigationExtension : IActorExtension
    {
        public ExtensionPackage Package => AgentsArenaExtensionPackage.Create();
        public ActorEventPublisher<RepairResultEvent> RepairResults { get; private set; } = null!;

        public void Register(IActorExtensionRegistrar scope)
        {
            scope.RegisterObservation<OwnInvestigationViewObservation>("agents-arena.own-investigation-view", "Authorized own view",
                _ => new() { RecipientActorId = "engineer", IncidentId = "incident", MatchId = "match" });
            scope.RegisterCommand<InspectCoolingCommand>("agents-arena.inspect-cooling", "Inspect", (_, _) => CommandOutcome.Accepted());
            scope.RegisterEvent<DiagnosticEvidenceEvent>("agents-arena.diagnostic-evidence", "Private diagnostics");
            RepairResults = scope.RegisterEvent<RepairResultEvent>("agents-arena.repair-result", "Repair stages");
        }
    }

    [Fact]
    public void TypedInvestigationRegistrationBuildsManifestWithoutChangingCore()
    {
        var registry = new ActorCapabilityRegistry();
        registry.RegisterExtension(new InvestigationExtension());
        var manifest = registry.BuildManifest(new GameIdentity("agents-arena", "0.7.0"), "engineer");
        Assert.Equal(1u, manifest.ProtocolVersion.Major);
        var extension = Assert.Single(manifest.Extensions);
        Assert.Equal("agents-arena", extension.ExtensionId);
        Assert.Equal("0.5.0", extension.ExtensionVersion);
        Assert.Equal(AgentsArenaExtensionPackage.SchemaHash, extension.SchemaHash);
        Assert.Equal(4, manifest.Capabilities.Count);
        var inspect = Assert.Single(manifest.Capabilities, c => c.Kind == CapabilityKind.Command);
        Assert.Equal(CommandDelivery.Ordered, inspect.CommandDelivery);
        Assert.Equal("type.googleapis.com/actor.extensions.agents_arena.v1.InspectCoolingCommand", inspect.InputTypeUrl);
        var observation = Assert.Single(registry.CaptureObservations("engineer", "engineer", 1).Observations);
        Assert.Equal(ActorProtocol.TypeUrl<OwnInvestigationViewObservation>(), observation.Payload.TypeUrl);
        Assert.Equal("engineer", observation.Payload.Unpack<OwnInvestigationViewObservation>().RecipientActorId);
    }

    [Theory]
    [InlineData(CrewRole.Engineer)]
    [InlineData(CrewRole.Sheriff)]
    public void PrivateOwnViewRoundTripsOneAuthorizedEvidenceBundle(CrewRole role)
    {
        var provenance = new EvidenceProvenance { EvidenceId = "evidence", IncidentId = "incident", MatchId = "match",
            SourceEntityId = "console", RecipientActorId = "holder", RecipientRole = role,
            Visibility = EvidenceVisibility.RolePrivate, ExpiresAtUnixMs = 2000 };
        var view = new OwnInvestigationViewObservation { RecipientActorId = "holder", IncidentId = "incident", MatchId = "match",
            ExpiresAtUnixMs = 2000, ServerRevision = 4 };
        if (role == CrewRole.Engineer)
            view.Diagnostic = new DiagnosticEvidence { Provenance = provenance, CircuitRef = "alpha", EquipmentWear = .4,
                FailureWindowStartUnixMs = 1000, FailureWindowEndUnixMs = 1100,
                ControllerRevisionBefore = 7, ControllerRevisionAfter = 7,
                Samples = { new PressureSample { AtUnixMs = 1050, Pressure = .25 } } };
        else
            view.Security = new SecurityEvidence { Provenance = provenance, CoverageWindowStartUnixMs = 900,
                CoverageWindowEndUnixMs = 1200, CompleteForListedCircuits = true, CircuitRefs = { "alpha", "beta" } };
        var packed = Any.Pack(view);
        Assert.Equal("type.googleapis.com/actor.extensions.agents_arena.v1.OwnInvestigationViewObservation", packed.TypeUrl);
        var copy = Any.Parser.ParseFrom(packed.ToByteArray()).Unpack<OwnInvestigationViewObservation>();
        Assert.Equal(view, copy);
        Assert.Equal(role == CrewRole.Engineer ? OwnInvestigationViewObservation.EvidenceOneofCase.Diagnostic
            : OwnInvestigationViewObservation.EvidenceOneofCase.Security, copy.EvidenceCase);
        copy.ClearEvidence();
        Assert.Equal(OwnInvestigationViewObservation.EvidenceOneofCase.None, copy.EvidenceCase);
        Assert.Equal("holder", copy.RecipientActorId);
    }

    [Fact]
    public void PublicIncidentMessagesCannotReachPrivateEvidenceOrOriginHypotheses()
    {
        var privateTypes = new HashSet<string> { DiagnosticEvidence.Descriptor.FullName, SecurityEvidence.Descriptor.FullName,
            EvidenceProvenance.Descriptor.FullName, OwnInvestigationViewObservation.Descriptor.FullName };
        foreach (var descriptor in new[] { CoolingStateObservation.Descriptor, IncidentStartedEvent.Descriptor, IncidentChangedEvent.Descriptor })
        {
            foreach (var field in ReachableFields(descriptor, new HashSet<string>()))
            {
                Assert.False(field.FieldType == FieldType.Enum && field.EnumType.FullName.EndsWith(".ProposedOrigin", StringComparison.Ordinal));
                Assert.False(field.FieldType == FieldType.Message && privateTypes.Contains(field.MessageType.FullName));
                Assert.DoesNotContain("origin", field.Name, StringComparison.OrdinalIgnoreCase);
                Assert.DoesNotContain("cause", field.Name, StringComparison.OrdinalIgnoreCase);
            }
        }
        Assert.Contains(SubmitInvestigationReportCommand.Descriptor.Fields.InFieldNumberOrder(),
            field => field.FieldType == FieldType.Enum && field.EnumType.FullName.EndsWith(".ProposedOrigin", StringComparison.Ordinal));
        Assert.DoesNotContain(InvestigationResultEvent.Descriptor.Fields.InFieldNumberOrder(),
            field => field.FieldType == FieldType.Enum && field.EnumType.FullName.EndsWith(".ProposedOrigin", StringComparison.Ordinal));
    }

    [Fact]
    public void EmptySecurityRecordsStillCarryExplicitBoundedCoverage()
    {
        var evidence = new SecurityEvidence { Provenance = new EvidenceProvenance { EvidenceId = "security-evidence" },
            CoverageWindowStartUnixMs = 1000, CoverageWindowEndUnixMs = 2000,
            CompleteForListedCircuits = true, CircuitRefs = { "alpha" } };
        var copy = Any.Pack(evidence).Unpack<SecurityEvidence>();
        Assert.Empty(copy.Records);
        Assert.True(copy.CompleteForListedCircuits);
        Assert.Equal("alpha", Assert.Single(copy.CircuitRefs));
        Assert.Equal(1000, copy.CoverageWindowStartUnixMs);
        Assert.Equal(2000, copy.CoverageWindowEndUnixMs);
        Assert.False(new SecurityEvidence().CompleteForListedCircuits);
    }

    [Fact]
    public void ReportPreservesDistinctTypedCitationKindsWithoutServerAnswerField()
    {
        var report = new SubmitInvestigationReportCommand { IncidentId = "incident", ProposedOrigin = ProposedOrigin.InsufficientEvidence,
            SupportingEvidenceIds = { "diagnostic-evidence", "security-evidence", "audit-record" },
            Explanation = "The available view does not establish a cause.", CorrelationId = "report", MatchId = "match" };
        var copy = Any.Pack(report).Unpack<SubmitInvestigationReportCommand>();
        Assert.Equal(report, copy);
        Assert.Equal(new[] { "diagnostic-evidence", "security-evidence", "audit-record" }, copy.SupportingEvidenceIds);
        Assert.Equal(ProposedOrigin.InsufficientEvidence, copy.ProposedOrigin);
    }

    [Fact]
    public void TypedPublisherRetainsRepairStartAndCompletionWithSameCorrelation()
    {
        var registry = new ActorCapabilityRegistry(); var extension = new InvestigationExtension();
        registry.RegisterExtension(extension);
        var events = new List<GameEventEnvelope>(); registry.EventPublished += events.Add;
        var started = new RepairResultEvent { CorrelationId = "repair", Accepted = true, Completed = false,
            CoolingStatus = CoolingStatus.Repairing, IncidentStatus = IncidentStatus.Active, IncidentId = "incident", MatchId = "match" };
        var completed = started.Clone(); completed.Completed = true; completed.CoolingStatus = CoolingStatus.Restored;
        extension.RepairResults.Publish(started, "engineer", 1);
        extension.RepairResults.Publish(completed, "engineer", 2);
        Assert.Equal(2, events.Count);
        Assert.All(events, envelope => Assert.Equal(ActorProtocol.TypeUrl<RepairResultEvent>(), envelope.Payload.TypeUrl));
        Assert.False(events[0].Payload.Unpack<RepairResultEvent>().Completed);
        Assert.True(events[1].Payload.Unpack<RepairResultEvent>().Completed);
        Assert.Equal(events[0].Payload.Unpack<RepairResultEvent>().CorrelationId, events[1].Payload.Unpack<RepairResultEvent>().CorrelationId);
    }

    private static IEnumerable<FieldDescriptor> ReachableFields(MessageDescriptor message, HashSet<string> seen)
    {
        if (!seen.Add(message.FullName)) yield break;
        foreach (var field in message.Fields.InFieldNumberOrder())
        {
            yield return field;
            if (field.FieldType == FieldType.Message)
                foreach (var child in ReachableFields(field.MessageType, seen)) yield return child;
        }
    }
}
