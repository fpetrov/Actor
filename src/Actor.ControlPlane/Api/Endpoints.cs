using Actor.ControlPlane.Application;
using Actor.ControlPlane.Configuration;
using Actor.ControlPlane.Domain;
using Actor.ControlPlane.Infrastructure;
using Microsoft.Extensions.Options;

namespace Actor.ControlPlane.Api;

public sealed record ApiError(string Code, string Message);
public sealed record ErrorEnvelope(ApiError Error);

public static class Endpoints
{
    public static void MapActorApi(this WebApplication app)
    {
        app.Use(async (context, next) =>
        {
            try { await next(context); }
            catch (LobbyException error)
            {
                context.Response.StatusCode = error.Status;
                await context.Response.WriteAsJsonAsync(new ErrorEnvelope(new ApiError(error.Code, error.Message)));
            }
        });
        var api = app.MapGroup("/api/v1");
        api.MapGet("/lobbies", async (LobbyManager manager, CancellationToken token)
            => Results.Ok(new { lobbies = await manager.ListAsync(token) })).WithName("ListLobbies");
        api.MapPost("/lobbies", async (CreateLobbyRequest request, LobbyManager manager, CancellationToken token) =>
        {
            LobbySummary lobby = await manager.CreateAsync(request, token);
            return Results.Created($"/api/v1/lobbies/{lobby.RoomCode}", lobby);
        }).RequireRateLimiting("create").WithName("CreateLobby");
        api.MapGet("/lobbies/{roomCode}", async (string roomCode, LobbyManager manager, CancellationToken token)
            => Results.Ok(await manager.GetAsync(roomCode, token))).WithName("GetLobby");
        api.MapPost("/lobbies/{roomCode}/join", async (string roomCode, JoinLobbyRequest request, LobbyManager manager, CancellationToken token)
            => Results.Ok(await manager.JoinAsync(roomCode, request, token))).RequireRateLimiting("join").WithName("JoinLobby");
        api.MapPost("/lobbies/{roomCode}/resume", async (string roomCode, ResumeLobbyRequest request, LobbyManager manager, HttpResponse response, CancellationToken token) =>
        {
            response.Headers.CacheControl = "no-store";
            return Results.Ok(await manager.ResumeAsync(roomCode, request, token));
        }).RequireRateLimiting("join").WithName("ResumeLobby");
        api.MapGet("/game/version", (IOptions<MatchOptions> options)
            => Results.Ok(new { gameVersion = options.Value.GameVersion, protocolVersion = options.Value.ProtocolVersion, maxConcurrentGameServers = options.Value.MaxConcurrentGameServers, defaultMaxPlayers = options.Value.DefaultMaxPlayers,
                gameModes = new[] { "Sandbox", "SocialDeduction" }, socialMinPlayers = options.Value.SocialDeduction.MinPlayers, socialMaxPlayers = options.Value.SocialDeduction.MaxPlayers })).WithName("GameVersion");
        app.MapGet("/health", async (ILobbyRepository repository, IOptions<MatchOptions> options, CancellationToken token)
            => options.Value.IsValid() && await repository.CheckHealthAsync(token)
                ? Results.Ok(new { status = "healthy" }) : Results.Json(new { status = "unhealthy" }, statusCode: 503));
        var servers = app.MapGroup("/internal/game-servers").ExcludeFromDescription();
        servers.MapPost("/{lobbyId:guid}/ready", async (Guid lobbyId, HttpRequest request, LobbyManager manager, CancellationToken token) =>
        {
            await manager.ReadyAsync(lobbyId, request.Headers["X-Actor-Match-Secret"], token);
            return Results.NoContent();
        });
        servers.MapPost("/{lobbyId:guid}/heartbeat", async (Guid lobbyId, GameServerHeartbeat heartbeat, HttpRequest request, LobbyManager manager, CancellationToken token) =>
        {
            await manager.HeartbeatAsync(lobbyId, request.Headers["X-Actor-Match-Secret"], heartbeat, token);
            return Results.NoContent();
        });
        servers.MapPost("/{lobbyId:guid}/finished", async (Guid lobbyId, HttpRequest request, LobbyManager manager, CancellationToken token) =>
        {
            await manager.FinishedAsync(lobbyId, request.Headers["X-Actor-Match-Secret"], token);
            return Results.NoContent();
        });
    }
}
