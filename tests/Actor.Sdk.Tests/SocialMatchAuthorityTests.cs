using Actor.Networking;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;

namespace Actor.Sdk.Tests;

public sealed class SocialMatchAuthorityTests
{
    private sealed class Fixture
    {
        internal const long Start = 1800000000000;
        internal const string Match = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        internal readonly SocialMatchAuthority Authority;
        internal readonly List<SocialMatchAuthority.Participant> Players = new();
        internal readonly HashSet<(string Observer, string Target)> Hidden = new();
        internal readonly List<SocialMatchAuthority.Terminal> Terminals;
        internal readonly List<SocialMatchAuthority.TaskDefinition> Definitions;
        internal long Now = Start;
        private int request;
        internal Fixture(int count = 4, SocialMatchAuthority.Configuration? config = null, bool start = true, int? seed = 42)
        {
            for (int i = 0; i < count; i++) Players.Add(new SocialMatchAuthority.Participant
            { Id = "actor-" + i, Name = "Player " + i, Room = "main-hall", X = i * .2, Y = 0, JoinOrder = i, ConnectionEpoch = (ulong)i });
            Authority = new SocialMatchAuthority(Match, config ?? FastConfig(), seed,
                (viewer, x, y) => !Players.Any(p => Math.Abs(p.X - x) < .00001 && Math.Abs(p.Y - y) < .00001 && Hidden.Contains((viewer, p.Id))));
            Terminals = new()
            {
                new() { Id = "meeting", Kind = "meeting", Room = "main-hall", X = 0, Y = 0 },
                new() { Id = "life-support", Kind = "life-support", Room = "main-hall", X = 0, Y = 0 }
            };
            Definitions = new();
            for (int i = 0; i < 5; i++)
            {
                Terminals.Add(new() { Id = "terminal-" + i, Kind = "task", Room = "main-hall", X = 0, Y = 0 });
                Definitions.Add(new() { Id = "task-" + i, TerminalId = "terminal-" + i });
            }
            Authority.SetWorld(Terminals, Definitions); Refresh();
            if (start) StartMatch();
        }
        internal static SocialMatchAuthority.Configuration FastConfig() => new()
        { DiscussionDurationMs = 2000, VotingDurationMs = 2000, KillCooldownMs = 1000,
            EmergencyCooldownMs = 2000, SabotageCooldownMs = 1000, WorkDurationMs = 1000, SabotageTimeoutMs = 10000, TaskThreshold = 4 };
        internal string Impostor => Players.Single(p => Authority.OwnState(p.Id).Alignment == SocialAlignment.Impostor).Id;
        internal string[] Crew => Players.Where(p => Authority.OwnState(p.Id).Alignment == SocialAlignment.Crew).Select(p => p.Id).ToArray();
        internal SocialActionContext Context(string? correlation = null) => new()
        { MatchId = Match, PhaseRevision = Authority.State().PhaseRevision, CorrelationId = correlation ?? "request-" + ++request, ExpiresAtUnixMs = Now + 9000, ConnectionGeneration = 1 };
        internal List<SocialMatchAuthority.Delivery> Do(string actor, IMessage command) => Authority.Execute(actor, command, Now);
        internal List<SocialMatchAuthority.Delivery> Refresh() => Authority.Refresh(Players, Now);
        internal List<SocialMatchAuthority.Delivery> Advance(long ms) { Now += ms; return Authority.Tick(Now); }
        internal SocialActionResultEvent Ready(string actor) => Reply(Do(actor, new SocialReadyCommand { Context = Context(), Ready = true }));
        internal void StartMatch()
        {
            foreach (var p in Players) Assert.True(Ready(p.Id).Accepted);
            Assert.Equal(SocialPhase.RoleAssignment, Authority.Phase);
            Advance(1000); Assert.Equal(SocialPhase.Exploration, Authority.Phase);
        }
        internal SocialActionResultEvent Kill(string target) => Reply(Do(Impostor, new SocialKillCommand { Context = Context(), TargetActorId = target }));
        internal SocialActionResultEvent Call(string? caller = null) => Reply(Do(caller ?? Crew[0], new SocialCallMeetingCommand { Context = Context() }));
        internal void Voting()
        { Assert.True(Call().Accepted); Advance(2000); Assert.Equal(SocialPhase.Voting, Authority.Phase); }
        internal SocialActionResultEvent Vote(string voter, string candidate = "", bool skip = false) => Reply(Do(voter, new SocialCastVoteCommand
        { Context = Context(), MeetingId = Authority.Meeting(voter).MeetingId, CandidateActorId = candidate, Skip = skip }));
        internal SocialActionResultEvent Work(string actor, string task = "task-0") => Reply(Do(actor, new SocialWorkTaskCommand { Context = Context(), TaskId = task }));
        internal SocialActionResultEvent Sabotage() => Reply(Do(Impostor, new SocialSabotageCommand { Context = Context(), TerminalEntityId = "life-support" }));
        internal SocialActionResultEvent Repair(string actor) => Reply(Do(actor, new SocialRepairEmergencyCommand { Context = Context(), EmergencyId = Authority.Emergency().EmergencyId }));
        internal SocialMatchAuthority.Participant Pose(string id) => Players.Single(p => p.Id == id);
    }
    private static SocialActionResultEvent Reply(IEnumerable<SocialMatchAuthority.Delivery> deliveries)
        => deliveries.Select(d => d.Payload).OfType<SocialActionResultEvent>().Last();

