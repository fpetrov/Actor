using System;
using System.Collections.Generic;
using System.Linq;
using Actor.Contracts;

namespace Actor.Networking
{
    // Pure bounded server transport state, not an alternate game state. Game
    // role/life/task truth remains entirely in SocialMatchAuthority.
    internal sealed class ResumeAdmissionRegistry
    {
        internal sealed class Binding
        {
            internal string Actor = "", Name = "", CredentialId = "", PreviousCredentialId = "";
            internal ulong Generation, ClientId, PreviousGeneration;
            internal int Slot;
            internal bool Connected, CredentialAcknowledged;
            internal long GraceUntil, PreviousExpiresAt, CredentialExpiresAt;
            public override string ToString() => "ResumeBinding [private]";
        }
        private readonly Dictionary<string, Binding> bindings = new();
        private readonly int capacity;
        private readonly long graceMs;
        internal ResumeAdmissionRegistry(int capacity, long graceMs)
        {
            if (capacity < 2 || capacity > 16 || graceMs < 1000 || graceMs > 120000) throw new ArgumentException("Invalid admission limits.");
            this.capacity = capacity; this.graceMs = graceMs;
        }
        internal IEnumerable<Binding> Bindings => bindings.Values;
        internal Binding Find(string actor) => actor != null && bindings.TryGetValue(actor, out var value) ? value : null;
        internal bool TryAdmit(JoinTicketClaims claims, ulong clientId, long now, out Binding result, out string reason)
        {
            result = null; reason = "resume_unavailable";
            if (claims == null || !Guid.TryParseExact(claims.actorId, "N", out _) || !JoinTicketCodec.IsPlayerNameValid(claims.playerName)) return false;
            if (!claims.resume)
            {
                if (claims.connectionGeneration != 1 || claims.resumeGeneration != 0 || !string.IsNullOrEmpty(claims.resumeCredentialId)) return false;
                if (bindings.ContainsKey(claims.actorId) || bindings.Count >= 32 || bindings.Values.Count(x => x.Connected || x.GraceUntil > now) >= capacity)
                { reason = "room_full"; return false; }
                int slot = Enumerable.Range(0, capacity).First(i => !bindings.Values.Any(x => x.Slot == i && (x.Connected || x.GraceUntil > now)));
                result = new Binding { Actor = claims.actorId, Name = claims.playerName, Generation = 1, ClientId = clientId, Slot = slot,
                    CredentialId = Guid.NewGuid().ToString("N"), Connected = true };
                bindings.Add(result.Actor, result); claims.connectionGeneration = 1; reason = ""; return true;
            }
            if (!bindings.TryGetValue(claims.actorId, out var existing) || existing.Name != claims.playerName || existing.Connected
                || existing.GraceUntil <= now || existing.Generation == ulong.MaxValue) return false;
            bool current = claims.resumeGeneration == existing.Generation && claims.resumeCredentialId == existing.CredentialId
                && existing.CredentialExpiresAt > now;
            // If a transport died between rotation and acknowledgement, allow
            // the previous bearer only while disconnected and inside its old
            // expiry. It never authorizes taking over a connected endpoint.
            bool previous = !existing.CredentialAcknowledged && claims.resumeGeneration == existing.PreviousGeneration
                && claims.resumeCredentialId == existing.PreviousCredentialId && existing.PreviousExpiresAt > now;
            if (!current && !previous) return false;
            if (current)
            { existing.PreviousCredentialId = existing.CredentialId; existing.PreviousGeneration = existing.Generation; existing.PreviousExpiresAt = existing.CredentialExpiresAt; }
            ++existing.Generation; existing.ClientId = clientId; existing.Connected = true; existing.GraceUntil = 0;
            existing.CredentialAcknowledged = false; existing.CredentialId = Guid.NewGuid().ToString("N"); existing.CredentialExpiresAt = 0;
            claims.connectionGeneration = existing.Generation; result = existing; reason = ""; return true;
        }
        internal void Disconnected(ulong clientId, long now, bool forfeited = false)
        {
            var b = bindings.Values.SingleOrDefault(x => x.Connected && x.ClientId == clientId);
            if (b == null) return;
            forfeited |= b.CredentialId.Length == 0;
            b.Connected = false; b.GraceUntil = forfeited ? 0 : now + graceMs;
            if (forfeited) Revoke(b.Actor);
        }
        internal void Revoke(string actor)
        {
            var b = Find(actor); if (b == null) return;
            b.CredentialId = ""; b.PreviousCredentialId = ""; b.CredentialExpiresAt = b.PreviousExpiresAt = b.GraceUntil = 0;
        }
        internal bool Acknowledge(string actor, ulong generation, ulong clientId)
        {
            var b = Find(actor);
            if (b == null || !b.Connected || b.ClientId != clientId || b.Generation != generation) return false;
            b.CredentialAcknowledged = true; b.PreviousCredentialId = ""; b.PreviousExpiresAt = 0; return true;
        }
        internal ResumeCredentialClaims Credential(string actor, string match, string gameVersion, string protocolVersion, long now)
        {
            var b = Find(actor);
            if (b == null || !b.Connected || b.CredentialId.Length == 0) return null;
            b.CredentialExpiresAt = now + 120000;
            return new ResumeCredentialClaims { matchId = match, actorId = actor, playerName = b.Name, generation = b.Generation,
                credentialId = b.CredentialId, expiresAt = b.CredentialExpiresAt / 1000, gameVersion = gameVersion, protocolVersion = protocolVersion };
        }
    }
}
