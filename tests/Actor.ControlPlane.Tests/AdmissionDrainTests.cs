using Actor.ControlPlane.Application;
using Actor.ControlPlane.Domain;
using Actor.ControlPlane.Infrastructure;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;

namespace Actor.ControlPlane.Tests;

public sealed class AdmissionDrainTests
{
    [Fact]
    public async Task DrainRejectsCreateJoinAndResumeWithoutReservingAnythingButKeepsReadOnlyHealthViews()
    {
        await using var f = new LobbyFixture(); f.Settings.AdmissionDrainFile = Path.Combine(f.Folder,"admission-drain");
        await f.InitializeAsync(); var lobby=await f.CreateAsync(); await f.ReadyAsync(lobby);
        await File.WriteAllTextAsync(f.Settings.AdmissionDrainFile,"owned-test-marker");
        Assert.Equal("admission_draining",(await Assert.ThrowsAsync<LobbyException>(f.CreateAsync)).Code);
        Assert.Equal("admission_draining",(await Assert.ThrowsAsync<LobbyException>(()=>f.Manager.JoinAsync(lobby.RoomCode,
            new JoinLobbyRequest("Player A",f.Settings.GameVersion,f.Settings.ProtocolVersion),default))).Code);
        Assert.Equal("admission_draining",(await Assert.ThrowsAsync<LobbyException>(()=>f.Manager.ResumeAsync(lobby.RoomCode,
            new ResumeLobbyRequest("invalid",f.Settings.GameVersion,f.Settings.ProtocolVersion),default))).Code);
        Assert.Single(await f.Manager.ListAsync(default)); Assert.Equal(lobby.RoomCode,(await f.Manager.GetAsync(lobby.RoomCode,default)).RoomCode);
        Assert.Single(f.Allocator.Running); File.Delete(f.Settings.AdmissionDrainFile);
        for(int i=0;i<8;i++) Assert.NotNull(await f.Manager.JoinAsync(lobby.RoomCode,
            new JoinLobbyRequest("Player "+i,f.Settings.GameVersion,f.Settings.ProtocolVersion),default));
    }

    private sealed class HeldAllocator : IGameServerAllocator
    {
        internal readonly TaskCompletionSource Entered = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal readonly TaskCompletionSource Release = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal readonly HashSet<Guid> Running=new();
        public async Task<GameServerAllocation> AllocateAsync(Lobby lobby,byte[] secret,CancellationToken token)
        { Entered.TrySetResult();await Release.Task.WaitAsync(token);Running.Add(lobby.LobbyId);return new(1234,DateTimeOffset.UtcNow); }
        public Task ReleaseAsync(Guid id,CancellationToken token){Running.Remove(id);return Task.CompletedTask;}
        public bool IsRunning(Guid id)=>Running.Contains(id);
        public Task ReapPersistedAsync(Lobby lobby,CancellationToken token)=>Task.CompletedTask;
    }
    [Fact]
    public async Task FinalZeroRoomCheckWaitsForEarlierAllocationAndQueuedMutationCannotSlipBehindIt()
    {
        await using var f=new LobbyFixture(); f.Settings.AdmissionDrainFile=Path.Combine(f.Folder,"drain");
        var allocator=new HeldAllocator(); var options=Options.Create(f.Settings);
        var manager=new LobbyManager(f.Repository,allocator,new PortAllocator(options),f.Tickets,options,f.Clock,NullLogger<LobbyManager>.Instance);
        await manager.InitializeAsync(default);
        Task<LobbySummary> Create()=>manager.CreateAsync(new("Race Room",4,f.Settings.GameVersion,f.Settings.ProtocolVersion),default);
        var started=Create();await allocator.Entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        await File.WriteAllTextAsync(f.Settings.AdmissionDrainFile,"drained");
        var finalCheck=manager.ListAsync(default);var queued=Create();
        Assert.False(finalCheck.IsCompleted);Assert.False(queued.IsCompleted);
        allocator.Release.TrySetResult();
        await started;Assert.Single(await finalCheck);
        Assert.Equal("admission_draining",(await Assert.ThrowsAsync<LobbyException>(()=>queued)).Code);
        Assert.Single(allocator.Running);await manager.ShutdownAsync(default);
    }
}
