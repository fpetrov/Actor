using System;
using System.Collections.Generic;
using System.Linq;
using AgentsArena.ActorProtocol.V1;

namespace Actor.Networking
{
    // Separate, bounded admission bookkeeping. Never a player slot or resume actor.
    internal sealed class BroadcastObserverRules
    {
        internal sealed class Binding
        {
            internal ulong Client; internal BroadcastScope Scope;
        }
        private readonly Dictionary<ulong, Binding> active = new();
        private readonly Dictionary<string, ulong> generations = new(StringComparer.Ordinal);
        private readonly Dictionary<string, long> nonces = new(StringComparer.Ordinal);
        private readonly HashSet<string> revokedSessions = new(StringComparer.Ordinal);
        internal IEnumerable<Binding> Active => active.Values;
        internal bool Contains(ulong client) => active.ContainsKey(client);
        internal Binding Find(ulong client) => active.TryGetValue(client, out var value) ? value : null;
        internal bool Admit(ulong client, BroadcastScope scope, string nonce, long expiresSeconds, long nowSeconds)
        {
            foreach (var expiredNonce in nonces.Where(p => p.Value <= nowSeconds).Select(p => p.Key).ToArray()) nonces.Remove(expiredNonce);
            if (!ValidScope(scope) || !Id(nonce) || expiresSeconds <= nowSeconds || expiresSeconds > nowSeconds + 60
                || revokedSessions.Contains(scope.BroadcastSessionId) || revokedSessions.Count >= 128
                || active.Count >= 2 || active.ContainsKey(client) || nonces.ContainsKey(nonce) || nonces.Count >= 128
                || active.Values.Any(p => p.Scope.ObserverId == scope.ObserverId || p.Scope.BroadcastSessionId == scope.BroadcastSessionId)) return false;
            string key = scope.BroadcastSessionId + ":" + scope.ObserverId;
            if ((generations.TryGetValue(key, out ulong previous) && scope.ConnectionGeneration <= previous)
                || !generations.ContainsKey(key) && generations.Count >= 128) return false;
            nonces.Add(nonce, expiresSeconds); generations[key] = scope.ConnectionGeneration;
            active.Add(client, new Binding { Client = client, Scope = scope.Clone() }); return true;
        }
        internal void Remove(ulong client) => active.Remove(client);
        // Retained for the whole match: DELETE cannot be undone by any still-valid ticket.
        internal ulong[] Revoke(string session)
        {
            if (!Id(session)) return Array.Empty<ulong>();
            var clients = active.Values.Where(b => b.Scope.BroadcastSessionId == session).Select(b => b.Client).ToArray();
            if (revokedSessions.Count < 128) revokedSessions.Add(session);
            return clients; // Keep classification until NGO disconnect callback removes the binding.
        }
        internal static bool Id(string value) => Guid.TryParseExact(value, "N", out _);
        internal static bool ValidScope(BroadcastScope scope) => scope != null && Id(scope.MatchId) && Id(scope.BroadcastSessionId)
            && Id(scope.ObserverId) && scope.ConnectionGeneration > 0 && scope.ConnectionGeneration <= 1000000 && scope.ViewEpoch > 0;
        internal static bool SameScope(BroadcastScope a, BroadcastScope b) => a != null && b != null && a.Equals(b);
    }
}
