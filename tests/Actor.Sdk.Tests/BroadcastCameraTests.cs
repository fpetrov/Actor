using Actor.Networking;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;

namespace Actor.Sdk.Tests;

public sealed class BroadcastCameraTests
{
    private const long Now = 100000;
    private static string Id(int n) => n.ToString("x32");
    private static BroadcastScope Scope() => new() { BroadcastSessionId = Id(1), MatchId = Id(2), ObserverId = Id(3), ConnectionGeneration = 1, ViewEpoch = 1 };
    private static SocialMatchStateObservation State() => new() { MatchId = Id(2), Phase = SocialPhase.Exploration, PhaseRevision = 1, VisibilityRadius = 3.5f };
    private static BroadcastViewProjector.Actor Actor(int n = 10, float x = 0, bool alive = true)
        => new() { Id = Id(n), Name = "Player " + n, X = x, Y = -.94f, Alive = alive, Room = "main-hall" };
    private static BroadcastView Build(BroadcastViewProjector p, long at, params BroadcastViewProjector.Actor[] actors)
        => p.Build(State(), null, null, actors, Array.Empty<BroadcastViewProjector.Body>(), null, at);
    private static BroadcastPresentationAck Ack(BroadcastView view, bool roster = true) => new()
    {
        Scope = view.Scope.Clone(), RenderedSequence = view.Sequence, FrameCounter = view.Sequence,
        PresentedAtMonotonicMs = 1, Width = 800, Height = 600, CameraTargetsPresented = roster,
        PresentedEvidence = { view.Facts.Select(f => new BroadcastEvidenceRef { EvidenceId = f.EvidenceId, Version = f.Version }) }
    };
    private static BroadcastSelectViewCommand Select(BroadcastViewProjector p, int n = 10, long at = Now + 8000)
        => new() { Scope = p.Scope, CorrelationId = Id(90), FocusActorId = Id(n), ExpiresAtUnixMs = at + 500 };
    private static void Presented(BroadcastViewProjector p, BroadcastView v, bool roster = true)
        => Assert.True(p.ConfirmPresented(Ack(v, roster), v.ObservedAtUnixMs));

