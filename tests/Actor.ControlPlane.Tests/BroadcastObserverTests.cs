using System.Net;
using System.Text.Json;
using Actor.Contracts;
using Actor.ControlPlane.Api;
using Actor.ControlPlane.Application;
using Actor.ControlPlane.Domain;
using Microsoft.AspNetCore.Http;

namespace Actor.ControlPlane.Tests;

public sealed class BroadcastObserverTests
{
    private const string Operator = "test-only-operator-key-with-at-least-32-characters";
    private static readonly JsonSerializerOptions Json = new() { IncludeFields = true };
    private static async Task<LobbySummary> Social(LobbyFixture f)
    {
        f.Settings.BroadcastOperatorKey = Operator;
        var lobby = await f.Manager.CreateAsync(new CreateLobbyRequest("Broadcast test", 4,
            f.Settings.GameVersion, f.Settings.ProtocolVersion, "SocialDeduction"), default);
        await f.ReadyAsync(lobby); return lobby;
    }
    private static Task<ObserverAdmissionResult> Observe(LobbyFixture f, LobbySummary lobby, string? session = null,
        string? key = Operator) => f.Manager.ObserveAsync(lobby.RoomCode,
            new ObserveLobbyRequest(session ?? Guid.NewGuid().ToString("N"), f.Settings.GameVersion, f.Settings.ProtocolVersion), key, default);

