using System.Security.Cryptography;
using Actor.Contracts;
using Actor.ControlPlane.Application;
using Actor.ControlPlane.Configuration;
using Actor.ControlPlane.Domain;
using Actor.ControlPlane.Infrastructure;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;

namespace Actor.ControlPlane.Tests;

public sealed class MutableClock : TimeProvider
{
    public DateTimeOffset Now { get; set; } = DateTimeOffset.UtcNow;
    public override DateTimeOffset GetUtcNow() => Now;
}

public sealed class FakeAllocator : IGameServerAllocator
{
    public Dictionary<Guid, byte[]> Keys { get; } = new();
    public HashSet<Guid> Running { get; } = new();
    public List<Guid> Reaped { get; } = new();
    public bool FailNext { get; set; }
    public int Peak { get; private set; }
    public Task<GameServerAllocation> AllocateAsync(Lobby lobby, byte[] secret, CancellationToken cancellationToken)
    {
        if (FailNext) { FailNext = false; throw new IOException("Intentional startup failure."); }
        Keys.Add(lobby.LobbyId, secret);
        Running.Add(lobby.LobbyId);
        Peak = Math.Max(Peak, Running.Count);
        return Task.FromResult(new GameServerAllocation(1000 + Keys.Count, DateTimeOffset.UtcNow));
    }
    public Task ReleaseAsync(Guid lobbyId, CancellationToken cancellationToken) { Running.Remove(lobbyId); return Task.CompletedTask; }
    public bool IsRunning(Guid lobbyId) => Running.Contains(lobbyId);
    public Task ReapPersistedAsync(Lobby lobby, CancellationToken cancellationToken) { Reaped.Add(lobby.LobbyId); return Task.CompletedTask; }
}

public sealed class LobbyFixture : IAsyncDisposable
{
    public readonly string Folder = Path.Combine(Path.GetTempPath(), "actor-tests-" + Guid.NewGuid().ToString("N"));
    public readonly MatchOptions Settings = new() { UnityServerExecutable = "/unused/test-server", GameServerPortStart = 24000, GameServerPortEnd = 24009 };
    public readonly FakeAllocator Allocator = new();
    public readonly MutableClock Clock = new();
    public readonly JoinTicketService Tickets = new();
    public SqliteLobbyRepository Repository { get; }
    public LobbyManager Manager { get; }

    public LobbyFixture()
    {
        Settings.DatabasePath = Path.Combine(Folder, "actor.db");
        var options = Options.Create(Settings);
        Repository = new SqliteLobbyRepository(options);
        Manager = new LobbyManager(Repository, Allocator, new PortAllocator(options), Tickets, options, Clock, NullLogger<LobbyManager>.Instance);
    }
    public async Task InitializeAsync() => await Manager.InitializeAsync(CancellationToken.None);
    public Task<LobbySummary> CreateAsync() => Manager.CreateAsync(new CreateLobbyRequest("Test Hall", null, Settings.GameVersion, Settings.ProtocolVersion), CancellationToken.None);
    public async Task ReadyAsync(LobbySummary lobby) => await Manager.ReadyAsync(Guid.Parse(lobby.LobbyId), Convert.ToBase64String(Allocator.Keys[Guid.Parse(lobby.LobbyId)]), CancellationToken.None);
    public async ValueTask DisposeAsync()
    {
        await Manager.ShutdownAsync(CancellationToken.None);
        Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
        if (Directory.Exists(Folder)) Directory.Delete(Folder, true);
    }
}

public sealed class LobbyTests
{
    [Fact]
    public async Task TenConcurrentCreatesAllocateOnlyFiveUniquePortsAndCodes()
    {
        await using var fixture = new LobbyFixture();
        await fixture.InitializeAsync();
        var attempts = await Task.WhenAll(Enumerable.Range(0, 10).Select(async _ =>
        {
            try { return (Lobby: await fixture.CreateAsync(), Error: (LobbyException?)null); }
            catch (LobbyException error) { return (Lobby: (LobbySummary?)null, Error: error); }
        }));
        var created = attempts.Where(a => a.Lobby != null).Select(a => a.Lobby!).ToArray();
        Assert.Equal(5, created.Length);
        Assert.Equal(5, created.Select(l => l.GamePort).Distinct().Count());
        Assert.Equal(5, created.Select(l => l.RoomCode).Distinct().Count());
        Assert.All(created, l => Assert.Matches("^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6}$", l.RoomCode));
        Assert.All(attempts.Where(a => a.Error != null), a => { Assert.Equal(409, a.Error!.Status); Assert.Equal("server_capacity_reached", a.Error.Code); });
        Assert.Equal(5, fixture.Allocator.Peak);
    }

