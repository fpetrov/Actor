using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using UnityEngine;

namespace Actor.Networking
{
    // Explicit local integration fixture. Every request travels through the
    // ordinary owner's Submit/RPC path; this component has no authority access.
    public sealed class SocialProtocolFaultProbe : MonoBehaviour
    {
        private const double LifetimeSeconds = 300, ReceiptTimeoutSeconds = 10;
        [Serializable] private sealed class Check
        {
            public string name, outcome = "prerequisite_not_observed";
            public bool completed, passed;
        }
        [Serializable] private sealed class Report
        {
            public string schema = "actor.social-protocol-fault-probe.v1";
            public string scope = "Actual ordinary owned RPC and private authoritative action results; no roles, poses, IDs or payloads.";
            public bool completed, passed;
            public double duration_seconds;
            public int requests_submitted, matching_action_results, probe_failures;
            public Check[] checks;
        }
        private sealed class Receipt
        {
            internal long Sequence;
            internal SocialActionResultEvent Value;
        }
        private readonly Report report = new();
        private readonly List<Receipt> receipts = new();
        private readonly Check[] checks =
        {
            new() { name = "wrong_match_rejected" },
            new() { name = "expired_request_rejected" },
            new() { name = "stale_phase_kill_rejected" },
            new() { name = "self_target_kill_rejected" },
            new() { name = "identical_request_replayed" },
            new() { name = "cross_type_correlation_conflict_rejected" },
            new() { name = "final_ballot_not_overwritten" },
            new() { name = "cancel_before_start_ack_stops_work" }
        };
        private NetworkSocialMatch owner;
        private string output, pendingCorrelation = "", workCorrelation = "", cancelCorrelation = "";
        private long sequence, pendingAfter, workAfter;
        private double started, sentAt, nextSend, nextSave, workSentAt;
        private int basicStage;
        private SocialKillCommand replayCommand;
        private SocialActionResultEvent originalReply;
        private SocialAction pendingAction;
        private string pendingReason = "";
        private bool voteStarted, voteSecondSent, workStarted, cancelSubmitted, ackBeforeCancel;
        private bool workAccepted, workInterrupted, cancelAccepted, workCompleted;
        private string ballotCandidate = "";
        private bool ballotSkip;
        private string ballotMeeting = "";
        private ulong ballotPhase;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap()
        {
#if !UNITY_SERVER
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "--social-protocol-fault-report");
            if (index < 0 || index + 1 >= args.Length || FindFirstObjectByType<SocialProtocolFaultProbe>() != null) return;
            var root = new GameObject("Opt-in Social Protocol Fault Probe");
            DontDestroyOnLoad(root);
            var probe = root.AddComponent<SocialProtocolFaultProbe>();
            probe.output = Path.GetFullPath(args[index + 1]);
#endif
        }
        private void Awake() { started = Time.realtimeSinceStartupAsDouble; report.checks = checks; }
        private void Update()
        {
            if (report.completed) return;
            double now = Time.realtimeSinceStartupAsDouble;
            try
            {
                var current = NetworkSocialMatch.LocalOwner;
                if (owner != current)
                {
                    if (owner != null) owner.PermittedEvent -= Observe;
                    owner = current;
                    if (owner != null) owner.PermittedEvent += Observe;
                }
                if (now - started >= LifetimeSeconds)
                {
                    foreach (var check in checks.Where(check => !check.completed)) Complete(check, false, "bounded_prerequisite_or_receipt_timeout");
                    Finish(); return;
                }
                if (owner != null && owner.HasOwnState && owner.Own.Life == SocialLife.Alive)
                {
                    if (basicStage < 6) Basic(now);
                    else
                    {
                        Vote(now);
                        CancelBeforeAck(now);
                    }
                }
                if (checks.All(check => check.completed)) Finish();
                if (now >= nextSave) { nextSave = now + .5; Save(); }
            }
            catch (Exception)
            {
                ++report.probe_failures;
                foreach (var check in checks.Where(check => !check.completed)) Complete(check, false, "probe_failed");
                Finish();
            }
        }
        private void Observe(IMessage value)
        {
            if (value is not SocialActionResultEvent result) return;
            receipts.Add(new Receipt { Sequence = ++sequence, Value = result.Clone() });
            while (receipts.Count > 64) receipts.RemoveAt(0);
            if (workStarted && result.CorrelationId == workCorrelation && !cancelSubmitted) ackBeforeCancel = true;
        }
        private SocialActionResultEvent Take(string correlation, long after)
        {
            var receipt = receipts.FirstOrDefault(item => item.Sequence > after && item.Value.CorrelationId == correlation);
            if (receipt == null) return null;
            receipts.Remove(receipt); ++report.matching_action_results; return receipt.Value;
        }
        private bool Submit(IMessage command)
        {
            if (!owner.Submit(command)) return false;
            ++report.requests_submitted; return true;
        }
        private void Basic(double now)
        {
            if (pendingCorrelation.Length > 0)
            {
                var result = Take(pendingCorrelation, pendingAfter);
                if (result == null && now - sentAt < ReceiptTimeoutSeconds) return;
                bool valid = result != null && !result.Accepted && !result.Completed && result.Action == pendingAction && result.ReasonCode == pendingReason;
                if (basicStage == 3 && valid) originalReply = result.Clone();
                if (basicStage == 4) valid &= originalReply != null && originalReply.Equals(result);
                Complete(checks[basicStage], valid, valid ? "authoritative_outcome_verified" : "authoritative_outcome_missing_or_unexpected");
                pendingCorrelation = ""; ++basicStage; nextSend = now + .75; return;
            }
            if (now < nextSend) return;
            var context = owner.Context();
            IMessage command;
            pendingAction = SocialAction.Kill;
            switch (basicStage)
            {
                case 0: context.MatchId = Guid.NewGuid().ToString("N"); pendingReason = "match_mismatch"; break;
                case 1: context.ExpiresAtUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - 1000; pendingReason = "action_expired"; break;
                case 2: context.PhaseRevision = owner.State.PhaseRevision - 1; pendingReason = "phase_changed"; break;
                default: pendingReason = "target_unavailable"; break;
            }
            if (basicStage == 4)
            {
                if (replayCommand == null) { Complete(checks[basicStage++], false, "initial_rejection_not_recorded"); return; }
                command = replayCommand.Clone(); context = replayCommand.Context;
            }
            else if (basicStage == 5)
            {
                if (replayCommand == null) { Complete(checks[basicStage++], false, "initial_rejection_not_recorded"); return; }
                context = replayCommand.Context.Clone();
                command = new SocialCancelWorkCommand { Context = context, WorkId = context.CorrelationId };
                pendingAction = SocialAction.CancelWork; pendingReason = "correlation_conflict";
            }
            else
            {
                var kill = new SocialKillCommand { Context = context, TargetActorId = owner.Self };
                if (basicStage == 3) replayCommand = kill.Clone();
                command = kill;
            }
            pendingAfter = sequence; pendingCorrelation = context.CorrelationId; sentAt = now;
            if (!Submit(command))
            { Complete(checks[basicStage++], false, "local_submit_rejected"); pendingCorrelation = ""; }
        }
        private void Vote(double now)
        {
            var check = checks[6];
            if (check.completed) return;
            if (!voteStarted)
            {
                if (owner.State.Phase != SocialPhase.Voting || owner.Meeting.MeetingId.Length == 0) return;
                voteStarted = true;
                ballotMeeting = owner.Meeting.MeetingId; ballotPhase = owner.State.PhaseRevision;
                if (!owner.Meeting.OwnBallotSubmitted)
                {
                    var context = owner.Context(); pendingCorrelation = context.CorrelationId; pendingAfter = sequence; sentAt = now;
                    if (!Submit(new SocialCastVoteCommand { Context = context, MeetingId = owner.Meeting.MeetingId, Skip = true }))
                        Complete(check, false, "local_submit_rejected");
                    return;
                }
                pendingCorrelation = "";
            }
            if (!voteSecondSent)
            {
                if (owner.State.Phase != SocialPhase.Voting || owner.State.PhaseRevision != ballotPhase || owner.Meeting.MeetingId != ballotMeeting)
                { Complete(check, false, "voting_ended_before_second_ballot"); pendingCorrelation = ""; return; }
                if (pendingCorrelation.Length > 0)
                {
                    var first = Take(pendingCorrelation, pendingAfter);
                    if (first == null && now - sentAt < ReceiptTimeoutSeconds) return;
                    if (first == null || !first.Accepted || first.Action != SocialAction.CastVote || first.ReasonCode != "vote_recorded")
                    { Complete(check, false, "initial_ballot_not_accepted"); pendingCorrelation = ""; return; }
                    pendingCorrelation = ""; nextSend = now + .75;
                }
                if (now < nextSend || !owner.Meeting.OwnBallotSubmitted) return;
                ballotCandidate = owner.Meeting.OwnCandidateActorId; ballotSkip = owner.Meeting.OwnSkip;
                var context = owner.Context(); pendingCorrelation = context.CorrelationId; pendingAfter = sequence; sentAt = now;
                voteSecondSent = true;
                if (!Submit(new SocialCastVoteCommand { Context = context, MeetingId = owner.Meeting.MeetingId,
                    Skip = !ballotSkip, CandidateActorId = ballotSkip ? owner.Self : "" })) Complete(check, false, "local_submit_rejected");
                return;
            }
            var result = Take(pendingCorrelation, pendingAfter);
            if (result == null && now - sentAt < ReceiptTimeoutSeconds) return;
            bool valid = result != null && !result.Accepted && result.Action == SocialAction.CastVote && result.ReasonCode == "ballot_unavailable"
                && result.PhaseRevision == ballotPhase
                && owner.Meeting.OwnBallotSubmitted && owner.Meeting.OwnSkip == ballotSkip && owner.Meeting.OwnCandidateActorId == ballotCandidate;
            Complete(check, valid, valid ? "authoritative_final_ballot_preserved" : "authoritative_ballot_outcome_missing_or_unexpected");
            pendingCorrelation = "";
        }
        private void CancelBeforeAck(double now)
        {
            var check = checks[7];
            if (check.completed) return;
            if (!workStarted)
            {
                if (owner.State.Phase != SocialPhase.Exploration || owner.ActiveWorkId.Length > 0 || now < nextSend) return;
                var character = owner.GetComponent<NetworkCharacter>();
                if (!character.HasAuthoritativeSnapshot || character.AuthoritativeSnapshotAge > .5f || character.AuthoritativeVelocity.sqrMagnitude > .0025f) return;
                Vector2 center = character.AuthoritativePosition + owner.GetComponent<CapsuleCollider2D>().offset;
                var world = owner.CaptureWorld();
                var task = owner.Tasks.Tasks.FirstOrDefault(task => !task.Completed && world.Interactables.Any(terminal => terminal.EntityId == task.TerminalEntityId
                    && Vector2.Distance(center, new Vector2(terminal.X, terminal.Y)) <= Math.Min(terminal.Radius, owner.State.InteractionRadius)));
                if (task == null) return;
                var start = owner.Context(); var cancel = owner.Context();
                workCorrelation = start.CorrelationId; cancelCorrelation = cancel.CorrelationId; workAfter = sequence; workSentAt = now; workStarted = true;
                if (!Submit(new SocialWorkTaskCommand { Context = start, TaskId = task.TaskId }))
                { Complete(check, false, "local_submit_rejected"); return; }
                // Deliberately do not wait for or inspect the start receipt.
                cancelSubmitted = true;
                if (!Submit(new SocialCancelWorkCommand { Context = cancel, WorkId = workCorrelation }))
                    Complete(check, false, "local_submit_rejected");
                return;
            }
            foreach (var receipt in receipts.Where(item => item.Sequence > workAfter && (item.Value.CorrelationId == workCorrelation || item.Value.CorrelationId == cancelCorrelation)).ToArray())
            {
                receipts.Remove(receipt); ++report.matching_action_results;
                var result = receipt.Value;
                if (result.CorrelationId == workCorrelation)
                {
                    workAccepted |= result.Action == SocialAction.WorkTask && result.Accepted && !result.Completed && result.ReasonCode == "work_started";
                    workInterrupted |= result.Action == SocialAction.WorkTask && !result.Accepted && !result.Completed && result.ReasonCode == "work_cancelled";
                    workCompleted |= result.Completed;
                }
                else cancelAccepted |= result.Action == SocialAction.CancelWork && result.Accepted && result.Completed && result.ReasonCode == "work_cancelled";
            }
            if (workAccepted && workInterrupted && cancelAccepted || workCompleted || ackBeforeCancel || now - workSentAt >= ReceiptTimeoutSeconds)
            {
                bool valid = workAccepted && workInterrupted && cancelAccepted && !workCompleted && !ackBeforeCancel && owner.ActiveWorkId.Length == 0;
                Complete(check, valid, valid ? "ordinary_work_cancelled_before_start_ack" : "authoritative_cancel_outcome_missing_or_unexpected");
            }
        }
        private static void Complete(Check check, bool passed, string outcome)
        { check.completed = true; check.passed = passed; check.outcome = outcome; }
        private void Finish()
        { report.completed = true; report.passed = report.probe_failures == 0 && checks.All(check => check.completed && check.passed); Save(); }
        private void Save()
        {
            if (string.IsNullOrEmpty(output)) return;
            report.duration_seconds = Time.realtimeSinceStartupAsDouble - started;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(output));
                string temporary = output + ".tmp";
                File.WriteAllText(temporary, JsonUtility.ToJson(report, true) + "\n");
                if(File.Exists(output))File.Replace(temporary,output,null);
                else File.Move(temporary,output);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
            { ++report.probe_failures; report.passed = false; }
        }
        private void OnDestroy()
        { if (owner != null) owner.PermittedEvent -= Observe; Save(); }
    }
}
