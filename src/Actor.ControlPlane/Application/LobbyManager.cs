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
public sealed record ResumeLobbyRequest(string ResumeCredential, string GameVersion, string ProtocolVersion)
{ public override string ToString() => "ResumeLobbyRequest [private]"; }
public sealed record ObserveLobbyRequest(string BroadcastSessionId, string GameVersion, string ProtocolVersion);
public sealed record ObserverAdmissionResult(string LobbyId, string RoomCode, string Host, int Port,
    string JoinTicket, DateTimeOffset ExpiresAt, string GameMode, string BroadcastSessionId,
    string ObserverId, ulong ConnectionGeneration)
{ public override string ToString() => "ObserverAdmissionResult [private]"; }
public sealed record JoinLobbyResult(string LobbyId, string RoomCode, string Host, int Port, string JoinTicket, DateTimeOffset ExpiresAt, string GameMode = "Sandbox", bool Resume = false)
{ public override string ToString() => "JoinLobbyResult [private]"; }
public sealed record ObserverConnectionBinding(string BroadcastSessionId, string ObserverId, ulong ConnectionGeneration);
public sealed record GameServerHeartbeat(int CurrentPlayers, string ServerState, string[]? ConnectedNonces,
    string[]? ConnectedActorIds = null, ObserverConnectionBinding[]? ConnectedObserverSessions = null);
