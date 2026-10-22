using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;

namespace Actor.Networking
{
    // Server-only game rules. All poses are physical capsule centers. The
    // adapter supplies approved identities and the same LOS used by NGO interest
    // management; neither a client claim nor a Brain decision is game truth.
    internal sealed class SocialMatchAuthority
    {
        internal sealed class Configuration
        {
            internal int MinPlayers = 4, MaxPlayers = 6, ImpostorCount = 1;
            internal long MatchDurationMs = 900000, DiscussionDurationMs = 90000, VotingDurationMs = 60000;
            internal long KillCooldownMs = 30000, EmergencyCooldownMs = 90000, SabotageTimeoutMs = 150000;
            internal long WorkDurationMs = 12000, SabotageCooldownMs = 90000, WaitingTimeoutMs = 300000;
            internal long ReconnectGraceMs = 30000, CalibrationStepMs = 2500, SensorSampleDelayMs = 8000, TaskDecisionTimeoutMs = 30000;
            internal double KillDistance = .9, VisibilityRadius = 3.5, BodyReportDistance = 1.2, InteractionRadius = 1.25;
            internal int TaskThreshold = 8, MeetingLimit = 3, PlayerMeetingLimit = 1;
            internal Configuration Copy() => (Configuration)MemberwiseClone();
        }
        internal sealed class Participant
        {
            internal string Id = "", Name = "", Room = "";
            internal double X, Y, VelocityX, VelocityY;
            internal bool PoseFresh = true;
            internal ulong ConnectionEpoch;
            internal ulong ConnectionGeneration = 1;
            internal bool ResumeEligible, AuthenticatedResume;
            internal int JoinOrder;
            internal Participant Copy() => (Participant)MemberwiseClone();
        }
        internal sealed class Terminal
        {
            internal string Id = "", Room = "", Kind = "";
            internal double X, Y, ApproachX, ApproachY, Radius;
            internal bool Available = true;
            internal Terminal Copy() => (Terminal)MemberwiseClone();
        }
        internal sealed class TaskDefinition
        { internal string Id = "", TerminalId = ""; internal SocialTaskMechanic Mechanic = SocialTaskMechanic.MaintenanceHold; }
        internal sealed class Delivery
        {
            internal readonly string Recipient;
            internal readonly IMessage Payload;
            internal Delivery(string recipient, IMessage payload) { Recipient = recipient; Payload = payload; }
        }
        private sealed class Player
        {
            internal Participant Pose;
            internal bool Connected = true, Ready, Assigned;
            internal SocialAlignment Alignment;
            internal CrewRole Duty = CrewRole.Crew;
            internal SocialLife Life = SocialLife.Alive;
            internal ulong OwnRevision;
            internal long KillReady, SabotageReady, MeetingReady, EliminatedAt;
            internal int MeetingsUsed;
            internal long TransportLostUntil;
            internal ulong Generation = 1;
        }
        private sealed class Work
        {
            internal string Id = "", Actor = "", Terminal = "", Task = "", Correlation = "";
            internal bool Repair;
            internal long Ends;
            internal ulong Phase;
            internal SocialTaskMechanic Mechanic;
            internal int Step, Attempts, SampleA, SampleB, CalibrationOffset;
            internal ulong StepRevision = 1;
            internal long NotBefore;
        }
        private sealed class Replay
        {
            internal byte[] Hash;
            internal SocialActionResultEvent Reply;
            internal long Expires;
        }
        private sealed class Bucket { internal double Tokens = 4; internal long At; }
        private readonly Configuration config;
        private readonly string match;
        private readonly Func<string, float, float, bool> canSee;
        private readonly Random fixtureRandom;
        private readonly Dictionary<string, Player> players = new();
        private readonly Dictionary<string, Terminal> terminals = new();
        private readonly Dictionary<string, string> taskTerminals = new();
        private readonly Dictionary<string, SocialTaskMechanic> taskMechanics = new();
        private readonly HashSet<string> completedTasks = new();
        private readonly Dictionary<string, Work> works = new();
        private readonly Dictionary<string, SocialVisibleBody> bodies = new();
        private readonly HashSet<string> reportedBodies = new();
        private readonly Dictionary<string, string> ballots = new();
        private readonly Dictionary<string, Replay> replays = new();
        private readonly Dictionary<string, Bucket> buckets = new();
        private readonly Dictionary<string, ulong> visibleRevisions = new();
        private readonly List<SocialReplayEntry> history = new();
        private readonly SocialMatchStateObservation state;
        private readonly SocialEmergencyStateObservation emergency;
        private SocialMeetingObservation meeting;
        private SocialMatchSummary summary;
        private long now, waitingSince = -1, globalAt, pausedEmergencyRemaining;
        private double globalTokens = 16;
        private ulong sequence;
        private ulong waitingRevision = 1;
        private uint kills, reports, meetings, votes, sabotages;
        private bool replayTruncated;
        internal const int MaxReplayEntries = 512, MaxHistoryEntries = 256, MaxPlayersTracked = 32;
        private const long ActionHorizonMs = 10000, ReplayRetentionMs = 30000, AssignmentDurationMs = 1000, ResolutionDurationMs = 1000;

