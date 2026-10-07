using Actor.ControlPlane.Domain;

namespace Actor.ControlPlane.Infrastructure;

public sealed record GameServerAllocation(int ProcessId, DateTimeOffset StartedAt);

public interface IGameServerAllocator
{
    Task<GameServerAllocation> AllocateAsync(Lobby lobby, byte[] secret, CancellationToken cancellationToken);
    Task ReleaseAsync(Guid lobbyId, CancellationToken cancellationToken);
    bool IsRunning(Guid lobbyId);
    Task ReapPersistedAsync(Lobby lobby, CancellationToken cancellationToken);
}
