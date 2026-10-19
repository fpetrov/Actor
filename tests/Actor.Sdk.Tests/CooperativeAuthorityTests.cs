using Actor.Networking;
using Actor.Protocol.Standard.Communication.V1;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;

namespace Actor.Sdk.Tests;

public sealed class CooperativeAuthorityTests
{
    private sealed class Fixture
    {
        internal const long Time = 1790000000000;
        internal readonly string Match = Guid.NewGuid().ToString("N");
        internal readonly CooperativeAuthority Authority;
        internal readonly CooperativeAuthority.Participant A = new() { Id = "coordinator", Name = "A", Room = "main-hall", X = .9, Y = -1.94 };
        internal readonly CooperativeAuthority.Participant B = new() { Id = "technician", Name = "B", Room = "main-hall", X = 0, Y = -.94 };
        internal readonly CooperativeAuthority.Participant C = new() { Id = "observer", Name = "C", Room = "main-hall", X = -1.8, Y = -.94 };
        internal Fixture(Func<string, string, bool>? taskScope = null, Func<string, SendMessageCommand, string>? messagePolicy = null)
        { Authority = new(Match, taskScope!, messagePolicy!); Refresh(Time); }
        internal List<CooperativeAuthority.Delivery> Refresh(long now) => Authority.Refresh(new[] { A, B, C }, now);
        internal SendMessageCommand Send(string text = "Hello", string correlation = "message", string recipient = "technician")
            => new() { Text = text, CorrelationId = correlation, RecipientActorId = recipient, ChannelId = "direct", MatchId = Match, TtlMs = 300000 };
        internal List<CooperativeAuthority.Delivery> Start()
            => Authority.Execute(A.Id, new StartCalibrationCommand { TechnicianActorId = B.Id, CorrelationId = "start", MatchId = Match }, Time);
        internal string Task => Authority.StateFor(A.Id).TaskId;
        internal DiagnosticResultEvent Inspect(long now = Time + 1000)
            => Authority.Execute(A.Id, new InspectDiagnosticCommand { TaskId = Task, CorrelationId = "inspect", MatchId = Match }, now)
                .Select(x => x.Payload).OfType<DiagnosticResultEvent>().Single();
        internal List<CooperativeAuthority.Delivery> Submit(string code, long now, string actor = "technician", string correlation = "submit")
            => Authority.Execute(actor, new SubmitCalibrationCommand { TaskId = Task, AuthorizationCode = code, CorrelationId = correlation, MatchId = Match }, now);
        internal void AtEngineering() { B.Room = "engineering"; B.X = 0; B.Y = -6.54; Refresh(Time + 1500); }
        internal MessageReceivedEvent Transfer(string code, bool acknowledge = true)
        {
            var request = Send(code); request.TaskId = Task;
            var message = Authority.Execute(A.Id, request, Time + 2000).Select(x => x.Payload).OfType<MessageReceivedEvent>().Single();
            if (acknowledge) Authority.Acknowledge(B.Id, message.MessageId, Match, Time + 2100);
            return message;
        }
    }
    private static T Payload<T>(IEnumerable<CooperativeAuthority.Delivery> d) where T : IMessage => d.Select(x => x.Payload).OfType<T>().Single();