public sealed record GameServerHeartbeatResult(string[] RevokedObserverSessions);
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
        public HashSet<string> ConnectedActors { get; } = new(StringComparer.Ordinal);
        public Dictionary<string, ObserverIdentity> Observers { get; } = new(StringComparer.Ordinal);
        public HashSet<string> RetiredObservers { get; } = new(StringComparer.Ordinal);
    }
    private sealed class ObserverIdentity
    {
        public string Id { get; } = Guid.NewGuid().ToString("N");
        public ulong Generation { get; set; }
        public bool Revoking { get; set; }
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
            ThrowIfDraining();
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
            ThrowIfDraining();
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
                , actorId = Guid.NewGuid().ToString("N"), connectionGeneration = 1
            };
            string ticket = tickets.Issue(claims, match.Secret);
            match.PendingTickets.Add(newNonce, expires);
            return new JoinLobbyResult(claims.lobbyId, lobby.RoomCode, lobby.PublicHost, lobby.GamePort, ticket, expires, lobby.GameMode);
        }
        finally { gate.Release(); }
    }

    public async Task<JoinLobbyResult> ResumeAsync(string roomCode, ResumeLobbyRequest request, CancellationToken cancellationToken)
    {
        ValidateVersions(request.GameVersion, request.ProtocolVersion);
        await gate.WaitAsync(cancellationToken);
        try
        {
            ThrowIfDraining();
            Match match = Find(roomCode); Lobby lobby = match.Lobby;
            if (lobby.GameMode != "SocialDeduction" || lobby.Status is not (LobbyStatus.Ready or LobbyStatus.Running))
                throw new LobbyException(409, "resume_unavailable", "This match cannot be resumed.");
            var now = clock.GetUtcNow(); ResumeCredentialClaims? credential = null;
            if (ResumeCredentialCodec.TryVerify(request.ResumeCredential, match.Secret, out string payload))
            {
                try { credential = System.Text.Json.JsonSerializer.Deserialize<ResumeCredentialClaims>(payload,
                    new System.Text.Json.JsonSerializerOptions { IncludeFields = true }); }
                catch (System.Text.Json.JsonException) { }
            }
            if (!ResumeCredentialCodec.ClaimsMatch(credential!, lobby.LobbyId.ToString("N"), lobby.GameVersion, lobby.ProtocolVersion, now.ToUnixTimeSeconds()))
                throw new LobbyException(401, "invalid_resume", "The resume credential is unavailable or expired.");
            if (match.ConnectedActors.Contains(credential!.actorId))
                throw new LobbyException(409, "resume_busy", "The previous connection is still closing. Please retry shortly.");
            DateTimeOffset expires = now.AddSeconds(Math.Min(settings.JoinTicketLifetime, credential.expiresAt - now.ToUnixTimeSeconds()));
            var claims = new JoinTicketClaims { lobbyId = lobby.LobbyId.ToString("N"), playerName = credential.playerName,
                actorId = credential.actorId, gameVersion = lobby.GameVersion, protocolVersion = lobby.ProtocolVersion,
                nonce = Guid.NewGuid().ToString("N"), expiresAt = expires.ToUnixTimeSeconds(), resume = true,
                resumeGeneration = credential.generation, resumeCredentialId = credential.credentialId };
            // The existing actor owns the reservation. Resume ticket retries
            // must not consume new capacity or extend a bearer lifetime.
            return new JoinLobbyResult(claims.lobbyId, lobby.RoomCode, lobby.PublicHost, lobby.GamePort,
                tickets.Issue(claims, match.Secret), expires, lobby.GameMode, true);
        }
        finally { gate.Release(); }
    }

    public async Task<ObserverAdmissionResult> ObserveAsync(string roomCode, ObserveLobbyRequest request,
        string? operatorKey, CancellationToken cancellationToken)
    {
        AuthenticateBroadcastOperator(operatorKey);
        ValidateVersions(request.GameVersion, request.ProtocolVersion);
        if (!Guid.TryParseExact(request.BroadcastSessionId, "N", out _))
            throw new LobbyException(400, "invalid_broadcast_session", "A canonical broadcast session is required.");
        await gate.WaitAsync(cancellationToken);
        try
        {
            ThrowIfDraining();
            Match match = Find(roomCode); Lobby lobby = match.Lobby;
            if (lobby.GameMode != "SocialDeduction" || lobby.Status is not (LobbyStatus.Ready or LobbyStatus.Running))
                throw new LobbyException(409, "observer_unavailable", "This match cannot be observed.");
            if (match.RetiredObservers.Contains(request.BroadcastSessionId)
                || match.Observers.TryGetValue(request.BroadcastSessionId, out var prior) && prior.Revoking)
                throw new LobbyException(409, "observer_session_retired", "This broadcast session has been stopped.");
            if (!match.Observers.TryGetValue(request.BroadcastSessionId, out var identity))
            {
                // Observer identities and player reservations have distinct bounded registries.
                // Reconnects reuse the broadcast session and advance its generation.
                if (match.Observers.Count >= 2)
                    throw new LobbyException(409, "observer_capacity_reached", "Observer identities are occupied for this match.");
                if (match.RetiredObservers.Count + match.Observers.Count >= 128)
                    throw new LobbyException(409, "observer_session_limit", "The observer session limit for this match was reached.");
                identity = new ObserverIdentity();
                match.Observers.Add(request.BroadcastSessionId, identity);
            }
            if (identity.Generation >= ObserverTicketCodec.MaximumConnectionGeneration)
                throw new LobbyException(409, "observer_generation_exhausted", "Observer reconnect limit reached.");
            identity.Generation++;
            DateTimeOffset expires = clock.GetUtcNow().AddSeconds(Math.Min(settings.JoinTicketLifetime,
                ObserverTicketCodec.MaximumLifetimeSeconds));
            var claims = new ObserverTicketClaims
            {
                audience = ObserverTicketCodec.Audience, lobbyId = lobby.LobbyId.ToString("N"),
                broadcastSessionId = request.BroadcastSessionId, observerId = identity.Id,
                connectionGeneration = identity.Generation, gameVersion = lobby.GameVersion,
                protocolVersion = lobby.ProtocolVersion, expiresAt = expires.ToUnixTimeSeconds(),
                nonce = Guid.NewGuid().ToString("N")
            };
            string ticket = ObserverTicketCodec.Sign(System.Text.Json.JsonSerializer.Serialize(claims,
                new System.Text.Json.JsonSerializerOptions { IncludeFields = true }), match.Secret);
            return new ObserverAdmissionResult(claims.lobbyId, lobby.RoomCode, lobby.PublicHost, lobby.GamePort,
                ticket, expires, lobby.GameMode, claims.broadcastSessionId, claims.observerId, claims.connectionGeneration);
        }
        finally { gate.Release(); }
    }

    public async Task ReleaseObserverAsync(string roomCode, string broadcastSessionId, string? operatorKey,
        CancellationToken cancellationToken)
    {
        AuthenticateBroadcastOperator(operatorKey);
        if (!Guid.TryParseExact(broadcastSessionId, "N", out _))
            throw new LobbyException(400, "invalid_broadcast_session", "A canonical broadcast session is required.");
        await gate.WaitAsync(cancellationToken);
        try
        {
            Match match = Find(roomCode);
            // A subsequent authenticated server heartbeat acknowledges actual
            // disconnect before the reservation becomes reusable.
            if (match.Observers.TryGetValue(broadcastSessionId, out var identity)) identity.Revoking = true;
        }
        finally { gate.Release(); }
    }

    private void AuthenticateBroadcastOperator(string? incoming)
    {
        if (settings.BroadcastOperatorKey.Length < 32 || incoming == null || incoming.Length is < 32 or > 512)
            throw new LobbyException(403, "invalid_broadcast_operator", "Broadcast observer admission is unavailable.");
        byte[] expected = SHA256.HashData(Encoding.UTF8.GetBytes(settings.BroadcastOperatorKey));
        byte[] supplied = SHA256.HashData(Encoding.UTF8.GetBytes(incoming));
        if (!CryptographicOperations.FixedTimeEquals(expected, supplied))
            throw new LobbyException(403, "invalid_broadcast_operator", "Broadcast observer admission is unavailable.");
    }

    private void ThrowIfDraining()
    {
        // Checked INSIDE the same mutation gate as allocation/admission. A final
        // ListAsync zero-room guard waits for earlier allocations; requests
        // queued behind it cannot reopen admission after the deployment check.
        if (File.Exists(settings.AdmissionDrainFile))
            throw new LobbyException(503, "admission_draining", "The station is updating. Please retry shortly.");
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

    public async Task<GameServerHeartbeatResult> HeartbeatAsync(Guid lobbyId, string? secret, GameServerHeartbeat heartbeat, CancellationToken cancellationToken)
    {
        await gate.WaitAsync(cancellationToken);
        try
        {
            Match match = Authenticate(lobbyId, secret);
            Lobby lobby = match.Lobby;
            if (heartbeat.CurrentPlayers < 0 || heartbeat.CurrentPlayers > lobby.MaxPlayers)
                throw new LobbyException(400, "invalid_player_count", "Invalid player count.");
            if (heartbeat.ConnectedActorIds != null && (heartbeat.ConnectedActorIds.Length > lobby.MaxPlayers
                || heartbeat.ConnectedActorIds.Any(id => !Guid.TryParseExact(id, "N", out _))
                || heartbeat.ConnectedActorIds.Distinct().Count() != heartbeat.ConnectedActorIds.Length))
                throw new LobbyException(400, "invalid_actor_bindings", "Invalid connected actor bindings.");
            if (heartbeat.ConnectedObserverSessions is { } observed && (observed.Length > 2
                || observed.Any(x => x == null || !Guid.TryParseExact(x.BroadcastSessionId, "N", out _)
                    || !Guid.TryParseExact(x.ObserverId, "N", out _) || x.ConnectionGeneration is < 1
                    or > ObserverTicketCodec.MaximumConnectionGeneration)
                || observed.Select(x => x.BroadcastSessionId).Distinct().Count() != observed.Length))
                throw new LobbyException(400, "invalid_observer_bindings", "Invalid connected observer bindings.");
            if (lobby.Status is not (LobbyStatus.Ready or LobbyStatus.Running)) return new([]);
            if (lobby.CurrentPlayers != heartbeat.CurrentPlayers)
                logger.LogInformation("PlayerCountChanged {LobbyId} {CurrentPlayers}", lobbyId, heartbeat.CurrentPlayers);
            if (lobby.CurrentPlayers > 0 && heartbeat.CurrentPlayers == 0) lobby.LastEmptyAt = clock.GetUtcNow();
            lobby.CurrentPlayers = heartbeat.CurrentPlayers;
            lobby.LastHeartbeatAt = clock.GetUtcNow();
            if (lobby.CurrentPlayers > 0) lobby.HasHadPlayers = true;
            lobby.Status = lobby.CurrentPlayers > 0 ? LobbyStatus.Running : LobbyStatus.Ready;
            foreach (string nonce in heartbeat.ConnectedNonces ?? []) match.PendingTickets.Remove(nonce);
            match.ConnectedActors.Clear();
            foreach (string actor in heartbeat.ConnectedActorIds ?? []) match.ConnectedActors.Add(actor);
            if (heartbeat.ConnectedObserverSessions != null)
            {
                var connected = heartbeat.ConnectedObserverSessions.Select(x => x.BroadcastSessionId).ToHashSet(StringComparer.Ordinal);
                foreach (string session in match.Observers.Where(x => x.Value.Revoking && !connected.Contains(x.Key)).Select(x => x.Key).ToArray())
                {
                    match.Observers.Remove(session);
                    match.RetiredObservers.Add(session);
                }
            }
            await repository.SaveAsync(lobby, cancellationToken);
            // Retained tombstones also cover tickets that were issued before
            // DELETE but had not connected at the empty heartbeat. Otherwise
            // the server could never learn that their future admission is revoked.
            return new(match.RetiredObservers.Concat(match.Observers.Where(x => x.Value.Revoking)
                .Select(x => x.Key)).Order(StringComparer.Ordinal).ToArray());
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
