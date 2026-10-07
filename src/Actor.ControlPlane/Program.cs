using System.Net;
using System.Threading.RateLimiting;
using Actor.ControlPlane.Api;
using Actor.ControlPlane.Application;
using Actor.ControlPlane.Configuration;
using Actor.ControlPlane.Infrastructure;
using Microsoft.AspNetCore.HttpOverrides;

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.ConfigureKestrel(server => server.Limits.MaxRequestBodySize = 16 * 1024);
builder.Services.AddOptions<MatchOptions>().BindConfiguration("Match")
    .Validate(settings => settings.IsValid(), "Invalid Match configuration.").ValidateOnStart();
builder.Services.AddSingleton(TimeProvider.System);
builder.Services.AddSingleton<ILobbyRepository, SqliteLobbyRepository>();
builder.Services.AddSingleton<IGameServerAllocator, LocalProcessGameServerAllocator>();
builder.Services.AddSingleton<IJoinTicketService, JoinTicketService>();
builder.Services.AddSingleton<PortAllocator>();
builder.Services.AddSingleton<LobbyManager>();
builder.Services.AddHostedService<LobbyLifecycleService>();
builder.Services.AddOpenApi();
builder.Services.Configure<ForwardedHeadersOptions>(options =>
{
    options.ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto;
    options.KnownIPNetworks.Clear();
    options.KnownProxies.Add(IPAddress.Loopback);
    options.KnownProxies.Add(IPAddress.IPv6Loopback);
});
builder.Services.AddRateLimiter(options =>
{
    options.RejectionStatusCode = 429;
    options.OnRejected = async (context, token) =>
        await context.HttpContext.Response.WriteAsJsonAsync(new ErrorEnvelope(new ApiError("rate_limit_exceeded", "Too many requests. Try again shortly.")), token);
    foreach (var policy in new[] { ("create", 12), ("join", 60) })
        options.AddPolicy(policy.Item1, context => RateLimitPartition.GetFixedWindowLimiter(
            context.Connection.RemoteIpAddress?.ToString() ?? "unknown",
            _ => new FixedWindowRateLimiterOptions { PermitLimit = policy.Item2, Window = TimeSpan.FromMinutes(1), QueueLimit = 0 }));
});
builder.Logging.ClearProviders();
builder.Logging.AddJsonConsole();
var app = builder.Build();
app.UseForwardedHeaders();
app.UseRateLimiter();
app.MapOpenApi("/api/openapi/{documentName}.json");
app.MapActorApi();
app.Run();

public partial class Program { }
