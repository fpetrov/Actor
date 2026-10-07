#nullable disable
using System;
using System.Security.Cryptography;
using System.Text;

namespace Actor.Contracts
{
    [Serializable]
    public sealed class JoinTicketClaims
    {
        public string lobbyId;
        public string playerName;
        public string gameVersion;
        public string protocolVersion;
        public long expiresAt;
        public string nonce;
    }

    public static class JoinTicketCodec
    {
        public static string Sign(string payloadJson, byte[] secret)
        {
            string payload = Encode(Encoding.UTF8.GetBytes(payloadJson));
            using (var hmac = new HMACSHA256(secret))
                return payload + "." + Encode(hmac.ComputeHash(Encoding.ASCII.GetBytes(payload)));
        }

        public static bool TryVerify(string ticket, byte[] secret, out string payloadJson)
        {
            payloadJson = null;
            if (string.IsNullOrEmpty(ticket) || ticket.Length > 1024 || secret == null || secret.Length < 32) return false;
            string[] parts = ticket.Split('.');
            if (parts.Length != 2) return false;
            try
            {
                byte[] signature = Decode(parts[1]);
                using (var hmac = new HMACSHA256(secret))
                {
                    byte[] expected = hmac.ComputeHash(Encoding.ASCII.GetBytes(parts[0]));
                    if (signature.Length != expected.Length) return false;
                    int difference = 0;
                    for (int i = 0; i < expected.Length; i++) difference |= signature[i] ^ expected[i];
                    if (difference != 0) return false;
                }
                payloadJson = new UTF8Encoding(false, true).GetString(Decode(parts[0]));
                return true;
            }
            catch (Exception error) when (error is FormatException || error is DecoderFallbackException || error is ArgumentException)
            { return false; }
        }

        public static bool ClaimsMatch(JoinTicketClaims claims, string lobbyId, string gameVersion, string protocolVersion, long now)
            => claims != null && claims.lobbyId == lobbyId && claims.gameVersion == gameVersion
                && claims.protocolVersion == protocolVersion && claims.expiresAt > now
                && claims.expiresAt <= now + 300 && IsPlayerNameValid(claims.playerName)
                && Guid.TryParseExact(claims.nonce, "N", out _);

        public static bool IsPlayerNameValid(string name)
        {
            if (name == null || name.Length < 2 || name.Length > 24 || name != name.Trim()) return false;
            foreach (char c in name)
                if (!char.IsLetterOrDigit(c) && c != ' ' && c != '_' && c != '-' && c != '.') return false;
            return true;
        }

        private static string Encode(byte[] value) => Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        private static byte[] Decode(string value)
        {
            foreach (char c in value)
                if (!(c >= 'a' && c <= 'z') && !(c >= 'A' && c <= 'Z') && !(c >= '0' && c <= '9') && c != '-' && c != '_')
                    throw new FormatException("Invalid base64url.");
            string padded = value.Replace('-', '+').Replace('_', '/');
            padded = padded.PadRight((padded.Length + 3) / 4 * 4, '=');
            return Convert.FromBase64String(padded);
        }
    }
}
