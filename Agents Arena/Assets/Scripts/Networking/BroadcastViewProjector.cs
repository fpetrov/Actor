using System;
using System.Collections.Generic;
using System.Linq;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;

namespace Actor.Networking
{
    // All inputs stay on the server. This is the ONLY observer wire projector.
    // Its actor input deliberately has no alignment, task truth, cooldown or duty.
    internal sealed class BroadcastViewProjector
    {
        internal sealed class Actor
        {
            internal string Id, Name, Room; internal float X, Y, VelocityX, VelocityY;
            internal uint Facing, Cosmetic; internal bool Alive;
        }
        internal sealed class Body
        { internal string Id, Victim, Name; internal float X, Y; internal uint Cosmetic; }
        private readonly BroadcastScope scope;
        private readonly Func<float, float, float, float, bool> sight;
        private readonly List<BroadcastEditorialFact> facts = new();
        private readonly Queue<string> revoked = new();
        private readonly HashSet<string> seen = new(StringComparer.Ordinal);
        private readonly Queue<string> seenOrder = new();
        private readonly Dictionary<string, string> lastRooms = new(StringComparer.Ordinal);
        private readonly Dictionary<string, (float X, float Y)> anchors = new(StringComparer.Ordinal)
        { ["main-hall"] = (0, -.94f), ["engineering"] = (0, -6.54f), ["security"] = (6.44f, 3.77f), ["storage"] = (5.34f, -6.83f) };
        private BroadcastCamera camera = new() { AnchorId = "main-hall", X = 0, Y = -.94f, VisibilityRadius = 3.5f, OrthographicSize = 3.4f, Aspect = 4f / 3 };
        private BroadcastView last, presented;
        private readonly Dictionary<ulong, BroadcastView> prepared = new();
        private readonly HashSet<string> committed = new(StringComparer.Ordinal);
        private long switchedAt, now;
        private ulong sequence, semantic = 1;
        private string publicSignature = "";
        internal BroadcastViewProjector(BroadcastScope identity, Func<float, float, float, float, bool> lineOfSight, long at)
        {
            if (!BroadcastObserverRules.ValidScope(identity)) throw new ArgumentException("Invalid observer scope.");
            scope = identity.Clone(); sight = lineOfSight ?? throw new ArgumentNullException(nameof(lineOfSight)); switchedAt = at;
        }
        internal BroadcastScope Scope => scope.Clone();
        internal BroadcastView Last => last?.Clone();
        internal bool Permits(float x, float y)
        {
            if (!float.IsFinite(x) || !float.IsFinite(y)) return false;
            float dx = x - camera.X, dy = y - camera.Y;
            return dx * dx + dy * dy <= camera.VisibilityRadius * camera.VisibilityRadius
                && Math.Abs(dx) <= camera.OrthographicSize * camera.Aspect && Math.Abs(dy) <= camera.OrthographicSize
                && sight(camera.X, camera.Y, x, y);
        }
        internal BroadcastViewSelectionResultEvent Select(BroadcastSelectViewCommand command, long at)
        {
            bool allowed = command != null && BroadcastObserverRules.SameScope(scope, command.Scope)
                && BroadcastObserverRules.Id(command.CorrelationId) && command.ExpiresAtUnixMs > at && command.ExpiresAtUnixMs <= at + 5000
                && at - switchedAt >= 8000 && (string.IsNullOrEmpty(command.AnchorId) != string.IsNullOrEmpty(command.FocusActorId));
            BroadcastCamera next = camera.Clone();
            if (allowed && command.AnchorId.Length > 0)
            {
                allowed = anchors.TryGetValue(command.AnchorId, out var anchor);
                if (allowed) { next.AnchorId = command.AnchorId; next.FocusActorId = ""; next.X = anchor.X; next.Y = anchor.Y; }
            }
            else if (allowed)
            {
                var actor = presented != null && at - presented.ObservedAtUnixMs <= 500 ? presented.Actors.FirstOrDefault(p => p.ActorId == command.FocusActorId) : null;
                allowed = actor != null;
                if (allowed) { next.AnchorId = ""; next.FocusActorId = actor.ActorId; next.X = actor.X; next.Y = actor.Y; }
            }
            if (allowed) { camera = next; ++scope.ViewEpoch; switchedAt = at; last = presented = null; prepared.Clear(); lastRooms.Clear(); }
            return new BroadcastViewSelectionResultEvent { Scope = scope.Clone(), CorrelationId = command?.CorrelationId?.Length <= 64 ? command.CorrelationId : "",
                Accepted = allowed, ReasonCode = allowed ? "view_selected" : "view_unavailable" };
        }
        internal bool ConfirmPresented(BroadcastPresentationAck ack, long at)
        {
            if (ack == null || !prepared.TryGetValue(ack.RenderedSequence, out var frame) || !BroadcastPresentationRules.CanAcknowledge(frame, ack, false)
                || !BroadcastObserverRules.SameScope(scope, ack.Scope) || at - frame.ObservedAtUnixMs > 1000 || presented != null && frame.Sequence <= presented.Sequence) return false;
            presented = BroadcastPresentationRules.PresentedSubset(frame, ack); foreach (var fact in presented.Facts) if (fact.RetentionExpiresAtUnixMs > at) committed.Add(fact.EvidenceId);
            foreach (var key in prepared.Keys.Where(k => k <= frame.Sequence).ToArray()) prepared.Remove(key); return true;
        }
        internal bool SeenActorRecently(string actor, long at) => presented != null && at - presented.ObservedAtUnixMs <= 500 && presented.Actors.Any(p => p.ActorId == actor);
        internal bool Witnesses(string attacker, string victim, float ax, float ay, float vx, float vy, long at)
            => SeenActorRecently(attacker, at) && SeenActorRecently(victim, at) && Permits(ax, ay) && Permits(vx, vy);
        internal void Note(string predicate, string eventId, string actor, string target, string text, string room, long at, int priority = 2, string label = "", long value = 0)
        {
            if (!BroadcastObserverRules.Id(eventId) || seen.Contains(eventId) || predicate == null || room == null || label == null || actor == null || target == null || text == null || System.Text.Encoding.UTF8.GetByteCount(text) > 512
                || predicate.Length > 48 || room.Length > 32 || label.Length > 128 || actor.Length > 128 || target.Length > 128) return;
            seen.Add(eventId); seenOrder.Enqueue(eventId); while (seenOrder.Count > 512) seen.Remove(seenOrder.Dequeue());
            var fact = new BroadcastEditorialFact { EvidenceId = eventId, Version = 1, Kind = predicate == "discussion-claim" ? BroadcastFactKind.PlayerClaim : BroadcastFactKind.Observation,
                SourceEventId = eventId, Predicate = predicate, ObservedAtUnixMs = at, RetentionExpiresAtUnixMs = at + 300000,
                Priority = (uint)Math.Clamp(priority, 0, 3), TopicKey = predicate + ":" + actor + ":" + target, Text = text, RoomId = room,
                ActorId = actor, TargetActorId = target, Label = label, Value = value };
            if (actor.Length > 0) fact.SubjectIds.Add(actor); if (target.Length > 0 && target != actor) fact.SubjectIds.Add(target);
            facts.Add(fact); ++semantic; Trim(at);
        }
        private void Trim(long at)
        {
            foreach (var fact in facts.Where(f => f.RetentionExpiresAtUnixMs <= at).ToArray()) { facts.Remove(fact); committed.Remove(fact.EvidenceId); revoked.Enqueue(fact.EvidenceId); }
            while (facts.Count > 96 || facts.Sum(f => f.CalculateSize()) > 32768)
            { revoked.Enqueue(facts[0].EvidenceId); committed.Remove(facts[0].EvidenceId); facts.RemoveAt(0); }
            while (revoked.Count > 96) revoked.Dequeue();
        }
        internal BroadcastView Build(SocialMatchStateObservation state, SocialEmergencyStateObservation emergency, SocialMeetingObservation meeting,
            IEnumerable<Actor> actorInputs, IEnumerable<Body> bodyInputs, SocialMatchSummary terminal, long at)
        {
            if (state == null || state.MatchId != scope.MatchId) throw new ArgumentException("Wrong match.");
            now = Math.Max(now, at); Trim(now);
            var actors = actorInputs.Take(7).ToArray(); var bodies = bodyInputs.Take(7).ToArray();
            if (actors.Length > 6 || bodies.Length > 6) throw new ArgumentException("Observer world bound.");
            if (camera.FocusActorId.Length > 0)
            {
                var focus = actors.FirstOrDefault(a => a.Id == camera.FocusActorId && a.Alive && float.IsFinite(a.X) && float.IsFinite(a.Y));
                if (focus != null) { camera.X = focus.X; camera.Y = focus.Y; } else camera.FocusActorId = "";
            }
            camera.VisibilityRadius = float.IsFinite(state.VisibilityRadius) ? Math.Clamp(state.VisibilityRadius, 1, 8) : 3.5f;
            var view = new BroadcastView { Scope = scope.Clone(), Sequence = ++sequence, SemanticRevision = semantic, ObservedAtUnixMs = now, ExpiresAtUnixMs = now + 500,
                Camera = camera.Clone(), Phase = state.Phase, PhaseRevision = state.PhaseRevision, PhaseEndsAtUnixMs = state.PhaseEndsAtUnixMs,
                CrewTasksCompleted = state.CrewTasksCompleted, TaskThreshold = state.TaskThreshold, Terminal = state.Phase == SocialPhase.GameOver };
            foreach (var a in actors.Where(a => a.Alive && Permits(a.X, a.Y)).OrderBy(a => a.Id))
            {
                if (!BroadcastObserverRules.Id(a.Id) || !global::Actor.Contracts.JoinTicketCodec.IsPlayerNameValid(a.Name) || !float.IsFinite(a.VelocityX) || !float.IsFinite(a.VelocityY)) continue;
                view.Actors.Add(new BroadcastActor { ActorId = a.Id, DisplayName = a.Name, X = a.X, Y = a.Y, VelocityX = a.VelocityX, VelocityY = a.VelocityY,
                    Facing = Math.Min(a.Facing, 7), CosmeticIndex = Math.Min(a.Cosmetic, 5) });
                if (!lastRooms.TryGetValue(a.Id, out var previous) || previous != a.Room)
                    Note("actor-seen", Guid.NewGuid().ToString("N"), a.Id, "", "", a.Room ?? "", now, 2, a.Name);
            }
            lastRooms.Clear(); foreach (var a in view.Actors) lastRooms[a.ActorId] = actors.First(p => p.Id == a.ActorId).Room;
            foreach (var b in bodies.Where(b => Permits(b.X, b.Y)).OrderBy(b => b.Id))
            {
                if (!BroadcastObserverRules.Id(b.Id) || !BroadcastObserverRules.Id(b.Victim)) continue;
                view.Bodies.Add(new BroadcastBody { BodyId = b.Id, VictimActorId = b.Victim, VictimDisplayName = b.Name ?? "", X = b.X, Y = b.Y, CosmeticIndex = Math.Min(b.Cosmetic, 5) });
                if (last == null || !last.Bodies.Any(p => p.BodyId == b.Id)) Note("body-seen", Guid.NewGuid().ToString("N"), b.Victim, "", "", "", now, 1, b.Name ?? "");
            }
            if (emergency != null && emergency.MatchId == scope.MatchId)
            { view.EmergencyActive = emergency.Active; view.EmergencyEndsAtUnixMs = emergency.EndsAtUnixMs; view.EmergencyRoomId = emergency.Active ? emergency.RoomId : ""; }
            if (meeting != null && meeting.MatchId == scope.MatchId)
            {
                view.Meeting = new BroadcastMeeting { MeetingId = meeting.MeetingId, EjectedActorId = meeting.EjectedActorId, Resolution = meeting.Resolution };
                if (state.Phase is SocialPhase.Discussion or SocialPhase.Voting) view.Meeting.Participants.Add(meeting.Participants.Take(6).Select(p => p.Clone()));
                view.Meeting.Result.Add(meeting.Result.Take(7).Select(p => p.Clone()));
            }
            if (view.Terminal && terminal != null && terminal.MatchId == scope.MatchId)
            {
                view.WinningSide = terminal.WinningSide; view.WinReason = terminal.WinReason;
                view.FinalPlayers.Add(terminal.Players.Take(6).Select(p => new BroadcastFinalPlayer { ActorId = p.ActorId, DisplayName = p.DisplayName, Alignment = p.Alignment, Life = p.Life }));
            }
            string signature = state.Phase + ":" + state.PhaseRevision + ":" + view.CrewTasksCompleted + ":" + view.EmergencyActive + ":" + view.Meeting?.Resolution + ":"
                + string.Join(",", view.Actors.Select(p => p.ActorId)) + ":" + string.Join(",", view.Bodies.Select(p => p.BodyId));
            if (signature != publicSignature)
            { publicSignature = signature; ++semantic; Note("public-state", Guid.NewGuid().ToString("N"), "", "", "", view.EmergencyRoomId, now, view.Terminal ? 0 : 2, state.Phase.ToString(), view.CrewTasksCompleted); }
            // A never-presented transient sighting cannot become later historical evidence.
            facts.RemoveAll(f => !committed.Contains(f.EvidenceId) && ((f.Predicate == "actor-seen" && !view.Actors.Any(a => a.ActorId == f.ActorId))
                || (f.Predicate == "body-seen" && !view.Bodies.Any(b => b.VictimActorId == f.ActorId))
                || (f.Predicate == "public-state" && f.Label != state.Phase.ToString())));
            view.SemanticRevision = semantic; view.Facts.Add(facts.Select(f => f.Clone())); view.RevokedEvidenceIds.Add(revoked);
            last = view.Clone(); prepared[view.Sequence] = view.Clone(); while (prepared.Count > 8) prepared.Remove(prepared.Keys.Min()); return view;
        }
    }
}
