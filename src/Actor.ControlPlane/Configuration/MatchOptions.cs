namespace Actor.ControlPlane.Configuration;

public sealed class MatchOptions
{
    public int MaxConcurrentGameServers { get; set; } = 5;
    public int GameServerPortStart { get; set; } = 7777;
    public int GameServerPortEnd { get; set; } = 7781;
    public int DefaultMaxPlayers { get; set; } = 8;
    public string PublicGameHost { get; set; } = "127.0.0.1";
    public string UnityServerExecutable { get; set; } = "";
    public string DatabasePath { get; set; } = "runtime/actor.db";
    public string GameServerLogDirectory { get; set; } = "runtime/matches";
    public string InternalApiBaseUrl { get; set; } = "http://127.0.0.1:5080";
    public string AdmissionDrainFile { get; set; } = "/run/actor-admission-drain";
    // Empty disables operator-only observer admission. Never use a match, Twitch or OBS credential here.
    public string BroadcastOperatorKey { get; set; } = "";
    public int LobbyStartupTimeout { get; set; } = 45;
    public int EmptyLobbyLifetime { get; set; } = 300;
    public int EmptyLobbyGracePeriod { get; set; } = 60;
    public int HeartbeatTimeout { get; set; } = 20;
    public int JoinTicketLifetime { get; set; } = 45;
    public string GameVersion { get; set; } = "0.10.0";
    public string ProtocolVersion { get; set; } = "4";
    public SocialMatchOptions SocialDeduction { get; set; } = new();

    public bool IsValid() => MaxConcurrentGameServers > 0
        && GameServerPortStart >= 1024 && GameServerPortEnd <= 65535
        && GameServerPortEnd - GameServerPortStart + 1 >= MaxConcurrentGameServers
        && DefaultMaxPlayers is >= 2 and <= 16
        && LobbyStartupTimeout > 0 && EmptyLobbyLifetime > 0 && EmptyLobbyGracePeriod > 0
        && HeartbeatTimeout >= 5 && JoinTicketLifetime is > 0 and <= 300
        && !string.IsNullOrWhiteSpace(PublicGameHost) && !string.IsNullOrWhiteSpace(UnityServerExecutable)
        && Uri.TryCreate(InternalApiBaseUrl, UriKind.Absolute, out var uri) && uri.IsLoopback
        && !string.IsNullOrWhiteSpace(AdmissionDrainFile) && Path.IsPathRooted(AdmissionDrainFile)
        && (BroadcastOperatorKey == "" || BroadcastOperatorKey.Length is >= 32 and <= 512
            && BroadcastOperatorKey.All(c => c is >= '!' and <= '~'))
        && !string.IsNullOrWhiteSpace(GameVersion) && !string.IsNullOrWhiteSpace(ProtocolVersion)
        && SocialDeduction != null && SocialDeduction.IsValid();
}