    [Fact]
    public void DistinctAssignmentAndPrivateStateNeverExposeDiagnosticToObserver()
    {
        var f = new Fixture(); var output = f.Start();
        Assert.Equal(new[] { "coordinator", "technician" }, output.Where(x => x.Payload is CalibrationAssignmentEvent).Select(x => x.Recipient).Order());
        Assert.Equal(CalibrationStatus.AwaitingDiagnostic, f.Authority.StateFor(f.A.Id).Status);
        Assert.Empty(f.Authority.StateFor(f.C.Id).TaskId);
        Assert.Equal(CalibrationStatus.AwaitingDiagnostic, f.Authority.Progress().Status);
        Assert.DoesNotContain(output, x => x.Recipient == f.C.Id && (x.Payload is CalibrationAssignmentEvent || x.Payload is CalibrationTaskChangedEvent));
        var own = new Fixture();
        var failure = own.Authority.Execute(own.A.Id, new StartCalibrationCommand { TechnicianActorId = own.A.Id, CorrelationId = "same", MatchId = own.Match }, Fixture.Time);
        Assert.Equal("distinct_partner_required", Payload<CalibrationResultEvent>(failure).ReasonCode);
    }
    [Fact]
    public void OnlyNearbyAssignedCoordinatorCanReadPrivate128BitChallenge()
    {
        var f = new Fixture(); f.Start(); var diagnostic = f.Inspect();
        Assert.True(diagnostic.Accepted); Assert.True(diagnostic.Sensitive); Assert.Equal(32, diagnostic.AuthorizationCode.Length);
        Assert.DoesNotContain(diagnostic.AuthorizationCode, f.Authority.StateFor(f.A.Id).ToString());
        Assert.DoesNotContain(diagnostic.AuthorizationCode, f.Authority.Progress().ToString());
        foreach (string actor in new[] { f.B.Id, f.C.Id })
        {
            var denied = f.Authority.Execute(actor, new InspectDiagnosticCommand { TaskId = f.Task, CorrelationId = "inspect-denied", MatchId = f.Match }, Fixture.Time + 2000);
            Assert.False(Payload<DiagnosticResultEvent>(denied).Accepted); Assert.Empty(Payload<DiagnosticResultEvent>(denied).AuthorizationCode);
        }
        f.A.Room = "engineering"; f.Refresh(Fixture.Time + 2500);
        Assert.Equal("diagnostic_out_of_range", Payload<DiagnosticResultEvent>(f.Authority.Execute(f.A.Id,
            new InspectDiagnosticCommand { TaskId = f.Task, CorrelationId = "wrong-room", MatchId = f.Match }, Fixture.Time + 3000)).ReasonCode);
    }
    [Fact]
    public void CompletionRequiresActualPrivateTransferAcknowledgedByTechnician()
    {
        var f = new Fixture(); f.Start(); string code = f.Inspect().AuthorizationCode; f.AtEngineering();
        Assert.Equal("authorization_not_delivered", Payload<CalibrationResultEvent>(f.Submit(code, Fixture.Time + 1900, correlation: "before")).ReasonCode);
        var message = f.Transfer(code, false);
        Assert.True(message.Sensitive); Assert.Equal(f.Task, message.TaskId);
        Assert.Equal("authorization_not_delivered", Payload<CalibrationResultEvent>(f.Submit(code, Fixture.Time + 2200, correlation: "unacked")).ReasonCode);
        Assert.Empty(f.Authority.Acknowledge(f.C.Id, message.MessageId, f.Match, Fixture.Time + 2300));
        f.Authority.Acknowledge(f.B.Id, message.MessageId, f.Match, Fixture.Time + 2400);
        var result = f.Submit(code, Fixture.Time + 3000);
        Assert.True(Payload<CalibrationResultEvent>(result).Accepted);
        Assert.Equal(CalibrationStatus.Completed, f.Authority.Progress().Status);
        Assert.Contains(result, d => d.Recipient == f.C.Id && d.Payload is CalibrationProgressObservation p && p.Status == CalibrationStatus.Completed);
        Assert.DoesNotContain(result, d => d.Recipient == f.C.Id && (d.Payload is DiagnosticResultEvent || d.Payload is CalibrationTaskChangedEvent));
        Assert.DoesNotContain(result, d => d.Payload.ToString()!.Contains(code, StringComparison.Ordinal));
    }
    [Fact]
    public void CoordinatorWrongRoomWrongCodeAndCooldownCannotCompleteTask()
    {
        var f = new Fixture(); f.Start(); string code = f.Inspect().AuthorizationCode;
        Assert.Equal("technician_required", Payload<CalibrationResultEvent>(f.Submit(code, Fixture.Time + 2000, "coordinator", "wrong-role")).ReasonCode);
        Assert.Equal("calibration_out_of_range", Payload<CalibrationResultEvent>(f.Submit(code, Fixture.Time + 2100, correlation: "wrong-room")).ReasonCode);
        f.AtEngineering(); f.Transfer(code);
        for (int i = 0; i < 3; ++i)
        {
            long now = Fixture.Time + 4000 + 2200 * i;
            Assert.Equal("invalid_authorization", Payload<CalibrationResultEvent>(f.Submit("incorrect", now, correlation: "bad" + i)).ReasonCode);
            if (i == 0) Assert.Equal("attempt_cooldown", Payload<CalibrationResultEvent>(f.Submit(code, now + 100, correlation: "too-soon")).ReasonCode);
        }
        Assert.Equal(CalibrationStatus.Failed, f.Authority.Progress().Status);
        Assert.Equal(0u, f.Authority.StateFor(f.B.Id).AttemptsRemaining);
    }
    [Fact]
    public void SecretAndDeclaredSensitiveMessagesCannotUseStationChannel()
    {
        var f = new Fixture(); f.Start(); string code = f.Inspect().AuthorizationCode;
        var request = f.Send(code); request.ChannelId = "station"; request.RecipientActorId = "";
        Assert.Equal("authorization_requires_private_partner", Payload<MessageDeliveryResultEvent>(f.Authority.Execute(f.A.Id, request, Fixture.Time + 2000)).ReasonCode);
        var explicitSensitive = f.Send("private", "sensitive"); explicitSensitive.ChannelId = "station"; explicitSensitive.RecipientActorId = ""; explicitSensitive.Sensitive = true;
        Assert.Equal("sensitive_requires_direct", Payload<MessageDeliveryResultEvent>(f.Authority.Execute(f.A.Id, explicitSensitive, Fixture.Time + 3000)).ReasonCode);
        var third = f.Send(code, "third", "observer");
        Assert.Equal("authorization_requires_private_partner", Payload<MessageDeliveryResultEvent>(f.Authority.Execute(f.A.Id, third, Fixture.Time + 4000)).ReasonCode);
    }
    [Fact]
    public void DirectMessageContentOnlyGoesToRecipientAndSenderGetsReceipt()
    {
        var f = new Fixture(); var command = f.Send();
        var output = f.Authority.Execute(f.A.Id, command, Fixture.Time);
        Assert.Equal(f.B.Id, output.Single(d => d.Payload is MessageReceivedEvent).Recipient);
        Assert.Equal(f.A.Id, output.Single(d => d.Payload is MessageDeliveryResultEvent).Recipient);
        Assert.DoesNotContain(output, d => d.Recipient == f.C.Id);
        var message = Payload<MessageReceivedEvent>(output);
        Assert.Equal(f.A.Id, message.SenderActorId); Assert.Equal(f.B.Id, message.RecipientActorId);
        Assert.Equal(MessageDeliveryStage.ServerAccepted, Payload<MessageDeliveryResultEvent>(output).Stage);
        Assert.Empty(f.Authority.Execute("forged-actor", command, Fixture.Time + 1));
        var ack = f.Authority.Acknowledge(f.B.Id, message.MessageId, f.Match, Fixture.Time + 20);
        Assert.Equal(f.A.Id, ack.Single().Recipient); Assert.Equal(MessageDeliveryStage.ClientReceived, Payload<MessageDeliveryResultEvent>(ack).Stage);
    }
    [Fact]
    public void BroadcastUsesActualRecipientIdentityAndAllClientAcknowledgements()
    {
        var f = new Fixture(); var request = f.Send(); request.ChannelId = "station"; request.RecipientActorId = "";
        var output = f.Authority.Execute(f.A.Id, request, Fixture.Time);
        var messages = output.Where(d => d.Payload is MessageReceivedEvent).ToArray(); Assert.Equal(3, messages.Length);
        foreach (var d in messages) Assert.Equal(d.Recipient, ((MessageReceivedEvent)d.Payload).RecipientActorId);
        string id = ((MessageReceivedEvent)messages[0].Payload).MessageId;
        Assert.Empty(f.Authority.Acknowledge(f.A.Id, id, f.Match, Fixture.Time + 10));
        Assert.Empty(f.Authority.Acknowledge(f.B.Id, id, f.Match, Fixture.Time + 20));
        Assert.Equal(MessageDeliveryStage.ClientReceived, Payload<MessageDeliveryResultEvent>(f.Authority.Acknowledge(f.C.Id, id, f.Match, Fixture.Time + 30)).Stage);
    }
    [Fact]
    public void ReplayNeverDuplicatesPayloadAndConflictingTypesCannotAlias()
    {
        var f = new Fixture(); var command = f.Send(); var first = f.Authority.Execute(f.A.Id, command, Fixture.Time);
        var replay = f.Authority.Execute(f.A.Id, command, Fixture.Time + 10);
        Assert.DoesNotContain(replay, d => d.Payload is MessageReceivedEvent);
        Assert.Equal(Payload<MessageDeliveryResultEvent>(first).MessageId, Payload<MessageDeliveryResultEvent>(replay).MessageId);
        command.Text = "different";
        Assert.Equal("correlation_conflict", Payload<MessageDeliveryResultEvent>(f.Authority.Execute(f.A.Id, command, Fixture.Time + 20)).ReasonCode);
        f.Start(); f.Inspect();
        var conflict = f.Authority.Execute(f.A.Id, new CancelCalibrationCommand { TaskId = f.Task, CorrelationId = "inspect", MatchId = f.Match }, Fixture.Time + 2000);
        Assert.Equal("correlation_conflict", Payload<CalibrationResultEvent>(conflict).ReasonCode);
        Assert.True(f.Authority.Active);
    }
    [Theory]
    [InlineData(false)] [InlineData(true)]
    public void ReceiptReplayRetainsTimeoutOrDisconnectedOutcome(bool disconnect)
    {
        var f = new Fixture(); var request = f.Send(); f.Authority.Execute(f.A.Id, request, Fixture.Time);
        if (disconnect) f.Authority.Refresh(new[] { f.A, f.C }, Fixture.Time + 500);
        else f.Authority.Tick(Fixture.Time + 10001);
        var replay = Payload<MessageDeliveryResultEvent>(f.Authority.Execute(f.A.Id, request, Fixture.Time + 11000));
        Assert.Equal(MessageDeliveryStage.Rejected, replay.Stage);
        Assert.Equal(disconnect ? "recipient_disconnected" : "client_ack_timeout", replay.ReasonCode);
    }
    [Fact]
    public void ExpiryDisconnectAndRuntimeLossCancelActiveButPreserveCompleted()
    {
        var f = new Fixture(); f.Start(); f.Inspect();
        f.Authority.Tick(Fixture.Time + 300001); Assert.Equal(CalibrationStatus.Expired, f.Authority.Progress().Status);
        var expired = f.Authority.Execute(f.A.Id, new InspectDiagnosticCommand { TaskId = f.Task, CorrelationId = "late", MatchId = f.Match }, Fixture.Time + 300002);
        Assert.Empty(Payload<DiagnosticResultEvent>(expired).AuthorizationCode);
        var disconnected = new Fixture(); disconnected.Start(); disconnected.Authority.Refresh(new[] { disconnected.A, disconnected.C }, Fixture.Time + 1000);
        Assert.Equal(CalibrationStatus.Cancelled, disconnected.Authority.Progress().Status);
        var control = new Fixture(); control.Start(); control.Authority.ControlUnavailable(control.A.Id);
        Assert.Equal(CalibrationStatus.Cancelled, control.Authority.Progress().Status);
        var completed = new Fixture(); completed.Start(); string code = completed.Inspect().AuthorizationCode;
        completed.AtEngineering(); completed.Transfer(code); completed.Submit(code, Fixture.Time + 3000);
        completed.Authority.ControlUnavailable(completed.A.Id); completed.Authority.Refresh(new[] { completed.C }, Fixture.Time + 4000);
        Assert.Equal(CalibrationStatus.Completed, completed.Authority.Progress().Status);
    }
    [Theory]
    [InlineData("channel", "channel_unavailable")]
    [InlineData("recipient", "recipient_unavailable")]
    [InlineData("utf8", "invalid_text")]
    [InlineData("ttl", "invalid_ttl")]
    [InlineData("match", "match_mismatch")]
    [InlineData("correlation", "invalid_correlation")]
    [InlineData("task", "invalid_identifier")]
    public void MalformedRequestsHaveBoundedStructuredRejections(string field, string reason)
    {
        var f = new Fixture(); var request = f.Send();
        switch (field)
        {
            case "channel": request.ChannelId = "unknown"; break;
            case "recipient": request.RecipientActorId = "offline"; break;
            case "utf8": request.Text = new string('я', 257); break;
            case "ttl": request.TtlMs = 300001; break;
            case "match": request.MatchId = Guid.NewGuid().ToString("N"); break;
            case "correlation": request.CorrelationId = new string('x', 1000); break;
            case "task": request.TaskId = new string('x', 1000); break;
        }
        var result = Payload<MessageDeliveryResultEvent>(f.Authority.Execute(f.A.Id, request, Fixture.Time));
        Assert.Equal(reason, result.ReasonCode); Assert.False(result.AcceptedByServer);
        Assert.True(result.CorrelationId.Length <= 64 && result.TaskId.Length <= 64 && result.RecipientActorId.Length <= 64);
    }
    [Fact]
    public void SpamIsBoundedAndOldMatchCommandsCannotAffectNewMatch()
    {
        var f = new Fixture();
        var responses = Enumerable.Range(0, 100).SelectMany(i => f.Authority.Execute(f.A.Id, f.Send(correlation: "spam" + i), Fixture.Time)).ToArray();
        Assert.InRange(responses.Count(d => d.Payload is MessageReceivedEvent), 1, 4);
        Assert.Contains(responses, d => d.Payload is MessageDeliveryResultEvent r && r.ReasonCode == "rate_limited");
        var next = new Fixture();
        Assert.Equal("match_mismatch", Payload<MessageDeliveryResultEvent>(next.Authority.Execute(next.A.Id, f.Send(), Fixture.Time)).ReasonCode);
    }

