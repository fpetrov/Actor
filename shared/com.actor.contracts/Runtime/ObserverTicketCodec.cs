#nullable disable
using System;
using System.Security.Cryptography;
using System.Text;

namespace Actor.Contracts
{
    [Serializable]
    public sealed class ObserverTicketClaims
    {
        public string audience;
        public string lobbyId;
        public string broadcastSessionId;
        public string observerId;
        public string gameVersion;
        public string protocolVersion;
        public long expiresAt;
        public string nonce;
        public ulong connectionGeneration;
        public override string ToString() => "ObserverTicketClaims [private]";
    }

    // This audience and signing domain cannot be used as player/resume admission.
    public static class ObserverTicketCodec
    {
        public const string Audience = "actor.agents-arena.broadcast-observer.v1";
        public const string WirePrefix = "observer-v1.";
        public const int MaximumLifetimeSeconds = 60;
        public const ulong MaximumConnectionGeneration = 1000000;

        public static string Sign(string payloadJson, byte[] secret)
        {
            if (secret == null || secret.Length < 32) throw new ArgumentException("Invalid observer signing key.");
            if (string.IsNullOrEmpty(payloadJson) || Encoding.UTF8.GetByteCount(payloadJson) > 700)
                throw new ArgumentException("Invalid observer admission payload.");
            byte[] key = Derive(secret);
            try { return WirePrefix + JoinTicketCodec.Sign(payloadJson, key); }
            finally { Array.Clear(key, 0, key.Length); }
        }

        public static bool TryVerify(string ticket, byte[] secret, out string payloadJson)
        {
            payloadJson = null;
            if (string.IsNullOrEmpty(ticket) || ticket.Length > 1024 + WirePrefix.Length
                || !ticket.StartsWith(WirePrefix, StringComparison.Ordinal) || secret == null || secret.Length < 32)
                return false;
            byte[] key = Derive(secret);
            try { return JoinTicketCodec.TryVerify(ticket.Substring(WirePrefix.Length), key, out payloadJson); }
            finally { Array.Clear(key, 0, key.Length); }
        }

        public static bool ClaimsMatch(ObserverTicketClaims claims, string lobbyId, string gameVersion,
            string protocolVersion, long now)
            => now >= 0 && claims != null && claims.audience == Audience && claims.lobbyId == lobbyId
                && Guid.TryParseExact(claims.lobbyId, "N", out _)
                && claims.gameVersion == gameVersion && claims.protocolVersion == protocolVersion
                && claims.expiresAt > now && claims.expiresAt - now <= MaximumLifetimeSeconds
                && Guid.TryParseExact(claims.broadcastSessionId, "N", out _)
                && Guid.TryParseExact(claims.observerId, "N", out _)
                && Guid.TryParseExact(claims.nonce, "N", out _)
                && claims.connectionGeneration >= 1 && claims.connectionGeneration <= MaximumConnectionGeneration;

        private static byte[] Derive(byte[] secret)
        {
            using (var hmac = new HMACSHA256(secret))
                return hmac.ComputeHash(Encoding.UTF8.GetBytes(Audience));
        }
    }
}
