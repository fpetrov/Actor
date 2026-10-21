using Actor.ControlPlane.Application;
using Actor.ControlPlane.Configuration;
using Actor.ControlPlane.Domain;

namespace Actor.ControlPlane.Tests;

public sealed class SocialLobbyTests
{
    [Fact]
    public async Task ModeAndRulesAreSnapshottedAndPersistedAtCreation()
    {
        await using var fixture = new LobbyFixture();
        await fixture.InitializeAsync();
        var social = await fixture.Manager.CreateAsync(new CreateLobbyRequest("Social Hall", 4,
            fixture.Settings.GameVersion, fixture.Settings.ProtocolVersion, "SocialDeduction"), CancellationToken.None);
        fixture.Settings.SocialDeduction = fixture.Settings.SocialDeduction with { KillCooldownMs = 60000 };
        var persisted = (await fixture.Repository.ListAsync()).Single();
        Assert.Equal("SocialDeduction", social.GameMode);
        Assert.Equal("SocialDeduction", persisted.GameMode);
        Assert.Equal(4, persisted.SocialConfiguration!.MaxPlayers);
        Assert.Equal(30000, persisted.SocialConfiguration.KillCooldownMs);
        await fixture.ReadyAsync(social);
        var admission = await fixture.Manager.JoinAsync(social.RoomCode,
            new JoinLobbyRequest("Player A", fixture.Settings.GameVersion, fixture.Settings.ProtocolVersion), CancellationToken.None);
        Assert.Equal("SocialDeduction", admission.GameMode);
        var sandbox = await fixture.CreateAsync();
        Assert.Equal("Sandbox", sandbox.GameMode);
        Assert.Null((await fixture.Repository.ListAsync()).Single(l => l.RoomCode == sandbox.RoomCode).SocialConfiguration);
    }

    [Theory]
    [InlineData("SocialDeduction", 3)]
    [InlineData("SocialDeduction", 7)]
    [InlineData("client-debug-mode", 4)]
    public async Task InvalidModeOrSocialCapacityNeverAllocatesServer(string mode, int capacity)
    {
        await using var fixture = new LobbyFixture();
        await fixture.InitializeAsync();
        var error = await Assert.ThrowsAsync<LobbyException>(() => fixture.Manager.CreateAsync(new CreateLobbyRequest(
            "Invalid Room", capacity, fixture.Settings.GameVersion, fixture.Settings.ProtocolVersion, mode), CancellationToken.None));
        Assert.Equal(400, error.Status);
        Assert.Empty(fixture.Allocator.Running);
        Assert.Empty(await fixture.Repository.ListAsync());
    }

    [Fact]
    public void DeploymentRulesHaveMeaningfulProductionBounds()
    {
        Assert.True(new SocialMatchOptions().IsValid());
        Assert.False((new SocialMatchOptions() with { MinPlayers = 2 }).IsValid());
        Assert.False((new SocialMatchOptions() with { ImpostorCount = 2 }).IsValid());
        Assert.False((new SocialMatchOptions() with { KillDistance = double.NaN }).IsValid());
        Assert.False((new SocialMatchOptions() with { DiscussionDurationMs = 1 }).IsValid());
        Assert.False((new SocialMatchOptions() with { MaxPlayers = 7 }).IsValid());
    }
}