    [Theory]
    [InlineData("expired", CalibrationStatus.Expired)]
    [InlineData("cancelled", CalibrationStatus.Cancelled)]
    [InlineData("completed", CalibrationStatus.Completed)]
    public void TerminalTaskNeverReplaysPreviouslyCachedPrivateDiagnostic(string terminal, CalibrationStatus expected)
    {
        var f = new Fixture(); f.Start(); string code = f.Inspect().AuthorizationCode;
        long now = Fixture.Time + 4000;
        switch (terminal)
        {
            case "expired": now = Fixture.Time + 300001; f.Authority.Tick(now); break;
            case "cancelled": f.Authority.ControlUnavailable(f.A.Id); break;
            case "completed": f.AtEngineering(); f.Transfer(code); f.Submit(code, Fixture.Time + 3000); break;
        }
        Assert.Equal(expected, f.Authority.Progress().Status);
        var replay = f.Authority.Execute(f.A.Id, new InspectDiagnosticCommand
            { TaskId = f.Task, CorrelationId = "inspect", MatchId = f.Match }, now + 1);
        var diagnostic = Payload<DiagnosticResultEvent>(replay);
        Assert.False(diagnostic.Accepted); Assert.Empty(diagnostic.AuthorizationCode);
        Assert.DoesNotContain(replay, d => d.Payload.ToString()!.Contains(code, StringComparison.Ordinal));
        Assert.Equal(expected, f.Authority.Progress().Status);
    }

