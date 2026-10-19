using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Actor.Protocol.Standard.Communication.V1;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;

namespace Actor.Networking
{
    // Server-only reference-game state. No Unity or transport dependency: the
    // caller supplies approved identities/physical poses and routes each result
    // to its exact recipient. Secrets never enter public progress/peer state.
    internal sealed class CooperativeAuthority
    {
        internal sealed class Participant
        {
            internal string Id = "", Name = "", Room = "";
            internal double X = 0, Y = 0;
        }
        internal sealed class Delivery
        {
            internal string Recipient;
            internal IMessage Payload;
            internal Delivery(string recipient, IMessage payload) { Recipient = recipient; Payload = payload; }
        }
        private sealed class Replay
        {
            internal byte[] Hash = Array.Empty<byte>();
            internal IMessage Reply = new MessageDeliveryResultEvent();
            internal long Expires;
        }
        private sealed class Pending
        {
            internal string Sender = "", Correlation = "", Task = "";
            internal HashSet<string> Recipients = new();
            internal MessageDeliveryResultEvent Receipt = new();
            internal long Expires;
            internal bool Authorization;
        }
        private sealed class Bucket { internal double Tokens = 4; internal long At; }
        private readonly Dictionary<string, Participant> actors = new();
        private readonly Dictionary<string, Replay> replay = new();
        private readonly Dictionary<string, Pending> pending = new();
        private readonly Dictionary<string, Bucket> buckets = new();
        private readonly string match;
        private readonly Func<string, string, bool> additionalTaskScope;
        private readonly Func<string, SendMessageCommand, string> additionalMessagePolicy;
        private readonly CalibrationTaskStateObservation task = new();
        private string challenge = "";
        private long nextAttempt, lastStart, globalAt;
        private double globalTokens = 16;
        private bool authorizationDelivered;
        private ulong sequence, rosterRevision;
        private bool diagnosticAvailable = true, calibrationAvailable = true;
        private double diagnosticX = 1, diagnosticY = -.65, calibrationX = .25, calibrationY = -6.54;
        internal const int MaxTextBytes = 512;
        internal const int MaxPending = 64;
        internal const int MaxReplays = 512;
        internal static readonly string[] Channels = { "direct", "station" };
        internal bool Active => task.Status == CalibrationStatus.AwaitingDiagnostic || task.Status == CalibrationStatus.AwaitingAuthorization;
        internal string MatchId => match;
        internal void SetTerminals(bool diagnosticExists, double dx, double dy, bool calibrationExists, double cx, double cy)
        { diagnosticAvailable = diagnosticExists; diagnosticX = dx; diagnosticY = dy; calibrationAvailable = calibrationExists; calibrationX = cx; calibrationY = cy; }

        internal CooperativeAuthority(string matchId, Func<string, string, bool> additionalTaskScope = null,
            Func<string, SendMessageCommand, string> additionalMessagePolicy = null)
        {
            if (!Guid.TryParseExact(matchId, "N", out _)) throw new ArgumentException("Expected server match identity.");
            match = matchId; task.MatchId = match; task.Status = CalibrationStatus.Available;
            this.additionalTaskScope = additionalTaskScope;
            this.additionalMessagePolicy = additionalMessagePolicy;
        }

