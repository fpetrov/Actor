using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using Actor.ControlPlane.Configuration;
using Actor.ControlPlane.Domain;
using Microsoft.Extensions.Options;

namespace Actor.ControlPlane.Infrastructure;

public sealed class LocalProcessGameServerAllocator(IOptions<MatchOptions> options, ILogger<LocalProcessGameServerAllocator> logger)
    : IGameServerAllocator
{
    private readonly ConcurrentDictionary<Guid, Process> processes = new();

    public Task<GameServerAllocation> AllocateAsync(Lobby lobby, byte[] secret, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var settings = options.Value;
        Directory.CreateDirectory(settings.GameServerLogDirectory);
        string executable = Path.GetFullPath(settings.UnityServerExecutable);
        var start = new ProcessStartInfo(executable)
        {
            WorkingDirectory = Path.GetDirectoryName(executable)!, UseShellExecute = false,
            RedirectStandardOutput = true, RedirectStandardError = true
        };
        foreach (string value in new[] { "-batchmode", "-nographics", "-logFile",
            Path.GetFullPath(Path.Combine(settings.GameServerLogDirectory, lobby.LobbyId.ToString("N") + ".log")),
            "--match-id", lobby.LobbyId.ToString("N"), "--room-code", lobby.RoomCode,
            "--port", lobby.GamePort.ToString(), "--max-players", lobby.MaxPlayers.ToString(),
            "--game-version", lobby.GameVersion, "--protocol-version", lobby.ProtocolVersion, "--game-mode", lobby.GameMode })
            start.ArgumentList.Add(value);
        start.Environment["ACTOR_MATCH_SECRET"] = Convert.ToBase64String(secret);
        start.Environment["ACTOR_INTERNAL_API_URL"] = settings.InternalApiBaseUrl;
        if (lobby.SocialConfiguration != null)
        {
            start.Environment["ACTOR_SOCIAL_CONFIG"] = JsonSerializer.Serialize(lobby.SocialConfiguration);
            start.Environment["ACTOR_SOCIAL_SUMMARY_DIRECTORY"] = Path.GetFullPath(Path.Combine(settings.GameServerLogDirectory, "social"));
        }
        var process = new Process { StartInfo = start, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, args) => { if (args.Data != null) logger.LogDebug("GameServerOutput {LobbyId} {Line}", lobby.LobbyId, args.Data); };
        process.ErrorDataReceived += (_, args) => { if (args.Data != null) logger.LogDebug("GameServerError {LobbyId} {Line}", lobby.LobbyId, args.Data); };
        process.Exited += (_, _) =>
        {
            try { logger.LogInformation("ProcessExited {LobbyId} {Pid} {ExitCode}", lobby.LobbyId, process.Id, process.ExitCode); }
            catch (InvalidOperationException) { }
        };
        try
        {
            if (!process.Start()) throw new InvalidOperationException("Game server process could not start.");
            processes[lobby.LobbyId] = process;
            process.BeginOutputReadLine(); process.BeginErrorReadLine();
            logger.LogInformation("ServerProcessStarted {LobbyId} {RoomCode} {Pid} {Port}", lobby.LobbyId, lobby.RoomCode, process.Id, lobby.GamePort);
            return Task.FromResult(new GameServerAllocation(process.Id, new DateTimeOffset(process.StartTime.ToUniversalTime())));
        }
        catch
        {
            if (processes.TryRemove(lobby.LobbyId, out var started)) { if (!started.HasExited) started.Kill(true); started.Dispose(); }
            else process.Dispose();
            throw;
        }
    }

    public bool IsRunning(Guid lobbyId) => processes.TryGetValue(lobbyId, out var process) && !process.HasExited;

    public async Task ReleaseAsync(Guid lobbyId, CancellationToken cancellationToken)
    {
        if (!processes.TryGetValue(lobbyId, out var process)) return;
        if (!process.HasExited)
        {
            process.Kill(entireProcessTree: true);
            await process.WaitForExitAsync(cancellationToken).WaitAsync(TimeSpan.FromSeconds(5), cancellationToken);
        }
        processes.TryRemove(lobbyId, out _);
        process.Dispose();
    }

    public async Task ReapPersistedAsync(Lobby lobby, CancellationToken cancellationToken)
    {
        if (!OperatingSystem.IsLinux() || lobby.ServerPid == null || lobby.ProcessStartedAt == null) return;
        try
        {
            using var process = Process.GetProcessById(lobby.ServerPid.Value);
            if (process.HasExited || Math.Abs((process.StartTime.ToUniversalTime() - lobby.ProcessStartedAt.Value.UtcDateTime).TotalSeconds) > 2) return;
            string[] args = (await File.ReadAllTextAsync($"/proc/{process.Id}/cmdline", cancellationToken)).Split('\0');
            string[] environment = (await File.ReadAllTextAsync($"/proc/{process.Id}/environ", cancellationToken)).Split('\0');
            if (!args.Contains(lobby.LobbyId.ToString("N")) || !args.Contains("--match-id")
                || !environment.Any(e => e.StartsWith("ACTOR_MATCH_SECRET=", StringComparison.Ordinal))) return;
            process.Kill(true);
            await process.WaitForExitAsync(cancellationToken).WaitAsync(TimeSpan.FromSeconds(5), cancellationToken);
            logger.LogInformation("ReapedPersistedServer {LobbyId} {Pid}", lobby.LobbyId, lobby.ServerPid);
        }
        catch (ArgumentException) { }
        catch (IOException) { }
    }
}
