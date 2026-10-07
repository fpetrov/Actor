namespace Actor.ControlPlane.Domain;

public enum LobbyStatus { Starting, Ready, Running, Stopping, Finished, Failed }

public sealed class Lobby
{
    public Guid LobbyId { get; set; }
    public string RoomCode { get; set; } = "";
    public string DisplayName { get; set; } = "";
    public LobbyStatus Status { get; set; }
    public DateTimeOffset CreatedAt { get; set; }
    public string GameVersion { get; set; } = "";
    public string ProtocolVersion { get; set; } = "";
    public string PublicHost { get; set; } = "";
    public int GamePort { get; set; }
    public int CurrentPlayers { get; set; }
    public int MaxPlayers { get; set; }
    public int? ServerPid { get; set; }
    public DateTimeOffset? ProcessStartedAt { get; set; }
    public DateTimeOffset? LastHeartbeatAt { get; set; }
    public DateTimeOffset LastEmptyAt { get; set; }
    public bool HasHadPlayers { get; set; }
    public string? EndReason { get; set; }
    public bool IsActive => Status is LobbyStatus.Starting or LobbyStatus.Ready or LobbyStatus.Running or LobbyStatus.Stopping;
}

public sealed class LobbyException(int status, string code, string message) : Exception(message)
{
    public int Status { get; } = status;
    public string Code { get; } = code;
}