        internal List<Delivery> Refresh(IEnumerable<Participant> approved, long now)
        {
            var list = approved.Take(17).ToArray();
            if (list.Length > 16 || list.Any(p => !Identifier(p.Id)) || list.Select(p => p.Id).Distinct().Count() != list.Length)
                throw new ArgumentException("Approved roster must contain distinct bounded actors.");
            var next = list.ToDictionary(p => p.Id);
            var output = Tick(now);
            foreach (string removed in actors.Keys.Where(id => !next.ContainsKey(id)).ToArray())
            {
                if (Active && ParticipantOfTask(removed)) Finish(CalibrationStatus.Cancelled, "participant_disconnected");
                buckets.Remove(removed);
                foreach (var p in pending.Where(p => p.Value.Sender == removed || p.Value.Recipients.Contains(removed)).ToArray())
                {
                    if (p.Value.Sender != removed)
                    {
                        var receipt = p.Value.Receipt.Clone(); receipt.Stage = MessageDeliveryStage.Rejected;
                        receipt.ReasonCode = "recipient_disconnected"; CacheReceipt(p.Value.Sender, p.Value.Correlation, receipt);
                        output.Add(new Delivery(p.Value.Sender, receipt));
                    }
                    pending.Remove(p.Key);
                }
            }
            bool changed = actors.Count != next.Count || actors.Any(p => !next.TryGetValue(p.Key, out var value) || value.Name != p.Value.Name);
            actors.Clear(); foreach (var p in next) actors.Add(p.Key, p.Value);
            if (changed)
            {
                ++rosterRevision;
                foreach (string id in actors.Keys) output.Add(new Delivery(id, Peers()));
                output.AddRange(StateDeliveries());
            }
            return output;
        }

        internal CommunicationPeersObservation Peers()
        {
            var value = new CommunicationPeersObservation { MatchId = match, ServerRevision = rosterRevision };
            value.AllowedChannels.Add(Channels);
            value.Peers.Add(actors.Values.OrderBy(p => p.Id).Select(p => new CommunicationPeer { ActorId = p.Id, DisplayName = p.Name ?? "" }));
            return value;
        }
        internal CalibrationTaskStateObservation StateFor(string actor)
            => ParticipantOfTask(actor) ? task.Clone() : new CalibrationTaskStateObservation { MatchId = match };
        internal CalibrationProgressObservation Progress() => new()
        { TaskId = task.TaskId, Status = task.Status, ServerRevision = task.ServerRevision, MatchId = match, ReasonCode = task.ReasonCode };
        private bool ParticipantOfTask(string actor) => actor == task.CoordinatorActorId || actor == task.TechnicianActorId;

        internal List<Delivery> Tick(long now)
        {
            var output = new List<Delivery>();
            if (Active && now >= task.ExpiresAtUnixMs)
            { Finish(CalibrationStatus.Expired, "authorization_expired"); output.AddRange(StateDeliveries()); }
            foreach (string key in replay.Where(x => x.Value.Expires <= now).Select(x => x.Key).ToArray()) replay.Remove(key);
            foreach (var p in pending.Where(x => x.Value.Expires <= now).ToArray())
            {
                var receipt = p.Value.Receipt.Clone(); receipt.Stage = MessageDeliveryStage.Rejected;
                receipt.ReasonCode = "client_ack_timeout"; CacheReceipt(p.Value.Sender, p.Value.Correlation, receipt);
                output.Add(new Delivery(p.Value.Sender, receipt)); pending.Remove(p.Key);
            }
            return output;
        }

        internal List<Delivery> Execute(string actor, IMessage command, long now)
        {
            var output = Tick(now);
            if (!actors.TryGetValue(actor, out var caller)) return output;
            string correlation = Correlation(command), commandMatch = CommandMatch(command);
            if (!Identifier(correlation) || commandMatch != match)
            { output.Add(new Delivery(actor, Rejection(command, correlation, commandMatch != match ? "match_mismatch" : "invalid_correlation", now))); return output; }
            string key = actor + ":" + correlation;
            byte[] hash;
            using (var sha = SHA256.Create())
                hash = sha.ComputeHash(Encoding.UTF8.GetBytes(command.Descriptor.FullName + "\0").Concat(command.ToByteArray()).ToArray());
            if (replay.TryGetValue(key, out var previous))
            {
                if (!previous.Hash.SequenceEqual(hash)) output.Add(new Delivery(actor, Rejection(command, correlation, "correlation_conflict", now)));
                else if (previous.Reply is DiagnosticResultEvent && !Active)
                    output.Add(new Delivery(actor, Rejection(command, correlation, "task_not_active", now)));
                else output.Add(new Delivery(actor, Clone(previous.Reply)));
                return output;
            }
            if (replay.Count >= MaxReplays || !Rate(actor, now))
            { output.Add(new Delivery(actor, Rejection(command, correlation, replay.Count >= MaxReplays ? "request_capacity" : "rate_limited", now))); return output; }
            List<Delivery> action = command is SendMessageCommand send ? Send(actor, send, now) : Calibrate(caller, command, now);
            output.AddRange(action);
            var reply = action.LastOrDefault(d => d.Recipient == actor && (d.Payload is MessageDeliveryResultEvent || d.Payload is CalibrationResultEvent || d.Payload is DiagnosticResultEvent));
            if (command is InspectDiagnosticCommand)
                reply = action.LastOrDefault(d => d.Recipient == actor && d.Payload is DiagnosticResultEvent) ?? reply;
            if (reply != null) replay[key] = new Replay { Hash = hash, Reply = Clone(reply.Payload), Expires = now + 300000 };
            return output;
        }