    [Theory]
    [InlineData(100, 100)]
    [InlineData(double.NaN, -.65)]
    [InlineData(double.PositiveInfinity, -.65)]
    [InlineData(1, double.NegativeInfinity)]
    public void ClaimedRoomDoesNotOverrideDistanceOrNonfiniteAuthoritativePose(double x, double y)
    {
        var start = new Fixture(); start.A.X = x; start.A.Y = y; start.Refresh(Fixture.Time);
        Assert.Equal("diagnostic_out_of_range", Payload<CalibrationResultEvent>(start.Start()).ReasonCode);
        Assert.Equal(CalibrationStatus.Available, start.Authority.Progress().Status);
        var inspect = new Fixture(); inspect.Start(); inspect.A.X = x; inspect.A.Y = y; inspect.Refresh(Fixture.Time + 500);
        var denied = inspect.Inspect();
        Assert.Equal("diagnostic_out_of_range", denied.ReasonCode); Assert.Empty(denied.AuthorizationCode);
        Assert.Equal(CalibrationStatus.AwaitingDiagnostic, inspect.Authority.Progress().Status);
        var submit = new Fixture(); submit.Start(); string code = submit.Inspect().AuthorizationCode;
        submit.AtEngineering(); submit.Transfer(code); submit.B.X = x; submit.B.Y = y; submit.Refresh(Fixture.Time + 2500);
        Assert.Equal("calibration_out_of_range", Payload<CalibrationResultEvent>(submit.Submit(code, Fixture.Time + 3000)).ReasonCode);
        Assert.Equal(3u, submit.Authority.StateFor(submit.B.Id).AttemptsRemaining);
    }

