using Actor.Contracts;
using Actor.Networking;
using System.Security.Cryptography;
using System.Text.Json;

namespace Actor.Sdk.Tests;

public sealed class ResumeAdmissionRegistryTests
{
    private const long Now = 1800000000000;
    private const string Match = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private static JoinTicketClaims Initial(string? actor = null) => new()
    { actorId = actor ?? Guid.NewGuid().ToString("N"), playerName = "Player A", connectionGeneration = 1 };
    private static JoinTicketClaims Resume(ResumeCredentialClaims credential) => new()
    { actorId = credential.actorId, playerName = credential.playerName, resume = true,
        resumeGeneration = credential.generation, resumeCredentialId = credential.credentialId };
    private static ResumeCredentialClaims Credential(ResumeAdmissionRegistry r, string actor, long now = Now)
        => r.Credential(actor, Match, "0.9.0", "4", now);

    [Fact]
    public void ResumeCodecHasSeparateSignatureDomainAndStrictMatchActorGenerationExpiry()
    {
        byte[] key = RandomNumberGenerator.GetBytes(32); var r = new ResumeAdmissionRegistry(4, 30000); var claims = Initial();
        Assert.True(r.TryAdmit(claims, 1, Now, out _, out _)); var credential = Credential(r, claims.actorId);
        string payload = JsonSerializer.Serialize(credential, new JsonSerializerOptions { IncludeFields = true });
        string bearer = ResumeCredentialCodec.Sign(payload, key);
        Assert.True(ResumeCredentialCodec.TryVerify(bearer, key, out var decoded));
        Assert.True(decoded == payload);
        Assert.False(JoinTicketCodec.TryVerify(bearer, key, out _));
        Assert.False(ResumeCredentialCodec.TryVerify("ar1." + JoinTicketCodec.Sign(payload, key), key, out _));
        Assert.False(ResumeCredentialCodec.TryVerify(bearer, RandomNumberGenerator.GetBytes(32), out _));
        Assert.False(ResumeCredentialCodec.TryVerify(bearer + "x", key, out _));
        Assert.True(ResumeCredentialCodec.ClaimsMatch(credential, Match, "0.9.0", "4", Now / 1000));
        Assert.False(ResumeCredentialCodec.ClaimsMatch(credential, Guid.NewGuid().ToString("N"), "0.9.0", "4", Now / 1000));
        credential.generation = 0;
        Assert.False(ResumeCredentialCodec.ClaimsMatch(credential, Match, "0.9.0", "4", Now / 1000));
        credential.generation = 1; credential.expiresAt = Now / 1000;
        Assert.False(ResumeCredentialCodec.ClaimsMatch(credential, Match, "0.9.0", "4", Now / 1000));
        Assert.DoesNotContain(claims.actorId, credential.ToString());
    }
    [Fact]
    public void ConnectedEndpointCannotBeTakenOverAndNameIsNotIdentity()
    {
        var r = new ResumeAdmissionRegistry(4, 30000); var initial = Initial();
        Assert.True(r.TryAdmit(initial, 1, Now, out var binding, out _)); var credential = Credential(r, initial.actorId);
        Assert.False(r.TryAdmit(Resume(credential), 2, Now + 1000, out _, out _));
        r.Disconnected(1, Now + 1000);
        var forged = Resume(credential); forged.actorId = Guid.NewGuid().ToString("N");
        Assert.False(r.TryAdmit(forged, 2, Now + 2000, out _, out _));
        forged = Resume(credential); forged.playerName = "Another Player";
        Assert.False(r.TryAdmit(forged, 2, Now + 2000, out _, out _));
        Assert.True(r.TryAdmit(Resume(credential), 2, Now + 2000, out var resumed, out _));
        Assert.Same(binding, resumed); Assert.Equal(2ul, resumed.Generation); Assert.Equal(2ul, resumed.ClientId);
        Assert.False(r.TryAdmit(Resume(credential), 3, Now + 2001, out _, out _));
    }
    [Fact]
    public void RotationAcknowledgementFencesOldBearerAndRetriesBeforeAckAreBounded()
    {
        var r = new ResumeAdmissionRegistry(4, 30000); var initial = Initial();
        Assert.True(r.TryAdmit(initial, 1, Now, out _, out _)); var first = Credential(r, initial.actorId);
        r.Disconnected(1, Now + 1000); Assert.True(r.TryAdmit(Resume(first), 2, Now + 2000, out _, out _));
        // Network failed before delivery/ack of the rotated credential.
        r.Disconnected(2, Now + 3000); var retry = Resume(first);
        Assert.True(r.TryAdmit(retry, 3, Now + 4000, out _, out _)); Assert.Equal(3ul, retry.connectionGeneration);
        var third = Credential(r, initial.actorId, Now + 4000);
        Assert.False(r.Acknowledge(initial.actorId, 2, 3));
        Assert.False(r.Acknowledge(initial.actorId, 3, 2));
        Assert.True(r.Acknowledge(initial.actorId, 3, 3)); r.Disconnected(3, Now + 5000);
        Assert.False(r.TryAdmit(Resume(first), 4, Now + 6000, out _, out _));
        Assert.True(r.TryAdmit(Resume(third), 4, Now + 6000, out _, out _));
    }
    [Fact]
    public void GraceExpirationAndExplicitForfeitNeverReauthorizeOrReserveCapacity()
    {
        var r = new ResumeAdmissionRegistry(2, 30000); var initial = Initial();
        Assert.True(r.TryAdmit(initial, 1, Now, out _, out _)); var credential = Credential(r, initial.actorId);
        r.Disconnected(1, Now + 1000);
        Assert.False(r.TryAdmit(Resume(credential), 2, Now + 31000, out _, out _));
        var second = Initial(); Assert.True(r.TryAdmit(second, 2, Now + 32000, out _, out _));
        var other = Credential(r, second.actorId, Now + 32000); r.Revoke(second.actorId); r.Disconnected(2, Now + 33000);
        Assert.False(r.TryAdmit(Resume(other), 3, Now + 34000, out _, out _));
        Assert.True(r.TryAdmit(Initial(), 3, Now + 34000, out _, out _));
        Assert.Equal(0, r.Find(second.actorId).GraceUntil);
    }
    [Fact]
    public void RenewedPacketsKeepSameGenerationAndDoNotInvalidatePreviouslyDeliveredCredential()
    {
        var r = new ResumeAdmissionRegistry(2, 30000); var claims = Initial();
        Assert.True(r.TryAdmit(claims, 1, Now, out _, out _)); var first = Credential(r, claims.actorId);
        var renewed = Credential(r, claims.actorId, Now + 20000);
        Assert.True(first.credentialId == renewed.credentialId); Assert.Equal(first.generation, renewed.generation);
        r.Disconnected(1, Now + 21000);
        Assert.True(r.TryAdmit(Resume(first), 2, Now + 22000, out _, out _));
    }
    [Fact]
    public void DetachedRegistryIsBoundedAndGraceReservationsCannotBeStolenByFreshNames()
    {
        var r = new ResumeAdmissionRegistry(2, 30000);
        Assert.True(r.TryAdmit(Initial(), 1, Now, out _, out _)); Assert.True(r.TryAdmit(Initial(), 2, Now, out _, out _));
        r.Disconnected(1, Now + 1); Assert.False(r.TryAdmit(Initial(), 3, Now + 2, out _, out _));
        Assert.True(r.TryAdmit(Initial(), 3, Now + 30001, out var replacement, out _)); Assert.Equal(0, replacement.Slot);
        r.Disconnected(3, Now + 30002, true);
        for (int i = 0; i < 29; i++)
        { Assert.True(r.TryAdmit(Initial(), (ulong)(i + 4), Now + 30003, out _, out _)); r.Disconnected((ulong)(i + 4), Now + 30003, true); }
        Assert.Equal(32, r.Bindings.Count()); Assert.False(r.TryAdmit(Initial(), 100, Now + 30004, out _, out _));
    }
}
