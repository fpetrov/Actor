#nullable disable
using System;
using System.Security.Cryptography;
using System.Text;

namespace Actor.Contracts
{
    [Serializable]
    public sealed class ResumeCredentialClaims
    {
        public string matchId, actorId, playerName, gameVersion, protocolVersion, credentialId;
        public ulong generation;
        public long expiresAt;
        public override string ToString() => "ResumeCredentialClaims [private]";
    }

    // A separate authentication domain prevents a resume bearer being used as
    // a join ticket (or vice versa), even though CP and match share one key.
    public static class ResumeCredentialCodec
    {
        public const int MaximumLength = 2048;
        private static readonly byte[] Domain = Encoding.ASCII.GetBytes("ACTOR:resume:v1");
        public static string Sign(string payloadJson, byte[] secret)
        {
            if (payloadJson == null || Encoding.UTF8.GetByteCount(payloadJson) > 700 || secret == null || secret.Length != 32)
                throw new ArgumentException("Invalid bounded resume credential input.");
            using (var hmac = new HMACSHA256(secret))
                return "ar1." + JoinTicketCodec.Sign(payloadJson, hmac.ComputeHash(Domain));
        }
        public static bool TryVerify(string credential, byte[] secret, out string payloadJson)
        {
            payloadJson = null;
            if (credential == null || credential.Length > MaximumLength || !credential.StartsWith("ar1.", StringComparison.Ordinal)
                || secret == null || secret.Length != 32) return false;
            using (var hmac = new HMACSHA256(secret))
                return JoinTicketCodec.TryVerify(credential.Substring(4), hmac.ComputeHash(Domain), out payloadJson);
        }
        public static bool ClaimsMatch(ResumeCredentialClaims claims, string matchId, string gameVersion, string protocolVersion, long now)
            => claims != null && claims.matchId == matchId && claims.gameVersion == gameVersion && claims.protocolVersion == protocolVersion
                && Guid.TryParseExact(claims.matchId, "N", out _) && Guid.TryParseExact(claims.actorId, "N", out _)
                && Guid.TryParseExact(claims.credentialId, "N", out _) && JoinTicketCodec.IsPlayerNameValid(claims.playerName)
                && claims.generation > 0 && claims.generation < ulong.MaxValue
                && claims.expiresAt > now && claims.expiresAt <= now + 180;
    }
}
