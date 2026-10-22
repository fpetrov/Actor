using Actor.Presentation;

namespace Actor.Sdk.Tests;

public sealed class PresentationLedgerTests
{
    private const long Now=1790000000000;
    private static string Id()=>Guid.NewGuid().ToString("N");
    private static bool Accept(PresentationLedger ledger,string? id=null,string expected="match",string match="match",string self="owner",string recipient="owner",float x=0,float y=0,long at=Now)
        =>ledger.Accept(expected,self,match,recipient,id??Id(),Id(),"victim",x,y,at,Now);
    [Fact] public void SamePermittedEventPlaysOnlyOnceAcrossRepeatedDelivery()
    {var ledger=new PresentationLedger();string id=Id();Assert.True(Accept(ledger,id));Assert.False(Accept(ledger,id));Assert.Equal(1,ledger.Count);}
    [Theory]
    [InlineData("match","foreign","owner","owner")]
    [InlineData("match","match","owner","third-party")]
    [InlineData("match","match","","owner")]
    [InlineData("","","owner","owner")]
    public void WrongMatchOrRecipientDoesNotEnterLedger(string expected,string match,string self,string recipient)
    {var ledger=new PresentationLedger();Assert.False(Accept(ledger,expected:expected,match:match,self:self,recipient:recipient));Assert.Equal(0,ledger.Count);}
    [Fact] public void StaleReplayAndFutureClockClaimsCannotStartEffects()
    {var ledger=new PresentationLedger();Assert.False(Accept(ledger,at:Now-3001));Assert.False(Accept(ledger,at:Now+1001));Assert.False(Accept(ledger,at:0));Assert.True(Accept(ledger,at:Now-3000));}
    [Theory]
    [InlineData(float.NaN,0)] [InlineData(0,float.PositiveInfinity)] [InlineData(float.NegativeInfinity,0)]
    public void NonfiniteWorldPositionsNeverStartPresentation(float x,float y)
    {Assert.False(Accept(new PresentationLedger(),x:x,y:y));}
    [Fact] public void InvalidStableIdsAreNotStored()
    {
        var ledger=new PresentationLedger();Assert.False(Accept(ledger,"not-an-id"));
        Assert.False(ledger.Accept("match","owner","match","owner",Id(),"bad-body","victim",0,0,Now,Now));
        Assert.Equal(0,ledger.Count);
    }
    [Fact] public void MatchChangeClearsPreviousBoundedHistoryButCannotAcceptOldMatch()
    {var ledger=new PresentationLedger();string id=Id();Assert.True(Accept(ledger,id));Assert.True(Accept(ledger,id,expected:"new",match:"new"));Assert.False(Accept(ledger,expected:"new",match:"match"));Assert.Equal(1,ledger.Count);}
    [Fact] public void EventHistoryIsBoundedEvenForManyPermittedEvents()
    {var ledger=new PresentationLedger();for(int i=0;i<300;++i)Assert.True(Accept(ledger));Assert.Equal(256,ledger.Count);}
}
