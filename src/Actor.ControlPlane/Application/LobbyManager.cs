using System.Security.Cryptography;
using System.Text;
using Actor.Contracts;
using Actor.ControlPlane.Configuration;
using Actor.ControlPlane.Domain;
using Actor.ControlPlane.Infrastructure;
using Microsoft.Extensions.Options;

namespace Actor.ControlPlane.Application;

public sealed record CreateLobbyRequest(string DisplayName, int? MaxPlayers, string GameVersion, string ProtocolVersion, string GameMode = "Sandbox");
public sealed record JoinLobbyRequest(string PlayerName, string GameVersion, string ProtocolVersion);
public sealed record JoinLobbyResult(string LobbyId, string RoomCode, string Host, int Port, string JoinTicket, DateTimeOffset ExpiresAt, string GameMode = "Sandbox");
public sealed record GameServerHeartbeat(int CurrentPlayers, string ServerState, string[]? ConnectedNonces);
public sealed record LobbySummary(string LobbyId, string RoomCode, string DisplayName, string Status, DateTimeOffset CreatedAt,
    string GameVersion, string ProtocolVersion, string PublicHost, int GamePort, int CurrentPlayers, int MaxPlayers, string GameMode = "Sandbox")
{
    public static LobbySummary From(Lobby lobby) => new(lobby.LobbyId.ToString("N"), lobby.RoomCode, lobby.DisplayName,
        lobby.Status.ToString(), lobby.CreatedAt, lobby.GameVersion, lobby.ProtocolVersion, lobby.PublicHost,
        lobby.GamePort, lobby.CurrentPlayers, lobby.MaxPlayers, lobby.GameMode);
}

