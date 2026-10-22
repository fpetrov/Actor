using System;
using System.Collections.Generic;
using System.Linq;
using Google.Protobuf;
using AgentsArena.ActorProtocol.V1;
namespace Actor.Networking
{
    internal static class BroadcastPresentationRules
    {
        internal static bool SameIdentity(BroadcastScope a, BroadcastScope b) => a != null && b != null
            && a.MatchId == b.MatchId && a.BroadcastSessionId == b.BroadcastSessionId && a.ObserverId == b.ObserverId && a.ConnectionGeneration == b.ConnectionGeneration;
        internal static bool ValidView(BroadcastView value, BroadcastScope expected)
        {
            var c = value?.Camera;
            if (value == null || !BroadcastObserverRules.ValidScope(value.Scope) || !SameIdentity(value.Scope, expected) || value.Scope.ViewEpoch < expected.ViewEpoch
                || value.Sequence == 0 || value.SemanticRevision == 0 || value.ObservedAtUnixMs <= 0 || value.ExpiresAtUnixMs <= value.ObservedAtUnixMs || value.ExpiresAtUnixMs - value.ObservedAtUnixMs > 1000
                || c == null || !float.IsFinite(c.X) || !float.IsFinite(c.Y) || !float.IsFinite(c.VisibilityRadius) || c.VisibilityRadius < 1 || c.VisibilityRadius > 8
                || !float.IsFinite(c.OrthographicSize) || c.OrthographicSize < 1 || c.OrthographicSize > 5 || !float.IsFinite(c.Aspect) || c.Aspect < .75f || c.Aspect > 2
                || value.Actors.Count > 6 || value.Bodies.Count > 6 || value.Facts.Count > 96 || value.RevokedEvidenceIds.Count > 96
                || !value.Terminal && (value.FinalPlayers.Count > 0 || value.WinningSide != SocialAlignment.Unspecified || value.WinReason.Length > 0)
                || value.Terminal != (value.Phase == SocialPhase.GameOver)) return false;
            foreach (var a in value.Actors) if (!BroadcastObserverRules.Id(a.ActorId) || !global::Actor.Contracts.JoinTicketCodec.IsPlayerNameValid(a.DisplayName) || !float.IsFinite(a.X) || !float.IsFinite(a.Y) || !float.IsFinite(a.VelocityX) || !float.IsFinite(a.VelocityY) || a.CosmeticIndex > 5 || a.Facing > 7) return false;
            foreach (var b in value.Bodies) if (!BroadcastObserverRules.Id(b.BodyId) || !BroadcastObserverRules.Id(b.VictimActorId) || !float.IsFinite(b.X) || !float.IsFinite(b.Y) || b.CosmeticIndex > 5) return false;
            return true;
        }
        internal static bool CanAcknowledge(BroadcastView view, BroadcastPresentationAck ack, bool headless)
            => !headless && view != null && ack != null && BroadcastObserverRules.SameScope(view.Scope, ack.Scope)
                && ack.RenderedSequence == view.Sequence && ack.FrameCounter > 0 && double.IsFinite(ack.PresentedAtMonotonicMs) && ack.PresentedAtMonotonicMs >= 0
                && ack.Width >= 320 && ack.Width <= 7680 && ack.Height >= 180 && ack.Height <= 4320 && ValidReferences(view, ack);
        private static bool ValidReferences(BroadcastView view, BroadcastPresentationAck ack)
        {
            if (ack.PresentedEvidence.Count > 96) return false;
            var ids = new HashSet<string>(StringComparer.Ordinal);
            foreach (var reference in ack.PresentedEvidence)
                if (!BroadcastObserverRules.Id(reference.EvidenceId) || reference.Version == 0 || !ids.Add(reference.EvidenceId)
                    || !view.Facts.Any(f => f.EvidenceId == reference.EvidenceId && f.Version == reference.Version)) return false;
            return true;
        }
        internal static BroadcastView PresentedSubset(BroadcastView source, BroadcastPresentationAck ack)
        {
            if (!CanAcknowledge(source, ack, false)) return null;
            var result = source.Clone(); result.Facts.Clear();
            result.Facts.Add(source.Facts.Where(f => ack.PresentedEvidence.Any(r => r.EvidenceId == f.EvidenceId && r.Version == f.Version)).Select(f => f.Clone()));
            return result;
        }
        internal static BroadcastView ForCognition(BroadcastView source, BroadcastPresentationAck ack, IReadOnlyDictionary<string, ulong> known)
        {
            var result = PresentedSubset(source, ack); if (result == null) return null;
            result.RevokedEvidenceIds.Clear(); result.RevokedEvidenceIds.Add(source.RevokedEvidenceIds.Where(known.ContainsKey));
            return result;
        }
        internal static string SemanticSignature(BroadcastView source)
        {
            var value = source.Clone(); value.Sequence = value.SemanticRevision = 0;
            value.ObservedAtUnixMs = value.ExpiresAtUnixMs = 0;
            value.Camera.X = value.Camera.Y = 0;
            foreach (var actor in value.Actors) { actor.X = actor.Y = actor.VelocityX = actor.VelocityY = 0; actor.Facing = 0; }
            foreach (var body in value.Bodies) body.X = body.Y = 0;
            return Convert.ToBase64String(value.ToByteArray());
        }
        internal static BroadcastEditorialFact[] SelectHudFacts(BroadcastView view, IReadOnlyDictionary<string, ulong> confirmed)
        {
            var result = new List<BroadcastEditorialFact>(3); int bytes = 0;
            foreach (var fact in view.Facts.Where(f => !confirmed.TryGetValue(f.EvidenceId, out ulong version) || version != f.Version)
                         .OrderBy(f => f.Priority).ThenBy(f => f.ObservedAtUnixMs).ThenBy(f => f.EvidenceId))
            {
                int size = System.Text.Encoding.UTF8.GetByteCount(fact.Text + fact.Label + fact.RoomId);
                if (result.Count == 3 || result.Count > 0 && bytes + size > 512) break;
                result.Add(fact); bytes += size;
            }
            return result.ToArray();
        }
        internal static bool FogMatches(ulong appliedEpoch, float appliedX, float appliedY, ulong wantedEpoch, float wantedX, float wantedY, bool ready)
            => ready && appliedEpoch == wantedEpoch && float.IsFinite(appliedX) && float.IsFinite(appliedY)
                && float.IsFinite(wantedX) && float.IsFinite(wantedY) && Math.Abs(appliedX - wantedX) < .0001f && Math.Abs(appliedY - wantedY) < .0001f;
    }
}