        private bool Rate(string actor, long now)
        {
            if (!buckets.TryGetValue(actor, out var b)) buckets[actor] = b = new Bucket { At = now };
            b.Tokens = Math.Min(4, b.Tokens + Math.Max(0, now - b.At) * .002); b.At = now;
            globalTokens = Math.Min(16, globalTokens + Math.Max(0, now - globalAt) * .008); globalAt = now;
            if (b.Tokens < 1 || globalTokens < 1) return false;
            --b.Tokens; --globalTokens; return true;
        }

        private List<Delivery> Send(string actor, SendMessageCommand command, long now)
        {
            string reason = "";
            if (command.ChannelId != "direct" && command.ChannelId != "station") reason = "channel_unavailable";
            else if (string.IsNullOrWhiteSpace(command.Text) || Encoding.UTF8.GetByteCount(command.Text) > MaxTextBytes) reason = "invalid_text";
            else if (command.TtlMs < 1000 || command.TtlMs > 300000) reason = "invalid_ttl";
            else if (command.ChannelId == "direct" && (!actors.ContainsKey(command.RecipientActorId) || command.RecipientActorId == actor)) reason = "recipient_unavailable";
            else if (command.ChannelId == "station" && command.RecipientActorId.Length > 0) reason = "invalid_recipient";
            else if (command.RecipientActorId.Length > 64 || command.TaskId.Length > 64) reason = "invalid_identifier";
            else if (command.Sensitive && command.ChannelId != "direct") reason = "sensitive_requires_direct";
            else if (pending.Count >= MaxPending) reason = "delivery_capacity";
            else if (command.TaskId.Length > 0 && !(command.TaskId == task.TaskId && ParticipantOfTask(actor))
                && !(additionalTaskScope?.Invoke(actor, command.TaskId) ?? false)) reason = "task_not_assigned";
            if (reason.Length == 0) reason = additionalMessagePolicy?.Invoke(actor, command) ?? "";
            bool hasChallenge = challenge.Length > 0 && command.Text.IndexOf(challenge, StringComparison.OrdinalIgnoreCase) >= 0;
            if (hasChallenge && (command.ChannelId != "direct" || actor != task.CoordinatorActorId || command.RecipientActorId != task.TechnicianActorId))
                reason = "authorization_requires_private_partner";
            if (reason.Length > 0) return new List<Delivery> { new(actor, Rejection(command, command.CorrelationId, reason, now)) };
            bool sensitive = command.Sensitive || hasChallenge;
            string taskId = hasChallenge ? task.TaskId : command.TaskId;
            long expiry = now + command.TtlMs;
            if (sensitive && taskId == task.TaskId && task.TaskId.Length > 0) expiry = Math.Min(expiry, task.ExpiresAtUnixMs);
            if (expiry <= now) return new List<Delivery> { new(actor, Rejection(command, command.CorrelationId, "authorization_expired", now)) };
            string messageId = Guid.NewGuid().ToString("N");
            var recipients = command.ChannelId == "direct" ? new[] { command.RecipientActorId } : actors.Keys.ToArray();
            var receipt = new MessageDeliveryResultEvent { CorrelationId = command.CorrelationId, MessageId = messageId,
                AcceptedByServer = true, Stage = MessageDeliveryStage.ServerAccepted, ReasonCode = "server_accepted",
                ServerUnixMs = now, RecipientActorId = command.RecipientActorId, TaskId = taskId, MatchId = match };
            var output = new List<Delivery> { new(actor, receipt) };
            ulong serverSequence = ++sequence;
            foreach (string recipient in recipients)
                output.Add(new Delivery(recipient, new MessageReceivedEvent { MessageId = messageId, SenderActorId = actor,
                    RecipientActorId = recipient, ChannelId = command.ChannelId, Text = command.Text, ServerUnixMs = now,
                    CorrelationId = command.CorrelationId, TaskId = taskId, Sensitive = sensitive,
                    ExpiresAtUnixMs = expiry, MatchId = match, ServerSequence = serverSequence }));
            pending[messageId] = new Pending { Sender = actor, Correlation = command.CorrelationId, Task = taskId,
                Recipients = new HashSet<string>(recipients), Receipt = receipt, Expires = Math.Min(expiry, now + 10000), Authorization = hasChallenge };
            return output;
        }