public sealed class LobbyManager(ILobbyRepository repository, IGameServerAllocator allocator, PortAllocator ports,
    IJoinTicketService tickets, IOptions<MatchOptions> options, TimeProvider clock, ILogger<LobbyManager> logger)
{
    private sealed class Match(Lobby lobby, byte[] secret)
    {
        public Lobby Lobby { get; } = lobby;
        public byte[] Secret { get; } = secret;
        public Dictionary<string, DateTimeOffset> PendingTickets { get; } = new();
    }
    private readonly SemaphoreSlim gate = new(1, 1);
    private readonly Dictionary<Guid, Match> matches = new();
    private readonly HashSet<string> usedCodes = new(StringComparer.Ordinal);
    private readonly MatchOptions settings = options.Value;

    public async Task InitializeAsync(CancellationToken cancellationToken)
    {
        await repository.InitializeAsync(cancellationToken);
        foreach (Lobby lobby in await repository.ListAsync(cancellationToken))
        {
            usedCodes.Add(lobby.RoomCode);
            if (!lobby.IsActive) continue;
            // Signing secrets are intentionally not persisted: old matches cannot safely admit new players.
            await allocator.ReapPersistedAsync(lobby, cancellationToken);
            lobby.Status = LobbyStatus.Failed;
            lobby.CurrentPlayers = 0;
            lobby.EndReason = "control_plane_restarted";
            await repository.SaveAsync(lobby, cancellationToken);
        }
    }

    public async Task<IReadOnlyList<LobbySummary>> ListAsync(CancellationToken cancellationToken)
    {
        await gate.WaitAsync(cancellationToken);
        try { return matches.Values.Select(m => LobbySummary.From(m.Lobby)).OrderBy(l => l.CreatedAt).ToArray(); }
        finally { gate.Release(); }
    }

    public async Task<LobbySummary> GetAsync(string roomCode, CancellationToken cancellationToken)
    {
        await gate.WaitAsync(cancellationToken);
        try { return LobbySummary.From(Find(roomCode).Lobby); }
        finally { gate.Release(); }
    }

    public async Task<LobbySummary> CreateAsync(CreateLobbyRequest request, CancellationToken cancellationToken)
    {
        ValidateVersions(request.GameVersion, request.ProtocolVersion);
        string name = request.DisplayName?.Trim() ?? "";
        if (name.Length < 2 || name.Length > 48 || name.Any(char.IsControl))
            throw new LobbyException(400, "invalid_room_name", "Room name must contain 2–48 characters.");
        string mode = request.GameMode ?? "Sandbox";
        if (mode is not ("Sandbox" or "SocialDeduction"))
            throw new LobbyException(400, "invalid_game_mode", "Choose Sandbox or SocialDeduction.");
        bool social = mode == "SocialDeduction";
        int maxPlayers = request.MaxPlayers ?? (social ? settings.SocialDeduction.MaxPlayers : settings.DefaultMaxPlayers);
        if (maxPlayers is < 2 or > 16) throw new LobbyException(400, "invalid_capacity", "Room capacity must be between 2 and 16.");
        if (social && (maxPlayers < settings.SocialDeduction.MinPlayers || maxPlayers > settings.SocialDeduction.MaxPlayers))
            throw new LobbyException(400, "invalid_capacity", "Social Deduction requires a capacity of 4–6 within the server limits.");
        await gate.WaitAsync(cancellationToken);
        try
        {
            if (matches.Count >= settings.MaxConcurrentGameServers)
                throw new LobbyException(409, "server_capacity_reached", "All game server slots are occupied.");
            DateTimeOffset now = clock.GetUtcNow();
            Guid id = Guid.NewGuid();
            int port = ports.Reserve(id);
            var lobby = new Lobby
            {
                LobbyId = id, RoomCode = NewRoomCode(), DisplayName = name, Status = LobbyStatus.Starting,
                CreatedAt = now, LastEmptyAt = now, GameVersion = settings.GameVersion,
                ProtocolVersion = settings.ProtocolVersion, PublicHost = settings.PublicGameHost,
                GamePort = port, MaxPlayers = maxPlayers, GameMode = mode,
                SocialConfiguration = social ? settings.SocialDeduction with { MaxPlayers = maxPlayers } : null
            };
            var match = new Match(lobby, RandomNumberGenerator.GetBytes(32));
            matches.Add(id, match);
            logger.LogInformation("PortAllocated {LobbyId} {Port}", id, port);
            try
            {
                // After reservation, complete the operation even if the HTTP client disconnects.
                await repository.SaveAsync(lobby, CancellationToken.None);
                GameServerAllocation allocation = await allocator.AllocateAsync(lobby, match.Secret, CancellationToken.None);
                lobby.ServerPid = allocation.ProcessId;
                lobby.ProcessStartedAt = allocation.StartedAt;
                await repository.SaveAsync(lobby, CancellationToken.None);
                logger.LogInformation("LobbyCreated {LobbyId} {RoomCode}", id, lobby.RoomCode);
                return LobbySummary.From(lobby);
            }
            catch (Exception error)
            {
                await StopMatchAsync(match, LobbyStatus.Failed, "startup_failed", CancellationToken.None);
                logger.LogError(error, "ServerStartupFailed {LobbyId}", id);
                throw new LobbyException(503, "server_start_failed", "The game server could not start.");
            }
        }
        finally { gate.Release(); }
    }

    public async Task<JoinLobbyResult> JoinAsync(string roomCode, JoinLobbyRequest request, CancellationToken cancellationToken)
    {
        ValidateVersions(request.GameVersion, request.ProtocolVersion);
        string name = request.PlayerName?.Trim() ?? "";
        if (!JoinTicketCodec.IsPlayerNameValid(name))
            throw new LobbyException(400, "invalid_player_name", "Player name must contain 2–24 letters, numbers, spaces, dots, underscores or hyphens.");
        await gate.WaitAsync(cancellationToken);
        try
        {
            Match match = Find(roomCode);
            Lobby lobby = match.Lobby;
            if (lobby.Status == LobbyStatus.Starting) throw new LobbyException(409, "server_starting", "The game server is starting.");
            if (lobby.Status is not (LobbyStatus.Ready or LobbyStatus.Running)) throw new LobbyException(409, "room_unavailable", "The room is unavailable.");
            DateTimeOffset now = clock.GetUtcNow();
            foreach (string nonce in match.PendingTickets.Where(p => p.Value <= now).Select(p => p.Key).ToArray())
                match.PendingTickets.Remove(nonce);
            if (lobby.CurrentPlayers + match.PendingTickets.Count >= lobby.MaxPlayers)
                throw new LobbyException(409, "room_full", "The room is full.");
            string newNonce = Guid.NewGuid().ToString("N");
            DateTimeOffset expires = now.AddSeconds(settings.JoinTicketLifetime);
            var claims = new JoinTicketClaims
            {
                lobbyId = lobby.LobbyId.ToString("N"), playerName = name, gameVersion = lobby.GameVersion,
                protocolVersion = lobby.ProtocolVersion, expiresAt = expires.ToUnixTimeSeconds(), nonce = newNonce
            };
            string ticket = tickets.Issue(claims, match.Secret);
            match.PendingTickets.Add(newNonce, expires);
            return new JoinLobbyResult(claims.lobbyId, lobby.RoomCode, lobby.PublicHost, lobby.GamePort, ticket, expires, lobby.GameMode);
        }
        finally { gate.Release(); }
    }

    public async Task ReadyAsync(Guid lobbyId, string? secret, CancellationToken cancellationToken)
    {
        await gate.WaitAsync(cancellationToken);
        try
        {
            Match match = Authenticate(lobbyId, secret);
            if (match.Lobby.Status == LobbyStatus.Starting)
            {
                match.Lobby.Status = LobbyStatus.Ready;
                match.Lobby.LastHeartbeatAt = clock.GetUtcNow();
                await repository.SaveAsync(match.Lobby, cancellationToken);
                logger.LogInformation("ServerReady {LobbyId}", lobbyId);
            }
        }
        finally { gate.Release(); }
    }

    public async Task HeartbeatAsync(Guid lobbyId, string? secret, GameServerHeartbeat heartbeat, CancellationToken cancellationToken)
    {
        await gate.WaitAsync(cancellationToken);
        try
        {
            Match match = Authenticate(lobbyId, secret);
            Lobby lobby = match.Lobby;
            if (heartbeat.CurrentPlayers < 0 || heartbeat.CurrentPlayers > lobby.MaxPlayers)
                throw new LobbyException(400, "invalid_player_count", "Invalid player count.");
            if (lobby.Status is not (LobbyStatus.Ready or LobbyStatus.Running)) return;
            if (lobby.CurrentPlayers != heartbeat.CurrentPlayers)
                logger.LogInformation("PlayerCountChanged {LobbyId} {CurrentPlayers}", lobbyId, heartbeat.CurrentPlayers);
            if (lobby.CurrentPlayers > 0 && heartbeat.CurrentPlayers == 0) lobby.LastEmptyAt = clock.GetUtcNow();
            lobby.CurrentPlayers = heartbeat.CurrentPlayers;
            lobby.LastHeartbeatAt = clock.GetUtcNow();
            if (lobby.CurrentPlayers > 0) lobby.HasHadPlayers = true;
            lobby.Status = lobby.CurrentPlayers > 0 ? LobbyStatus.Running : LobbyStatus.Ready;
            foreach (string nonce in heartbeat.ConnectedNonces ?? []) match.PendingTickets.Remove(nonce);
            await repository.SaveAsync(lobby, cancellationToken);
        }
        finally { gate.Release(); }
    }

    public async Task FinishedAsync(Guid lobbyId, string? secret, CancellationToken cancellationToken)
    {
        await gate.WaitAsync(cancellationToken);
        try { await StopMatchAsync(Authenticate(lobbyId, secret), LobbyStatus.Finished, "match_finished", cancellationToken); }
        finally { gate.Release(); }
    }

    public async Task MonitorAsync(CancellationToken cancellationToken)
    {
        await gate.WaitAsync(cancellationToken);
        try
        {
            DateTimeOffset now = clock.GetUtcNow();
            foreach (Match match in matches.Values.ToArray())
            {
                Lobby lobby = match.Lobby;
                string? reason = null;
                LobbyStatus status = LobbyStatus.Failed;
                if (!allocator.IsRunning(lobby.LobbyId)) reason = "process_exited";
                else if (lobby.Status == LobbyStatus.Starting && (now - lobby.CreatedAt).TotalSeconds > settings.LobbyStartupTimeout) reason = "startup_timeout";
                else if (lobby.LastHeartbeatAt != null && (now - lobby.LastHeartbeatAt.Value).TotalSeconds > settings.HeartbeatTimeout) reason = "heartbeat_timeout";
                else if (lobby.CurrentPlayers == 0)
                {
                    int lifetime = lobby.HasHadPlayers ? settings.EmptyLobbyGracePeriod : settings.EmptyLobbyLifetime;
                    if ((now - lobby.LastEmptyAt).TotalSeconds > lifetime) { reason = "empty_lobby_timeout"; status = LobbyStatus.Finished; }
                }
                if (reason != null) await StopMatchAsync(match, status, reason, cancellationToken);
            }
        }
        finally { gate.Release(); }
    }

    public async Task ShutdownAsync(CancellationToken cancellationToken)
    {
        await gate.WaitAsync(cancellationToken);
        try
        {
            foreach (Match match in matches.Values.ToArray())
                await StopMatchAsync(match, LobbyStatus.Finished, "control_plane_shutdown", cancellationToken);
        }
        finally { gate.Release(); }
    }

    private async Task StopMatchAsync(Match match, LobbyStatus finalStatus, string reason, CancellationToken cancellationToken)
    {
        match.Lobby.Status = LobbyStatus.Stopping;
        try { await repository.SaveAsync(match.Lobby, cancellationToken); }
        catch (Exception error) when (error is not OperationCanceledException)
        { logger.LogError(error, "StoppingStatePersistenceFailed {LobbyId}", match.Lobby.LobbyId); }
        await allocator.ReleaseAsync(match.Lobby.LobbyId, cancellationToken);
        ports.Release(match.Lobby.LobbyId);
        matches.Remove(match.Lobby.LobbyId);
        match.Lobby.Status = finalStatus;
        match.Lobby.CurrentPlayers = 0;
        match.Lobby.EndReason = reason;
        await repository.SaveAsync(match.Lobby, cancellationToken);
        logger.LogInformation("LobbyStopped {LobbyId} {Reason} {Status}", match.Lobby.LobbyId, reason, finalStatus);
    }

    private Match Find(string roomCode)
    {
        string code = roomCode.Trim().ToUpperInvariant();
        return matches.Values.FirstOrDefault(m => m.Lobby.RoomCode == code)
            ?? throw new LobbyException(404, "room_not_found", "Room was not found.");
    }

    private Match Authenticate(Guid id, string? secret)
    {
        if (!matches.TryGetValue(id, out Match? match) || secret == null)
            throw new LobbyException(403, "invalid_server_credentials", "Invalid server credentials.");
        byte[] incoming = Encoding.UTF8.GetBytes(secret);
        byte[] expected = Encoding.UTF8.GetBytes(Convert.ToBase64String(match.Secret));
        if (!CryptographicOperations.FixedTimeEquals(incoming, expected))
            throw new LobbyException(403, "invalid_server_credentials", "Invalid server credentials.");
        return match;
    }

    private string NewRoomCode()
    {
        const string alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
        string code;
        do { code = new string(Enumerable.Range(0, 6).Select(_ => alphabet[RandomNumberGenerator.GetInt32(alphabet.Length)]).ToArray()); }
        while (!usedCodes.Add(code));
        return code;
    }

    private void ValidateVersions(string gameVersion, string protocolVersion)
    {
        if (gameVersion != settings.GameVersion || protocolVersion != settings.ProtocolVersion)
            throw new LobbyException(409, "version_mismatch", "Game or protocol version is incompatible with this server.");
    }
}
