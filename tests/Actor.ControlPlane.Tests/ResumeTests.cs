using System.Text.Json;
using Actor.Contracts;
using Actor.ControlPlane.Application;
using Actor.ControlPlane.Domain;

namespace Actor.ControlPlane.Tests;

public sealed class ResumeTests
{
    private static readonly JsonSerializerOptions Json = new() { IncludeFields = true };
    private static async Task<LobbySummary> Social(LobbyFixture f)
    {
        var lobby = await f.Manager.CreateAsync(new CreateLobbyRequest("Resume Test", 4,
            f.Settings.GameVersion, f.Settings.ProtocolVersion, "SocialDeduction"), default);
        await f.ReadyAsync(lobby); return lobby;
    }
    private static ResumeCredentialClaims Claims(LobbyFixture f, LobbySummary lobby, string? actor = null) => new()
    { matchId = lobby.LobbyId, actorId = actor ?? Guid.NewGuid().ToString("N"), playerName = "Player A",
        gameVersion = f.Settings.GameVersion, protocolVersion = f.Settings.ProtocolVersion,
        credentialId = Guid.NewGuid().ToString("N"), generation = 7, expiresAt = f.Clock.Now.AddSeconds(100).ToUnixTimeSeconds() };
    private static string Sign(LobbyFixture f, LobbySummary lobby, ResumeCredentialClaims c)
        => ResumeCredentialCodec.Sign(JsonSerializer.Serialize(c, Json), f.Allocator.Keys[Guid.Parse(lobby.LobbyId)]);
    private static Task<JoinLobbyResult> Resume(LobbyFixture f, LobbySummary lobby, string token)
        => f.Manager.ResumeAsync(lobby.RoomCode, new ResumeLobbyRequest(token, f.Settings.GameVersion, f.Settings.ProtocolVersion), default);

    [Fact]
    public async Task InitialAdmissionFourBindsRandomActorAndFirstGenerationWithoutNameIdentity()
    {
        await using var f = new LobbyFixture(); await f.InitializeAsync(); var lobby = await Social(f);
        var first = await f.Manager.JoinAsync(lobby.RoomCode, new JoinLobbyRequest("Same Name", f.Settings.GameVersion, f.Settings.ProtocolVersion), default);
        var second = await f.Manager.JoinAsync(lobby.RoomCode, new JoinLobbyRequest("Same Name", f.Settings.GameVersion, f.Settings.ProtocolVersion), default);
        byte[] key = f.Allocator.Keys[Guid.Parse(lobby.LobbyId)];
        Assert.True(JoinTicketCodec.TryVerify(first.JoinTicket, key, out var a)); Assert.True(JoinTicketCodec.TryVerify(second.JoinTicket, key, out var b));
        var ca = JsonSerializer.Deserialize<JoinTicketClaims>(a, Json)!; var cb = JsonSerializer.Deserialize<JoinTicketClaims>(b, Json)!;
        Assert.True(Guid.TryParseExact(ca.actorId, "N", out _)); Assert.NotEqual(ca.actorId, cb.actorId);
        Assert.Equal(1ul, ca.connectionGeneration); Assert.False(ca.resume); Assert.Equal("4", ca.protocolVersion);
        Assert.DoesNotContain(first.JoinTicket, first.ToString());
    }
    [Fact]
    public async Task ResumeBindsActorSourceGenerationAndLifetimeWithoutConsumingCapacity()
    {
        await using var f = new LobbyFixture(); await f.InitializeAsync(); var lobby = await Social(f);
        var c = Claims(f, lobby); string token = Sign(f, lobby, c);
        for (int i = 0; i < 8; i++)
        {
            var admission = await Resume(f, lobby, token); Assert.True(admission.Resume);
            Assert.True(JoinTicketCodec.TryVerify(admission.JoinTicket, f.Allocator.Keys[Guid.Parse(lobby.LobbyId)], out var payload));
            var joined = JsonSerializer.Deserialize<JoinTicketClaims>(payload, Json)!;
            Assert.True(joined.resume); Assert.Equal(c.actorId, joined.actorId); Assert.Equal(c.generation, joined.resumeGeneration);
            Assert.True(joined.resumeCredentialId == c.credentialId); Assert.Equal(c.playerName, joined.playerName);
            Assert.True(admission.ExpiresAt.ToUnixTimeSeconds() <= c.expiresAt);
        }
        // Resume retries didn't reserve eight new players.
        var normal = await f.Manager.JoinAsync(lobby.RoomCode, new JoinLobbyRequest("Fresh Player", f.Settings.GameVersion, f.Settings.ProtocolVersion), default);
        Assert.False(normal.Resume); Assert.DoesNotContain(token, new ResumeLobbyRequest(token, "0.9.0", "4").ToString());
    }
    [Fact]
    public async Task WrongMatchForgedExpiredAndConnectedCredentialsFailClosed()
    {
        await using var f = new LobbyFixture(); await f.InitializeAsync(); var lobby = await Social(f); var other = await Social(f);
        var c = Claims(f, lobby); string token = Sign(f, lobby, c);
        Assert.Equal("invalid_resume", (await Assert.ThrowsAsync<LobbyException>(() => Resume(f, other, token))).Code);
        Assert.Equal("invalid_resume", (await Assert.ThrowsAsync<LobbyException>(() => Resume(f, lobby, token+"x"))).Code);
        c.expiresAt = f.Clock.Now.ToUnixTimeSeconds();
        Assert.Equal("invalid_resume", (await Assert.ThrowsAsync<LobbyException>(() => Resume(f, lobby, Sign(f,lobby,c)))).Code);
        c = Claims(f,lobby); token=Sign(f,lobby,c);
        await f.Manager.HeartbeatAsync(Guid.Parse(lobby.LobbyId), Convert.ToBase64String(f.Allocator.Keys[Guid.Parse(lobby.LobbyId)]),
            new GameServerHeartbeat(1,"Running",[],[c.actorId]),default);
        Assert.Equal("resume_busy", (await Assert.ThrowsAsync<LobbyException>(() => Resume(f,lobby,token))).Code);
        await f.Manager.HeartbeatAsync(Guid.Parse(lobby.LobbyId), Convert.ToBase64String(f.Allocator.Keys[Guid.Parse(lobby.LobbyId)]),
            new GameServerHeartbeat(0,"Ready",[],[]),default);
        Assert.True((await Resume(f,lobby,token)).Resume);
    }
    [Fact]
    public async Task ResumeDoesNotConvertLegacySandboxIntoResumableGame()
    {
        await using var f = new LobbyFixture(); await f.InitializeAsync(); var lobby = await f.CreateAsync(); await f.ReadyAsync(lobby);
        Assert.Equal("resume_unavailable", (await Assert.ThrowsAsync<LobbyException>(() => Resume(f,lobby,Sign(f,lobby,Claims(f,lobby))))).Code);
    }
}