    [Theory]
    [InlineData(0)]
    [InlineData(1)]
    [InlineData(3)]
    public void ProductionMinimumPreventsMeaninglessMatches(int count)
    {
        var f = new Fixture(count, start: false);
        foreach (var p in f.Players) Assert.True(f.Ready(p.Id).Accepted);
        f.Advance(100000); Assert.Equal(SocialPhase.WaitingForPlayers, f.Authority.Phase); Assert.Null(f.Authority.Summary());
    }
    [Fact]
    public void WaitingHasBoundedTerminalDrawWithoutInventedAlignments()
    {
        var config = Fixture.FastConfig(); config.WaitingTimeoutMs = 2000;
        var f = new Fixture(3, config, start: false); f.Advance(2000);
        Assert.Equal(SocialPhase.GameOver, f.Authority.Phase); Assert.Equal("waiting_timeout", f.Authority.Summary()!.WinReason);
        Assert.Empty(f.Authority.Summary()!.Players); Assert.Equal(SocialAlignment.Unspecified, f.Authority.Summary()!.WinningSide);
    }
    [Theory]
    [InlineData(4)]
    [InlineData(5)]
    [InlineData(6)]
    public void ReadinessAssignsExactlyOneImpostorAndIndependentOwnDuties(int count)
    {
        var f = new Fixture(count); var own = f.Players.Select(p => f.Authority.OwnState(p.Id)).ToArray();
        Assert.Single(own.Where(p => p.Alignment == SocialAlignment.Impostor));
        Assert.Equal(count - 1, own.Count(p => p.Alignment == SocialAlignment.Crew));
        Assert.Single(own.Where(p => p.Duty == CrewRole.Engineer)); Assert.Single(own.Where(p => p.Duty == CrewRole.Sheriff));
        Assert.All(own, p => { Assert.True(p.Assigned); Assert.True(p.CanMove); });
        Assert.Null(f.Authority.Summary());
    }
    [Fact]
    public void SecureOrdinaryAssignmentStillHasValidCompositionWithoutTrustedSeed()
    {
        var f = new Fixture(config: new SocialMatchAuthority.Configuration(), seed: null); Assert.Single(f.Players.Where(p => f.Authority.OwnState(p.Id).Alignment == SocialAlignment.Impostor));
    }
    [Fact]
    public void ConfigIsCopiedAndMalformedProductionBoundsAreRejected()
    {
        var c = Fixture.FastConfig(); var f = new Fixture(config: c); c.KillDistance = 100;
        f.Pose(f.Crew[0]).X = 2; f.Refresh(); Assert.False(f.Kill(f.Crew[0]).Accepted);
        Assert.Throws<ArgumentException>(() => new SocialMatchAuthority(Fixture.Match, new() { MinPlayers = 3 }));
        Assert.Throws<ArgumentException>(() => new SocialMatchAuthority(Fixture.Match, new() { ImpostorCount = 2 }));
        Assert.Throws<ArgumentException>(() => new SocialMatchAuthority(Fixture.Match, new() { VisibilityRadius = double.NaN }));
        Assert.Throws<ArgumentException>(() => new SocialMatchAuthority("not-a-match"));
    }
    [Fact]
    public void WorldMustHaveRealTerminalsAndEnoughBoundedTaskInstances()
    {
        var f = new Fixture(start: false); var tasks = f.Definitions.Take(2).ToArray();
        Assert.Throws<ArgumentException>(() => f.Authority.SetWorld(f.Terminals, tasks));
        f.Terminals[0].X = double.NaN;
        Assert.Throws<ArgumentException>(() => f.Authority.SetWorld(f.Terminals, f.Definitions));
    }
    [Fact]
    public void WorldIdentityCannotBeRewrittenAfterAssignment()
    {
        var f = new Fixture(); f.Definitions[0].Id = "replacement";
        Assert.Throws<InvalidOperationException>(() => f.Authority.SetWorld(f.Terminals, f.Definitions));
    }
    [Fact]
    public void LastUnreadyActorAndStalePosePreventStart()
    {
        var f = new Fixture(start: false);
        foreach (var p in f.Players.Take(3)) f.Ready(p.Id);
        Assert.Equal(SocialPhase.WaitingForPlayers, f.Authority.Phase);
        f.Players[3].PoseFresh = false; f.Refresh(); f.Ready(f.Players[3].Id); Assert.False(f.Authority.Started);
        f.Players[3].PoseFresh = true; f.Refresh(); Assert.True(f.Authority.Started);
    }
    [Fact]
    public void OwnStateAndVisibleBundlesHaveNoOtherAlignmentOrDutyFields()
    {
        var f = new Fixture(); var view = f.Authority.VisibleWorld(f.Crew[0], f.Now);
        Assert.Equal(3, view.Actors.Count);
        Assert.DoesNotContain(SocialVisibleActor.Descriptor.Fields.InDeclarationOrder(), p => p.Name.Contains("alignment") || p.Name.Contains("duty") || p.Name.Contains("life"));
        Assert.DoesNotContain(SocialMatchStateObservation.Descriptor.Fields.InDeclarationOrder(), p => p.Name.Contains("alive")
            || p.Name.Contains("death") || p.Name == "kills" || p.Name == "kill_count");
        Assert.Equal(0, f.Authority.OwnState(f.Crew[0]).KillReadyAtUnixMs);
    }
    [Fact]
    public void MissingLosPolicyFailsClosedForActorsBodiesAndInteractions()
    {
        var a = new SocialMatchAuthority(Fixture.Match); var f = new Fixture();
        a.SetWorld(f.Terminals, f.Definitions); a.Refresh(f.Players, f.Now);
        foreach (var p in f.Players) a.Execute(p.Id, new SocialReadyCommand { Context = new() { MatchId = Fixture.Match, PhaseRevision = a.State().PhaseRevision,
            CorrelationId = p.Id, ExpiresAtUnixMs = f.Now + 9000 }, Ready = true }, f.Now);
        a.Tick(f.Now + 1000); Assert.False(a.IsVisible(f.Players[0].Id, f.Players[1].Id));
        Assert.Empty(a.VisibleWorld(f.Players[0].Id, f.Now).Interactables);
    }
    [Fact]
    public void RadiusWallsLossAndReappearanceProduceFullReplacementViews()
    {
        var f = new Fixture(); var observer = f.Crew[0]; var target = f.Crew[1];
        Assert.Contains(f.Authority.VisibleWorld(observer, f.Now).Actors, p => p.ActorId == target);
        f.Pose(target).X = 10; f.Refresh(); Assert.DoesNotContain(f.Authority.VisibleWorld(observer, f.Now).Actors, p => p.ActorId == target);
        f.Pose(target).X = .7; f.Refresh(); f.Hidden.Add((observer, target));
        Assert.DoesNotContain(f.Authority.VisibleWorld(observer, f.Now).Actors, p => p.ActorId == target);
        f.Hidden.Clear(); Assert.Contains(f.Authority.VisibleWorld(observer, f.Now).Actors, p => p.ActorId == target);
    }
    [Fact]
    public void DoorwayVisibilityUsesActualLosRatherThanRoomEquality()
    {
        var f = new Fixture(); var observer = f.Crew[0]; var target = f.Crew[1]; f.Pose(target).Room = "engineering";
        f.Refresh(); Assert.True(f.Authority.IsVisible(observer, target));
    }
    [Fact]
    public void InvisibleKillHasNoPublicRevisionDeathNoticeBodyOrWitnessForRemoteActor()
    {
        var f = new Fixture(); var victim = f.Crew[0]; var remote = f.Crew[1];
        f.Pose(remote).X = 8; f.Refresh(); var before = f.Authority.State().ToByteArray();
        var ownBefore = f.Authority.OwnState(remote).ToByteArray();
        var deliveries = f.Do(f.Impostor, new SocialKillCommand { Context = f.Context(), TargetActorId = victim });
        Assert.True(Reply(deliveries).Accepted); Assert.Equal(SocialLife.Dead, f.Authority.OwnState(victim).Life);
        Assert.Equal(before, f.Authority.State().ToByteArray()); Assert.Equal(ownBefore, f.Authority.OwnState(remote).ToByteArray());
        Assert.DoesNotContain(deliveries, d => d.Recipient == remote); Assert.Empty(f.Authority.Bodies(remote));
        Assert.Single(f.Authority.Bodies(f.Impostor));
    }
    [Fact]
    public void WitnessRequiresVisibilityOfBothAttackerAndVictimAtTheInstant()
    {
        var f = new Fixture(); var victim = f.Crew[0]; var witness = f.Crew[1]; var blocked = f.Crew[2];
        f.Hidden.Add((blocked, f.Impostor));
        var events = f.Do(f.Impostor, new SocialKillCommand { Context = f.Context(), TargetActorId = victim });
        Assert.Single(events.Where(d => d.Recipient == witness && d.Payload is SocialVisibleAttackEvent));
        Assert.DoesNotContain(events, d => d.Recipient == blocked && d.Payload is SocialVisibleAttackEvent);
    }
    [Fact]
    public void KillUnknownOutsideRadiusBehindWallAndOutOfRangeHaveUniformErrors()
    {
        var f = new Fixture(); var target = f.Crew[0];
        string unknown = Reply(f.Do(f.Impostor, new SocialKillCommand { Context = f.Context(), TargetActorId = "unknown" })).ReasonCode;
        f.Pose(target).X = 8; f.Refresh(); Assert.Equal(unknown, f.Kill(target).ReasonCode);
        f.Pose(target).X = .8; f.Refresh(); f.Hidden.Add((f.Impostor, target)); Assert.Equal(unknown, f.Kill(target).ReasonCode);
        f.Hidden.Clear(); f.Advance(1000); f.Pose(target).X = f.Pose(f.Impostor).X + 1.3; f.Refresh(); Assert.Equal(unknown, f.Kill(target).ReasonCode);
    }
    [Fact]
    public void CooldownAndOwnAlignmentAreIndependentlyEnforced()
    {
        var f = new Fixture(); Assert.True(f.Kill(f.Crew[0]).Accepted);
        Assert.False(f.Kill(f.Crew[1]).Accepted);
        Assert.False(Reply(f.Do(f.Crew[1], new SocialKillCommand { Context = f.Context(), TargetActorId = f.Impostor })).Accepted);
        f.Advance(1000); Assert.True(f.Kill(f.Crew[1]).Accepted); Assert.Equal("parity", f.Authority.State().WinReason);
    }
    [Fact]
    public void NonfiniteOrStalePoseCannotKillSeeOrWork()
    {
        var f = new Fixture(); var impostor = f.Impostor;
        f.Pose(impostor).X = double.NaN; f.Refresh(); Assert.False(f.Kill(f.Crew[0]).Accepted);
        Assert.Empty(f.Authority.VisibleWorld(impostor, f.Now).Actors); Assert.False(f.Work(impostor).Accepted);
    }
    [Fact]
    public void DeadCannotMoveVoteWorkMessageOrCallMeetingAndHasNoSpectatorOracle()
    {
        var f = new Fixture(); var victim = f.Crew[0]; Assert.True(f.Kill(victim).Accepted);
        Assert.False(f.Authority.CanMove(victim)); Assert.False(f.Work(victim).Accepted); Assert.False(f.Call(victim).Accepted);
        Assert.False(f.Authority.CanCommunicate(victim, f.Crew[1], "direct")); Assert.Empty(f.Authority.VisibleWorld(victim, f.Now).Actors);
        Assert.Empty(f.Authority.Tasks(victim).Tasks);
    }
    [Fact]
    public void BodyIsVisibleLocallyReportRequiresActualRangeAndStartsMeeting()
    {
        var f = new Fixture(); Assert.True(f.Kill(f.Crew[0]).Accepted); string reporter = f.Crew[1];
        string body = f.Authority.Bodies(reporter).Single().BodyId;
        f.Pose(reporter).X = 2; f.Refresh(); Assert.False(Reply(f.Do(reporter, new SocialReportBodyCommand { Context = f.Context(), BodyId = body })).Accepted);
        f.Pose(reporter).X = .1; f.Refresh(); Assert.True(Reply(f.Do(reporter, new SocialReportBodyCommand { Context = f.Context(), BodyId = body })).Accepted);
        Assert.Equal(SocialPhase.Discussion, f.Authority.Phase); Assert.Empty(f.Authority.Bodies(reporter));
        Assert.Contains(f.Authority.Meeting(reporter).Participants, p => p.ActorId == f.Crew[0] && p.Life == SocialLife.Dead);
    }
    [Fact]
    public void EmergencyRequiresActualTerminalAndFreezesServerMovementAndKills()
    {
        var f = new Fixture(); var caller = f.Crew[0]; f.Pose(caller).X = 2; f.Refresh(); Assert.False(f.Call(caller).Accepted);
        f.Pose(caller).X = 0; f.Refresh(); Assert.True(f.Call(caller).Accepted);
        Assert.All(f.Players, p => Assert.False(f.Authority.CanMove(p.Id))); Assert.False(f.Kill(caller).Accepted);
        Assert.False(f.Work(caller).Accepted);
    }
    [Fact]
    public void CommunicationPhaseLifeLosAndStationRulesAreServerOwned()
    {
        var f = new Fixture(); var sender = f.Crew[0]; var receiver = f.Crew[1];
        Assert.True(f.Authority.CanCommunicate(sender, receiver, "direct")); Assert.True(f.Authority.CanCommunicate(sender, "", "proximity"));
        Assert.False(f.Authority.CanCommunicate(sender, "", "station")); f.Hidden.Add((sender, receiver));
        Assert.False(f.Authority.CanCommunicate(sender, receiver, "direct")); f.Hidden.Clear(); f.Call();
        Assert.True(f.Authority.CanCommunicate(sender, "", "discussion")); Assert.False(f.Authority.CanCommunicate(sender, receiver, "direct"));
    }
    [Fact]
    public void DiscussionVotingResolutionAndExplorationUseDistinctEpochs()
    {
        var f = new Fixture(); ulong exploration = f.Authority.State().PhaseRevision;
        f.Call(); Assert.Equal(exploration + 1, f.Authority.State().PhaseRevision);
        f.Advance(2000); Assert.Equal(SocialPhase.Voting, f.Authority.Phase);
        f.Advance(2000); Assert.Equal(SocialPhase.VoteResolution, f.Authority.Phase);
        f.Advance(1000); Assert.Equal(SocialPhase.Exploration, f.Authority.Phase);
    }
    [Fact]
    public void OneFinalBallotCannotBeReplacedAndOtherPlayersCannotReadItEarly()
    {
        var f = new Fixture(); f.Voting(); var crew = f.Crew;
        Assert.True(f.Vote(crew[0], f.Impostor).Accepted); Assert.False(f.Vote(crew[0], crew[1]).Accepted);
        Assert.True(f.Authority.Meeting(crew[0]).OwnBallotSubmitted); Assert.False(f.Authority.Meeting(crew[1]).OwnBallotSubmitted);
        Assert.Empty(f.Authority.Meeting(crew[1]).Result);
    }
    [Fact]
    public void UniqueLeaderAboveSkipEjectsAndCrewWinsWithoutPreterminalRoleDisclosure()
    {
        var f = new Fixture(); f.Voting(); string impostor = f.Impostor;
        Assert.DoesNotContain(SocialMeetingParticipant.Descriptor.Fields.InDeclarationOrder(), field => field.Name.Contains("alignment"));
        foreach (string crew in f.Crew) Assert.True(f.Vote(crew, impostor).Accepted);
        Assert.True(f.Vote(impostor, skip: true).Accepted); f.Advance(2000);
        Assert.Equal(SocialPhase.GameOver, f.Authority.Phase); Assert.Equal(SocialAlignment.Crew, f.Authority.Summary()!.WinningSide);
        Assert.Equal(SocialLife.Ejected, f.Authority.Summary()!.Players.Single(p => p.ActorId == impostor).Life);
    }
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void CandidateTieOrSkipTieDoesNotEject(bool skipTie)
    {
        var f = new Fixture(); f.Voting(); var ids = f.Players.Select(p => p.Id).ToArray();
        f.Vote(ids[0], ids[2]); f.Vote(ids[1], skipTie ? "" : ids[3], skipTie); f.Advance(2000);
        Assert.Equal("no_ejection", f.Authority.Meeting(ids[0]).Resolution); Assert.Empty(f.Authority.Meeting(ids[0]).EjectedActorId);
        Assert.False(f.Authority.IsTerminal);
    }
    [Fact]
    public void AbstentionDoesNotFabricateBallotsAndAllAbstainResolvesSafely()
    {
        var f = new Fixture(); f.Voting(); f.Advance(2000);
        Assert.Empty(f.Authority.Meeting(f.Crew[0]).Result); Assert.Equal("no_ejection", f.Authority.Meeting(f.Crew[0]).Resolution);
    }
    [Fact]
    public void MeetingLimitsPreventRepeatedEmergencySpam()
    {
        var f = new Fixture(); string caller = f.Crew[0]; Assert.True(f.Call(caller).Accepted);
        f.Advance(5000); Assert.False(f.Call(caller).Accepted); Assert.Equal(0u, f.Authority.OwnState(caller).RemainingMeetings);
    }
    [Fact]
    public void StalePhaseCommandCannotExecuteAfterReturningToExploration()
    {
        var f = new Fixture(); var old = new SocialKillCommand { Context = f.Context(), TargetActorId = f.Crew[0] };
        f.Call(); f.Advance(5000); var reply = Reply(f.Do(f.Impostor, old));
        Assert.Equal("phase_changed", reply.ReasonCode); Assert.Equal(SocialLife.Alive, f.Authority.OwnState(f.Crew[0]).Life);
    }
    [Fact]
    public void MatchAndExpiryFenceShortLivedActions()
    {
        var f = new Fixture(); var cmd = new SocialWorkTaskCommand { Context = f.Context(), TaskId = "task-0" };
        cmd.Context.MatchId = new string('b', 32); Assert.Equal("match_mismatch", Reply(f.Do(f.Crew[0], cmd)).ReasonCode);
        cmd.Context = f.Context(); cmd.Context.ExpiresAtUnixMs = f.Now; Assert.Equal("action_expired", Reply(f.Do(f.Crew[0], cmd)).ReasonCode);
        cmd.Context = f.Context(); cmd.Context.ExpiresAtUnixMs = f.Now + 10001; Assert.Equal("action_expired", Reply(f.Do(f.Crew[0], cmd)).ReasonCode);
    }
    [Fact]
    public void TypedReplayDoesNotRepeatKillsAndCrossCommandCorrelationConflicts()
    {
        var f = new Fixture(); var cmd = new SocialKillCommand { Context = f.Context("same"), TargetActorId = f.Crew[0] };
        var first = f.Do(f.Impostor, cmd); Assert.True(Reply(first).Accepted);
        var second = f.Do(f.Impostor, cmd); Assert.True(Reply(second).Accepted); Assert.Single(second);
        var conflict = f.Do(f.Impostor, new SocialWorkTaskCommand { Context = cmd.Context.Clone(), TaskId = "task-0" });
        Assert.Equal("correlation_conflict", Reply(conflict).ReasonCode); Assert.Single(f.Authority.Bodies(f.Impostor));
    }
    [Fact]
    public void PerActorBudgetBoundsUnknownTargetScanning()
    {
        var f = new Fixture(); var reasons = Enumerable.Range(0, 10).Select(i => Reply(f.Do(f.Impostor,
            new SocialKillCommand { Context = f.Context(), TargetActorId = "unknown-" + i })).ReasonCode).ToArray();
        Assert.Contains("rate_limited", reasons);
    }
    [Fact]
    public void RealCrewWorkCompletesOnlyAfterContinuousAuthoritativePresence()
    {
        var f = new Fixture(); var worker = f.Crew[0]; var start = f.Work(worker);
        Assert.True(start.Accepted); Assert.False(start.Completed); Assert.NotEmpty(start.WorkId);
        f.Advance(999); Assert.Equal(0u, f.Authority.State().CrewTasksCompleted);
        var complete = f.Advance(1); Assert.True(Reply(complete).Completed); Assert.Equal(1u, f.Authority.State().CrewTasksCompleted);
        Assert.DoesNotContain(complete.Select(d => d.Payload), p => p is SocialVisibleActivityEvent a && a.Activity != "stopped");
    }
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void StalePoseOrLeavingTerminalInterruptsWork(bool stale)
    {
        var f = new Fixture(); var worker = f.Crew[0]; f.Work(worker);
        if (stale) f.Pose(worker).PoseFresh = false; else f.Pose(worker).X = 4;
        var output = f.Refresh(); Assert.False(Reply(output).Accepted); f.Advance(2000); Assert.Equal(0u, f.Authority.State().CrewTasksCompleted);
    }
    [Fact]
    public void ActualMovedTerminalIsUsedInsteadOfHardcodedApproach()
    {
        var f = new Fixture(); var t = f.Terminals.Single(t => t.Id == "terminal-0"); t.X = 8;
        f.Authority.SetWorld(f.Terminals, f.Definitions); Assert.False(f.Work(f.Crew[0]).Accepted);
    }
    [Fact]
    public void CancellationIsBoundToOwnedCurrentWorkAndReplayBecomesTerminal()
    {
        var f = new Fixture(); var worker = f.Crew[0]; var command = new SocialWorkTaskCommand { Context = f.Context(), TaskId = "task-0" };
        var start = Reply(f.Do(worker, command));
        Assert.False(Reply(f.Do(f.Crew[1], new SocialCancelWorkCommand { Context = f.Context(), WorkId = start.WorkId })).Accepted);
        Assert.True(Reply(f.Do(worker, new SocialCancelWorkCommand { Context = f.Context(), WorkId = start.WorkId })).Accepted);
        Assert.False(Reply(f.Do(worker, command)).Accepted); f.Advance(2000); Assert.Equal(0u, f.Authority.State().CrewTasksCompleted);
    }
    [Fact]
    public void FakeWorkHasSameWitnessActivityWithoutCrewProgressOrAutomaticReveal()
    {
        var real = new Fixture(); var fake = new Fixture();
        var r = real.Do(real.Crew[0], new SocialWorkTaskCommand { Context = real.Context(), TaskId = "task-0" });
        var f = fake.Do(fake.Impostor, new SocialWorkTaskCommand { Context = fake.Context(), TaskId = "task-0" });
        Assert.True(Reply(r).Accepted); Assert.True(Reply(f).Accepted);
        Assert.All(r.Concat(f).Select(d => d.Payload).OfType<SocialVisibleActivityEvent>(), a => Assert.Equal("working", a.Activity));
        real.Advance(1000); fake.Advance(1000); Assert.Equal(1u, real.Authority.State().CrewTasksCompleted); Assert.Equal(0u, fake.Authority.State().CrewTasksCompleted);
        Assert.DoesNotContain(SocialVisibleActivityEvent.Descriptor.Fields.InDeclarationOrder(), p => p.Name.Contains("fake") || p.Name.Contains("contributes"));
    }
    [Fact]
    public void CrewTaskThresholdIsAnActualServerVictory()
    {
        var f = new Fixture(); var worker = f.Crew[0];
        for (int i = 0; i < 4; i++) { Assert.True(f.Work(worker, "task-" + i).Accepted); f.Advance(1000); }
        Assert.Equal(SocialAlignment.Crew, f.Authority.State().WinningSide); Assert.Equal("tasks_completed", f.Authority.State().WinReason);
        Assert.Equal(4u, f.Authority.Summary()!.CrewTasksCompleted);
    }
    [Fact]
    public void MeetingCancelsLeasesAndOwnerInputWithoutAccidentalTaskCompletion()
    {
        var f = new Fixture(); f.Work(f.Crew[0]); Assert.True(f.Call(f.Crew[1]).Accepted);
        f.Advance(1000); Assert.Equal(0u, f.Authority.State().CrewTasksCompleted);
    }
    [Fact]
    public void OnlyLiveImpostorAtRealTerminalCanStartSabotageAndNoInitiatorIsPublic()
    {
        var f = new Fixture(); Assert.False(Reply(f.Do(f.Crew[0], new SocialSabotageCommand { Context = f.Context(), TerminalEntityId = "life-support" })).Accepted);
        f.Pose(f.Impostor).X = 2; f.Refresh(); Assert.False(f.Sabotage().Accepted);
        f.Pose(f.Impostor).X = 0; f.Refresh(); Assert.True(f.Sabotage().Accepted);
        Assert.DoesNotContain(SocialEmergencyStateObservation.Descriptor.Fields.InDeclarationOrder(), p => p.Name.Contains("actor") || p.Name.Contains("initiator"));
    }
    [Fact]
    public void SabotageHasAuthoritativeTimeoutAndImpostorVictory()
    {
        var f = new Fixture(); Assert.True(f.Sabotage().Accepted); f.Advance(10000);
        Assert.Equal(SocialAlignment.Impostor, f.Authority.State().WinningSide); Assert.Equal("sabotage_expired", f.Authority.State().WinReason);
    }
    [Fact]
    public void OneRemainingEligibleCrewCanPhysicallyRepairWithoutExtraParticipants()
    {
        var f = new Fixture(); f.Sabotage(); var worker = f.Crew[0]; var start = f.Repair(worker);
        Assert.True(start.Accepted); Assert.False(start.Completed); f.Advance(1000);
        Assert.False(f.Authority.Emergency().Active); Assert.False(f.Authority.IsTerminal);
    }
    [Fact]
    public void SabotageTimerPausesForAllMeetingPhasesAndResumesRemainingBudget()
    {
        var f = new Fixture(); f.Sabotage(); f.Advance(1000); f.Call();
        Assert.True(f.Authority.Emergency().PausedForMeeting); Assert.Equal(9000, f.Authority.Emergency().RemainingMs);
        f.Advance(5000); Assert.False(f.Authority.Emergency().PausedForMeeting); Assert.Equal(9000, f.Authority.Emergency().RemainingMs);
        f.Advance(8999); Assert.False(f.Authority.IsTerminal); f.Advance(1); Assert.Equal("sabotage_expired", f.Authority.State().WinReason);
    }
    [Fact]
    public void LateTickHonorsRepairBeforeDeadlineButExactDeadlineLosesDeterministically()
    {
        var before = new Fixture(); before.Sabotage(); before.Advance(8999); before.Repair(before.Crew[0]); before.Advance(1100);
        Assert.False(before.Authority.Emergency().Active); Assert.False(before.Authority.IsTerminal);
        var equal = new Fixture(); equal.Sabotage(); equal.Advance(9000); equal.Repair(equal.Crew[0]); equal.Advance(1000);
        Assert.Equal("sabotage_expired", equal.Authority.State().WinReason);
    }
    [Fact]
    public void DisconnectForfeitsWithoutBodyAndDoesNotRebindByNameOrActorId()
    {
        var f = new Fixture(); var crew = f.Crew[0]; var original = f.Pose(crew); f.Players.Remove(original); f.Refresh();
        Assert.Equal(SocialLife.Disconnected, f.Authority.OwnState(crew).Life); Assert.Empty(f.Authority.Bodies(f.Impostor));
        var replacement = new SocialMatchAuthority.Participant { Id = "new-identity", Name = original.Name, Room = "main-hall", ConnectionEpoch = 99 };
        f.Players.Add(replacement); f.Refresh(); Assert.Equal(SocialLife.Spectator, f.Authority.OwnState(replacement.Id).Life);
        Assert.Equal(SocialAlignment.Unspecified, f.Authority.OwnState(replacement.Id).Alignment); Assert.False(f.Authority.CanMove(replacement.Id));
        original.ConnectionEpoch = 100; f.Players.Add(original); f.Refresh(); Assert.Equal(SocialLife.Disconnected, f.Authority.OwnState(crew).Life);
    }
    [Fact]
    public void ImpostorControlLossEndsMatchAndCompletedTerminalIsImmutable()
    {
        var f = new Fixture(); f.Authority.ControlUnavailable(f.Impostor, f.Now);
        Assert.Equal(SocialAlignment.Crew, f.Authority.State().WinningSide); var summary = f.Authority.Summary()!.ToByteArray();
        f.Advance(10000); f.Authority.ControlUnavailable(f.Crew[0], f.Now); Assert.Equal(summary, f.Authority.Summary()!.ToByteArray());
        Assert.Equal("match_over", f.Work(f.Crew[0]).ReasonCode);
    }
    [Fact]
    public void RuntimeLossCancelsInFlightWorkAndNoBodyIsFabricated()
    {
        var f = new Fixture(); var actor = f.Crew[0]; f.Work(actor);
        f.Authority.ControlUnavailable(actor, f.Now); f.Advance(1000);
        Assert.Equal(0u, f.Authority.State().CrewTasksCompleted); Assert.Equal(SocialLife.Disconnected, f.Authority.OwnState(actor).Life);
        Assert.Empty(f.Authority.Bodies(f.Impostor));
    }
    [Fact]
    public void TimeoutDrawSeparatesTeamOutcomeAndIndividualSurvival()
    {
        var f = new Fixture(); f.Advance(900000);
        Assert.Equal("match_timeout", f.Authority.State().WinReason); Assert.Equal(SocialAlignment.Unspecified, f.Authority.Summary()!.WinningSide);
        Assert.All(f.Authority.Summary()!.Players, p => { Assert.False(p.TeamWon); Assert.True(p.Survived); });
    }
    [Fact]
    public void DeadCrewCanBelongToWinningTeamWithoutClaimingPersonalSurvival()
    {
        var f = new Fixture(); var victim = f.Crew[0]; f.Kill(victim); f.Authority.ControlUnavailable(f.Impostor, f.Now);
        var dead = f.Authority.Summary()!.Players.Single(p => p.ActorId == victim); Assert.True(dead.TeamWon); Assert.False(dead.Survived);
        Assert.Equal(1000, dead.SurvivalMs);
    }
    [Fact]
    public void SummaryAndReplayOnlyExistAfterGameOverAndAreIndependentClones()
    {
        var f = new Fixture(); Assert.Null(f.Authority.Summary()); f.Authority.ControlUnavailable(f.Impostor, f.Now);
        var summary = f.Authority.Summary()!; Assert.NotEmpty(summary.Replay); Assert.Equal(4, summary.Players.Count);
        Assert.True(summary.Replay.Zip(summary.Replay.Skip(1)).All(pair => pair.First.Sequence < pair.Second.Sequence));
        summary.Players.Clear(); Assert.Equal(4, f.Authority.Summary()!.Players.Count);
    }
    [Fact]
    public void CancelBeforeReceiptCanCiteOnlyTheSameOwnedRequestCorrelation()
    {
        var f = new Fixture(); string actor = f.Crew[0]; var first = new SocialWorkTaskCommand { Context = f.Context(), TaskId = "task-0" };
        Assert.True(Reply(f.Do(actor, first)).Accepted);
        Assert.True(Reply(f.Do(actor, new SocialCancelWorkCommand { Context = f.Context(), WorkId = first.Context.CorrelationId })).Accepted);
        f.Advance(1000); Assert.True(f.Work(actor, "task-1").Accepted);
        Assert.False(Reply(f.Do(actor, new SocialCancelWorkCommand { Context = f.Context(), WorkId = first.Context.CorrelationId })).Accepted);
        f.Advance(1000); Assert.Equal(1u, f.Authority.State().CrewTasksCompleted);
    }
    [Fact]
    public void DisconnectCannotOverrideAnAlreadyElapsedSabotageOutcome()
    {
        var a = new Fixture(); a.Sabotage(); a.Now += 11000; a.Authority.ControlUnavailable(a.Impostor, a.Now);
        Assert.Equal("sabotage_expired", a.Authority.State().WinReason);
        var b = new Fixture(); b.Sabotage(); b.Players.Remove(b.Pose(b.Impostor)); b.Now += 11000; b.Refresh();
        Assert.Equal("sabotage_expired", b.Authority.State().WinReason);
    }
    [Fact]
    public void LateTickRecordsActualDeadlineAndSurvivalRatherThanPollingDelay()
    {
        var f = new Fixture(); f.Sabotage(); f.Advance(50000); var summary = f.Authority.Summary()!;
        Assert.Equal(11000, summary.DurationMs); Assert.All(summary.Players, p => Assert.Equal(11000, p.SurvivalMs));
        Assert.Equal(11000, summary.Replay.Single(e => e.Kind == "match_ended").ElapsedMs);
    }
    [Fact]
    public void PostgameDisconnectCannotRewriteFinalOwnLifeOrFrozenSummary()
    {
        var f = new Fixture(); f.Authority.ControlUnavailable(f.Impostor, f.Now);
        string actor = f.Crew[0]; var own = f.Authority.OwnState(actor).ToByteArray(); var summary = f.Authority.Summary()!.ToByteArray();
        f.Players.Remove(f.Pose(actor)); f.Refresh();
        Assert.Equal(own, f.Authority.OwnState(actor).ToByteArray()); Assert.Equal(summary, f.Authority.Summary()!.ToByteArray());
    }
    [Fact]
    public void SameIdentityNewConnectionEpochForfeitsRatherThanRestoringLife()
    {
        var f = new Fixture(); string actor = f.Crew[0]; f.Pose(actor).ConnectionEpoch += 50; f.Refresh();
        Assert.Equal(SocialLife.Disconnected, f.Authority.OwnState(actor).Life); Assert.False(f.Authority.CanMove(actor));
        f.Refresh(); Assert.Equal(SocialLife.Disconnected, f.Authority.OwnState(actor).Life);
    }
    [Fact]
    public void TrustedFastFixtureDoesNotRelaxPublicProductionConfiguration()
    {
        Assert.Throws<ArgumentException>(() => new SocialMatchAuthority(Fixture.Match, Fixture.FastConfig()));
        Assert.NotNull(new SocialMatchAuthority(Fixture.Match, Fixture.FastConfig(), trustedSeed: 1));
    }
    [Fact]
    public void ExtremelyLargeFiniteCoordinatesCannotBecomeNonfiniteVisibleFields()
    {
        var f = new Fixture(); string actor = f.Crew[0]; f.Pose(actor).X = 1e200; f.Pose(f.Impostor).X = 1e200; f.Refresh();
        Assert.Empty(f.Authority.VisibleWorld(actor, f.Now).Actors); Assert.False(f.Kill(actor).Accepted);
        f.Terminals[0].X = 1e200; Assert.Throws<ArgumentException>(() => f.Authority.SetWorld(f.Terminals, f.Definitions));
    }
    [Fact]
    public void ServerBodyListIsClonedAndNeverEquivalentToRemotePermittedBodies()
    {
        var f = new Fixture(); string remote = f.Crew[1]; f.Pose(remote).X = 8; f.Refresh(); f.Kill(f.Crew[0]);
        Assert.Empty(f.Authority.Bodies(remote)); var all = f.Authority.BodyStates(); Assert.Single(all);
        all[0].X = 100; Assert.NotEqual(100, f.Authority.BodyStates()[0].X);
    }
    [Fact]
    public void MeetingRollCallUpdatesForForfeitWithoutWaitingForNextPhase()
    {
        var f = new Fixture(); string actor = f.Crew[0]; string observer = f.Crew[1]; f.Call(observer);
        var events = f.Authority.ControlUnavailable(actor, f.Now);
        Assert.Equal(SocialLife.Disconnected, f.Authority.Meeting(observer).Participants.Single(p => p.ActorId == actor).Life);
        Assert.Contains(events, d => d.Recipient == observer && d.Payload is SocialMeetingChangedEvent);
    }
    [Fact]
    public void ForfeitedVotersBallotIsVoidAndCannotDecideTheTally()
    {
        var f = new Fixture(6); f.Voting(); string voter = f.Crew[0]; string candidate = f.Crew[3];
        Assert.True(f.Vote(voter, f.Impostor).Accepted); Assert.True(f.Vote(f.Crew[1], candidate).Accepted);
        f.Authority.ControlUnavailable(voter, f.Now); f.Advance(2000);
        var result = f.Authority.Meeting(f.Crew[1]); Assert.Equal(candidate, result.EjectedActorId);
        Assert.DoesNotContain(result.Result, r => r.CandidateActorId == f.Impostor);
        Assert.False(f.Authority.Meeting(voter).OwnBallotSubmitted);
    }
    [Fact]
    public void PublicRulesReflectImmutableConfigurationWithoutPublishingPrivateRoles()
    {
        var config = Fixture.FastConfig(); config.KillDistance = .7; config.BodyReportDistance = 1.8;
        config.InteractionRadius = .8; config.VisibilityRadius = 4.2; config.KillCooldownMs = 2000;
        var f = new Fixture(config: config); var state = f.Authority.State();
        Assert.Equal(.7f, state.KillDistance); Assert.Equal(1.8f, state.BodyReportDistance);
        Assert.Equal(.8f, state.InteractionRadius); Assert.Equal(4.2f, state.VisibilityRadius); Assert.Equal(2000u, state.KillCooldownMs);
        f.Advance(1000); f.Kill(f.Crew[0]); var after = f.Authority.State();
        Assert.Equal(state.KillDistance, after.KillDistance); Assert.Equal(state.BodyReportDistance, after.BodyReportDistance);
        Assert.Equal(state.InteractionRadius, after.InteractionRadius); Assert.Equal(state.VisibilityRadius, after.VisibilityRadius);
        Assert.Equal(state.KillCooldownMs, after.KillCooldownMs); Assert.Equal(state.ServerRevision, after.ServerRevision);
    }
    [Fact]
    public void OwnTaskCompletionDoesNotRevealAnUnseenOtherActorsTemplateCompletion()
    {
        var f = new Fixture(); string worker = f.Crew[0], remote = f.Crew[1];
        ulong ownRevision = f.Authority.Tasks(worker).ServerRevision, remoteRevision = f.Authority.Tasks(remote).ServerRevision;
        f.Pose(remote).X = 8; f.Refresh(); Assert.True(f.Work(worker).Accepted); f.Advance(1000);
        Assert.True(f.Authority.Tasks(worker).Tasks.Single(t => t.TaskId == "task-0").Completed);
        Assert.False(f.Authority.Tasks(remote).Tasks.Single(t => t.TaskId == "task-0").Completed);
        Assert.Equal(1u, f.Authority.State().CrewTasksCompleted);
        Assert.True(f.Authority.Tasks(worker).ServerRevision > ownRevision);
        Assert.Equal(remoteRevision, f.Authority.Tasks(remote).ServerRevision);
    }
    [Fact]
    public void DifferentCrewCanCompleteOwnInstancesAtSamePublicTerminal()
    {
        var f = new Fixture(); Assert.True(f.Work(f.Crew[0]).Accepted); f.Advance(1000);
        Assert.True(f.Work(f.Crew[1]).Accepted); f.Advance(1000);
        Assert.Equal(2u, f.Authority.State().CrewTasksCompleted);
        Assert.False(f.Work(f.Crew[0]).Accepted);
        Assert.False(f.Authority.Tasks(f.Crew[2]).Tasks.Single(t => t.TaskId == "task-0").Completed);
    }
    [Fact]
    public void ThresholdFiveWithFourTemplatesHasEnoughActualCrewAssignments()
    {
        var c = Fixture.FastConfig(); c.TaskThreshold = 5; var f = new Fixture(config: c, start: false);
        f.Definitions.RemoveAt(4); f.Terminals.RemoveAll(t => t.Id == "terminal-4");
        f.Authority.SetWorld(f.Terminals, f.Definitions); f.StartMatch();
        for (int i = 0; i < 3; i++) { Assert.True(f.Work(f.Crew[0], "task-" + i).Accepted); f.Advance(1000); }
        for (int i = 0; i < 2; i++) { Assert.True(f.Work(f.Crew[1], "task-" + i).Accepted); f.Advance(1000); }
        Assert.Equal("tasks_completed", f.Authority.State().WinReason); Assert.Equal(5u, f.Authority.Summary()!.CrewTasksCompleted);
    }

