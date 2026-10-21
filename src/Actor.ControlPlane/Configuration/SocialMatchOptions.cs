namespace Actor.ControlPlane.Configuration;

// Deployment configuration, copied once when allocating a room. It contains
// rules only: no seed, assignment, position, or client-controlled game truth.
public sealed record SocialMatchOptions
{
    public int MinPlayers { get; init; } = 4;
    public int MaxPlayers { get; init; } = 6;
    public int ImpostorCount { get; init; } = 1;
    public long MatchDurationMs { get; init; } = 900000;
    public long DiscussionDurationMs { get; init; } = 90000;
    public long VotingDurationMs { get; init; } = 60000;
    public long KillCooldownMs { get; init; } = 30000;
    public long EmergencyCooldownMs { get; init; } = 90000;
    public long SabotageTimeoutMs { get; init; } = 150000;
    public long WorkDurationMs { get; init; } = 4000;
    public long SabotageCooldownMs { get; init; } = 90000;
    public long WaitingTimeoutMs { get; init; } = 300000;
    public double KillDistance { get; init; } = .9;
    public double VisibilityRadius { get; init; } = 3.5;
    public double BodyReportDistance { get; init; } = 1.2;
    public double InteractionRadius { get; init; } = 1.25;
    public int TaskThreshold { get; init; } = 4;
    public int MeetingLimit { get; init; } = 3;
    public int PlayerMeetingLimit { get; init; } = 1;

    public bool IsValid() => MinPlayers is >= 4 and <= 6 && MaxPlayers >= MinPlayers && MaxPlayers <= 6
        && ImpostorCount == 1 && MatchDurationMs is >= 60000 and <= 3600000
        && DiscussionDurationMs is >= 10000 and <= 300000 && VotingDurationMs is >= 10000 and <= 180000
        && KillCooldownMs is >= 5000 and <= 300000 && EmergencyCooldownMs is >= 10000 and <= 600000
        && SabotageTimeoutMs is >= 30000 and <= 600000 && WorkDurationMs is >= 1000 and <= 30000
        && SabotageCooldownMs is >= 10000 and <= 600000 && WaitingTimeoutMs is >= 30000 and <= 900000
        && double.IsFinite(KillDistance) && KillDistance is >= .4 and <= 2
        && double.IsFinite(VisibilityRadius) && VisibilityRadius is >= 2 and <= 8
        && double.IsFinite(BodyReportDistance) && BodyReportDistance is >= .5 and <= 2.5
        && double.IsFinite(InteractionRadius) && InteractionRadius is >= .5 and <= 2
        && TaskThreshold is >= 1 and <= 5 && MeetingLimit is >= 1 and <= 8
        && PlayerMeetingLimit is >= 1 and <= 3 && PlayerMeetingLimit <= MeetingLimit;
}