        internal SocialMatchAuthority(string matchId, Configuration configuration = null, int? trustedSeed = null,
            Func<string, float, float, bool> canSee = null)
        {
            if (!Guid.TryParseExact(matchId, "N", out _)) throw new ArgumentException("Expected server match GUID.");
            config = (configuration ?? new Configuration()).Copy(); ValidateConfiguration(config, trustedSeed.HasValue);
            match = matchId; this.canSee = canSee;
            if (trustedSeed.HasValue) fixtureRandom = new Random(trustedSeed.Value);
            state = new SocialMatchStateObservation { MatchId = match, Mode = "SocialDeduction", Phase = SocialPhase.WaitingForPlayers,
                PhaseRevision = 1, ServerRevision = 1, TaskThreshold = (uint)config.TaskThreshold,
                KillDistance = (float)config.KillDistance, BodyReportDistance = (float)config.BodyReportDistance,
                InteractionRadius = (float)config.InteractionRadius, VisibilityRadius = (float)config.VisibilityRadius,
                KillCooldownMs = (uint)config.KillCooldownMs };
            emergency = new SocialEmergencyStateObservation { MatchId = match };
            meeting = new SocialMeetingObservation { MatchId = match };
        }
        internal string MatchId => match;
        internal bool Started => state.StartedAtUnixMs > 0;
        internal bool IsTerminal => state.Phase == SocialPhase.GameOver;
        internal SocialPhase Phase => state.Phase;
        internal SocialMatchStateObservation State() => state.Clone();
        internal SocialMatchSummary Summary() => summary?.Clone();
        internal SocialEmergencyStateObservation Emergency()
        {
            var value = emergency.Clone();
            value.RemainingMs = value.Active ? value.PausedForMeeting ? pausedEmergencyRemaining : Math.Max(0, value.EndsAtUnixMs - now) : 0;
            return value;
        }
        internal SocialOwnStateObservation OwnState(string actor)
        {
            var result = new SocialOwnStateObservation { MatchId = match, ActorId = BoundActor(actor), Life = SocialLife.Spectator, Duty = CrewRole.Crew };
            if (!players.TryGetValue(actor, out var p)) return result;
            result.Alignment = p.Alignment; result.Duty = p.Duty; result.Life = p.Life;
            result.Assigned = p.Assigned; result.Ready = p.Ready; result.CanMove = CanMove(actor);
            if (p.Alignment == SocialAlignment.Impostor) { result.KillReadyAtUnixMs = p.KillReady; result.SabotageReadyAtUnixMs = p.SabotageReady; }
            result.EmergencyReadyAtUnixMs = p.MeetingReady;
            result.RemainingMeetings = (uint)Math.Max(0, config.PlayerMeetingLimit - p.MeetingsUsed);
            result.ConnectionGeneration = p.Generation;
            result.ServerRevision = p.OwnRevision; return result;
        }
        internal SocialWaitingPlayersObservation Waiting(string actor)
        {
            var view = new SocialWaitingPlayersObservation { MatchId = match, RecipientActorId = BoundActor(actor),
                MinPlayers = (uint)config.MinPlayers, MaxPlayers = (uint)config.MaxPlayers };
            if (state.Phase != SocialPhase.WaitingForPlayers) return view;
            view.ServerRevision = waitingRevision;
            foreach (var p in players.Values.Where(p => p.Connected || p.TransportLostUntil > now).OrderBy(p => p.Pose.JoinOrder).Take(6))
            { view.Players.Add(new SocialWaitingPlayer { ActorId = p.Pose.Id, DisplayName = SafeName(p.Pose.Name), Ready = p.Ready && p.Connected, Connected = p.Connected });
                if (p.Ready && p.Connected) ++view.ReadyCount; }
            return view;
        }
        internal SocialOwnTaskWorkObservation TaskWork(string actor)
        {
            var view = new SocialOwnTaskWorkObservation { MatchId = match, RecipientActorId = BoundActor(actor) };
            if (!players.TryGetValue(actor, out var p)) return view;
            view.ServerRevision = p.OwnRevision;
            if (!works.TryGetValue(actor, out var work) || work.Repair || !Living(actor) || IsTerminal) return view;
            view.WorkId = work.Id; view.TaskId = work.Task; view.Mechanic = work.Mechanic; view.StepRevision = work.StepRevision;
            view.StepIndex = (uint)(work.Step + 1); view.StepCount = work.Mechanic == SocialTaskMechanic.CircuitCalibration ? 3u : 1u;
            view.NotBeforeUnixMs = work.NotBefore; view.DeadlineUnixMs = work.Ends;
            if (work.Mechanic == SocialTaskMechanic.CircuitCalibration) view.RequiredChoice = CalibrationChoice(work);
            if (work.Mechanic == SocialTaskMechanic.SensorObservation)
            { view.SampleA = work.SampleA; view.SampleBReady = now >= work.NotBefore; if (view.SampleBReady) view.SampleB = work.SampleB; }
            return view;
        }
        internal SocialMeetingObservation Meeting(string actor)
        {
            var result = meeting.Clone();
            if (ballots.TryGetValue(actor, out string candidate))
            { result.OwnBallotSubmitted = true; result.OwnSkip = candidate.Length == 0; result.OwnCandidateActorId = candidate; }
            return result;
        }
        internal SocialOwnTasksObservation Tasks(string actor)
        {
            var result = new SocialOwnTasksObservation { MatchId = match, RecipientActorId = BoundActor(actor) };
            if (!players.TryGetValue(actor, out var p) || !p.Assigned || p.Life != SocialLife.Alive || IsTerminal) return result;
            result.ContributesToCrewProgress = p.Alignment == SocialAlignment.Crew; result.ServerRevision = p.OwnRevision;
            foreach (var task in taskTerminals.OrderBy(p => p.Key))
            {
                if (!terminals.TryGetValue(task.Value, out var t)) continue;
                result.Tasks.Add(new SocialTask { TaskId = task.Key, TerminalEntityId = t.Id, RoomId = t.Room,
                    X = (float)t.X, Y = (float)t.Y, ApproachX = (float)t.ApproachX, ApproachY = (float)t.ApproachY,
                    WorkDurationMs = (uint)config.WorkDurationMs, Mechanic = taskMechanics[task.Key],
                    StepCount = taskMechanics[task.Key] == SocialTaskMechanic.CircuitCalibration ? 3u : 1u,
                    SampleDelayMs = (uint)config.SensorSampleDelayMs,
                    Completed = result.ContributesToCrewProgress && completedTasks.Contains(TaskKey(actor, task.Key)) });
            }
            return result;
        }
        internal void SetWorld(IEnumerable<Terminal> worldTerminals, IEnumerable<TaskDefinition> tasks)
        {
            var ts = (worldTerminals ?? throw new ArgumentNullException(nameof(worldTerminals))).Take(17).ToArray();
            var definitions = (tasks ?? throw new ArgumentNullException(nameof(tasks))).Take(6).ToArray();
            if (ts.Length > 16 || ts.Any(t => t == null || !Identifier(t.Id) || !Identifier(t.Room) || !Coordinate(t.X) || !Coordinate(t.Y)
                || !Coordinate(t.ApproachX) || !Coordinate(t.ApproachY) || !Finite(t.Radius) || t.Radius < 0 || t.Radius > 5
                || t.Kind != "task" && t.Kind != "meeting" && t.Kind != "life-support") || ts.Select(t => t.Id).Distinct().Count() != ts.Length
                || definitions.Length < 3 || definitions.Length > 5 || definitions.Any(t => t == null || !Identifier(t.Id)
                    || t.Mechanic < SocialTaskMechanic.MaintenanceHold || t.Mechanic > SocialTaskMechanic.SensorObservation
                    || !ts.Any(s => s.Id == t.TerminalId && s.Kind == "task")) || definitions.Select(t => t.Id).Distinct().Count() != definitions.Length
                || ts.Count(t => t.Kind == "meeting") != 1 || ts.Count(t => t.Kind == "life-support") != 1)
                throw new ArgumentException("Expected bounded authored social terminals and tasks.");
            if (config.TaskThreshold > definitions.Length * (config.MinPlayers - config.ImpostorCount))
                throw new ArgumentException("Task threshold exceeds the minimum admitted Crew assignment capacity.");
            if (Started && (!taskTerminals.OrderBy(p => p.Key).SequenceEqual(definitions.Select(t => new KeyValuePair<string, string>(t.Id, t.TerminalId)).OrderBy(p => p.Key))
                || !terminals.Keys.OrderBy(p => p).SequenceEqual(ts.Select(t => t.Id).OrderBy(p => p))
                || !taskMechanics.OrderBy(p => p.Key).SequenceEqual(definitions.Select(t => new KeyValuePair<string, SocialTaskMechanic>(t.Id, t.Mechanic)).OrderBy(p => p.Key))))
                throw new InvalidOperationException("Social world identities are immutable after assignment.");
            terminals.Clear(); foreach (var t in ts) terminals.Add(t.Id, t.Copy());
            taskTerminals.Clear(); foreach (var task in definitions) taskTerminals.Add(task.Id, task.TerminalId);
            taskMechanics.Clear(); foreach (var task in definitions) taskMechanics.Add(task.Id, task.Mechanic);
        }
        internal List<Delivery> Refresh(IEnumerable<Participant> approved, long at)
        {
            var supplied = (approved ?? throw new ArgumentNullException(nameof(approved))).Take(17).ToArray();
            if (supplied.Length > 16 || supplied.Any(p => p == null || !ActorIdentifier(p.Id)) || supplied.Select(p => p.Id).Distinct().Count() != supplied.Length)
                throw new ArgumentException("Expected distinct bounded approved identities.");
            var output = new List<Delivery>(); var ids = new HashSet<string>(supplied.Select(p => p.Id));
            var lost = players.Values.Where(p => p.Connected && !ids.Contains(p.Pose.Id)).ToList();
            // Never finish work from a stale/disconnected pose, but settle an
            // already elapsed server deadline before applying a new disconnect.
            foreach (var p in lost) { p.Pose = p.Pose.Copy(); p.Pose.PoseFresh = false; }
            foreach (var pose in supplied)
            {
                if (players.TryGetValue(pose.Id, out var existing))
                {
                    if (!existing.Connected && existing.TransportLostUntil > at && pose.AuthenticatedResume && pose.ConnectionGeneration > existing.Generation)
                    { existing.Connected = true; existing.TransportLostUntil = 0; existing.Generation = pose.ConnectionGeneration;
                        existing.Pose = pose.Copy(); existing.Ready = false; ++waitingRevision; OwnChanged(existing, output); continue; }
                    if (existing.Connected && existing.Pose.ConnectionEpoch != pose.ConnectionEpoch) lost.Add(existing);
                    existing.Pose = pose.Copy(); if (lost.Contains(existing)) existing.Pose.PoseFresh = false;
                    continue;
                }
                if (players.Count >= MaxPlayersTracked) continue;
                var player = new Player { Pose = pose.Copy(), Life = Started || IsTerminal ? SocialLife.Spectator : SocialLife.Alive, OwnRevision = 1, Generation = pose.ConnectionGeneration };
                players.Add(pose.Id, player); visibleRevisions[pose.Id] = 0;
                ++waitingRevision;
                output.Add(new Delivery(pose.Id, new SocialOwnStateChangedEvent { State = OwnState(pose.Id) }));
            }
            output.AddRange(Tick(at));
            foreach (var p in lost)
            {
                if (!p.Pose.ResumeEligible) { Forfeit(p, output, "connection_lost"); continue; }
                p.Connected = false; p.Ready = false; p.TransportLostUntil = now + config.ReconnectGraceMs;
                p.Pose.PoseFresh = true; p.Pose.VelocityX = p.Pose.VelocityY = 0;
                if (works.TryGetValue(p.Pose.Id, out var work)) EndWork(work, false, "transport_lost", output);
                ++waitingRevision; OwnChanged(p, output);
            }
            EvaluateVictory(output); output.AddRange(Tick(now)); return output;
        }
        internal List<Delivery> ControlUnavailable(string actor, long at)
        {
            var output = Tick(at);
            if (players.TryGetValue(actor, out var p) && p.Connected && !IsTerminal) Forfeit(p, output, "control_unavailable");
            EvaluateVictory(output); output.AddRange(Tick(now)); return output;
        }
        internal void RefreshSuspendedPose(Participant pose)
        {
            if (pose == null || !players.TryGetValue(pose.Id, out var p) || p.Connected || p.TransportLostUntil <= now) return;
            // Only the server's retained physical avatar supplies this. It is
            // vulnerable, but is never an approved command-producing endpoint.
            if (!Coordinate(pose.X) || !Coordinate(pose.Y)) return;
            p.Pose.X = pose.X; p.Pose.Y = pose.Y; p.Pose.Room = pose.Room; p.Pose.PoseFresh = true;
            p.Pose.VelocityX = p.Pose.VelocityY = 0;
        }
        internal bool CanMove(string actor) => players.TryGetValue(actor, out var p) && p.Connected && p.Life == SocialLife.Alive
            && (state.Phase == SocialPhase.Exploration || state.Phase == SocialPhase.WaitingForPlayers);
        internal bool CanCommunicate(string sender, string recipient, string channel)
        {
            if (recipient == null || channel == null) return false;
            if (!Living(sender) || IsTerminal) return false;
            if (state.Phase == SocialPhase.Discussion || state.Phase == SocialPhase.Voting)
                return channel == "discussion" && (recipient.Length == 0 || Living(recipient));
            if (state.Phase != SocialPhase.Exploration) return false;
            return (channel == "direct" && recipient.Length > 0 && IsVisible(sender, recipient))
                || (channel == "proximity" && (recipient.Length == 0 || IsVisible(sender, recipient)));
        }
        internal bool IsVisible(string observer, string target)
        {
            if (!Living(observer) || !players.TryGetValue(target, out var t) || !TargetAlive(target) || observer == target) return false;
            return Sees(observer, t.Pose.X, t.Pose.Y) && Fresh(t.Pose);
        }
        internal bool CanSee(string observer, float x, float y) => Sees(observer, x, y);
        internal List<SocialVisibleBody> Bodies(string observer) => bodies.Values.Where(b => !reportedBodies.Contains(b.BodyId) && Sees(observer, b.X, b.Y))
            .Select(b => b.Clone()).ToList();
        // Server integration only: this list must never be exported as a public
        // observation. Each spawned body gets the same per-client interest test.
        internal List<SocialVisibleBody> BodyStates() => bodies.Values.Where(b => !reportedBodies.Contains(b.BodyId)).Select(b => b.Clone()).ToList();
        internal SocialVisibleWorldObservation VisibleWorld(string actor, long capturedAt)
        {
            var result = new SocialVisibleWorldObservation { MatchId = match, RecipientActorId = BoundActor(actor), CapturedAtUnixMs = capturedAt,
                VisibilityRadius = (float)config.VisibilityRadius, ServerRevision = NextVisibleRevision(actor) };
            if (!Living(actor) || IsTerminal) return result;
            foreach (var p in players.Values.Where(p => IsVisible(actor, p.Pose.Id)).OrderBy(p => p.Pose.Id))
                result.Actors.Add(new SocialVisibleActor { ActorId = p.Pose.Id, DisplayName = SafeName(p.Pose.Name), X = (float)p.Pose.X, Y = (float)p.Pose.Y,
                    VelocityX = (float)p.Pose.VelocityX, VelocityY = (float)p.Pose.VelocityY, ObservedRevision = result.ServerRevision });
            foreach (var body in Bodies(actor)) { body.ObservedRevision = result.ServerRevision; result.Bodies.Add(body); }
            foreach (var t in terminals.Values.Where(t => t.Available && Sees(actor, t.X, t.Y)).OrderBy(t => t.Id))
                result.Interactables.Add(new SocialVisibleInteractable { EntityId = t.Id, Kind = t.Kind, RoomId = t.Room, X = (float)t.X, Y = (float)t.Y,
                    Radius = (float)Radius(t), ApproachX = (float)t.ApproachX, ApproachY = (float)t.ApproachY });
            return result;
        }

