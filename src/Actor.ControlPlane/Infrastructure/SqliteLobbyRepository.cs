using System.Text.Json;
using Actor.ControlPlane.Configuration;
using Actor.ControlPlane.Domain;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Options;

namespace Actor.ControlPlane.Infrastructure;

public sealed class SqliteLobbyRepository(IOptions<MatchOptions> options) : ILobbyRepository
{
    private readonly string path = Path.GetFullPath(options.Value.DatabasePath);
    private SqliteConnection Open() => new(new SqliteConnectionStringBuilder { DataSource = path, DefaultTimeout = 5 }.ToString());

    public async Task InitializeAsync(CancellationToken cancellationToken = default)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        await using var connection = Open();
        await connection.OpenAsync(cancellationToken);
        var command = connection.CreateCommand();
        command.CommandText = "PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS lobbies (id TEXT PRIMARY KEY, room_code TEXT NOT NULL UNIQUE, status TEXT NOT NULL, pid INTEGER, payload TEXT NOT NULL);";
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    public async Task<IReadOnlyList<Lobby>> ListAsync(CancellationToken cancellationToken = default)
    {
        await using var connection = Open();
        await connection.OpenAsync(cancellationToken);
        var command = connection.CreateCommand();
        command.CommandText = "SELECT payload FROM lobbies ORDER BY rowid DESC";
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        var lobbies = new List<Lobby>();
        while (await reader.ReadAsync(cancellationToken)) lobbies.Add(JsonSerializer.Deserialize<Lobby>(reader.GetString(0))!);
        return lobbies;
    }

    public async Task SaveAsync(Lobby lobby, CancellationToken cancellationToken = default)
    {
        await using var connection = Open();
        await connection.OpenAsync(cancellationToken);
        var command = connection.CreateCommand();
        command.CommandText = "INSERT INTO lobbies (id,room_code,status,pid,payload) VALUES ($id,$code,$status,$pid,$payload) ON CONFLICT(id) DO UPDATE SET status=$status,pid=$pid,payload=$payload";
        command.Parameters.AddWithValue("$id", lobby.LobbyId.ToString("N"));
        command.Parameters.AddWithValue("$code", lobby.RoomCode);
        command.Parameters.AddWithValue("$status", lobby.Status.ToString());
        command.Parameters.AddWithValue("$pid", (object?)lobby.ServerPid ?? DBNull.Value);
        command.Parameters.AddWithValue("$payload", JsonSerializer.Serialize(lobby));
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    public async Task<bool> CheckHealthAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            await using var connection = Open();
            await connection.OpenAsync(cancellationToken);
            var command = connection.CreateCommand();
            command.CommandText = "SELECT COUNT(*) FROM lobbies";
            await command.ExecuteScalarAsync(cancellationToken);
            return true;
        }
        catch (SqliteException) { return false; }
    }
}
