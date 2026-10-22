using Actor.Networking;
using AgentsArena.ActorProtocol.V1;

namespace Actor.Sdk.Tests;
public sealed class BroadcastViewTests
{
    private const long Now = 100000;
    private static string Id(int value) => value.ToString("x32");
    private static BroadcastScope Scope(ulong generation = 1) => new() { BroadcastSessionId = Id(1), MatchId = Id(2), ObserverId = Id(3), ConnectionGeneration = generation, ViewEpoch = 1 };
    private static SocialMatchStateObservation State() => new() { MatchId = Id(2), Phase = SocialPhase.Exploration, PhaseRevision = 1, VisibilityRadius = 3.5f, TaskThreshold = 8 };
    private static BroadcastViewProjector.Actor Actor(int value = 10) => new() { Id = Id(value), Name = "Agent " + value, X = 0, Y = -.94f, Alive = true, Room = "main-hall" };
    private static BroadcastView Build(BroadcastViewProjector p, params BroadcastViewProjector.Actor[] actors)
    { var v = p.Build(State(), null, null, actors, Array.Empty<BroadcastViewProjector.Body>(), null, Now); Acknowledge(p, v); return v; }
    private static void Acknowledge(BroadcastViewProjector p, BroadcastView v) => Assert.True(p.ConfirmPresented(new BroadcastPresentationAck { Scope = v.Scope, RenderedSequence = v.Sequence, FrameCounter = v.Sequence, PresentedAtMonotonicMs = 1, Width = 800, Height = 600,
        PresentedEvidence = { v.Facts.Select(f => new BroadcastEvidenceRef { EvidenceId = f.EvidenceId, Version = f.Version }) } }, v.ObservedAtUnixMs));
    [Fact] public void ObserversHaveIndependentTwoSlotNonceAndGenerationBounds()
    {
        var registry = new BroadcastObserverRules(); Assert.True(registry.Admit(10, Scope(), Id(50), 110, 100));
        var second = Scope(); second.ObserverId = Id(4); second.BroadcastSessionId = Id(5);
        Assert.True(registry.Admit(11, second, Id(51), 110, 100));
        var third = Scope(); third.ObserverId = Id(6); third.BroadcastSessionId = Id(7);
        Assert.False(registry.Admit(12, third, Id(52), 110, 100));
        registry.Remove(10); Assert.False(registry.Admit(12, Scope(), Id(53), 110, 100));
        Assert.False(registry.Admit(12, Scope(2), Id(50), 110, 100));
        Assert.True(registry.Admit(12, Scope(2), Id(54), 110, 100));
    }
    [Fact] public void ActiveObserverCannotBeTakenOverByANewerGeneration()
    {
        var registry = new BroadcastObserverRules(); Assert.True(registry.Admit(10, Scope(), Id(50), 110, 100));
        Assert.False(registry.Admit(11, Scope(2), Id(51), 110, 100));
    }
    [Fact] public void ObserverTicketLifetimeAndGuidShapeAreBounded()
    {
        var r = new BroadcastObserverRules(); Assert.False(r.Admit(1, Scope(), Id(50), 161, 100));
        Assert.False(r.Admit(1, Scope(), Id(50), 100, 100));
        var s = Scope(); s.ObserverId = "arbitrary"; Assert.False(r.Admit(1, s, Id(50), 110, 100));
    }
    [Fact] public void RevocationDisconnectsOnlyItsSessionAndRejectsAllFutureTicketsForIt()
    {
        var r = new BroadcastObserverRules(); Assert.True(r.Admit(10, Scope(), Id(50), 160, 100));
        var other = Scope(); other.BroadcastSessionId = Id(4); other.ObserverId = Id(5);
        Assert.True(r.Admit(11, other, Id(51), 160, 100));
        Assert.Equal(new ulong[] { 10 }, r.Revoke(Scope().BroadcastSessionId));
        Assert.True(r.Contains(10)); Assert.True(r.Contains(11)); r.Remove(10);
        Assert.False(r.Admit(12, Scope(2), Id(52), 170, 110));
        Assert.Empty(r.Revoke(Scope().BroadcastSessionId));
        r.Remove(11); other.ConnectionGeneration = 2; Assert.True(r.Admit(12, other, Id(53), 170, 110));
    }
    [Fact] public void ServerProjectsOnlyRadiusSightAndViewportIntersection()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, x, _) => x != 1, Now);
        var hiddenWall = Actor(11); hiddenWall.X = 1;
        var outOfRange = Actor(12); outOfRange.X = 5;
        var outsideFrame = Actor(13); outsideFrame.Y = 2.5f; // within radius, outside vertical viewport.
        var dead = Actor(14); dead.Alive = false;
        var view = Build(p, Actor(), hiddenWall, outOfRange, outsideFrame, dead);
        Assert.Equal(Id(10), Assert.Single(view.Actors).ActorId); Assert.DoesNotContain(view.Facts, f => f.SubjectIds.Contains(Id(11)));
    }
    [Fact] public void HiddenMotionAndHiddenDeathDoNotChangeSemanticRevisionOrFacts()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now); var visible = Actor(); var hidden = Actor(11); hidden.X = 8;
        var first = Build(p, visible, hidden); hidden.X = 12; hidden.Alive = false;
        var second = Build(p, visible, hidden);
        Assert.Equal(first.SemanticRevision, second.SemanticRevision); Assert.Equal(first.Facts, second.Facts); Assert.Equal(first.Sequence + 1, second.Sequence);
    }
    [Fact] public void LostActorIsRemovedByCompleteReplacementWithoutRenewingItsFact()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now); var actor = Actor(); var first = Build(p, actor);
        actor.X = 9; var empty = Build(p, actor); Assert.Empty(empty.Actors);
        var old = Assert.Single(first.Facts.Where(f => f.Predicate == "actor-seen"));
        Assert.Equal(old, Assert.Single(empty.Facts.Where(f => f.Predicate == "actor-seen")));
    }
    [Fact] public void CameraDwellAndCurrentVisibilityRejectHiddenActorProbeUniformly()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now); Build(p, Actor());
        BroadcastSelectViewCommand Request(string actor, long at) => new() { Scope = p.Scope, CorrelationId = Id(60), FocusActorId = actor, ExpiresAtUnixMs = at + 1000 };
        Assert.False(p.Select(Request(Id(10), Now), Now).Accepted);
        Acknowledge(p, p.Build(State(), null, null, new[] { Actor() }, Array.Empty<BroadcastViewProjector.Body>(), null, Now + 8000));
        Assert.Equal("view_unavailable", p.Select(Request(Id(99), Now + 8000), Now + 8000).ReasonCode);
        Assert.True(p.Select(Request(Id(10), Now + 8000), Now + 8000).Accepted); Assert.Equal(2ul, p.Scope.ViewEpoch);
    }
    [Fact] public void SameCameraCommandCannotReplayAcrossViewEpoch()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now);
        var command = new BroadcastSelectViewCommand { Scope = p.Scope, CorrelationId = Id(60), AnchorId = "security", ExpiresAtUnixMs = Now + 9000 };
        Assert.True(p.Select(command, Now + 8000).Accepted); command.ExpiresAtUnixMs = Now + 17000;
        Assert.False(p.Select(command, Now + 16000).Accepted);
    }
    [Fact] public void PublicMeetingDropsOwnBallotAndTerminalDropsReplay()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now); var s = State(); s.Phase = SocialPhase.Discussion;
        var meeting = new SocialMeetingObservation { MatchId = s.MatchId, OwnBallotSubmitted = true, OwnCandidateActorId = Id(99) };
        var view = p.Build(s, null, meeting, Array.Empty<BroadcastViewProjector.Actor>(), Array.Empty<BroadcastViewProjector.Body>(), null, Now);
        Assert.DoesNotContain("own_ballot", Google.Protobuf.JsonFormatter.Default.Format(view)); Assert.DoesNotContain(Id(99), Google.Protobuf.JsonFormatter.Default.Format(view));
        var summary = new SocialMatchSummary { MatchId = s.MatchId, WinningSide = SocialAlignment.Crew }; summary.Replay.Add(new SocialReplayEntry { Kind = "hidden_private" });
        summary.Players.Add(new SocialFinalPlayer { ActorId = Id(10), DisplayName = "Agent", Alignment = SocialAlignment.Impostor });
        Assert.Empty(p.Build(s, null, null, Array.Empty<BroadcastViewProjector.Actor>(), Array.Empty<BroadcastViewProjector.Body>(), summary, Now).FinalPlayers);
        s.Phase = SocialPhase.GameOver; var terminal = p.Build(s, null, null, Array.Empty<BroadcastViewProjector.Actor>(), Array.Empty<BroadcastViewProjector.Body>(), summary, Now);
        Assert.Single(terminal.FinalPlayers); Assert.DoesNotContain("hidden_private", Google.Protobuf.JsonFormatter.Default.Format(terminal));
    }
    [Fact] public void DiscussionClaimIsAttributedBoundedAndNotPromotedToObservation()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now);
        p.Note("discussion-claim", Id(70), Id(10), "", "Я был на складе", "", Now, 2);
        p.Note("discussion-claim", Id(70), Id(10), "", "duplicate", "", Now, 2);
        p.Note("discussion-claim", Id(71), Id(10), "", new string('x', 513), "", Now, 2);
        var claim = Assert.Single(Build(p).Facts.Where(f => f.Predicate == "discussion-claim")); Assert.Equal(BroadcastFactKind.PlayerClaim, claim.Kind); Assert.Equal(Id(10), claim.ActorId);
    }
    [Fact] public void HistoricalFactExpiryEmitsRevocationInsteadOfExtendingRetention()
    {
        var p = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now); p.Note("activity-seen", Id(70), Id(10), "", "", "", Now);
        var view = p.Build(State(), null, null, Array.Empty<BroadcastViewProjector.Actor>(), Array.Empty<BroadcastViewProjector.Body>(), null, Now + 300001);
        Assert.DoesNotContain(view.Facts, f => f.EvidenceId == Id(70)); Assert.Contains(Id(70), view.RevokedEvidenceIds);
    }
    [Fact] public void ObserverAdmissionAndNumericClientGapsDoNotChangePlayerAssignment()
    {
        (SocialAlignment, CrewRole)[] Run(bool observers)
        {
            var registry = new BroadcastObserverRules();
            if (observers) Assert.True(registry.Admit(1, Scope(), Id(90), 110, 100));
            var authority = new SocialMatchAuthority(Id(2), new SocialMatchAuthority.Configuration(), 42);
            var terminals = new List<SocialMatchAuthority.Terminal>
            {
                new() { Id = "meeting", Room = "main-hall", Kind = "meeting" },
                new() { Id = "life-support", Room = "main-hall", Kind = "life-support" }
            };
            for (int i = 0; i < 3; i++) terminals.Add(new() { Id = "terminal-" + i, Room = "main-hall", Kind = "task" });
            authority.SetWorld(terminals, Enumerable.Range(0, 3).Select(i => new SocialMatchAuthority.TaskDefinition { Id = "task-" + i, TerminalId = "terminal-" + i }));
            var players = Enumerable.Range(0, 4).Select(i => new SocialMatchAuthority.Participant
            { Id = Id(10 + i), Name = "Agent " + i, Room = "main-hall", JoinOrder = i, ConnectionEpoch = (ulong)(observers ? 2 + i * 2 : 1 + i), PoseFresh = true }).ToArray();
            authority.Refresh(players, Now);
            foreach (var player in players) authority.Execute(player.Id, new SocialReadyCommand { Ready = true, Context = new SocialActionContext
            { MatchId = Id(2), PhaseRevision = authority.State().PhaseRevision, CorrelationId = "ready-" + player.Id,
                ExpiresAtUnixMs = Now + 1000, ConnectionGeneration = 1 } }, Now);
            Assert.Equal(SocialPhase.RoleAssignment, authority.Phase);
            Assert.All(players, player => Assert.True(authority.OwnState(player.Id).Assigned));
            return players.Select(p => (authority.OwnState(p.Id).Alignment, authority.OwnState(p.Id).Duty)).ToArray();
        }
        Assert.Equal(Run(false), Run(true));
    }

}