        internal List<Delivery> Tick(long at)
        {
            now = Math.Max(now, at); if (waitingSince < 0) waitingSince = now;
            var output = new List<Delivery>();
            foreach (var key in replays.Where(p => p.Value.Expires <= now).Select(p => p.Key).ToArray()) replays.Remove(key);
            if (IsTerminal) return output;
            foreach (var p in players.Values.Where(p => !p.Connected && p.TransportLostUntil > 0 && p.TransportLostUntil <= now).ToArray())
                Forfeit(p, output, "reconnect_expired");
            foreach (var work in works.Values.Where(w => !WorkValid(w)).ToArray()) EndWork(work, false, "work_interrupted", output);
            if (state.Phase == SocialPhase.WaitingForPlayers)
            {
                if (now - waitingSince >= config.WaitingTimeoutMs) { Finish(SocialAlignment.Unspecified, "waiting_timeout", output); return output; }
                TryStart(output);
            }
            // Process due server deadlines in order. A late tick must not turn
            // a repair completed BEFORE the crisis deadline into a defeat.
            for (int step = 0; step < 32 && !IsTerminal; step++)
            {
                long phaseAt = state.PhaseEndsAtUnixMs > 0 ? state.PhaseEndsAtUnixMs : long.MaxValue;
                long matchAt = Started ? state.EndsAtUnixMs : long.MaxValue;
                long emergencyAt = emergency.Active && !emergency.PausedForMeeting ? emergency.EndsAtUnixMs : long.MaxValue;
                long workAt = works.Count > 0 ? works.Values.Min(w => w.Ends) : long.MaxValue;
                long next = Math.Min(Math.Min(phaseAt, matchAt), Math.Min(emergencyAt, workAt));
                if (next > now || next == long.MaxValue) break;
                long capturedNow = now; now = next;
                try
                {
                    if (matchAt == next) { Finish(SocialAlignment.Unspecified, "match_timeout", output); break; }
                    if (emergencyAt == next) { Finish(SocialAlignment.Impostor, "sabotage_expired", output); break; }
                    if (phaseAt == next)
                    {
                        if (state.Phase == SocialPhase.RoleAssignment) Transition(SocialPhase.Exploration, 0, output);
                        else if (state.Phase == SocialPhase.Discussion) Transition(SocialPhase.Voting, meeting.VotingEndsAtUnixMs, output);
                        else if (state.Phase == SocialPhase.Voting) ResolveVote(next, output);
                        else if (state.Phase == SocialPhase.VoteResolution) { ResumeEmergency(next, output); Transition(SocialPhase.Exploration, 0, output); }
                        continue;
                    }
                    foreach (var work in works.Values.Where(w => w.Ends == next).ToArray())
                        EndWork(work, work.Repair || work.Mechanic == SocialTaskMechanic.MaintenanceHold,
                            work.Repair || work.Mechanic == SocialTaskMechanic.MaintenanceHold ? "work_completed" : "task_timed_out", output);
                    EvaluateVictory(output);
                }
                finally { now = capturedNow; }
            }
            EvaluateVictory(output); return output;
        }
        internal List<Delivery> Execute(string actor, IMessage command, long at)
        {
            var output = Tick(at);
            if (!players.TryGetValue(actor, out var p) || command == null) return output;
            var context = Context(command); SocialAction action = Action(command);
            if (context == null || action == SocialAction.Unspecified) return output;
            string invalid = context.MatchId != match ? "match_mismatch" : context.ConnectionGeneration != p.Generation ? "connection_changed"
                : !Identifier(context.CorrelationId) ? "invalid_correlation"
                : command.CalculateSize() > 2048 || !CommandBounds(command) ? "invalid_request" : "";
            if (invalid.Length > 0) { output.Add(new Delivery(actor, Result(command, false, invalid))); return output; }
            string key = actor + ":" + context.CorrelationId; byte[] hash;
            using (var sha = SHA256.Create()) hash = sha.ComputeHash(Encoding.UTF8.GetBytes(command.Descriptor.FullName + "\0").Concat(command.ToByteArray()).ToArray());
            if (replays.TryGetValue(key, out var previous))
            {
                output.Add(new Delivery(actor, previous.Hash.SequenceEqual(hash) ? previous.Reply.Clone() : Result(command, false, "correlation_conflict")));
                return output;
            }
            invalid = IsTerminal ? "match_over" : !p.Connected || p.Life != SocialLife.Alive ? "action_unavailable"
                : context.PhaseRevision != state.PhaseRevision ? "phase_changed"
                : context.ExpiresAtUnixMs <= now || context.ExpiresAtUnixMs > now + ActionHorizonMs ? "action_expired"
                : !Rate(actor) ? "rate_limited" : replays.Count >= MaxReplayEntries ? "request_capacity" : "";
            if (invalid.Length > 0) { output.Add(new Delivery(actor, Result(command, false, invalid))); return output; }
            SocialActionResultEvent reply;
            if (command is SocialReadyCommand ready) reply = Ready(p, ready, output);
            else if (command is SocialKillCommand kill) reply = Kill(p, kill, output);
            else if (command is SocialReportBodyCommand report) reply = Report(p, report, output);
            else if (command is SocialCallMeetingCommand call) reply = CallMeeting(p, call, output);
            else if (command is SocialCastVoteCommand vote) reply = Vote(p, vote);
            else if (command is SocialWorkTaskCommand task) reply = StartWork(p, task, output);
            else if (command is SocialSabotageCommand sabotage) reply = Sabotage(p, sabotage, output);
            else if (command is SocialRepairEmergencyCommand repair) reply = StartRepair(p, repair, output);
            else if (command is SocialTaskStepCommand step) reply = TaskStep(p, step, output);
            else reply = CancelWork(p, (SocialCancelWorkCommand)command, output);
            replays[key] = new Replay { Hash = hash, Reply = reply.Clone(), Expires = now + ReplayRetentionMs };
            output.Add(new Delivery(actor, reply)); return output;
        }