    [Theory]
    [InlineData("task", "invalid_identifier")]
    [InlineData("code", "invalid_identifier")]
    [InlineData("other-task", "task_not_found")]
    public void MalformedCalibrationIdentifiersDoNotConsumeAttemptsOrChangeTask(string field, string reason)
    {
        var f = new Fixture(); f.Start(); string code = f.Inspect().AuthorizationCode; f.AtEngineering(); f.Transfer(code);
        var command = new SubmitCalibrationCommand { TaskId = f.Task, AuthorizationCode = code, CorrelationId = "malformed", MatchId = f.Match };
        if (field == "task") command.TaskId = new string('x', 1000);
        else if (field == "code") command.AuthorizationCode = new string('x', 1000);
        else command.TaskId = Guid.NewGuid().ToString("N");
        var result = Payload<CalibrationResultEvent>(f.Authority.Execute(f.B.Id, command, Fixture.Time + 3000));
        Assert.False(result.Accepted); Assert.Equal(reason, result.ReasonCode); Assert.True(result.TaskId.Length <= 64);
        Assert.Equal(CalibrationStatus.AwaitingAuthorization, f.Authority.Progress().Status);
        Assert.Equal(3u, f.Authority.StateFor(f.B.Id).AttemptsRemaining);
        Assert.True(Payload<CalibrationResultEvent>(f.Submit(code, Fixture.Time + 3500, correlation: "valid")).Accepted);
    }