    [Fact]
    public async Task FailedStartupReleasesSlotAndFirstPort()
    {
        await using var fixture = new LobbyFixture();
        await fixture.InitializeAsync();
        fixture.Allocator.FailNext = true;
        var error = await Assert.ThrowsAsync<LobbyException>(fixture.CreateAsync);
        Assert.Equal("server_start_failed", error.Code);
        Assert.Empty(await fixture.Manager.ListAsync(CancellationToken.None));
        var lobby = await fixture.CreateAsync();
        Assert.Equal(fixture.Settings.GameServerPortStart, lobby.GamePort);
        Assert.Contains(await fixture.Repository.ListAsync(), l => l.Status == LobbyStatus.Failed);
    }

    [Fact]
    public async Task ExitedProcessReleasesSlotAndCanBeReplaced()
    {
        await using var fixture = new LobbyFixture();
        await fixture.InitializeAsync();
        var first = await fixture.CreateAsync();
        for (int i = 0; i < 4; i++) await fixture.CreateAsync();
        fixture.Allocator.Running.Remove(Guid.Parse(first.LobbyId));
        await fixture.Manager.MonitorAsync(CancellationToken.None);
        var replacement = await fixture.CreateAsync();
        Assert.Equal(first.GamePort, replacement.GamePort);
        Assert.NotEqual(first.RoomCode, replacement.RoomCode);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task EmptyRoomLifetimeAndGracePeriodReleaseServer(bool hadPlayers)
    {
        await using var fixture = new LobbyFixture();
        await fixture.InitializeAsync();
        var lobby = await fixture.CreateAsync();
        await fixture.ReadyAsync(lobby);
        Guid id = Guid.Parse(lobby.LobbyId);
        string key = Convert.ToBase64String(fixture.Allocator.Keys[id]);
        if (hadPlayers)
        {
            await fixture.Manager.HeartbeatAsync(id, key, new GameServerHeartbeat(1, "Running", []), CancellationToken.None);
            await fixture.Manager.HeartbeatAsync(id, key, new GameServerHeartbeat(0, "Ready", []), CancellationToken.None);
        }
        fixture.Clock.Now = fixture.Clock.Now.AddSeconds(hadPlayers ? fixture.Settings.EmptyLobbyGracePeriod + 1 : fixture.Settings.EmptyLobbyLifetime + 1);
        await fixture.Manager.HeartbeatAsync(id, key, new GameServerHeartbeat(0, "Ready", []), CancellationToken.None);
        await fixture.Manager.MonitorAsync(CancellationToken.None);
        Assert.Empty(await fixture.Manager.ListAsync(CancellationToken.None));
        Assert.Empty(fixture.Allocator.Running);
        Assert.Equal(LobbyStatus.Finished, (await fixture.Repository.ListAsync()).Single().Status);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task MissingReadyOrHeartbeatTimesOut(bool ready)
    {
        await using var fixture = new LobbyFixture();
        await fixture.InitializeAsync();
        var lobby = await fixture.CreateAsync();
        if (ready) await fixture.ReadyAsync(lobby);
        fixture.Clock.Now = fixture.Clock.Now.AddSeconds(ready ? fixture.Settings.HeartbeatTimeout + 1 : fixture.Settings.LobbyStartupTimeout + 1);
        await fixture.Manager.MonitorAsync(CancellationToken.None);
        Assert.Empty(fixture.Allocator.Running);
        Assert.Equal(LobbyStatus.Failed, (await fixture.Repository.ListAsync()).Single().Status);
    }

    [Fact]
    public async Task ReadyCallbackRequiresPerMatchSecretAndJoinRequiresCompatibleVersion()
    {
        await using var fixture = new LobbyFixture();
        await fixture.InitializeAsync();
        var lobby = await fixture.CreateAsync();
        var forbidden = await Assert.ThrowsAsync<LobbyException>(() => fixture.Manager.ReadyAsync(Guid.Parse(lobby.LobbyId), "bad", CancellationToken.None));
        Assert.Equal(403, forbidden.Status);
        await fixture.ReadyAsync(lobby);
        var error = await Assert.ThrowsAsync<LobbyException>(() => fixture.Manager.JoinAsync(lobby.RoomCode,
            new JoinLobbyRequest("Player A", "wrong", fixture.Settings.ProtocolVersion), CancellationToken.None));
        Assert.Equal("version_mismatch", error.Code);
    }

    [Fact]
    public async Task PendingTicketsPreventConcurrentOverbookingAndExpire()
    {
        await using var fixture = new LobbyFixture();
        fixture.Settings.DefaultMaxPlayers = 2;
        await fixture.InitializeAsync();
        var lobby = await fixture.CreateAsync();
        await fixture.ReadyAsync(lobby);
        var request = new JoinLobbyRequest("Player A", fixture.Settings.GameVersion, fixture.Settings.ProtocolVersion);
        var first = await fixture.Manager.JoinAsync(lobby.RoomCode, request, CancellationToken.None);
        await fixture.Manager.JoinAsync(lobby.RoomCode, request, CancellationToken.None);
        var error = await Assert.ThrowsAsync<LobbyException>(() => fixture.Manager.JoinAsync(lobby.RoomCode, request, CancellationToken.None));
        Assert.Equal("room_full", error.Code);
        Assert.True(fixture.Tickets.Verify(first.JoinTicket, fixture.Allocator.Keys[Guid.Parse(lobby.LobbyId)], lobby.LobbyId,
            fixture.Settings.GameVersion, fixture.Settings.ProtocolVersion, fixture.Clock.Now.ToUnixTimeSeconds()));
        fixture.Clock.Now = fixture.Clock.Now.AddSeconds(fixture.Settings.JoinTicketLifetime + 1);
        Assert.NotNull(await fixture.Manager.JoinAsync(lobby.RoomCode, request, CancellationToken.None));
    }

    [Fact]
    public async Task RestartMarksPersistedActiveRoomFailedAndReapsOnlyPersistedInstance()
    {
        await using var fixture = new LobbyFixture();
        await fixture.Repository.InitializeAsync();
        Guid id = Guid.NewGuid();
        await fixture.Repository.SaveAsync(new Lobby { LobbyId = id, RoomCode = "ABC234", Status = LobbyStatus.Running, ServerPid = 1234 });
        await fixture.InitializeAsync();
        var persisted = (await fixture.Repository.ListAsync()).Single();
        Assert.Equal(LobbyStatus.Failed, persisted.Status);
        Assert.Equal("control_plane_restarted", persisted.EndReason);
        Assert.Equal([id], fixture.Allocator.Reaped);
        Assert.Empty(await fixture.Manager.ListAsync(CancellationToken.None));
        Assert.True(await fixture.Repository.CheckHealthAsync());
    }

    [Fact]
    public async Task PortReservationsAreUniqueAcrossParallelThreadsAndReusable()
    {
        var settings = new MatchOptions { GameServerPortStart = 24100, GameServerPortEnd = 24119 };
        var ports = new PortAllocator(Options.Create(settings));
        Guid[] ids = Enumerable.Range(0, 20).Select(_ => Guid.NewGuid()).ToArray();
        int[] allocated = await Task.WhenAll(ids.Select(id => Task.Run(() => ports.Reserve(id))));
        Assert.Equal(20, allocated.Distinct().Count());
        ports.Release(ids[0]);
        Assert.Equal(allocated[0], ports.Reserve(Guid.NewGuid()));
    }

    [Theory]
    [InlineData("valid", true)]
    [InlineData("tampered", false)]
    [InlineData("expired", false)]
    [InlineData("wrong_lobby", false)]
    [InlineData("wrong_version", false)]
    public void JoinTicketRejectsInvalidClaimsAndSignature(string kind, bool expected)
    {
        var service = new JoinTicketService();
        byte[] secret = RandomNumberGenerator.GetBytes(32);
        long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        string id = Guid.NewGuid().ToString("N");
        var claims = new JoinTicketClaims { lobbyId = id, playerName = "Player A", gameVersion = "0.2.0", protocolVersion = "1", expiresAt = now + 45, nonce = Guid.NewGuid().ToString("N") };
        if (kind == "expired") claims.expiresAt = now - 1;
        string ticket = service.Issue(claims, secret);
        if (kind == "tampered") ticket = (ticket[0] == 'A' ? "B" : "A") + ticket[1..];
        Assert.Equal(expected, service.Verify(ticket, secret, kind == "wrong_lobby" ? "other" : id,
            kind == "wrong_version" ? "different" : "0.2.0", "1", now));
    }
}
