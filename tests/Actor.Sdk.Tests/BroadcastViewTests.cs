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
    private static SocialMatchSummary FinalSummary() => new()
    {
        MatchId = Id(2), WinningSide = SocialAlignment.Crew, WinReason = "impostors_eliminated",
        Players =
        {
            new SocialFinalPlayer { ActorId = Id(10), DisplayName = "Лена", Alignment = SocialAlignment.Crew, Life = SocialLife.Alive },
            new SocialFinalPlayer { ActorId = Id(11), DisplayName = "Олег", Alignment = SocialAlignment.Impostor, Life = SocialLife.Ejected }
        }
    };
    [Fact] public void AcknowledgedTerminalResultSurvivesPostgameActorBodyAndCadenceTeardown()
    {
        var projector = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now);
        var state = State(); state.Phase = SocialPhase.GameOver;
        var summary = FinalSummary();
        var body = new BroadcastViewProjector.Body { Id = Id(50), Victim = Id(11), Name = "Олег", X = 0, Y = -.94f };
        var first = projector.Build(state, null, null, new[] { Actor() }, new[] { body }, summary, Now);
        Acknowledge(projector, first);
        var terminalFact = Assert.Single(first.Facts.Where(f => f.Predicate == "public-state" && f.Priority == 0));
        // Reordering an unchanged final roster is not an authoritative correction.
        var reordered = summary.Players.Reverse().Select(p => p.Clone()).ToArray();
        summary.Players.Clear(); summary.Players.Add(reordered);
        ++state.PhaseRevision; state.PhaseEndsAtUnixMs = Now + 5000;
        state.CrewTasksCompleted = 5; state.TaskThreshold = 9;
        var emergency = new SocialEmergencyStateObservation { MatchId = state.MatchId, Active = true, RoomId = "engineering", EndsAtUnixMs = Now + 3000 };
        var meeting = new SocialMeetingObservation { MatchId = state.MatchId, Resolution = "postgame" };
        var after = projector.Build(state, emergency, meeting, Array.Empty<BroadcastViewProjector.Actor>(), Array.Empty<BroadcastViewProjector.Body>(), summary, Now + 100);
        Acknowledge(projector, after);
        Assert.Empty(after.Actors); Assert.Empty(after.Bodies);
        Assert.Equal(terminalFact, Assert.Single(after.Facts.Where(f => f.Predicate == "public-state")));
        Assert.Single(after.Facts.Where(f => f.Priority == 0));
    }
    [Theory]
    [InlineData("winner")] [InlineData("reason")] [InlineData("identity")] [InlineData("name")]
    [InlineData("alignment")] [InlineData("life")] [InlineData("roster")]
    public void AuthoritativeTerminalCorrectionsEmitANewCriticalPublicState(string correction)
    {
        var projector = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now);
        var state = State(); state.Phase = SocialPhase.GameOver; var summary = FinalSummary();
        var first = projector.Build(state, null, null, Array.Empty<BroadcastViewProjector.Actor>(), Array.Empty<BroadcastViewProjector.Body>(), summary, Now);
        Acknowledge(projector, first);
        var original = Assert.Single(first.Facts.Where(f => f.Predicate == "public-state"));
        switch (correction)
        {
            case "winner": summary.WinningSide = SocialAlignment.Impostor; break;
            case "reason": summary.WinReason = "impostor_parity"; break;
            case "identity": summary.Players[0].ActorId = Id(12); break;
            case "name": summary.Players[0].DisplayName = "Нина"; break;
            case "alignment": summary.Players[0].Alignment = SocialAlignment.Impostor; break;
            case "life": summary.Players[0].Life = SocialLife.Dead; break;
            case "roster": summary.Players.RemoveAt(0); break;
        }
        var changed = projector.Build(state, null, null, Array.Empty<BroadcastViewProjector.Actor>(), Array.Empty<BroadcastViewProjector.Body>(), summary, Now + 100);
        var added = Assert.Single(changed.Facts.Where(f => f.Predicate == "public-state" && f.EvidenceId != original.EvidenceId));
        Assert.Equal(0u, added.Priority); Assert.Equal(Now + 100, added.ObservedAtUnixMs);
        Assert.True(changed.SemanticRevision > first.SemanticRevision); Acknowledge(projector, changed);
        var stable = projector.Build(state, null, null, Array.Empty<BroadcastViewProjector.Actor>(), Array.Empty<BroadcastViewProjector.Body>(), summary, Now + 200);
        Assert.Equal(2, stable.Facts.Count(f => f.Predicate == "public-state"));
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

    private static BroadcastView MotionFrame(BroadcastViewProjector projector, BroadcastViewProjector.Actor actor, long at, bool acknowledge = true)
    {
        var frame = projector.Build(State(), null, null, new[] { actor }, Array.Empty<BroadcastViewProjector.Body>(), null, at);
        if (acknowledge) Acknowledge(projector, frame);
        return frame;
    }
    private static void Focus(BroadcastViewProjector projector, BroadcastViewProjector.Actor actor, long at)
    {
        MotionFrame(projector, actor, at);
        Assert.True(projector.Select(new BroadcastSelectViewCommand { Scope = projector.Scope,
            CorrelationId = Id(90), FocusActorId = actor.Id, ExpiresAtUnixMs = at + 1000 }, at).Accepted);
    }
    [Theory]
    [InlineData(0, 1, 1)] [InlineData(1, 1, 2)] [InlineData(1, 0, 3)] [InlineData(1, -1, 4)]
    [InlineData(0, -1, 5)] [InlineData(-1, -1, 6)] [InlineData(-1, 0, 7)] [InlineData(-1, 1, 8)]
    public void FocusMovementUsesPublicEightWayVelocityAndEmissionTime(float vx, float vy, long expected)
    {
        var projector = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now);
        var actor = Actor(); Focus(projector, actor, Now + 8000);
        MotionFrame(projector, actor, Now + 8100);
        actor.X += vx * .6f; actor.Y += vy * .6f; actor.VelocityX = vx; actor.VelocityY = vy;
        var frame = MotionFrame(projector, actor, Now + 8200);
        var fact = Assert.Single(frame.Facts.Where(f => f.Predicate == "movement-seen"));
        Assert.Equal(expected, fact.Value); Assert.Equal(Now + 8200, fact.ObservedAtUnixMs);
        Assert.Equal(actor.Id, fact.ActorId); Assert.Equal(actor.Name, fact.Label); Assert.Equal(actor.Room, fact.RoomId);
    }
    [Fact] public void UnfocusedStationaryAndBlockedVelocityDoNotCreateMovementFacts()
    {
        var projector = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now); var actor = Actor();
        actor.VelocityX = 1; MotionFrame(projector, actor, Now + 8000);
        actor.X = .8f;
        Assert.DoesNotContain(MotionFrame(projector, actor, Now + 9000).Facts, f => f.Predicate == "movement-seen");
        Focus(projector, actor, Now + 16000); actor.VelocityX = 0;
        MotionFrame(projector, actor, Now + 16100);
        actor.VelocityX = .02f;
        Assert.DoesNotContain(MotionFrame(projector, actor, Now + 26000).Facts, f => f.Predicate == "movement-seen");
        actor.VelocityX = 1; // Velocity alone cannot prove displacement against a wall.
        Assert.DoesNotContain(MotionFrame(projector, actor, Now + 36000).Facts, f => f.Predicate == "movement-seen");
    }
    [Fact] public void MotionRefreshRequiresEightSecondsAndHalfMetreButOneStopDoesNotRequireFurtherTravel()
    {
        var projector = new BroadcastViewProjector(Scope(), (_, _, _, _) => true, Now); var actor = Actor();
        Focus(projector, actor, Now + 8000); MotionFrame(projector, actor, Now + 8100);
        actor.VelocityX = 1; actor.X = .6f;
        MotionFrame(projector, actor, Now + 8200);
        actor.X = 1.2f;
        Assert.Single(MotionFrame(projector, actor, Now + 16199).Facts.Where(f => f.Predicate == "movement-seen"));
        actor.X = .65f;
        Assert.Single(MotionFrame(projector, actor, Now + 16200).Facts.Where(f => f.Predicate == "movement-seen"));
        actor.X = 1.2f;
        Assert.Equal(2, MotionFrame(projector, actor, Now + 16201).Facts.Count(f => f.Predicate == "movement-seen"));
        actor.VelocityX = 0;
        Assert.Equal(2, MotionFrame(projector, actor, Now + 24200).Facts.Count(f => f.Predicate == "movement-seen"));
        var stopped = MotionFrame(projector, actor, Now + 24201);
        var stop = Assert.Single(stopped.Facts.Where(f => f.Predicate == "movement-seen" && f.Value == 9));
        Assert.Equal(Now + 24201, stop.ObservedAtUnixMs);
        Assert.Equal(3, MotionFrame(projector, actor, Now + 40000).Facts.Count(f => f.Predicate == "movement-seen"));
    }
    [Fact] public void LostSightResetsMotionWithoutTrackingHiddenTravelOrInventingAnInitialStop()
    {
        bool visible = true; var projector = new BroadcastViewProjector(Scope(), (_, _, _, _) => visible, Now); var actor = Actor();
        Focus(projector, actor, Now + 8000); MotionFrame(projector, actor, Now + 8100);
        actor.VelocityX = 1; actor.X = .6f; MotionFrame(projector, actor, Now + 8200);
        visible = false; actor.X = 50; actor.VelocityX = -1;
        var hidden = MotionFrame(projector, actor, Now + 17000);
        Assert.Empty(hidden.Actors); Assert.Single(hidden.Facts.Where(f => f.Predicate == "movement-seen"));
        visible = true; actor.VelocityX = 0;
        Assert.DoesNotContain(MotionFrame(projector, actor, Now + 18000).Facts, f => f.Predicate == "movement-seen" && f.Value == 9);
        actor.VelocityY = 1; actor.Y += .1f;
        Assert.Single(MotionFrame(projector, actor, Now + 27000).Facts.Where(f => f.Predicate == "movement-seen"));
    }
    [Fact] public void CameraSelectionResetsDisplacementBaselineAndNeverAcknowledgedTransientMotionIsPruned()
    {
        bool visible = true; var projector = new BroadcastViewProjector(Scope(), (_, _, _, _) => visible, Now); var actor = Actor();
        Focus(projector, actor, Now + 8000); MotionFrame(projector, actor, Now + 8100);
        actor.VelocityX = 1; actor.X = .6f;
        var unconfirmed = MotionFrame(projector, actor, Now + 8200, false);
        Assert.Single(unconfirmed.Facts.Where(f => f.Predicate == "movement-seen"));
        actor.VelocityX = -1; actor.X = .5f;
        Assert.DoesNotContain(MotionFrame(projector, actor, Now + 8300).Facts, f => f.Predicate == "movement-seen");
        visible = false;
        Assert.DoesNotContain(MotionFrame(projector, actor, Now + 8400).Facts, f => f.Predicate == "movement-seen");
        visible = true; actor.X = 0; actor.VelocityX = 0;
        Focus(projector, actor, Now + 17000);
        Assert.DoesNotContain(MotionFrame(projector, actor, Now + 17100).Facts, f => f.Predicate == "movement-seen");
    }

}