        internal List<Delivery> Acknowledge(string actor, string messageId, string matchId, long now)
        {
            var output = Tick(now);
            if (matchId != match || !pending.TryGetValue(messageId, out var value) || !value.Recipients.Remove(actor)) return output;
            if (value.Authorization && actor == task.TechnicianActorId && value.Task == task.TaskId && Active) authorizationDelivered = true;
            if (value.Recipients.Count != 0) return output;
            var receipt = value.Receipt.Clone(); receipt.Stage = MessageDeliveryStage.ClientReceived;
            receipt.ReasonCode = "client_received"; receipt.ServerUnixMs = now;
            pending.Remove(messageId);
            CacheReceipt(value.Sender, value.Correlation, receipt);
            output.Add(new Delivery(value.Sender, receipt)); return output;
        }

        private void CacheReceipt(string sender, string correlation, MessageDeliveryResultEvent receipt)
        { if (replay.TryGetValue(sender + ":" + correlation, out var cached)) cached.Reply = receipt.Clone(); }

        private List<Delivery> Calibrate(Participant caller, IMessage command, long now)
        {
            string correlation = Correlation(command), reason = "";
            var action = Action(command); var output = new List<Delivery>();
            if (TaskId(command).Length > 64 || (command is SubmitCalibrationCommand invalid && invalid.AuthorizationCode.Length > 64))
                return new List<Delivery> { new(caller.Id, Rejection(command, correlation, "invalid_identifier", now)) };
            if (command is StartCalibrationCommand start)
            {
                if (Active) reason = "task_already_active";
                else if (!diagnosticAvailable) reason = "terminal_unavailable";
                else if (!actors.ContainsKey(start.TechnicianActorId) || start.TechnicianActorId == caller.Id) reason = "distinct_partner_required";
                else if (!AtDiagnostic(caller)) reason = "diagnostic_out_of_range";
                else if (now < lastStart + 2000) reason = "task_cooldown";
                else
                {
                    lastStart = now; authorizationDelivered = false; nextAttempt = 0;
                    byte[] bytes = new byte[16]; using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(bytes);
                    challenge = BitConverter.ToString(bytes).Replace("-", "");
                    task.TaskId = Guid.NewGuid().ToString("N"); task.Status = CalibrationStatus.AwaitingDiagnostic;
                    task.CoordinatorActorId = caller.Id; task.TechnicianActorId = start.TechnicianActorId;
                    task.ExpiresAtUnixMs = now + 300000; task.AttemptsRemaining = 3; task.ReasonCode = "participants_assigned"; ++task.ServerRevision;
                    foreach (string id in new[] { caller.Id, start.TechnicianActorId })
                        output.Add(new Delivery(id, new CalibrationAssignmentEvent { TaskId = task.TaskId,
                            CoordinatorActorId = caller.Id, TechnicianActorId = start.TechnicianActorId,
                            Role = id == caller.Id ? CalibrationRole.Coordinator : CalibrationRole.Technician,
                            ExpiresAtUnixMs = task.ExpiresAtUnixMs, MatchId = match, ServerRevision = task.ServerRevision }));
                    output.AddRange(StateDeliveries());
                }
            }
            else
            {
                string requested = TaskId(command);
                if (requested != task.TaskId || requested.Length == 0) reason = "task_not_found";
                else if (!Active) reason = task.Status == CalibrationStatus.Expired ? "authorization_expired" : "task_not_active";
                else if (!ParticipantOfTask(caller.Id)) reason = "task_not_assigned";
                else if (command is InspectDiagnosticCommand)
                {
                    if (caller.Id != task.CoordinatorActorId) reason = "coordinator_required";
                    else if (!diagnosticAvailable) reason = "terminal_unavailable";
                    else if (!AtDiagnostic(caller)) reason = "diagnostic_out_of_range";
                    else
                    {
                        task.Status = CalibrationStatus.AwaitingAuthorization; task.ReasonCode = "diagnostic_issued"; ++task.ServerRevision;
                        output.AddRange(StateDeliveries());
                    }
                }
                else if (command is SubmitCalibrationCommand submit)
                {
                    if (caller.Id != task.TechnicianActorId) reason = "technician_required";
                    else if (!calibrationAvailable) reason = "terminal_unavailable";
                    else if (!AtCalibration(caller)) reason = "calibration_out_of_range";
                    else if (task.Status != CalibrationStatus.AwaitingAuthorization) reason = "diagnostic_required";
                    else if (now < nextAttempt) reason = "attempt_cooldown";
                    else if (!authorizationDelivered) reason = "authorization_not_delivered";
                    else
                    {
                        nextAttempt = now + 2000;
                        if (!SecretEquals(submit.AuthorizationCode, challenge))
                        { --task.AttemptsRemaining; reason = "invalid_authorization"; ++task.ServerRevision;
                            if (task.AttemptsRemaining == 0) Finish(CalibrationStatus.Failed, "attempts_exhausted"); }
                        else Finish(CalibrationStatus.Completed, "calibration_completed");
                        output.AddRange(StateDeliveries());
                    }
                }
                else if (command is CancelCalibrationCommand)
                { Finish(CalibrationStatus.Cancelled, "participant_cancelled"); output.AddRange(StateDeliveries()); }
                else reason = "invalid_action";
            }
            output.Add(new Delivery(caller.Id, new CalibrationResultEvent { CorrelationId = correlation, Action = action,
                Accepted = reason.Length == 0, ReasonCode = reason.Length == 0 ? "accepted" : reason,
                TaskId = task.TaskId, Status = task.Status, ServerRevision = task.ServerRevision, MatchId = match }));
            if (command is InspectDiagnosticCommand)
                output.Add(new Delivery(caller.Id, new DiagnosticResultEvent { CorrelationId = correlation, TaskId = task.TaskId,
                    Accepted = reason.Length == 0, ReasonCode = reason.Length == 0 ? "accepted" : reason,
                    AuthorizationCode = reason.Length == 0 ? challenge : "", Sensitive = true,
                    ExpiresAtUnixMs = reason.Length == 0 ? task.ExpiresAtUnixMs : 0, ServerRevision = task.ServerRevision, MatchId = match }));
            return output;
        }