    [Fact]
    public async Task FullRoomCanBeObservedWithoutPlayerReservationOrPrivateIdentity()
    {
        await using var f = new LobbyFixture(); await f.InitializeAsync(); var lobby = await Social(f);
        for (int i = 0; i < 4; i++)
            await f.Manager.JoinAsync(lobby.RoomCode, new JoinLobbyRequest("Player " + i, f.Settings.GameVersion,
                f.Settings.ProtocolVersion), default);
        var result = await Observe(f, lobby);
        Assert.Equal(0, (await f.Manager.GetAsync(lobby.RoomCode, default)).CurrentPlayers);
        byte[] key = f.Allocator.Keys[Guid.Parse(lobby.LobbyId)];
        Assert.True(ObserverTicketCodec.TryVerify(result.JoinTicket, key, out string payload));
        var claims = JsonSerializer.Deserialize<ObserverTicketClaims>(payload, Json)!;
        Assert.True(ObserverTicketCodec.ClaimsMatch(claims, lobby.LobbyId, f.Settings.GameVersion,
            f.Settings.ProtocolVersion, f.Clock.Now.ToUnixTimeSeconds()));
        Assert.Equal(result.ObserverId, claims.observerId);
        Assert.Equal(ObserverTicketCodec.Audience, claims.audience);
        Assert.False(JoinTicketCodec.TryVerify(result.JoinTicket, key, out _));
        Assert.False(ResumeCredentialCodec.TryVerify(result.JoinTicket, key, out _));
        Assert.DoesNotContain(result.JoinTicket, result.ToString());
        Assert.DoesNotContain("playerName", payload);
        Assert.DoesNotContain("role", payload, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task ObserverReservationsNeverConsumeRemainingPlayerSlots()
    {
        await using var f = new LobbyFixture(); await f.InitializeAsync(); var lobby = await Social(f);
        await Observe(f, lobby); await Observe(f, lobby);
        for (int i = 0; i < 4; i++)
            await f.Manager.JoinAsync(lobby.RoomCode, new JoinLobbyRequest("Player " + i, f.Settings.GameVersion,
                f.Settings.ProtocolVersion), default);
        Assert.Equal("room_full", (await Assert.ThrowsAsync<LobbyException>(() => f.Manager.JoinAsync(lobby.RoomCode,
            new JoinLobbyRequest("Player five", f.Settings.GameVersion, f.Settings.ProtocolVersion), default))).Code);
        Assert.Equal("observer_capacity_reached", (await Assert.ThrowsAsync<LobbyException>(() => Observe(f, lobby))).Code);
    }

    [Fact]
    public async Task ConcurrentReconnectsAdvanceGenerationAndUseNewNonceWithSameObserverIdentity()
    {
        await using var f = new LobbyFixture(); await f.InitializeAsync(); var lobby = await Social(f);
        string session = Guid.NewGuid().ToString("N");
        var results = await Task.WhenAll(Enumerable.Range(0, 10).Select(_ => Observe(f, lobby, session)));
        Assert.Single(results.Select(x => x.ObserverId).Distinct());
        Assert.Equal(Enumerable.Range(1, 10).Select(x => (ulong)x), results.Select(x => x.ConnectionGeneration).Order());
        Assert.Equal(10, results.Select(x => x.JoinTicket).Distinct().Count());
        Assert.All(results, r => Assert.InRange((r.ExpiresAt - f.Clock.Now).TotalSeconds, 1, 60));
        // The other observer identity remains available; reconnects didn't fill it.
        await Observe(f, lobby);
    }

    [Fact]
    public async Task DisabledWrongOperatorSandboxStartingAndDrainFailClosed()
    {
        await using var f = new LobbyFixture(); await f.InitializeAsync(); var sandbox = await f.CreateAsync();
        await f.ReadyAsync(sandbox);
        Assert.Equal("invalid_broadcast_operator", (await Assert.ThrowsAsync<LobbyException>(() => Observe(f, sandbox))).Code);
        f.Settings.BroadcastOperatorKey = Operator;
        Assert.Equal("invalid_broadcast_operator", (await Assert.ThrowsAsync<LobbyException>(() => Observe(f, sandbox, key: new string('x', 64)))).Code);
        Assert.Equal("observer_unavailable", (await Assert.ThrowsAsync<LobbyException>(() => Observe(f, sandbox))).Code);
        var social = await Social(f);
        Assert.Equal("invalid_broadcast_session", (await Assert.ThrowsAsync<LobbyException>(() => Observe(f, social, "not-a-session"))).Code);
        f.Settings.AdmissionDrainFile = Path.Combine(f.Folder, "drain");
        await File.WriteAllTextAsync(f.Settings.AdmissionDrainFile, "1");
        Assert.Equal("admission_draining", (await Assert.ThrowsAsync<LobbyException>(() => Observe(f, social))).Code);
    }

    [Fact]
    public async Task ObserverSigningDomainCannotVerifyPlayerTicketAndExpiredOrOverflowTimesFail()
    {
        await using var f = new LobbyFixture(); await f.InitializeAsync(); var lobby = await Social(f);
        var admission = await Observe(f, lobby);
        byte[] key = f.Allocator.Keys[Guid.Parse(lobby.LobbyId)];
        Assert.True(ObserverTicketCodec.TryVerify(admission.JoinTicket, key, out var payload));
        var claims = JsonSerializer.Deserialize<ObserverTicketClaims>(payload, Json)!;
        Assert.False(ObserverTicketCodec.ClaimsMatch(claims, lobby.LobbyId, f.Settings.GameVersion, f.Settings.ProtocolVersion, long.MinValue));
        Assert.False(ObserverTicketCodec.ClaimsMatch(claims, lobby.LobbyId, f.Settings.GameVersion, f.Settings.ProtocolVersion, claims.expiresAt));
        Assert.False(ObserverTicketCodec.TryVerify(admission.JoinTicket + "x", key, out _));
        Assert.False(ObserverTicketCodec.TryVerify(admission.JoinTicket, new byte[32], out _));
        var player = await f.Manager.JoinAsync(lobby.RoomCode, new JoinLobbyRequest("Player A", f.Settings.GameVersion,
            f.Settings.ProtocolVersion), default);
        Assert.False(ObserverTicketCodec.TryVerify(ObserverTicketCodec.WirePrefix + player.JoinTicket, key, out _));
    }

    [Fact]
    public async Task ReleaseRequiresServerDisconnectAcknowledgementAndNeverResetsRetiredGeneration()
    {
        await using var f = new LobbyFixture(); await f.InitializeAsync(); var lobby = await Social(f);
        var first = await Observe(f, lobby); await Observe(f, lobby);
        Guid match = Guid.Parse(lobby.LobbyId); string secret = Convert.ToBase64String(f.Allocator.Keys[match]);
        await f.Manager.ReleaseObserverAsync(lobby.RoomCode, first.BroadcastSessionId, Operator, default);
        var binding = new ObserverConnectionBinding(first.BroadcastSessionId, first.ObserverId, first.ConnectionGeneration);
        var revoke = await f.Manager.HeartbeatAsync(match, secret, new GameServerHeartbeat(0, "Ready", [], [], [binding]), default);
        Assert.Equal([first.BroadcastSessionId], revoke.RevokedObserverSessions);
        Assert.Equal("observer_capacity_reached", (await Assert.ThrowsAsync<LobbyException>(() => Observe(f, lobby))).Code);
        // Legacy/omitted observer status cannot acknowledge a disconnect.
        await f.Manager.HeartbeatAsync(match, secret, new GameServerHeartbeat(0, "Ready", [], []), default);
        Assert.Equal("observer_session_retired", (await Assert.ThrowsAsync<LobbyException>(() => Observe(f, lobby, first.BroadcastSessionId))).Code);
        Assert.Equal([first.BroadcastSessionId], (await f.Manager.HeartbeatAsync(match, secret,
            new GameServerHeartbeat(0, "Ready", [], [], []), default)).RevokedObserverSessions);
        await Observe(f, lobby);
        Assert.Equal("observer_session_retired", (await Assert.ThrowsAsync<LobbyException>(() => Observe(f, lobby, first.BroadcastSessionId))).Code);
        Assert.Equal(0, (await f.Manager.GetAsync(lobby.RoomCode, default)).CurrentPlayers);
    }

    [Fact]
    public async Task ReleaseBeforePendingTicketConnectsRetainsServerRevocationAfterEmptyHeartbeat()
    {
        await using var f = new LobbyFixture(); await f.InitializeAsync(); var lobby = await Social(f);
        var pending = await Observe(f, lobby);
        await f.Manager.ReleaseObserverAsync(lobby.RoomCode, pending.BroadcastSessionId, Operator, default);
        Guid match = Guid.Parse(lobby.LobbyId); string secret = Convert.ToBase64String(f.Allocator.Keys[match]);
        for (int i = 0; i < 3; i++)
            Assert.Equal([pending.BroadcastSessionId], (await f.Manager.HeartbeatAsync(match, secret,
                new GameServerHeartbeat(0, "Ready", [], [], []), default)).RevokedObserverSessions);
        Assert.Equal("observer_session_retired", (await Assert.ThrowsAsync<LobbyException>(() => Observe(f, lobby, pending.BroadcastSessionId))).Code);
    }

    [Theory]
    [InlineData("127.0.0.1", null, true)]
    [InlineData("::1", null, true)]
    [InlineData("192.168.1.5", null, false)]
    [InlineData("127.0.0.1", "X-Original-For", false)]
    [InlineData("127.0.0.1", "X-Forwarded-For", false)]
    [InlineData("127.0.0.1", "Forwarded", false)]
    public void OperatorEndpointRequiresDirectLoopbackEvenBehindTrustedProxy(string ip, string? forwarded, bool allowed)
    {
        var context = new DefaultHttpContext(); context.Connection.RemoteIpAddress = IPAddress.Parse(ip);
        if (forwarded != null) context.Request.Headers[forwarded] = "127.0.0.1";
        if (allowed) BroadcastOperatorBoundary.RequireDirectLoopback(context);
        else Assert.Equal(403, Assert.Throws<LobbyException>(() => BroadcastOperatorBoundary.RequireDirectLoopback(context)).Status);
    }
}