    [Fact]
    public void UnassignedObserverCannotCancelTaskAndCompletedRequestReplayCannotRestartIt()
    {
        var f = new Fixture(); f.Start(); string code = f.Inspect().AuthorizationCode;
        var denied = f.Authority.Execute(f.C.Id, new CancelCalibrationCommand
            { TaskId = f.Task, CorrelationId = "cancel", MatchId = f.Match }, Fixture.Time + 1500);
        Assert.Equal("task_not_assigned", Payload<CalibrationResultEvent>(denied).ReasonCode);
        f.AtEngineering(); f.Transfer(code); f.Submit(code, Fixture.Time + 3000);
        string task = f.Task; ulong revision = f.Authority.Progress().ServerRevision;
        var startReplay = f.Authority.Execute(f.A.Id, new StartCalibrationCommand
            { TechnicianActorId = f.B.Id, CorrelationId = "start", MatchId = f.Match }, Fixture.Time + 4000);
        Assert.DoesNotContain(startReplay, d => d.Payload is CalibrationAssignmentEvent);
        var submitReplay = f.Submit(code, Fixture.Time + 4500);
        Assert.True(Payload<CalibrationResultEvent>(submitReplay).Accepted);
        Assert.DoesNotContain(submitReplay, d => d.Payload is CalibrationTaskChangedEvent);
        Assert.Equal(task, f.Task); Assert.Equal(revision, f.Authority.Progress().ServerRevision);
        Assert.Equal(CalibrationStatus.Completed, f.Authority.Progress().Status);
    }

    [Fact]
    public void PendingDeliveriesRejectAtCapacityAndRecoverAfterAcknowledgementDeadline()
    {
        var f = new Fixture(); var output = new List<CooperativeAuthority.Delivery>(); int index = 0;
        void SendFrom(string actor, long now)
        {
            string recipient = actor == f.C.Id ? f.A.Id : f.C.Id;
            output.AddRange(f.Authority.Execute(actor, f.Send(correlation: "pending" + index++, recipient: recipient), now));
        }
        for (int burst = 0; burst < 4; ++burst)
            foreach (string actor in new[] { f.A.Id, f.B.Id, f.C.Id }) SendFrom(actor, Fixture.Time);
        for (int step = 1; step <= 18; ++step)
            foreach (string actor in new[] { f.A.Id, f.B.Id, f.C.Id }) SendFrom(actor, Fixture.Time + step * 500);
        Assert.Equal(CooperativeAuthority.MaxPending, output.Count(d => d.Payload is MessageReceivedEvent));
        Assert.Contains(output, d => d.Payload is MessageDeliveryResultEvent r && r.ReasonCode == "delivery_capacity");
        var expired = f.Authority.Tick(Fixture.Time + 10001);
        Assert.Equal(12, expired.Count(d => d.Payload is MessageDeliveryResultEvent r && r.ReasonCode == "client_ack_timeout"));
        var recovered = f.Authority.Execute(f.A.Id, f.Send(correlation: "recovered"), Fixture.Time + 10002);
        Assert.Equal(MessageDeliveryStage.ServerAccepted, Payload<MessageDeliveryResultEvent>(recovered).Stage);
        Assert.Single(recovered, d => d.Payload is MessageReceivedEvent);
    }

    [Fact]
    public void ReplayCacheRejectsNewRequestsAtBoundAndRecoversAfterExpiry()
    {
        var f = new Fixture();
        for (int i = 0; i < CooperativeAuthority.MaxReplays; ++i)
        {
            long now = Fixture.Time + i * 500;
            var output = f.Authority.Execute(f.A.Id, f.Send(correlation: "history" + i), now);
            var message = Payload<MessageReceivedEvent>(output);
            f.Authority.Acknowledge(f.B.Id, message.MessageId, f.Match, now + 1);
        }
        var full = f.Authority.Execute(f.A.Id, f.Send(correlation: "one-too-many"), Fixture.Time + 256000);
        Assert.Equal("request_capacity", Payload<MessageDeliveryResultEvent>(full).ReasonCode);
        Assert.DoesNotContain(full, d => d.Payload is MessageReceivedEvent);
        f.Authority.Tick(Fixture.Time + 600000);
        var recovered = f.Authority.Execute(f.A.Id, f.Send(correlation: "after-expiry"), Fixture.Time + 600001);
        Assert.Equal(MessageDeliveryStage.ServerAccepted, Payload<MessageDeliveryResultEvent>(recovered).Stage);
        Assert.Single(recovered, d => d.Payload is MessageReceivedEvent);
    }