    [Fact] public void PublicRosterContainsOnlyStableIdentityAndNameAcrossHiddenDeathAndDisconnect()
    {
        double clock = 8;
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now, () => clock);
        var hidden = Actor(11, 8); var dead = Actor(12, 12, false);
        var first = Build(p, Now + 8000, Actor(), hidden, dead);
        Assert.Single(first.Actors); Assert.Equal(3, first.CameraTargets.Count);
        Assert.Equal(new[] { "actor_id", "display_name" }, BroadcastCameraTarget.Descriptor.Fields.InDeclarationOrder().Select(f => f.Name));
        var original = first.CameraTargets.Select(t => t.Clone()).ToArray();
        hidden.Alive = false; hidden.X = 15; hidden.Name = "Changed name";
        clock += .03;
        var second = Build(p, Now + 8030, hidden);
        Assert.Equal(original, second.CameraTargets); Assert.Empty(second.Actors);
        clock += .03;
        Assert.Equal(original, Build(p, Now + 8060).CameraTargets);
        Assert.DoesNotContain(first.Facts, f => f.SubjectIds.Contains(hidden.Id) || f.SubjectIds.Contains(dead.Id));
    }

    [Fact] public void RosterIsBoundedUniqueAndDoesNotAdmitInvalidPublicIdentity()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now);
        var bad = Actor(); bad.Id = "not-a-player";
        var badName = Actor(11); badName.Name = "<private>";
        Assert.Empty(Build(p, Now, bad, badName).CameraTargets);
        var six = Enumerable.Range(20, 6).Select(n => Actor(n, 12)).ToArray();
        var first = Build(p, Now, six);
        Assert.Equal(6, first.CameraTargets.Count);
        Assert.Equal(first.CameraTargets, Build(p, Now, Enumerable.Range(30, 6).Select(n => Actor(n, 20)).ToArray()).CameraTargets);
        var fresh = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now);
        Assert.Single(Build(fresh, Now, Actor(), Actor()).CameraTargets);
    }

    [Fact] public void HiddenRosterTargetNeedsActualRosterPresentationBeforeSelection()
    {
        double clock = 8;
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now, () => clock);
        var first = Build(p, Now + 8000, Actor(), Actor(11, 8));
        Presented(p, first, false);
        Assert.False(p.Select(Select(p, 11), Now + 8000).Accepted);
        var second = Build(p, Now + 8001, Actor(), Actor(11, 8));
        Presented(p, second);
        var result = p.Select(Select(p, 11, Now + 8001), Now + 8001);
        Assert.True(result.Accepted); Assert.Equal(2ul, result.Scope.ViewEpoch);
        Assert.Equal(new[] { "scope", "correlation_id", "accepted", "reason_code" }, BroadcastViewSelectionResultEvent.Descriptor.Fields.InDeclarationOrder().Select(f => f.Name));
        var focused = Build(p, Now + 8002, Actor(), Actor(11, 8));
        Assert.Equal(Id(11), focused.Camera.FocusActorId); Assert.Equal(8, focused.Camera.X);
    }

    [Theory] [InlineData("alive")] [InlineData("dead")] [InlineData("missing")] [InlineData("nonfinite")] [InlineData("stale")]
    public void SelectionSuccessNeverDisclosesPrivateLifeOrPoseAvailability(string condition)
    {
        double clock = 8;
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now, () => clock);
        var actor = Actor(11, 8); Presented(p, Build(p, Now + 8000, actor));
        actor.Alive = condition != "dead";
        if (condition == "nonfinite") actor.X = float.NaN;
        if (condition != "stale")
        {
            clock += .03;
            Build(p, Now + 8030, condition == "missing" ? Array.Empty<BroadcastViewProjector.Actor>() : new[] { actor });
        }
        else clock += .6; // Wall receipt is fresh but private monotonic pose is stale.
        var result = p.Select(Select(p, 11, Now + 8040), Now + 8040);
        Assert.True(result.Accepted); Assert.Equal("view_selected", result.ReasonCode); Assert.Equal(2ul, result.Scope.ViewEpoch);
        var next = Build(p, Now + 8041);
        Assert.Equal(Id(11), next.Camera.FocusActorId);
        Assert.Equal(condition is "alive" or "dead" ? 8 : 0, next.Camera.X);
    }

    [Theory] [InlineData("scope")] [InlineData("expired")] [InlineData("unknown")] [InlineData("old_presented")]
    public void PublicAuthorizationFencesStillRefuseWithoutChangingCamera(string condition)
    {
        double clock = 8;
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now, () => clock);
        Presented(p, Build(p, Now + 8000, Actor(11, 8)));
        var command = Select(p, condition == "unknown" ? 99 : 11);
        long at = Now + 8000;
        if (condition == "scope") command.Scope.ConnectionGeneration++;
        if (condition == "expired") command.ExpiresAtUnixMs = at;
        if (condition == "old_presented") { at += 501; command.ExpiresAtUnixMs = at + 500; }
        var result = p.Select(command, at);
        Assert.False(result.Accepted); Assert.Equal("view_unavailable", result.ReasonCode); Assert.Equal(1ul, p.Scope.ViewEpoch);
    }

    [Fact] public void FreshPrivatePoseIsCopiedAndSelectionNeverUsesAnOldPresentedPose()
    {
        double clock = 8;
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now, () => clock);
        var actor = Actor(11, 8); Presented(p, Build(p, Now + 8000, actor));
        actor.X = 9; clock += .03; Build(p, Now + 8030, actor);
        actor.X = 99; // Mutation of an input object cannot mutate the retained private snapshot.
        Assert.True(p.Select(Select(p, 11, Now + 8040), Now + 8040).Accepted);
        Assert.Equal(9, Build(p, Now + 8041).Camera.X);
    }

    [Fact] public void CanonicalDampingRunsBeforeAllVisibilityAndMovementFactProjection()
    {
        double clock = 8; var queriedEyes = new List<float>();
        var p = new BroadcastViewProjector(Scope(), (x, _, _, _) => { queriedEyes.Add(x); return true; }, Now, () => clock);
        var actor = Actor(); Presented(p, Build(p, Now + 8000, actor));
        Assert.True(p.Select(Select(p), Now + 8000).Accepted);
        actor.X = 1; actor.VelocityX = 1; clock += 1.0 / 30;
        var view = Build(p, Now + 8033, actor);
        Assert.InRange(view.Camera.X, .20f, .30f); Assert.NotEqual(actor.X, view.Camera.X);
        Assert.Contains(view.Camera.X, queriedEyes);
        Assert.Equal(actor.X, Assert.Single(view.Actors).X);
        // A subject in the target's radius but outside the lagged canonical eye is hidden.
        var edge = Actor(11, 4.2f); clock += 1.0 / 30;
        view = Build(p, Now + 8066, actor, edge);
        Assert.DoesNotContain(view.Actors, a => a.ActorId == edge.Id);
        Assert.DoesNotContain(view.Facts, f => f.Predicate == "actor-seen" && f.ActorId == edge.Id);
    }

    [Theory] [InlineData(true)] [InlineData(false)]
    public void DampingNeverCutsThroughABlockedCorner(bool firstLegBlocked)
    {
        double clock = 8;
        bool Sight(float x, float _, float tx, float __)
            => firstLegBlocked ? !(x == 0 && tx > 0 && tx < 1) : !(x > 0 && x < 1 && tx == 1);
        var p = new BroadcastViewProjector(Scope(), Sight, Now, () => clock);
        var actor = Actor(); Presented(p, Build(p, Now + 8000, actor));
        Assert.True(p.Select(Select(p), Now + 8000).Accepted);
        actor.X = 1; clock += .03;
        Assert.Equal(1, Build(p, Now + 8030, actor).Camera.X);
    }

    [Theory] [InlineData("teleport")] [InlineData("gap")]
    public void DiscontinuousPoseOrLongGapSnapsInsteadOfExtrapolating(string reason)
    {
        double clock = 8; var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now, () => clock);
        var actor = Actor(); Presented(p, Build(p, Now + 8000, actor));
        Assert.True(p.Select(Select(p), Now + 8000).Accepted);
        actor.X = reason == "teleport" ? 3 : 1; clock += reason == "gap" ? .3 : .03;
        Assert.Equal(actor.X, Build(p, Now + 8300, actor).Camera.X);
    }

    [Fact] public void RegressiveMonotonicClockCannotPublishAnInventedCameraFrame()
    {
        double clock = 8; var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now, () => clock);
        var first = Build(p, Now + 8000, Actor()); clock = 7;
        Assert.Throws<ArgumentException>(() => Build(p, Now + 8001, Actor()));
        Assert.Equal(first, p.Last);
    }

    [Fact] public void UnshownOrClippedRosterIsExcludedFromBothServerAndCognitivePresentedViews()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now);
        var view = Build(p, Now, Actor()); var ack = Ack(view, false);
        Assert.Empty(BroadcastPresentationRules.PresentedSubset(view, ack).CameraTargets);
        Assert.Empty(BroadcastPresentationRules.ForCognition(view, ack, new Dictionary<string, ulong>()).CameraTargets);
        string text = BroadcastPresentationRules.CameraTargetsText(view);
        Assert.True(BroadcastPresentationRules.CameraTargetsWerePresented(view, view.Scope, view.Sequence, text, true));
        Assert.False(BroadcastPresentationRules.CameraTargetsWerePresented(view, view.Scope, view.Sequence, text, false));
        Assert.False(BroadcastPresentationRules.CameraTargetsWerePresented(view, view.Scope, view.Sequence + 1, text, true));
        var other = view.Scope.Clone(); other.ConnectionGeneration++;
        Assert.False(BroadcastPresentationRules.CameraTargetsWerePresented(view, other, view.Sequence, text, true));
        Assert.False(BroadcastPresentationRules.CameraTargetsWerePresented(view, view.Scope, view.Sequence, "Old names", true));
        ack.CameraTargetsPresented = true;
        Assert.Single(BroadcastPresentationRules.PresentedSubset(view, ack).CameraTargets);
        view.CameraTargets.Add(view.CameraTargets[0].Clone());
        Assert.False(BroadcastPresentationRules.CanAcknowledge(view, ack, false));
        Assert.False(BroadcastPresentationRules.ValidView(view, Scope()));
    }

    [Fact] public void ActualRenderPoseGuardRefusesLagRotationPerspectiveAndLensChanges()
    {
        var c = new BroadcastCamera { X = 1, Y = 2, OrthographicSize = 3.4f, Aspect = 4f / 3 };
        Assert.True(BroadcastPresentationRules.CameraPoseMatches(c, true, 1, 2, -10, 3.4f, 4f / 3));
        Assert.False(BroadcastPresentationRules.CameraPoseMatches(c, true, .9f, 2, -10, 3.4f, 4f / 3));
        Assert.False(BroadcastPresentationRules.CameraPoseMatches(c, false, 1, 2, -10, 3.4f, 4f / 3));
        Assert.False(BroadcastPresentationRules.CameraPoseMatches(c, true, 1, 2, -9, 3.4f, 4f / 3));
        Assert.False(BroadcastPresentationRules.CameraPoseMatches(c, true, 1, 2, -10, 3, 4f / 3));
        Assert.False(BroadcastPresentationRules.CameraPoseMatches(c, true, 1, 2, -10, 3.4f, 1));
        Assert.False(BroadcastPresentationRules.CameraPoseMatches(c, true, 1, 2, -10, 3.4f, 4f / 3, rotationZ: .1f));
        Assert.False(BroadcastPresentationRules.CameraPoseMatches(c, true, float.NaN, 2, -10, 3.4f, 4f / 3));
        Assert.True(BroadcastPresentationRules.CameraViewportMatches(4f / 3, 800, 600, 0, 0, 1, 1));
        Assert.True(BroadcastPresentationRules.CameraViewportMatches(4f / 3, 1200, 600, 1f / 6, 0, 2f / 3, 1));
        Assert.False(BroadcastPresentationRules.CameraViewportMatches(4f / 3, 1200, 600, 0, 0, 1, 1));
        Assert.False(BroadcastPresentationRules.CameraViewportMatches(4f / 3, 800, 0, 0, 0, 1, 1));
    }
}
