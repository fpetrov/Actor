using System.Net;
using System.Net.Sockets;
using Actor.ControlPlane.Configuration;
using Actor.ControlPlane.Domain;
using Microsoft.Extensions.Options;

namespace Actor.ControlPlane.Infrastructure;

public sealed class PortAllocator(IOptions<MatchOptions> options)
{
    private readonly object gate = new();
    private readonly Dictionary<Guid, int> reservations = new();

    public int Reserve(Guid lobbyId)
    {
        lock (gate)
        {
            if (reservations.TryGetValue(lobbyId, out int existing)) return existing;
            for (int port = options.Value.GameServerPortStart; port <= options.Value.GameServerPortEnd; port++)
            {
                if (reservations.ContainsValue(port)) continue;
                try
                {
                    using var probe = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
                    probe.Bind(new IPEndPoint(IPAddress.Any, port));
                    reservations.Add(lobbyId, port);
                    return port;
                }
                catch (SocketException) { }
            }
            throw new LobbyException(409, "server_capacity_reached", "No game server port is available.");
        }
    }

    public void Release(Guid lobbyId) { lock (gate) reservations.Remove(lobbyId); }
}
