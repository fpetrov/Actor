using System.Text.Json;
using Actor.Contracts;

namespace Actor.ControlPlane.Infrastructure;

public sealed class JoinTicketService : IJoinTicketService
{
    private static readonly JsonSerializerOptions Json = new() { IncludeFields = true };
    public string Issue(JoinTicketClaims claims, byte[] secret) => JoinTicketCodec.Sign(JsonSerializer.Serialize(claims, Json), secret);

    public bool Verify(string ticket, byte[] secret, string lobbyId, string gameVersion, string protocolVersion, long now)
    {
        if (!JoinTicketCodec.TryVerify(ticket, secret, out string payload)) return false;
        try { return JoinTicketCodec.ClaimsMatch(JsonSerializer.Deserialize<JoinTicketClaims>(payload, Json)!, lobbyId, gameVersion, protocolVersion, now); }
        catch (JsonException) { return false; }
    }
}
