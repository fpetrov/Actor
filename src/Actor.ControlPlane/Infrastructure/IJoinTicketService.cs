using Actor.Contracts;

namespace Actor.ControlPlane.Infrastructure;

public interface IJoinTicketService
{
    string Issue(JoinTicketClaims claims, byte[] secret);
    bool Verify(string ticket, byte[] secret, string lobbyId, string gameVersion, string protocolVersion, long now);
}