    private static SocialActionResultEvent Step(Fixture f, string actor, SocialTaskChoice choice, ulong? revision = null)
    {
        var work = f.Authority.TaskWork(actor);
        return Reply(f.Do(actor, new SocialTaskStepCommand { Context = f.Context(), WorkId = work.WorkId,
            StepRevision = revision ?? work.StepRevision, Choice = choice }));
    }
    [Fact]
    public void WaitingRosterIsPublicReadinessAndBecomesStableEmptyAfterAssignment()
    {
        var f = new Fixture(start: false);
        var before = f.Authority.Waiting(f.Players[0].Id);
        Assert.Equal(4, before.Players.Count); Assert.Equal(0u, before.ReadyCount);
        Assert.All(before.Players, p => Assert.True(p.Connected));
        f.Ready(f.Players[0].Id);
        var after = f.Authority.Waiting(f.Players[1].Id);
        Assert.Equal(1u, after.ReadyCount); Assert.True(after.ServerRevision > before.ServerRevision);
        foreach (var p in f.Players.Skip(1)) f.Ready(p.Id);
        var cleared = f.Authority.Waiting(f.Players[0].Id);
        Assert.Empty(cleared.Players); Assert.Equal(0ul, cleared.ServerRevision);
        f.Advance(1000); f.Kill(f.Crew[0]);
        Assert.Equal(cleared, f.Authority.Waiting(f.Players[0].Id));
    }
    [Fact]
    public void CalibrationRequiresThreeTimedExactRevisionChoicesAndRetainsOriginalCompletionCorrelation()
    {
        var f = new Fixture(start: false); f.Definitions[0].Mechanic = SocialTaskMechanic.CircuitCalibration;
        f.Authority.SetWorld(f.Terminals, f.Definitions); f.StartMatch(); string actor = f.Crew[0];
        var start = f.Work(actor); Assert.True(start.Accepted); Assert.False(start.Completed);
        Assert.Equal(3u, f.Authority.Tasks(actor).Tasks[0].StepCount);
        Assert.Equal("task_step_not_ready", Step(f, actor, f.Authority.TaskWork(actor).RequiredChoice).ReasonCode);
        for (int i = 0; i < 3; i++)
        {
            f.Advance(2500); var work = f.Authority.TaskWork(actor);
            if (i == 1) Assert.Equal("task_step_changed", Step(f, actor, work.RequiredChoice, work.StepRevision - 1).ReasonCode);
            var deliveries = f.Do(actor, new SocialTaskStepCommand { Context = f.Context(), WorkId = work.WorkId,
                StepRevision = work.StepRevision, Choice = work.RequiredChoice });
            var reply = Reply(deliveries); Assert.True(reply.Accepted); Assert.Equal(SocialAction.TaskStep, reply.Action);
            Assert.Equal(start.WorkId, reply.WorkId); Assert.Equal(i == 2, reply.Completed);
            if (i < 2) Assert.Equal(0u, f.Authority.State().CrewTasksCompleted);
            else Assert.Contains(deliveries, d => d.Payload is SocialActionResultEvent result && result.Action == SocialAction.WorkTask
                && result.CorrelationId == start.CorrelationId && result.Accepted && result.Completed);
        }
        Assert.Equal(1u, f.Authority.State().CrewTasksCompleted); Assert.Empty(f.Authority.TaskWork(actor).WorkId);
    }
    [Fact]
    public void ObservationWithholdsSecondSampleAndRequiresActualTrendSelection()
    {
        var f = new Fixture(start: false); f.Definitions[0].Mechanic = SocialTaskMechanic.SensorObservation;
        f.Authority.SetWorld(f.Terminals, f.Definitions); f.StartMatch(); string actor = f.Crew[0];
        var start = f.Work(actor); var initial = f.Authority.TaskWork(actor);
        Assert.False(initial.SampleBReady); Assert.Equal(0, initial.SampleB);
        Assert.Equal("task_step_not_ready", Step(f, actor, SocialTaskChoice.Stable).ReasonCode);
        f.Advance(8000); var sampled = f.Authority.TaskWork(actor); Assert.True(sampled.SampleBReady);
        SocialTaskChoice correct = sampled.SampleB > sampled.SampleA ? SocialTaskChoice.Rising
            : sampled.SampleB < sampled.SampleA ? SocialTaskChoice.Falling : SocialTaskChoice.Stable;
        var wrong = correct == SocialTaskChoice.Rising ? SocialTaskChoice.Falling : SocialTaskChoice.Rising;
        Assert.False(Step(f, actor, wrong).Accepted); Assert.Equal(0u, f.Authority.State().CrewTasksCompleted);
        var completion = f.Do(actor, new SocialTaskStepCommand { Context = f.Context(), WorkId = sampled.WorkId,
            StepRevision = sampled.StepRevision, Choice = correct });
        Assert.True(Reply(completion).Completed);
        Assert.Contains(completion, d => d.Payload is SocialActionResultEvent e && e.CorrelationId == start.CorrelationId && e.Completed && e.Accepted);
        Assert.Equal(1u, f.Authority.State().CrewTasksCompleted);
    }
    [Fact]
    public void InteractiveTasksTimeoutOrCancelWithoutImplicitCompletionAndFakeWorkNeverCounts()
    {
        var f = new Fixture(start: false); f.Definitions[0].Mechanic = SocialTaskMechanic.CircuitCalibration;
        f.Authority.SetWorld(f.Terminals, f.Definitions); f.StartMatch();
        string actor = f.Crew[0]; var start = f.Work(actor); var terminal = f.Advance(37500);
        Assert.Contains(terminal, d => d.Payload is SocialActionResultEvent e && e.CorrelationId == start.CorrelationId
            && !e.Accepted && !e.Completed && e.ReasonCode == "task_timed_out");
        Assert.Equal(0u, f.Authority.State().CrewTasksCompleted); Assert.Empty(f.Authority.TaskWork(actor).WorkId);
        start = f.Work(actor);
        var cancel = Reply(f.Do(actor, new SocialCancelWorkCommand { Context = f.Context(), WorkId = start.CorrelationId }));
        Assert.True(cancel.Accepted); Assert.Empty(f.Authority.TaskWork(actor).WorkId);
        string fake = f.Impostor; Assert.True(f.Work(fake).Accepted);
        for (int i = 0; i < 3; i++) { f.Advance(2500); Assert.True(Step(f, fake, f.Authority.TaskWork(fake).RequiredChoice).Accepted); }
        Assert.Equal(0u, f.Authority.State().CrewTasksCompleted); Assert.Empty(f.Authority.TaskWork(fake).WorkId);
    }
    [Fact]
    public void TransportGraceIsStoppedVulnerableAndResumeCannotResurrectVictim()
    {
        var f = new Fixture(); string actor = f.Crew[0]; var pose = f.Pose(actor); pose.ResumeEligible = true; f.Refresh();
        var original = f.Authority.OwnState(actor); f.Players.Remove(pose); f.Refresh();
        Assert.False(f.Authority.CanMove(actor)); Assert.Equal(SocialLife.Alive, f.Authority.OwnState(actor).Life);
        Assert.Contains(f.Authority.VisibleWorld(f.Impostor, f.Now).Actors, p => p.ActorId == actor);
        Assert.True(f.Kill(actor).Accepted); Assert.Equal(SocialLife.Dead, f.Authority.OwnState(actor).Life);
        pose.AuthenticatedResume = true; pose.ConnectionGeneration = 2; pose.ConnectionEpoch += 100; f.Players.Add(pose); f.Refresh();
        var resumed = f.Authority.OwnState(actor);
        Assert.Equal(original.Alignment, resumed.Alignment); Assert.Equal(original.Duty, resumed.Duty);
        Assert.Equal(SocialLife.Dead, resumed.Life); Assert.False(resumed.CanMove); Assert.Equal(2ul, resumed.ConnectionGeneration);
        var command = new SocialReadyCommand { Context = f.Context("old-generation"), Ready = true };
        Assert.Equal("connection_changed", Reply(f.Do(actor, command)).ReasonCode);
    }
    [Fact]
    public void ResumePreservesFinalBallotAndExpiresToIrreversibleForfeit()
    {
        var f = new Fixture(); string actor = f.Crew[0]; var pose = f.Pose(actor); pose.ResumeEligible = true; f.Refresh();
        f.Voting(); Assert.True(f.Vote(actor, skip: true).Accepted); f.Players.Remove(pose); f.Refresh();
        Assert.True(f.Authority.Meeting(actor).OwnBallotSubmitted);
        pose.AuthenticatedResume = true; pose.ConnectionGeneration = 2; pose.ConnectionEpoch += 100; f.Players.Add(pose); f.Refresh();
        Assert.True(f.Authority.Meeting(actor).OwnBallotSubmitted); Assert.True(f.Authority.Meeting(actor).OwnSkip);
        f.Players.Remove(pose); f.Refresh(); f.Advance(30000);
        Assert.Equal(SocialLife.Disconnected, f.Authority.OwnState(actor).Life);
        pose.ConnectionGeneration = 3; pose.ConnectionEpoch += 100; f.Players.Add(pose); f.Refresh();
        Assert.Equal(SocialLife.Disconnected, f.Authority.OwnState(actor).Life); Assert.False(f.Authority.CanMove(actor));
    }
    [Fact]
    public void KillPresentationAudienceContainsNoForeignRecipientAndVictimNeverGetsAttacker()
    {
        var f = new Fixture(); string victim = f.Crew[0], far = f.Crew[1]; f.Pose(far).X = 20; f.Refresh();
        var deliveries = f.Do(f.Impostor, new SocialKillCommand { Context = f.Context(), TargetActorId = victim });
        var presentations = deliveries.Where(d => d.Payload is SocialKillPresentationEvent).ToArray();
        Assert.DoesNotContain(presentations, d => d.Recipient == far);
        Assert.Contains(presentations, d => d.Recipient == f.Impostor);
        var own = (SocialKillPresentationEvent)Assert.Single(presentations, d => d.Recipient == victim).Payload;
        Assert.Empty(own.AttackerActorId); Assert.Equal(victim, own.VictimActorId);
        Assert.All(presentations, d => Assert.Equal(d.Recipient, ((SocialKillPresentationEvent)d.Payload).RecipientActorId));
        Assert.Single(presentations.Select(d => ((SocialKillPresentationEvent)d.Payload).PresentationId).Distinct());
    }

