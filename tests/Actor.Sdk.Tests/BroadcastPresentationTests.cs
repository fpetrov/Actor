using Actor.Networking;
using Actor.Presentation;
using AgentsArena.ActorProtocol.V1;
namespace Actor.Sdk.Tests;
public sealed class BroadcastPresentationTests
{
    private static BroadcastScope Scope() => new() { MatchId = "00000000000000000000000000000001", BroadcastSessionId = "00000000000000000000000000000002", ObserverId = "00000000000000000000000000000003", ConnectionGeneration = 1, ViewEpoch = 1 };
    private static BroadcastView View() => new() { Scope = Scope(), Sequence = 1, SemanticRevision = 1, ObservedAtUnixMs = 1000, ExpiresAtUnixMs = 1500,
        Phase = SocialPhase.Exploration, Camera = new BroadcastCamera { VisibilityRadius = 3.5f, OrthographicSize = 3.4f, Aspect = 4f / 3 } };
    private static BroadcastPresentationAck Ack() => new() { Scope = Scope(), RenderedSequence = 1, FrameCounter = 10, PresentedAtMonotonicMs = 10, Width = 800, Height = 600 };
    [Fact] public void AcknowledgementRequiresExactSequenceScopeAndActualNativeFrame()
    {
        var v = View(); var a = Ack(); Assert.True(BroadcastPresentationRules.CanAcknowledge(v, a, false));
        Assert.False(BroadcastPresentationRules.CanAcknowledge(v, a, true)); a.RenderedSequence = 2; Assert.False(BroadcastPresentationRules.CanAcknowledge(v, a, false));
        a = Ack(); a.Scope.ViewEpoch++; Assert.False(BroadcastPresentationRules.CanAcknowledge(v, a, false));
        a = Ack(); a.FrameCounter = 0; Assert.False(BroadcastPresentationRules.CanAcknowledge(v, a, false));
        a = Ack(); a.PresentedAtMonotonicMs = double.NaN; Assert.False(BroadcastPresentationRules.CanAcknowledge(v, a, false));
    }
    [Fact] public void ValidViewFailsClosedOnForeignGenerationNonfiniteAndPrematureRoles()
    {
        var v = View(); Assert.True(BroadcastPresentationRules.ValidView(v, Scope()));
        v.Scope.ConnectionGeneration++; Assert.False(BroadcastPresentationRules.ValidView(v, Scope())); v = View();
        v.Camera.X = float.NaN; Assert.False(BroadcastPresentationRules.ValidView(v, Scope())); v = View();
        v.FinalPlayers.Add(new BroadcastFinalPlayer { Alignment = SocialAlignment.Impostor }); Assert.False(BroadcastPresentationRules.ValidView(v, Scope()));
    }
    [Fact] public void NeverPresentedTransientActorCannotBecomeLaterHistoricalFact()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, 1000);
        var state = new SocialMatchStateObservation { MatchId = Scope().MatchId, Phase = SocialPhase.Exploration, PhaseRevision = 1, VisibilityRadius = 3.5f };
        var actor = new BroadcastViewProjector.Actor { Id = "00000000000000000000000000000010", Name = "Agent", X = 0, Y = -.94f, Room = "main-hall", Alive = true };
        var unsent = p.Build(state, null, null, new[] { actor }, Array.Empty<BroadcastViewProjector.Body>(), null, 1000);
        Assert.Contains(unsent.Facts, f => f.Predicate == "actor-seen");
        actor.X = 10; var later = p.Build(state, null, null, new[] { actor }, Array.Empty<BroadcastViewProjector.Body>(), null, 1100);
        Assert.DoesNotContain(later.Facts, f => f.Predicate == "actor-seen");
    }
    [Fact] public void WitnessNeedsBothActorsInFreshPresentedViewAndCurrentSight()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, x, _) => x < 2, 1000);
        var state = new SocialMatchStateObservation { MatchId = Scope().MatchId, Phase = SocialPhase.Exploration, VisibilityRadius = 3.5f };
        var actors = new[] { new BroadcastViewProjector.Actor { Id = "00000000000000000000000000000010", Name = "A1", X = 0, Y = -.94f, Room = "main-hall", Alive = true },
            new BroadcastViewProjector.Actor { Id = "00000000000000000000000000000011", Name = "A2", X = .5f, Y = -.94f, Room = "main-hall", Alive = true } };
        var v = p.Build(state, null, null, actors, Array.Empty<BroadcastViewProjector.Body>(), null, 1000);
        Assert.False(p.Witnesses(actors[0].Id, actors[1].Id, 0, -.94f, .5f, -.94f, 1000));
        var ack = Ack(); ack.Scope = v.Scope; ack.RenderedSequence = v.Sequence; Assert.True(p.ConfirmPresented(ack, 1000));
        Assert.True(p.Witnesses(actors[0].Id, actors[1].Id, 0, -.94f, .5f, -.94f, 1100));
        Assert.False(p.Witnesses(actors[0].Id, actors[1].Id, 0, -.94f, 2.5f, -.94f, 1100));
        Assert.False(p.Witnesses(actors[0].Id, actors[1].Id, 0, -.94f, .5f, -.94f, 1600));
    }
    [Fact] public void TemporalOpeningIsBoundedButClosingIsImmediate()
    {
        float displayed = 1;
        for (int i = 0; i < 30; i++) { float next = TemporalFogRules.Step(3, displayed, 1f / 30, false); Assert.InRange(next, displayed, 3); displayed = next; }
        Assert.True(displayed > 2.99f); Assert.Equal(.1f, TemporalFogRules.Step(.1f, displayed, 1f / 30, false));
        Assert.Equal(0, TemporalFogRules.Step(0, displayed, 1f / 30, false));
    }
    [Fact] public void TemporalResetDoesNotInterpolateOldViewAndNonfiniteSightCloses()
    {
        Assert.Equal(2, TemporalFogRules.Step(2, 10, .03f, true));
        Assert.Equal(0, TemporalFogRules.Step(float.NaN, 10, .03f, false));
        Assert.Equal(2, TemporalFogRules.Step(2, float.NaN, .03f, false));
        Assert.Equal(1, TemporalFogRules.Step(2, 1, 0, false));
    }
    [Fact] public void EveryTemporalRadiusStaysInsideCurrentStarShapedSightPolygon()
    {
        var random = new Random(73);
        for (int i = 0; i < 5000; i++)
        {
            float allowed = (float)random.NextDouble() * 8, old = (float)random.NextDouble() * 8, dt = (float)random.NextDouble() * .1f;
            float next = TemporalFogRules.Step(allowed, old, dt, false); Assert.InRange(next, 0, allowed);
            if (allowed < old) Assert.Equal(allowed, next);
        }
    }
    [Fact] public void FourCoalescedClaimsRequireTwoActuallyPresentedHudSelections()
    {
        var view = View();
        for (int i = 1; i <= 4; i++) view.Facts.Add(new BroadcastEditorialFact { EvidenceId = i.ToString("x32"), Version = 1,
            Predicate = "discussion-claim", Text = "Claim " + i, Priority = 2, ObservedAtUnixMs = 1000 });
        var confirmed = new Dictionary<string, ulong>();
        var shown = BroadcastPresentationRules.SelectHudFacts(view, confirmed); Assert.Equal(3, shown.Length);
        var ack = Ack(); ack.PresentedEvidence.Add(shown.Select(f => new BroadcastEvidenceRef { EvidenceId = f.EvidenceId, Version = f.Version }));
        var published = BroadcastPresentationRules.PresentedSubset(view, ack); Assert.Equal(3, published.Facts.Count);
        Assert.DoesNotContain(published.Facts, f => f.EvidenceId == view.Facts[3].EvidenceId);
        foreach (var reference in ack.PresentedEvidence) confirmed[reference.EvidenceId] = reference.Version;
        Assert.Equal(view.Facts[3], Assert.Single(BroadcastPresentationRules.SelectHudFacts(view, confirmed)));
    }
    [Fact] public void EmptyEvidenceAckNeverClaimsThatAllIncomingFactsWereShown()
    {
        var v = View(); v.Facts.Add(new BroadcastEditorialFact { EvidenceId = "00000000000000000000000000000020", Version = 1 });
        Assert.Empty(BroadcastPresentationRules.PresentedSubset(v, Ack()).Facts);
        Assert.Single(v.Facts); // no mutation of the pending transport frame.
    }
    [Fact] public void ForgedUnknownVersionAndDuplicateEvidenceRefsRejectTheWholeAck()
    {
        var v = View(); var fact = new BroadcastEditorialFact { EvidenceId = "00000000000000000000000000000020", Version = 2 }; v.Facts.Add(fact);
        var ack = Ack(); ack.PresentedEvidence.Add(new BroadcastEvidenceRef { EvidenceId = fact.EvidenceId, Version = 2 });
        Assert.True(BroadcastPresentationRules.CanAcknowledge(v, ack, false));
        ack.PresentedEvidence[0].Version = 1; Assert.Null(BroadcastPresentationRules.PresentedSubset(v, ack));
        ack.PresentedEvidence[0].Version = 2; ack.PresentedEvidence.Add(ack.PresentedEvidence[0].Clone());
        Assert.Null(BroadcastPresentationRules.PresentedSubset(v, ack));
        ack.PresentedEvidence.RemoveAt(1); ack.PresentedEvidence[0].EvidenceId = "00000000000000000000000000000021";
        Assert.Null(BroadcastPresentationRules.PresentedSubset(v, ack));
    }
    [Fact] public void FogMustMatchTheActuallyAppliedCameraAndEpochBeforePresentation()
    {
        Assert.True(BroadcastPresentationRules.FogMatches(2, 1, -2, 2, 1, -2, true));
        Assert.False(BroadcastPresentationRules.FogMatches(1, 1, -2, 2, 1, -2, true));
        Assert.False(BroadcastPresentationRules.FogMatches(2, 1, -2, 2, 1.01f, -2, true));
        Assert.False(BroadcastPresentationRules.FogMatches(2, 1, -2, 2, 1, -2, false));
        Assert.False(BroadcastPresentationRules.FogMatches(2, float.NaN, -2, 2, 1, -2, true));
    }
    [Fact] public void ServerCommitsOnlyExplicitlyConfirmedHistoricalSightings()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, 1000);
        var state = new SocialMatchStateObservation { MatchId = Scope().MatchId, Phase = SocialPhase.Exploration, VisibilityRadius = 3.5f };
        var actor = new BroadcastViewProjector.Actor { Id = "00000000000000000000000000000010", Name = "A1", X = 0, Y = -.94f, Room = "main-hall", Alive = true };
        var first = p.Build(state, null, null, new[] { actor }, Array.Empty<BroadcastViewProjector.Body>(), null, 1000);
        var ack = Ack(); ack.Scope = first.Scope; ack.RenderedSequence = first.Sequence;
        Assert.True(p.ConfirmPresented(ack, 1000)); // rendered public context, no sighting note shown.
        actor.X = 10;
        var later = p.Build(state, null, null, new[] { actor }, Array.Empty<BroadcastViewProjector.Body>(), null, 1100);
        Assert.DoesNotContain(later.Facts, f => f.Predicate == "actor-seen");
    }

    [Fact] public void UnshownClaimsAndTransformHeartbeatsDoNotChangeCognitiveSemantics()
    {
        var raw = View(); var known = new Dictionary<string, ulong>();
        raw.Actors.Add(new BroadcastActor { ActorId = "00000000000000000000000000000010", DisplayName = "Agent", X = 1, Y = 1 });
        string before = BroadcastPresentationRules.SemanticSignature(BroadcastPresentationRules.ForCognition(raw, Ack(), known));
        raw.SemanticRevision += 9; raw.Sequence++; raw.ObservedAtUnixMs += 100; raw.ExpiresAtUnixMs += 100;
        raw.Actors[0].X = 2; raw.Actors[0].VelocityX = 1; raw.Camera.X = 1;
        raw.Facts.Add(new BroadcastEditorialFact { EvidenceId = "00000000000000000000000000000020", Version = 1, Predicate = "discussion-claim", Text = "Unshown claim" });
        var ack = Ack(); ack.RenderedSequence = raw.Sequence;
        Assert.Equal(before, BroadcastPresentationRules.SemanticSignature(BroadcastPresentationRules.ForCognition(raw, ack, known)));
        ack.PresentedEvidence.Add(new BroadcastEvidenceRef { EvidenceId = raw.Facts[0].EvidenceId, Version = 1 });
        Assert.NotEqual(before, BroadcastPresentationRules.SemanticSignature(BroadcastPresentationRules.ForCognition(raw, ack, known)));
    }
    [Fact] public void UnknownPendingRevocationsNeverReachCognitionOrItsSemanticCounter()
    {
        var raw = View(); var known = new Dictionary<string, ulong> { ["00000000000000000000000000000020"] = 1 };
        string before = BroadcastPresentationRules.SemanticSignature(BroadcastPresentationRules.ForCognition(raw, Ack(), known));
        raw.RevokedEvidenceIds.Add("00000000000000000000000000000021");
        var projected = BroadcastPresentationRules.ForCognition(raw, Ack(), known); Assert.Empty(projected.RevokedEvidenceIds);
        Assert.Equal(before, BroadcastPresentationRules.SemanticSignature(projected));
        raw.RevokedEvidenceIds.Add(known.Keys.Single());
        projected = BroadcastPresentationRules.ForCognition(raw, Ack(), known);
        Assert.Equal(known.Keys.Single(), Assert.Single(projected.RevokedEvidenceIds));
        Assert.NotEqual(before, BroadcastPresentationRules.SemanticSignature(projected));
    }

}