    [Fact]
    public void MissingPhysicalTerminalRejectsStartInspectAndSubmitWithoutConsumingAttempts()
    {
        var start = new Fixture(); start.Authority.SetTerminals(false, 1, -.65, true, .25, -6.54);
        Assert.Equal("terminal_unavailable", Payload<CalibrationResultEvent>(start.Start()).ReasonCode);
        Assert.Equal(CalibrationStatus.Available, start.Authority.Progress().Status);
        var inspect = new Fixture(); inspect.Start(); inspect.Authority.SetTerminals(false, 1, -.65, true, .25, -6.54);
        Assert.Equal("terminal_unavailable", inspect.Inspect().ReasonCode);
        Assert.Equal(CalibrationStatus.AwaitingDiagnostic, inspect.Authority.Progress().Status);
        var submit = new Fixture(); submit.Start(); string code = submit.Inspect().AuthorizationCode;
        submit.AtEngineering(); submit.Transfer(code); submit.Authority.SetTerminals(true, 1, -.65, false, .25, -6.54);
        Assert.Equal("terminal_unavailable", Payload<CalibrationResultEvent>(submit.Submit(code, Fixture.Time + 3000)).ReasonCode);
        Assert.Equal(3u, submit.Authority.StateFor(submit.B.Id).AttemptsRemaining);
        Assert.Equal(CalibrationStatus.AwaitingAuthorization, submit.Authority.Progress().Status);
    }

    [Fact]
    public void InteractionRangeUsesActualSuppliedTerminalPositions()
    {
        var f = new Fixture(); f.Authority.SetTerminals(true, 50, 50, true, 70, 70);
        Assert.Equal("diagnostic_out_of_range", Payload<CalibrationResultEvent>(f.Start()).ReasonCode);
        f.A.X = 50; f.A.Y = 50; f.Refresh(Fixture.Time + 500);
        var started = f.Authority.Execute(f.A.Id, new StartCalibrationCommand
            { TechnicianActorId = f.B.Id, CorrelationId = "moved-terminal", MatchId = f.Match }, Fixture.Time + 1000);
        Assert.True(Payload<CalibrationResultEvent>(started).Accepted);
        string code = f.Inspect(Fixture.Time + 1500).AuthorizationCode;
        f.AtEngineering(); f.Transfer(code);
        Assert.Equal("calibration_out_of_range", Payload<CalibrationResultEvent>(f.Submit(code, Fixture.Time + 3000, correlation: "old-position")).ReasonCode);
        f.B.X = 70; f.B.Y = 70; f.Refresh(Fixture.Time + 3500);
        Assert.True(Payload<CalibrationResultEvent>(f.Submit(code, Fixture.Time + 4000, correlation: "actual-position")).Accepted);
    }

    [Fact]
    public void AdditionalTaskScopeIsBoundToApprovedSenderAndCurrentGamePermission()
    {
        bool allowed = false;
        var calls = new List<(string Actor, string Task)>();
        var f = new Fixture((actor, task) => { calls.Add((actor, task)); return allowed && actor == "coordinator" && task == "incident"; });
        var first = f.Send(correlation: "before-permission"); first.TaskId = "incident";
        var denied = f.Authority.Execute(f.A.Id, first, Fixture.Time);
        Assert.Equal("task_not_assigned", Payload<MessageDeliveryResultEvent>(denied).ReasonCode);
        Assert.DoesNotContain(denied, d => d.Payload is MessageReceivedEvent);
        allowed = true;
        var permitted = first.Clone(); permitted.CorrelationId = "after-permission";
        var delivered = f.Authority.Execute(f.A.Id, permitted, Fixture.Time + 1000);
        Assert.Equal(f.B.Id, delivered.Single(d => d.Payload is MessageReceivedEvent).Recipient);
        Assert.Equal("incident", Payload<MessageReceivedEvent>(delivered).TaskId);
        var impersonated = f.Send(correlation: "different-sender", recipient: f.C.Id); impersonated.TaskId = "incident";
        Assert.Equal("task_not_assigned", Payload<MessageDeliveryResultEvent>(f.Authority.Execute(f.B.Id, impersonated, Fixture.Time + 1500)).ReasonCode);
        allowed = false;
        var revoked = first.Clone(); revoked.CorrelationId = "revoked";
        Assert.Equal("task_not_assigned", Payload<MessageDeliveryResultEvent>(f.Authority.Execute(f.A.Id, revoked, Fixture.Time + 2000)).ReasonCode);
        Assert.Contains((f.A.Id, "incident"), calls); Assert.Contains((f.B.Id, "incident"), calls);
        Assert.Equal(CalibrationStatus.Available, f.Authority.Progress().Status);
    }