    [Fact]
    public void FreshGenerationMayStartNewWorkButOldGenerationCannotReplayAnAcceptedCommand()
    {
        var f=new Fixture();string actor=f.Crew[0];var pose=f.Pose(actor);pose.ResumeEligible=true;f.Refresh();
        var oldCommand=new SocialWorkTaskCommand{Context=f.Context("old-work-request"),TaskId="task-0"};
        Assert.True(Reply(f.Do(actor,oldCommand)).Accepted);f.Players.Remove(pose);f.Refresh();
        Assert.Empty(f.Authority.TaskWork(actor).WorkId);f.Advance(5000);Assert.Equal(0u,f.Authority.State().CrewTasksCompleted);
        pose.ConnectionGeneration=2;pose.ConnectionEpoch+=100;pose.AuthenticatedResume=true;f.Players.Add(pose);f.Refresh();
        Assert.Equal("connection_changed",Reply(f.Do(actor,oldCommand)).ReasonCode);
        var fresh=f.Context();fresh.ConnectionGeneration=2;
        Assert.True(Reply(f.Do(actor,new SocialWorkTaskCommand{Context=fresh,TaskId="task-0"})).Accepted);
        f.Advance(1000);Assert.Equal(1u,f.Authority.State().CrewTasksCompleted);
    }
    [Fact]
    public void ExplicitControlLossCannotUseTransportGraceToComeBackAlive()
    {
        var f=new Fixture();string actor=f.Crew[0];var pose=f.Pose(actor);pose.ResumeEligible=true;f.Refresh();
        f.Authority.ControlUnavailable(actor,f.Now);Assert.Equal(SocialLife.Disconnected,f.Authority.OwnState(actor).Life);
        f.Players.Remove(pose);f.Refresh();pose.AuthenticatedResume=true;pose.ConnectionGeneration=2;pose.ConnectionEpoch+=100;
        f.Players.Add(pose);f.Refresh();Assert.Equal(SocialLife.Disconnected,f.Authority.OwnState(actor).Life);Assert.False(f.Authority.CanMove(actor));
    }
}
