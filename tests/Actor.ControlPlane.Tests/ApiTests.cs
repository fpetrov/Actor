using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Actor.ControlPlane.Application;
using Actor.ControlPlane.Configuration;
using Actor.ControlPlane.Infrastructure;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace Actor.ControlPlane.Tests;

public sealed class ApiFactory : WebApplicationFactory<Program>
{
    public FakeAllocator Allocator { get; } = new();
    private readonly string folder = Path.Combine(Path.GetTempPath(), "actor-api-tests-" + Guid.NewGuid().ToString("N"));
    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.ConfigureAppConfiguration((_, config) => config.AddInMemoryCollection(new Dictionary<string, string?>
        {
            ["Match:DatabasePath"] = Path.Combine(folder, "actor.db"),
            ["Match:UnityServerExecutable"] = "/unused/test-server",
            ["Match:GameServerPortStart"] = "24200", ["Match:GameServerPortEnd"] = "24204"
        }));
        builder.ConfigureServices(services =>
        {
            services.RemoveAll<IGameServerAllocator>();
            services.AddSingleton<IGameServerAllocator>(Allocator);
        });
    }
    protected override void Dispose(bool disposing)
    {
        base.Dispose(disposing);
        Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
        if (Directory.Exists(folder)) Directory.Delete(folder, true);
    }
}

public sealed class ApiTests
{
    [Fact]
    public async Task MinimalApiCreateReadyJoinReturnsTicketWithoutInfrastructureSecrets()
    {
        using var factory = new ApiFactory();
        using var client = factory.CreateClient();
        Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/health")).StatusCode);
        var create = await client.PostAsJsonAsync("/api/v1/lobbies", new CreateLobbyRequest("API Hall", null, "0.2.0", "1"));
        Assert.Equal(HttpStatusCode.Created, create.StatusCode);
        string body = await create.Content.ReadAsStringAsync();
        Assert.DoesNotContain("serverPid", body);
        Assert.DoesNotContain("secret", body, StringComparison.OrdinalIgnoreCase);
        var lobby = JsonSerializer.Deserialize<LobbySummary>(body, new JsonSerializerOptions(JsonSerializerDefaults.Web))!;
        using var ready = new HttpRequestMessage(HttpMethod.Post, $"/internal/game-servers/{lobby.LobbyId}/ready");
        ready.Headers.Add("X-Actor-Match-Secret", Convert.ToBase64String(factory.Allocator.Keys[Guid.Parse(lobby.LobbyId)]));
        Assert.Equal(HttpStatusCode.NoContent, (await client.SendAsync(ready)).StatusCode);
        var join = await client.PostAsJsonAsync($"/api/v1/lobbies/{lobby.RoomCode}/join", new JoinLobbyRequest("Player A", "0.2.0", "1"));
        Assert.Equal(HttpStatusCode.OK, join.StatusCode);
        var ticket = await join.Content.ReadFromJsonAsync<JoinLobbyResult>();
        Assert.NotNull(ticket);
        Assert.Contains('.', ticket.JoinTicket);
        Assert.Equal(lobby.GamePort, ticket.Port);
        var openapi = await client.GetAsync("/api/openapi/v1.json");
        Assert.Equal(HttpStatusCode.OK, openapi.StatusCode);
        Assert.DoesNotContain("/internal", await openapi.Content.ReadAsStringAsync());
        Assert.Equal(HttpStatusCode.NotFound, (await client.GetAsync("/")).StatusCode);
    }

    [Fact]
    public async Task ApiRejectsSixthRoomWithStructuredCapacityError()
    {
        using var factory = new ApiFactory();
        using var client = factory.CreateClient();
        for (int i = 0; i < 5; i++)
            Assert.Equal(HttpStatusCode.Created, (await client.PostAsJsonAsync("/api/v1/lobbies", new CreateLobbyRequest("Capacity Hall", null, "0.2.0", "1"))).StatusCode);
        var sixth = await client.PostAsJsonAsync("/api/v1/lobbies", new CreateLobbyRequest("Sixth Hall", null, "0.2.0", "1"));
        Assert.Equal(HttpStatusCode.Conflict, sixth.StatusCode);
        Assert.Contains("server_capacity_reached", await sixth.Content.ReadAsStringAsync());
        Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("/health")).StatusCode);
    }
}