        private SocialActionResultEvent Ready(Player p, SocialReadyCommand command, List<Delivery> output)
        {
            if (state.Phase != SocialPhase.WaitingForPlayers) return Result(command, false, "phase_unavailable");
            p.Ready = command.Ready; ++waitingRevision; OwnChanged(p, output); TryStart(output);
            return Result(command, true, "ready_updated", true);
        }
        private void TryStart(List<Delivery> output)
        {
            var eligible = players.Values.Where(p => p.Connected && p.Life == SocialLife.Alive).OrderBy(p => p.Pose.JoinOrder).ThenBy(p => p.Pose.Id).ToArray();
            if (eligible.Length < config.MinPlayers || eligible.Length > config.MaxPlayers || eligible.Any(p => !p.Ready || !Fresh(p.Pose))
                || taskTerminals.Count < 3 || taskTerminals.Count * (eligible.Length - config.ImpostorCount) < config.TaskThreshold
                || terminals.Values.Any(t => !t.Available)) return;
            var alignmentOrder = eligible.ToArray(); Shuffle(alignmentOrder); var dutyOrder = eligible.ToArray(); Shuffle(dutyOrder);
            state.StartedAtUnixMs = now; state.EndsAtUnixMs = now + config.MatchDurationMs;
            foreach (var p in eligible)
            {
                p.Assigned = true; p.Alignment = SocialAlignment.Crew; p.Duty = CrewRole.Crew;
                p.KillReady = now + config.KillCooldownMs; p.SabotageReady = now + config.SabotageCooldownMs; p.MeetingReady = now;
            }
            for (int i = 0; i < config.ImpostorCount; i++) alignmentOrder[i].Alignment = SocialAlignment.Impostor;
            dutyOrder[0].Duty = CrewRole.Engineer; dutyOrder[1].Duty = CrewRole.Sheriff;
            Record("match_started", "", "", "assigned"); Transition(SocialPhase.RoleAssignment, now + AssignmentDurationMs, output);
        }
        private SocialActionResultEvent Kill(Player attacker, SocialKillCommand command, List<Delivery> output)
        {
            if (state.Phase != SocialPhase.Exploration || attacker.Alignment != SocialAlignment.Impostor || works.ContainsKey(attacker.Pose.Id)
                || now < attacker.KillReady || command.TargetActorId == attacker.Pose.Id || !TargetAlive(command.TargetActorId)
                || !IsVisible(attacker.Pose.Id, command.TargetActorId)
                || Distance(attacker.Pose, players[command.TargetActorId].Pose.X, players[command.TargetActorId].Pose.Y) > config.KillDistance)
                return Result(command, false, "target_unavailable");
            var victim = players[command.TargetActorId];
            var witnesses = players.Values.Where(p => p.Pose.Id != attacker.Pose.Id && p.Pose.Id != victim.Pose.Id
                && IsVisible(p.Pose.Id, attacker.Pose.Id) && IsVisible(p.Pose.Id, victim.Pose.Id)).Select(p => p.Pose.Id).ToArray();
            string bodyId = Guid.NewGuid().ToString("N");
            bodies.Add(bodyId, new SocialVisibleBody { BodyId = bodyId, VictimActorId = victim.Pose.Id, VictimDisplayName = SafeName(victim.Pose.Name),
                X = (float)victim.Pose.X, Y = (float)victim.Pose.Y });
            Eliminate(victim, SocialLife.Dead, output); attacker.KillReady = now + config.KillCooldownMs; OwnChanged(attacker, output); ++kills;
            foreach (string witness in witnesses) output.Add(new Delivery(witness, new SocialVisibleAttackEvent { MatchId = match, ObserverActorId = witness,
                AttackerActorId = attacker.Pose.Id, VictimActorId = victim.Pose.Id, X = (float)victim.Pose.X, Y = (float)victim.Pose.Y,
                ObservedAtUnixMs = now, ObservedRevision = NextVisibleRevision(witness) }));
            string presentation = Guid.NewGuid().ToString("N");
            foreach (string recipient in witnesses.Concat(new[] { attacker.Pose.Id, victim.Pose.Id }).Distinct())
                output.Add(new Delivery(recipient, new SocialKillPresentationEvent { MatchId = match, RecipientActorId = recipient,
                    PresentationId = presentation, BodyId = bodyId, VictimActorId = victim.Pose.Id,
                    AttackerActorId = recipient == victim.Pose.Id ? "" : attacker.Pose.Id, X = (float)victim.Pose.X, Y = (float)victim.Pose.Y, ServerUnixMs = now }));
            Record("kill", attacker.Pose.Id, victim.Pose.Id, "dead"); EvaluateVictory(output);
            return Result(command, true, "kill_completed", true);
        }
        private SocialActionResultEvent Report(Player caller, SocialReportBodyCommand command, List<Delivery> output)
        {
            if (state.Phase != SocialPhase.Exploration || meetings >= config.MeetingLimit || !bodies.TryGetValue(command.BodyId, out var body)
                || reportedBodies.Contains(command.BodyId) || !Sees(caller.Pose.Id, body.X, body.Y)
                || Distance(caller.Pose, body.X, body.Y) > config.BodyReportDistance)
                return Result(command, false, "body_unavailable");
            reportedBodies.Add(body.BodyId); ++reports; BeginMeeting(caller.Pose.Id, "body_report", output);
            return Result(command, true, "body_reported", true);
        }
        private SocialActionResultEvent CallMeeting(Player caller, SocialCallMeetingCommand command, List<Delivery> output)
        {
            var terminal = terminals.Values.FirstOrDefault(t => t.Kind == "meeting");
            if (state.Phase != SocialPhase.Exploration || meetings >= config.MeetingLimit || caller.MeetingsUsed >= config.PlayerMeetingLimit
                || now < caller.MeetingReady || !At(caller, terminal)) return Result(command, false, "meeting_unavailable");
            ++caller.MeetingsUsed; caller.MeetingReady = now + config.EmergencyCooldownMs; OwnChanged(caller, output);
            BeginMeeting(caller.Pose.Id, "emergency_meeting", output); return Result(command, true, "meeting_started", true);
        }
        private void BeginMeeting(string caller, string reason, List<Delivery> output)
        {
            CancelAllWork("phase_changed", output); ballots.Clear(); ++meetings;
            meeting = new SocialMeetingObservation { MatchId = match, MeetingId = Guid.NewGuid().ToString("N"),
                DiscussionEndsAtUnixMs = now + config.DiscussionDurationMs, VotingEndsAtUnixMs = now + config.DiscussionDurationMs + config.VotingDurationMs };
            foreach (var p in players.Values.Where(p => p.Assigned).OrderBy(p => p.Pose.Id))
                meeting.Participants.Add(new SocialMeetingParticipant { ActorId = p.Pose.Id, DisplayName = SafeName(p.Pose.Name), Life = p.Life });
            if (emergency.Active)
            {
                pausedEmergencyRemaining = Math.Max(0, emergency.EndsAtUnixMs - now); emergency.PausedForMeeting = true; emergency.EndsAtUnixMs = 0;
                ++emergency.ServerRevision; EmergencyChanged(output);
            }
            Record("meeting", caller, "", reason); Transition(SocialPhase.Discussion, meeting.DiscussionEndsAtUnixMs, output);
        }
        private SocialActionResultEvent Vote(Player caller, SocialCastVoteCommand command)
        {
            if (state.Phase != SocialPhase.Voting || command.MeetingId != meeting.MeetingId || ballots.ContainsKey(caller.Pose.Id)
                || command.Skip && command.CandidateActorId.Length > 0 || !command.Skip && !TargetAlive(command.CandidateActorId))
                return Result(command, false, "ballot_unavailable");
            ballots.Add(caller.Pose.Id, command.Skip ? "" : command.CandidateActorId); ++votes;
            Record("vote", caller.Pose.Id, command.Skip ? "" : command.CandidateActorId, command.Skip ? "skip" : "cast");
            return Result(command, true, "vote_recorded", true);
        }
        private void ResolveVote(long deadline, List<Delivery> output)
        {
            var counts = ballots.Values.GroupBy(p => p).ToDictionary(p => p.Key, p => p.Count());
            int skips = counts.TryGetValue("", out var s) ? s : 0;
            var candidates = counts.Where(p => p.Key.Length > 0 && TargetAlive(p.Key)).OrderByDescending(p => p.Value).ThenBy(p => p.Key).ToArray();
            meeting.Result.Clear(); foreach (var tally in counts.OrderBy(p => p.Key)) meeting.Result.Add(new SocialVoteTally { CandidateActorId = tally.Key, Votes = (uint)tally.Value });
            bool eject = candidates.Length > 0 && candidates[0].Value > skips && (candidates.Length == 1 || candidates[0].Value > candidates[1].Value);
            meeting.Resolution = eject ? "ejected" : "no_ejection"; meeting.EjectedActorId = eject ? candidates[0].Key : "";
            if (eject) { Eliminate(players[candidates[0].Key], SocialLife.Ejected, output); Record("ejection", candidates[0].Key, "", "ejected"); }
            Transition(SocialPhase.VoteResolution, deadline + ResolutionDurationMs, output); EvaluateVictory(output);
        }
        private SocialActionResultEvent StartWork(Player caller, SocialWorkTaskCommand command, List<Delivery> output)
        {
            if (state.Phase != SocialPhase.Exploration || !taskTerminals.TryGetValue(command.TaskId, out string terminalId)
                || !terminals.TryGetValue(terminalId, out var terminal) || !At(caller, terminal) || works.ContainsKey(caller.Pose.Id)
                || caller.Alignment == SocialAlignment.Crew && completedTasks.Contains(TaskKey(caller.Pose.Id, command.TaskId)))
                return Result(command, false, "task_unavailable");
            var work = NewWork(caller, terminal, command.Context.CorrelationId); work.Task = command.TaskId;
            work.Mechanic = taskMechanics[command.TaskId];
            if (work.Mechanic == SocialTaskMechanic.CircuitCalibration)
            { work.CalibrationOffset = RandomNumber(3); work.NotBefore = now + config.CalibrationStepMs;
                work.Ends = now + 3 * config.CalibrationStepMs + config.TaskDecisionTimeoutMs; }
            else if (work.Mechanic == SocialTaskMechanic.SensorObservation)
            { work.SampleA = 20 + RandomNumber(60); work.SampleB = work.SampleA + RandomNumber(3) - 1;
                work.NotBefore = now + config.SensorSampleDelayMs; work.Ends = work.NotBefore + config.TaskDecisionTimeoutMs; }
            works.Add(caller.Pose.Id, work); Activity(work, "working", output);
            OwnChanged(caller, output);
            return Result(command, true, "work_started", false, work);
        }
        private SocialActionResultEvent TaskStep(Player caller, SocialTaskStepCommand command, List<Delivery> output)
        {
            if (!works.TryGetValue(caller.Pose.Id, out var work) || work.Id != command.WorkId || work.Repair || !WorkValid(work)
                || work.Mechanic == SocialTaskMechanic.MaintenanceHold) return Result(command, false, "task_step_unavailable");
            if (command.StepRevision != work.StepRevision) return Result(command, false, "task_step_changed", false, work);
            if (now < work.NotBefore) return Result(command, false, "task_step_not_ready", false, work);
            var expected = work.Mechanic == SocialTaskMechanic.CircuitCalibration ? CalibrationChoice(work)
                : work.SampleB > work.SampleA ? SocialTaskChoice.Rising : work.SampleB < work.SampleA ? SocialTaskChoice.Falling : SocialTaskChoice.Stable;
            if (command.Choice != expected)
            { if (++work.Attempts >= 3) EndWork(work, false, "task_attempts_exhausted", output);
                return Result(command, false, "incorrect_task_step", false, work); }
            ++work.Step; ++work.StepRevision; work.Attempts = 0; OwnChanged(caller, output);
            bool complete = work.Mechanic == SocialTaskMechanic.SensorObservation || work.Step >= 3;
            if (complete) EndWork(work, true, "work_completed", output);
            else work.NotBefore = now + config.CalibrationStepMs;
            return Result(command, true, complete ? "work_completed" : "task_step_completed", complete, work);
        }
        private static SocialTaskChoice CalibrationChoice(Work work) => (SocialTaskChoice)((int)SocialTaskChoice.Alpha + (work.CalibrationOffset + work.Step) % 3);
        private int RandomNumber(int upper)
        {
            if (fixtureRandom != null) return fixtureRandom.Next(upper);
            using (var rng = RandomNumberGenerator.Create())
            { byte[] bytes = new byte[4]; rng.GetBytes(bytes); return (int)(BitConverter.ToUInt32(bytes, 0) % (uint)upper); }
        }
        private SocialActionResultEvent Sabotage(Player caller, SocialSabotageCommand command, List<Delivery> output)
        {
            if (state.Phase != SocialPhase.Exploration || caller.Alignment != SocialAlignment.Impostor || emergency.Active || now < caller.SabotageReady
                || works.ContainsKey(caller.Pose.Id) || !terminals.TryGetValue(command.TerminalEntityId, out var terminal) || terminal.Kind != "life-support" || !At(caller, terminal))
                return Result(command, false, "sabotage_unavailable");
            caller.SabotageReady = now + config.SabotageCooldownMs; OwnChanged(caller, output);
            emergency.EmergencyId = Guid.NewGuid().ToString("N"); emergency.Active = true; emergency.PausedForMeeting = false;
            emergency.EndsAtUnixMs = now + config.SabotageTimeoutMs; emergency.TerminalEntityId = terminal.Id; emergency.RoomId = terminal.Room;
            emergency.ApproachX = (float)terminal.ApproachX; emergency.ApproachY = (float)terminal.ApproachY; ++emergency.ServerRevision; ++sabotages;
            Record("sabotage", caller.Pose.Id, terminal.Id, "started"); EmergencyChanged(output);
            return Result(command, true, "sabotage_started", true);
        }
        private SocialActionResultEvent StartRepair(Player caller, SocialRepairEmergencyCommand command, List<Delivery> output)
        {
            if (state.Phase != SocialPhase.Exploration || caller.Alignment != SocialAlignment.Crew || !emergency.Active || emergency.PausedForMeeting
                || command.EmergencyId != emergency.EmergencyId || works.ContainsKey(caller.Pose.Id)
                || !terminals.TryGetValue(emergency.TerminalEntityId, out var terminal) || !At(caller, terminal))
                return Result(command, false, "repair_unavailable");
            var work = NewWork(caller, terminal, command.Context.CorrelationId); work.Repair = true;
            works.Add(caller.Pose.Id, work); Activity(work, "working", output); return Result(command, true, "work_started", false, work);
        }
        private SocialActionResultEvent CancelWork(Player caller, SocialCancelWorkCommand command, List<Delivery> output)
        {
            if (!works.TryGetValue(caller.Pose.Id, out var work) || work.Id != command.WorkId && work.Correlation != command.WorkId)
                return Result(command, false, "work_unavailable");
            EndWork(work, false, "work_cancelled", output); return Result(command, true, "work_cancelled", true);
        }
        private Work NewWork(Player player, Terminal terminal, string correlation) => new()
        { Id = Guid.NewGuid().ToString("N"), Actor = player.Pose.Id, Terminal = terminal.Id, Correlation = correlation,
            Ends = now + config.WorkDurationMs, Phase = state.PhaseRevision };
        private bool WorkValid(Work work) => state.Phase == SocialPhase.Exploration && state.PhaseRevision == work.Phase && Living(work.Actor)
            && terminals.TryGetValue(work.Terminal, out var terminal) && At(players[work.Actor], terminal)
            && (!work.Repair || emergency.Active && !emergency.PausedForMeeting);
        private void EndWork(Work work, bool complete, string reason, List<Delivery> output)
        {
            works.Remove(work.Actor); bool actual = complete && WorkValid(work);
            OwnChanged(players[work.Actor], output);
            if (complete && !actual) reason = "work_interrupted";
            if (actual && work.Repair)
            {
                emergency.Active = false; emergency.PausedForMeeting = false; emergency.EndsAtUnixMs = 0; pausedEmergencyRemaining = 0;
                ++emergency.ServerRevision; EmergencyChanged(output); Record("repair", work.Actor, work.Terminal, "restored");
                foreach (var other in works.Values.Where(w => w.Repair).ToArray()) EndWork(other, false, "emergency_restored", output);
            }
            else if (actual && players[work.Actor].Alignment == SocialAlignment.Crew)
            {
                if (completedTasks.Add(TaskKey(work.Actor, work.Task)))
                {
                    OwnChanged(players[work.Actor], output); state.CrewTasksCompleted = (uint)completedTasks.Count;
                    PublicChanged(output); Record("task", work.Actor, work.Task, "completed");
                }
                else { actual = false; reason = "task_unavailable"; }
            }
            Activity(work, "stopped", output);
            var result = new SocialActionResultEvent { MatchId = match, CorrelationId = work.Correlation,
                Action = work.Repair ? SocialAction.RepairEmergency : SocialAction.WorkTask, Accepted = actual, Completed = actual,
                ReasonCode = reason, WorkId = work.Id, PhaseRevision = state.PhaseRevision,
                TaskStepRevision = work.StepRevision, TaskStepIndex = (uint)work.Step };
            if (replays.TryGetValue(work.Actor + ":" + work.Correlation, out var replay)) replay.Reply = result.Clone();
            output.Add(new Delivery(work.Actor, result));
        }
        private void Activity(Work work, string activity, List<Delivery> output)
        {
            foreach (string id in players.Keys.Where(id => id != work.Actor && IsVisible(id, work.Actor)).ToArray())
                output.Add(new Delivery(id, new SocialVisibleActivityEvent { MatchId = match, ObserverActorId = id, ActorId = work.Actor,
                    TerminalEntityId = work.Terminal, Activity = activity, ObservedAtUnixMs = now, ObservedRevision = NextVisibleRevision(id) }));
        }
        private void CancelAllWork(string reason, List<Delivery> output)
        { foreach (var work in works.Values.ToArray()) EndWork(work, false, reason, output); }
        private void ResumeEmergency(long at, List<Delivery> output)
        {
            if (!emergency.Active || !emergency.PausedForMeeting) return;
            emergency.PausedForMeeting = false; emergency.EndsAtUnixMs = at + pausedEmergencyRemaining; pausedEmergencyRemaining = 0;
            ++emergency.ServerRevision; EmergencyChanged(output);
        }
        private void Forfeit(Player p, List<Delivery> output, string reason)
        {
            p.Connected = false;
            p.TransportLostUntil = 0; ++waitingRevision;
            if (IsTerminal) return;
            p.Ready = false;
            if (p.Life == SocialLife.Alive) { Eliminate(p, SocialLife.Disconnected, output); Record("disconnect", p.Pose.Id, "", reason); }
            else if (works.TryGetValue(p.Pose.Id, out var work)) EndWork(work, false, "control_unavailable", output);
        }
        private void Eliminate(Player p, SocialLife life, List<Delivery> output)
        {
            p.Life = life; p.EliminatedAt = now;
            if (works.TryGetValue(p.Pose.Id, out var work)) EndWork(work, false, "life_changed", output);
            OwnChanged(p, output);
            if (meeting.MeetingId.Length > 0 && (state.Phase == SocialPhase.Discussion || state.Phase == SocialPhase.Voting || state.Phase == SocialPhase.VoteResolution))
            {
                // Roll-call is a deliberate meeting disclosure, never a global
                // exploration death feed. A forfeited voter's ballot is void;
                // ejection happens after tallying and preserves that final tally.
                if (life == SocialLife.Disconnected) ballots.Remove(p.Pose.Id);
                var entry = meeting.Participants.FirstOrDefault(entry => entry.ActorId == p.Pose.Id);
                if (entry != null) entry.Life = life;
                PublicChanged(output);
                foreach (string id in players.Keys) output.Add(new Delivery(id, new SocialMeetingChangedEvent { State = Meeting(id) }));
            }
        }
        private void EvaluateVictory(List<Delivery> output)
        {
            if (!Started || IsTerminal) return;
            int impostors = players.Values.Count(p => p.Assigned && p.Life == SocialLife.Alive && p.Alignment == SocialAlignment.Impostor);
            int crew = players.Values.Count(p => p.Assigned && p.Life == SocialLife.Alive && p.Alignment == SocialAlignment.Crew);
            if (impostors + crew == 0) Finish(SocialAlignment.Unspecified, "all_players_lost", output);
            else if (impostors == 0) Finish(SocialAlignment.Crew, "impostors_eliminated", output);
            else if (completedTasks.Count >= config.TaskThreshold) Finish(SocialAlignment.Crew, "tasks_completed", output);
            else if (impostors >= crew) Finish(SocialAlignment.Impostor, "parity", output);
        }
        private void Finish(SocialAlignment winner, string reason, List<Delivery> output)
        {
            if (IsTerminal) return;
            CancelAllWork("match_over", output); state.WinningSide = winner; state.WinReason = reason;
            emergency.Active = false; emergency.PausedForMeeting = false; emergency.EndsAtUnixMs = 0; pausedEmergencyRemaining = 0;
            ++emergency.ServerRevision; EmergencyChanged(output); Record("match_ended", "", "", reason);
            Transition(SocialPhase.GameOver, 0, output);
            summary = new SocialMatchSummary { MatchId = match, Mode = "SocialDeduction", WinningSide = winner, WinReason = reason,
                DurationMs = Started ? Math.Max(0, Math.Min(now, state.EndsAtUnixMs) - state.StartedAtUnixMs) : 0,
                Kills = kills, BodyReports = reports, Meetings = meetings, Votes = votes, CrewTasksCompleted = (uint)completedTasks.Count,
                Sabotages = sabotages, ReplayTruncated = replayTruncated };
            foreach (var p in players.Values.Where(p => p.Assigned).OrderBy(p => p.Pose.JoinOrder).ThenBy(p => p.Pose.Id))
                summary.Players.Add(new SocialFinalPlayer { ActorId = p.Pose.Id, DisplayName = SafeName(p.Pose.Name), Alignment = p.Alignment, Duty = p.Duty,
                    Life = p.Life, SurvivalMs = Math.Max(0, (p.EliminatedAt > 0 ? p.EliminatedAt : now) - state.StartedAtUnixMs),
                    TeamWon = winner != SocialAlignment.Unspecified && p.Alignment == winner, Survived = p.Life == SocialLife.Alive });
            summary.Replay.Add(history.Select(h => h.Clone()));
            foreach (var id in players.Keys) output.Add(new Delivery(id, new SocialMatchEndedEvent { Summary = summary.Clone() }));
        }
        private void Transition(SocialPhase phase, long end, List<Delivery> output)
        {
            state.Phase = phase; state.PhaseEndsAtUnixMs = end; ++state.PhaseRevision; PublicChanged(output);
            meeting.PhaseRevision = state.PhaseRevision;
            foreach (var p in players.Values)
            {
                OwnChanged(p, output);
                if (meeting.MeetingId.Length > 0 && (phase == SocialPhase.Discussion || phase == SocialPhase.Voting || phase == SocialPhase.VoteResolution))
                    output.Add(new Delivery(p.Pose.Id, new SocialMeetingChangedEvent { State = Meeting(p.Pose.Id) }));
            }
            Record("phase", "", "", phase.ToString());
        }
        private void PublicChanged(List<Delivery> output)
        { ++state.ServerRevision; foreach (string actor in players.Keys) output.Add(new Delivery(actor, new SocialMatchChangedEvent { State = State() })); }
        private void OwnChanged(Player p, List<Delivery> output)
        { ++p.OwnRevision; output.Add(new Delivery(p.Pose.Id, new SocialOwnStateChangedEvent { State = OwnState(p.Pose.Id) })); }
        private void EmergencyChanged(List<Delivery> output)
        { foreach (string actor in players.Keys) output.Add(new Delivery(actor, new SocialEmergencyChangedEvent { State = Emergency() })); }
        private bool Living(string actor) => actor != null && players.TryGetValue(actor, out var p) && p.Connected && p.Assigned && p.Life == SocialLife.Alive;
        private bool TargetAlive(string actor) => actor != null && players.TryGetValue(actor, out var p) && p.Assigned && p.Life == SocialLife.Alive
            && (p.Connected || p.TransportLostUntil > now);
        private bool Sees(string actor, double x, double y) => Living(actor) && Fresh(players[actor].Pose) && Finite(x) && Finite(y)
            && Distance(players[actor].Pose, x, y) <= config.VisibilityRadius && (canSee?.Invoke(actor, (float)x, (float)y) ?? false);
        private bool At(Player p, Terminal terminal) => terminal != null && terminal.Available && Fresh(p.Pose) && p.Pose.Room == terminal.Room
            && Distance(p.Pose, terminal.X, terminal.Y) <= Radius(terminal) && Sees(p.Pose.Id, terminal.X, terminal.Y);
        private double Radius(Terminal terminal) => terminal.Radius > 0 ? terminal.Radius : config.InteractionRadius;
        private static bool Fresh(Participant p) => p.PoseFresh && Coordinate(p.X) && Coordinate(p.Y)
            && Finite(p.VelocityX) && Math.Abs(p.VelocityX) <= 1000 && Finite(p.VelocityY) && Math.Abs(p.VelocityY) <= 1000;
        private static double Distance(Participant p, double x, double y) => Math.Sqrt((p.X - x) * (p.X - x) + (p.Y - y) * (p.Y - y));
        private ulong NextVisibleRevision(string actor)
        { if (!visibleRevisions.ContainsKey(actor)) return 0; return ++visibleRevisions[actor]; }
        private void Record(string kind, string actor, string target, string outcome)
        {
            if (history.Count >= MaxHistoryEntries) { replayTruncated = true; return; }
            history.Add(new SocialReplayEntry { Sequence = ++sequence, ElapsedMs = Started ? Math.Max(0, now - state.StartedAtUnixMs) : 0,
                Kind = kind, ActorId = actor, TargetId = target, Outcome = outcome });
        }
        private bool Rate(string actor)
        {
            if (!buckets.TryGetValue(actor, out var bucket)) buckets[actor] = bucket = new Bucket { At = now };
            bucket.Tokens = Math.Min(4, bucket.Tokens + Math.Max(0, now - bucket.At) / 1000.0 * 2); bucket.At = now;
            globalTokens = Math.Min(16, globalTokens + Math.Max(0, now - globalAt) / 1000.0 * 8); globalAt = now;
            if (bucket.Tokens < 1 || globalTokens < 1) return false;
            --bucket.Tokens; --globalTokens; return true;
        }
        private void Shuffle<T>(T[] values)
        {
            for (int i = values.Length - 1; i > 0; i--)
            {
                int j;
                if (fixtureRandom != null) j = fixtureRandom.Next(i + 1);
                else
                {
                    var bytes = new byte[4]; uint value, limit = uint.MaxValue - uint.MaxValue % (uint)(i + 1);
                    using (var random = RandomNumberGenerator.Create()) do { random.GetBytes(bytes); value = BitConverter.ToUInt32(bytes, 0); } while (value >= limit);
                    j = (int)(value % (uint)(i + 1));
                }
                T swap = values[i]; values[i] = values[j]; values[j] = swap;
            }
        }
        private static void ValidateConfiguration(Configuration c, bool trustedFixture)
        {
            if (c.MinPlayers < 4 || c.MaxPlayers > 6 || c.MinPlayers > c.MaxPlayers || c.ImpostorCount != 1
                || c.MatchDurationMs < 60000 || c.MatchDurationMs > 3600000 || c.DiscussionDurationMs < (trustedFixture ? 1000 : 10000) || c.DiscussionDurationMs > 300000
                || c.VotingDurationMs < (trustedFixture ? 1000 : 10000) || c.VotingDurationMs > 180000 || c.KillCooldownMs < (trustedFixture ? 1000 : 5000) || c.KillCooldownMs > 300000
                || c.EmergencyCooldownMs < (trustedFixture ? 1000 : 10000) || c.EmergencyCooldownMs > 600000 || c.SabotageCooldownMs < (trustedFixture ? 1000 : 10000) || c.SabotageCooldownMs > 600000
                || c.SabotageTimeoutMs < (trustedFixture ? 10000 : 30000) || c.SabotageTimeoutMs > 600000 || c.WorkDurationMs < 1000 || c.WorkDurationMs > 30000
                || c.WaitingTimeoutMs < (trustedFixture ? 1000 : 30000) || c.WaitingTimeoutMs > 900000 || c.TaskThreshold < 1 || c.TaskThreshold > 20
                || c.ReconnectGraceMs < 1000 || c.ReconnectGraceMs > 120000 || c.CalibrationStepMs < 500 || c.CalibrationStepMs > 10000
                || c.SensorSampleDelayMs < 1000 || c.SensorSampleDelayMs > 30000 || c.TaskDecisionTimeoutMs < 1000 || c.TaskDecisionTimeoutMs > 120000
                || c.MeetingLimit < 1 || c.MeetingLimit > 8 || c.PlayerMeetingLimit < 1 || c.PlayerMeetingLimit > 3 || c.PlayerMeetingLimit > c.MeetingLimit
                || !Finite(c.KillDistance) || c.KillDistance < .4 || c.KillDistance > 2 || !Finite(c.VisibilityRadius) || c.VisibilityRadius < 2 || c.VisibilityRadius > 8
                || !Finite(c.BodyReportDistance) || c.BodyReportDistance < .5 || c.BodyReportDistance > 2.5
                || !Finite(c.InteractionRadius) || c.InteractionRadius < .5 || c.InteractionRadius > 2 || c.KillDistance > c.VisibilityRadius)
                throw new ArgumentException("Invalid bounded social match configuration.");
        }
        private static SocialActionContext Context(IMessage message) => message switch
        {
            SocialReadyCommand c => c.Context, SocialKillCommand c => c.Context, SocialReportBodyCommand c => c.Context,
            SocialCallMeetingCommand c => c.Context, SocialCastVoteCommand c => c.Context, SocialWorkTaskCommand c => c.Context,
            SocialSabotageCommand c => c.Context, SocialRepairEmergencyCommand c => c.Context, SocialCancelWorkCommand c => c.Context,
            SocialTaskStepCommand c => c.Context, _ => null
        };
        private static SocialAction Action(IMessage message) => message switch
        {
            SocialReadyCommand _ => SocialAction.Ready, SocialKillCommand _ => SocialAction.Kill, SocialReportBodyCommand _ => SocialAction.ReportBody,
            SocialCallMeetingCommand _ => SocialAction.CallMeeting, SocialCastVoteCommand _ => SocialAction.CastVote, SocialWorkTaskCommand _ => SocialAction.WorkTask,
            SocialSabotageCommand _ => SocialAction.Sabotage, SocialRepairEmergencyCommand _ => SocialAction.RepairEmergency,
            SocialCancelWorkCommand _ => SocialAction.CancelWork, SocialTaskStepCommand _ => SocialAction.TaskStep, _ => SocialAction.Unspecified
        };
        private SocialActionResultEvent Result(IMessage command, bool accepted, string reason, bool completed = false, Work work = null) => new()
        { MatchId = match, CorrelationId = Bound(Context(command)?.CorrelationId), Action = Action(command), Accepted = accepted,
            Completed = completed, ReasonCode = reason, PhaseRevision = state.PhaseRevision, WorkId = work?.Id ?? "", WorkEndsAtUnixMs = work?.Ends ?? 0,
            TaskStepRevision = work?.StepRevision ?? 0, TaskStepIndex = (uint)(work?.Step ?? 0) };
        private static bool CommandBounds(IMessage command) => command switch
        {
            SocialKillCommand c => ActorIdentifier(c.TargetActorId), SocialReportBodyCommand c => Identifier(c.BodyId),
            SocialCastVoteCommand c => Identifier(c.MeetingId) && (c.Skip ? c.CandidateActorId.Length == 0 : ActorIdentifier(c.CandidateActorId)),
            SocialWorkTaskCommand c => Identifier(c.TaskId), SocialSabotageCommand c => Identifier(c.TerminalEntityId),
            SocialRepairEmergencyCommand c => Identifier(c.EmergencyId), SocialCancelWorkCommand c => Identifier(c.WorkId),
            SocialTaskStepCommand c => Identifier(c.WorkId) && c.StepRevision > 0 && c.Choice >= SocialTaskChoice.Confirm && c.Choice <= SocialTaskChoice.Falling, _ => true
        };
        private static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);
        private static string TaskKey(string actor, string task) => actor + ":" + task;
        private static bool Coordinate(double value) => Finite(value) && Math.Abs(value) <= 10000;
        private static bool Identifier(string value) => value != null && value.Length > 0 && value.Length <= 64 && value.All(c => char.IsLetterOrDigit(c) || c == '-' || c == '_' || c == '.');
        private static bool ActorIdentifier(string value) => value != null && value.Length > 0 && value.Length <= 128 && value.All(c => char.IsLetterOrDigit(c) || c == '-' || c == '_' || c == '.');
        private static string Bound(string value) => value == null ? "" : value.Substring(0, Math.Min(value.Length, 64));
        private static string BoundActor(string value) => value == null ? "" : value.Substring(0, Math.Min(value.Length, 128));
        private static string SafeName(string value) => value == null ? "" : value.Substring(0, Math.Min(value.Length, 24));
    }
}
