using Actor.Presentation;

namespace Actor.Sdk.Tests;

public sealed class StationAtmosphereTests
{
    [Fact]
    public void EmergencyRequiresTheSameNonemptyPublicMatchAsTheCurrentEndpoint()
    {
        Assert.True(StationAtmosphereRules.SamePublicMatch("match-a","match-a"));
        Assert.False(StationAtmosphereRules.SamePublicMatch("match-b","match-a"));
        Assert.False(StationAtmosphereRules.SamePublicMatch("",""));
        Assert.False(StationAtmosphereRules.SamePublicMatch("match-a",null!));
    }

    [Fact]
    public void RematchReconnectCameraEpochAndAudienceChangesResetTheCosmeticEnvelope()
    {
        var prior=new StationAtmosphereScope("match-a",1,7,true,"session-a","observer-a");
        Assert.True(prior.Matches(new StationAtmosphereScope("match-a",1,7,true,"session-a","observer-a")));
        Assert.False(prior.Matches(new StationAtmosphereScope("match-b",1,7,true,"session-a","observer-a")));
        Assert.False(prior.Matches(new StationAtmosphereScope("match-a",2,7,true,"session-a","observer-a")));
        Assert.False(prior.Matches(new StationAtmosphereScope("match-a",1,8,true,"session-a","observer-a")));
        Assert.False(prior.Matches(new StationAtmosphereScope("match-a",1,7,false)));
        Assert.False(prior.Matches(default));
        Assert.False(default(StationAtmosphereScope).Matches(default));
        Assert.False(new StationAtmosphereScope("match-a",0,7,true,"session-a","observer-a").Valid);
        Assert.False(new StationAtmosphereScope("match-a",1,0,true,"session-a","observer-a").Valid);
        Assert.False(prior.Matches(new StationAtmosphereScope("match-a",1,7,true,"session-b","observer-a")));
        Assert.False(prior.Matches(new StationAtmosphereScope("match-a",1,7,true,"session-a","observer-b")));
        Assert.False(new StationAtmosphereScope("match-a",1,7,true).Valid);
        Assert.True(new StationAtmosphereScope("match-a",1,7,false).Valid);
    }

    [Fact]
    public void NewScopeUsesItsCurrentSignalImmediatelyWithoutOldEmergencyOrBrightCameraFlash()
    {
        Assert.Equal(0,StationAtmosphereRules.EmergencyEnvelope(1,true,false,.016f,true));
        Assert.Equal(1,StationAtmosphereRules.EmergencyEnvelope(0,true,true,.016f,true));
        Assert.Equal(0,StationAtmosphereRules.EmergencyEnvelope(1,false,true,.016f,true));
    }

    [Fact]
    public void FeatherIsBoundedInScreenPixelsAndNeverUsesInvalidCameraDimensions()
    {
        float width=StationAtmosphereRules.FeatherWorldWidth(12,3.4f,720);
        Assert.InRange(width,.1133f,.1134f);
        Assert.Equal(width/2,StationAtmosphereRules.FeatherWorldWidth(12,3.4f,1440));
        Assert.Equal(StationAtmosphereRules.FeatherWorldWidth(16,3.4f,720),
            StationAtmosphereRules.FeatherWorldWidth(1000,3.4f,720));
        Assert.Equal(0,StationAtmosphereRules.FeatherWorldWidth(float.NaN,3.4f,720));
        Assert.Equal(0,StationAtmosphereRules.FeatherWorldWidth(12,float.PositiveInfinity,720));
        Assert.Equal(0,StationAtmosphereRules.FeatherWorldWidth(12,3.4f,0));
    }

    [Fact]
    public void LostPermissionImmediatelyClearsEmergencyInsteadOfRetainingAnOldPublicFrame()
    {
        Assert.Equal(0,StationAtmosphereRules.EmergencyEnvelope(1,false,true,1f/60));
        Assert.Equal(0,StationAtmosphereRules.EmergencyEnvelope(.5f,false,false,0));
        Assert.Equal(0,StationAtmosphereRules.EmergencyEnvelope(float.NaN,false,true,float.NaN));
    }

    [Fact]
    public void OrdinaryEmergencyAndRestorationAreSmoothBoundedAndEventuallySettle()
    {
        float current=0;
        float first=StationAtmosphereRules.EmergencyEnvelope(current,true,true,1f/60);
        Assert.InRange(first,.01f,.1f);
        for(int i=0;i<200;i++)
        {
            float next=StationAtmosphereRules.EmergencyEnvelope(current,true,true,1f/60);
            Assert.InRange(next,current,1);current=next;
        }
        Assert.Equal(1,current);
        for(int i=0;i<200;i++)
        {
            float next=StationAtmosphereRules.EmergencyEnvelope(current,true,false,1f/60);
            Assert.InRange(next,0,current);current=next;
        }
        Assert.Equal(0,current);
    }

    [Fact]
    public void PauseInvalidTimingAndCorruptEnvelopeCannotProduceFlickeringNonFiniteUniforms()
    {
        Assert.Equal(1,StationAtmosphereRules.EmergencyEnvelope(float.NaN,true,true,.016f));
        Assert.Equal(0,StationAtmosphereRules.EmergencyEnvelope(1,true,false,float.NaN));
        Assert.Equal(1,StationAtmosphereRules.EmergencyEnvelope(0,true,true,3));
        Assert.Equal(0,StationAtmosphereRules.EmergencyEnvelope(1,true,false,-1));
        Assert.Equal(.5f,StationAtmosphereRules.EmergencyEnvelope(.5f,true,true,0));
        Assert.InRange(StationAtmosphereRules.EmergencyEnvelope(100,true,false,.016f),0,1);
    }
}