    [Theory]
    [InlineData("channel", "channel_unavailable")]
    [InlineData("recipient", "recipient_unavailable")]
    [InlineData("self", "recipient_unavailable")]
    [InlineData("text", "invalid_text")]
    [InlineData("ttl", "invalid_ttl")]
    [InlineData("match", "match_mismatch")]
    public void AdditionalPoliciesCannotBypassMandatoryMessageValidation(string field, string expected)
    {
        int scopeCalls = 0, policyCalls = 0;
        var f = new Fixture((_, _) => { ++scopeCalls; return true; }, (_, _) => { ++policyCalls; return ""; });
        var command = f.Send(); command.TaskId = "external-incident";
        if (field == "channel") command.ChannelId = "unknown";
        else if (field == "recipient") command.RecipientActorId = "not-approved";
        else if (field == "self") command.RecipientActorId = f.A.Id;
        else if (field == "text") command.Text = new string('x', CooperativeAuthority.MaxTextBytes + 1);
        else if (field == "ttl") command.TtlMs = 999;
        else command.MatchId = Guid.NewGuid().ToString("N");
        var output = f.Authority.Execute(f.A.Id, command, Fixture.Time);
        Assert.Equal(expected, Payload<MessageDeliveryResultEvent>(output).ReasonCode);
        Assert.DoesNotContain(output, d => d.Payload is MessageReceivedEvent);
        Assert.Equal(0, scopeCalls); Assert.Equal(0, policyCalls);
    }

    [Fact]
    public void AdditionalMessagePolicyRejectsBeforeDeliveryAndReplayRetainsActualAckOutcome()
    {
        bool permit = false;
        var policyCallers = new List<string>();
        var f = new Fixture((_, task) => task == "incident", (actor, command) =>
        { policyCallers.Add(actor); return permit && command.Sensitive ? "" : "private_reference_requires_permission"; });
        var command = f.Send("fixture-private-reference", "policy-denied"); command.TaskId = "incident"; command.Sensitive = true;
        var rejected = f.Authority.Execute(f.A.Id, command, Fixture.Time);
        Assert.Equal("private_reference_requires_permission", Payload<MessageDeliveryResultEvent>(rejected).ReasonCode);
        Assert.DoesNotContain(rejected, d => d.Payload is MessageReceivedEvent);
        permit = true;
        Assert.Equal("private_reference_requires_permission", Payload<MessageDeliveryResultEvent>(f.Authority.Execute(f.A.Id, command, Fixture.Time + 100)).ReasonCode);
        Assert.Single(policyCallers);
        var fresh = command.Clone(); fresh.CorrelationId = "policy-permitted";
        var accepted = f.Authority.Execute(f.A.Id, fresh, Fixture.Time + 1000);
        var message = Payload<MessageReceivedEvent>(accepted);
        Assert.Equal(f.B.Id, accepted.Single(d => d.Payload is MessageReceivedEvent).Recipient);
        Assert.Equal(MessageDeliveryStage.ServerAccepted, Payload<MessageDeliveryResultEvent>(accepted).Stage);
        var replay = f.Authority.Execute(f.A.Id, fresh, Fixture.Time + 1100);
        Assert.DoesNotContain(replay, d => d.Payload is MessageReceivedEvent);
        Assert.Equal(MessageDeliveryStage.ServerAccepted, Payload<MessageDeliveryResultEvent>(replay).Stage);
        Assert.Empty(f.Authority.Acknowledge(f.C.Id, message.MessageId, f.Match, Fixture.Time + 1200));
        var ack = f.Authority.Acknowledge(f.B.Id, message.MessageId, f.Match, Fixture.Time + 1300);
        Assert.Equal(f.A.Id, ack.Single().Recipient);
        Assert.Equal(MessageDeliveryStage.ClientReceived, Payload<MessageDeliveryResultEvent>(ack).Stage);
        var finalReplay = f.Authority.Execute(f.A.Id, fresh, Fixture.Time + 1400);
        Assert.DoesNotContain(finalReplay, d => d.Payload is MessageReceivedEvent);
        Assert.Equal(MessageDeliveryStage.ClientReceived, Payload<MessageDeliveryResultEvent>(finalReplay).Stage);
        Assert.Equal(new[] { f.A.Id, f.A.Id }, policyCallers);
        Assert.Equal(CalibrationStatus.Available, f.Authority.Progress().Status);
    }
}
