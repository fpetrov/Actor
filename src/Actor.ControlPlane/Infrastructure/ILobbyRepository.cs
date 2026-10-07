using Actor.ControlPlane.Domain;

namespace Actor.ControlPlane.Infrastructure;

public interface ILobbyRepository
{
    Task InitializeAsync(CancellationToken cancellationToken = default);
    Task<IReadOnlyList<Lobby>> ListAsync(CancellationToken cancellationToken = default);
    Task SaveAsync(Lobby lobby, CancellationToken cancellationToken = default);
    Task<bool> CheckHealthAsync(CancellationToken cancellationToken = default);
}