        internal List<Delivery> ControlUnavailable(string actor)
        {
            if (!Active || !ParticipantOfTask(actor)) return new List<Delivery>();
            Finish(CalibrationStatus.Cancelled, "participant_control_unavailable"); return StateDeliveries();
        }
        private void Finish(CalibrationStatus status, string reason)
        {
            task.Status = status; task.ReasonCode = reason; ++task.ServerRevision; challenge = ""; authorizationDelivered = false;
            foreach (string key in replay.Where(x => x.Value.Reply is DiagnosticResultEvent).Select(x => x.Key).ToArray()) replay.Remove(key);
        }
        private List<Delivery> StateDeliveries()
        {
            var output = new List<Delivery>();
            foreach (string id in actors.Keys)
            {
                output.Add(new Delivery(id, Progress()));
                output.Add(new Delivery(id, StateFor(id)));
                if (ParticipantOfTask(id)) output.Add(new Delivery(id, new CalibrationTaskChangedEvent { State = task.Clone() }));
            }
            return output;
        }
        private bool AtDiagnostic(Participant p) => p.Room == "main-hall" && Within(p, diagnosticX, diagnosticY, 1.6);
        private bool AtCalibration(Participant p) => p.Room == "engineering" && Within(p, calibrationX, calibrationY, 1.25);
        private static bool Within(Participant p, double x, double y, double radius)
            => !double.IsNaN(p.X) && !double.IsNaN(p.Y) && !double.IsInfinity(p.X) && !double.IsInfinity(p.Y)
                && (p.X - x) * (p.X - x) + (p.Y - y) * (p.Y - y) <= radius * radius;
        private static bool Identifier(string value) => !string.IsNullOrWhiteSpace(value) && value.Length <= 64;
        private static bool SecretEquals(string a, string b)
        {
            if (a.Length != b.Length || a.Length == 0) return false;
            int different = 0; for (int i = 0; i < a.Length; ++i) different |= a[i] ^ b[i]; return different == 0;
        }
        private static IMessage Clone(IMessage value) => value.Descriptor.Parser.ParseFrom(value.ToByteArray());
        private static string Bound(string value) => value.Length <= 64 ? value : value.Substring(0, 64);
        private IMessage Rejection(IMessage command, string correlation, string reason, long now)
            => command is SendMessageCommand send ? new MessageDeliveryResultEvent { CorrelationId = Bound(correlation),
                AcceptedByServer = false, ReasonCode = reason, Stage = MessageDeliveryStage.Rejected,
                ServerUnixMs = now, RecipientActorId = Bound(send.RecipientActorId), TaskId = Bound(send.TaskId), MatchId = match }
            : command is InspectDiagnosticCommand ? new DiagnosticResultEvent { CorrelationId = Bound(correlation),
                TaskId = Bound(TaskId(command)), Accepted = false, ReasonCode = reason, Sensitive = true, MatchId = match }
            : new CalibrationResultEvent { CorrelationId = Bound(correlation), Action = Action(command), Accepted = false,
                ReasonCode = reason, TaskId = Bound(TaskId(command)), Status = task.Status, ServerRevision = task.ServerRevision, MatchId = match };
        private static string Correlation(IMessage m) => m switch { SendMessageCommand x => x.CorrelationId, StartCalibrationCommand x => x.CorrelationId,
            InspectDiagnosticCommand x => x.CorrelationId, SubmitCalibrationCommand x => x.CorrelationId, CancelCalibrationCommand x => x.CorrelationId, _ => "" };
        private static string CommandMatch(IMessage m) => m switch { SendMessageCommand x => x.MatchId, StartCalibrationCommand x => x.MatchId,
            InspectDiagnosticCommand x => x.MatchId, SubmitCalibrationCommand x => x.MatchId, CancelCalibrationCommand x => x.MatchId, _ => "" };
        private static string TaskId(IMessage m) => m switch { InspectDiagnosticCommand x => x.TaskId, SubmitCalibrationCommand x => x.TaskId, CancelCalibrationCommand x => x.TaskId, _ => "" };
        private static CalibrationAction Action(IMessage m) => m switch { StartCalibrationCommand _ => CalibrationAction.Start,
            InspectDiagnosticCommand _ => CalibrationAction.InspectDiagnostic, SubmitCalibrationCommand _ => CalibrationAction.Submit,
            CancelCalibrationCommand _ => CalibrationAction.Cancel, _ => CalibrationAction.Unspecified };
    }
}
