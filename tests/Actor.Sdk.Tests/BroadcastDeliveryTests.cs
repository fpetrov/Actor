using Actor.Networking;

namespace Actor.Sdk.Tests;

// Pure transport/timer cases. These are not Unity EOF, network, GPU or video
// acceptance, and do not mark any source fact as actually presented.
public sealed class BroadcastDeliveryTests
{
    [Fact]
    public void EightRealOutstandingFramesBoundCapacityAndOnlyExactReceiptCanRetireCumulatively()
    {
        var window = new BroadcastDeliveryWindow();
        for (ulong sequence = 1; sequence <= 8; sequence++)
            Assert.True(window.RecordSent(sequence, sequence / 100.0));
        Assert.Equal(8, window.Count); Assert.Equal(8, window.PeakCount); Assert.False(window.CanSend);
        Assert.False(window.RecordSent(9, .09));
        Assert.False(window.TryAcknowledge(9, .3, out int retired, out double rtt));
        Assert.Equal(0, retired); Assert.True(double.IsNaN(rtt)); Assert.Equal(8, window.Count);
        Assert.True(window.TryAcknowledge(7, .3, out retired, out rtt));
        Assert.Equal(7, retired); Assert.InRange(rtt, .229999, .230001);
        Assert.Equal(1, window.Count); Assert.True(window.CanSend);
        Assert.True(window.RecordSent(9, .31));
        Assert.Equal(2, window.Count); Assert.Equal(9UL, window.LastSentSequence);
    }

    [Fact]
    public void HolesDuplicatesFutureAndOutOfOrderReceiptsDoNotReleaseUnrelatedFrames()
    {
        var window = new BroadcastDeliveryWindow();
        Assert.True(window.RecordSent(1, 0)); Assert.True(window.RecordSent(3, .01));
        Assert.True(window.RecordSent(8, .02));
        foreach (ulong unknown in new ulong[] { 0, 2, 4, 7, 9, ulong.MaxValue })
        {
            Assert.False(window.TryAcknowledge(unknown, .2, out int retired, out double rtt));
            Assert.Equal(0, retired); Assert.True(double.IsNaN(rtt)); Assert.Equal(3, window.Count);
        }
        Assert.True(window.TryAcknowledge(3, .2, out int acknowledged, out _));
        Assert.Equal(2, acknowledged); Assert.Equal(1, window.Count);
        foreach (ulong stale in new ulong[] { 1, 2, 3 })
            Assert.False(window.TryAcknowledge(stale, .3, out _, out _));
        Assert.Equal(1, window.Count);
        Assert.True(window.TryAcknowledge(8, .3, out acknowledged, out _));
        Assert.Equal(1, acknowledged); Assert.Equal(0, window.Count);
    }

    [Fact]
    public void PresentationExpiryAndLongSilenceNeverReleaseReliableTransportOwnership()
    {
        var window = new BroadcastDeliveryWindow();
        for (ulong sequence = 1; sequence <= 8; sequence++) Assert.True(window.RecordSent(sequence, 0));
        Assert.Equal(30.0, window.OldestPendingAge(30));
        Assert.False(window.CanSend); Assert.Equal(8, window.Count);
        Assert.False(window.RecordSent(9, 30));
        Assert.True(window.TryAcknowledge(1, 30, out int retired, out double rtt));
        Assert.Equal(1, retired); Assert.Equal(30, rtt); Assert.Equal(7, window.Count);
    }

    [Fact]
    public void InvalidOrRegressiveClocksAndUnsentSequencesLeaveWindowUnchanged()
    {
        var window = new BroadcastDeliveryWindow();
        Assert.True(window.RecordSent(5, 10));
        foreach (double invalid in new[] { -1.0, double.NaN, double.NegativeInfinity, double.PositiveInfinity, 9.9 })
        {
            Assert.False(window.RecordSent(6, invalid));
            Assert.False(window.TryAcknowledge(5, invalid, out _, out double rtt));
            Assert.True(double.IsNaN(rtt)); Assert.Null(window.OldestPendingAge(invalid));
            Assert.Equal(1, window.Count); Assert.Equal(5UL, window.LastSentSequence);
        }
        // An unknown receipt must not move the clock gate or poison real RTT.
        Assert.False(window.TryAcknowledge(500, 1000, out _, out _));
        Assert.True(window.TryAcknowledge(5, 10.2, out _, out double actual));
        Assert.InRange(actual, .199999, .200001); Assert.Null(window.OldestPendingAge(10.2));
    }

    [Fact]
    public void SequenceReuseIsRejectedAfterReceiptAndOnlyNewTransportGetsNewSequenceSpace()
    {
        var old = new BroadcastDeliveryWindow();
        Assert.False(old.RecordSent(0, 0)); Assert.True(old.RecordSent(20, 0));
        Assert.True(old.TryAcknowledge(20, .2, out _, out _));
        Assert.False(old.RecordSent(20, .21)); Assert.False(old.RecordSent(19, .21));
        Assert.True(old.RecordSent(22, .21)); Assert.Equal(1, old.Count);
        var newTransport = new BroadcastDeliveryWindow();
        Assert.True(newTransport.RecordSent(1, .3)); Assert.Equal(1, newTransport.Count);
        Assert.Equal(1, old.Count); // A distinct observer/transport cannot retire the old one.
    }

    [Fact]
    public void UnsignedSequenceWrapCannotMasqueradeAsNewTraffic()
    {
        var window = new BroadcastDeliveryWindow();
        Assert.True(window.RecordSent(ulong.MaxValue, 0));
        Assert.True(window.TryAcknowledge(ulong.MaxValue, .1, out _, out _));
        Assert.False(window.RecordSent(0, .2)); Assert.False(window.RecordSent(1, .2));
    }

    [Fact]
    public void ObserverTickStillRunsWhenAuthorityTimerIsNotDueAndRepeatedCallCannotDuplicateTick()
    {
        var cadence = new BroadcastDeliveryCadence();
        var first = cadence.Take(0); Assert.True(first.Authority); Assert.True(first.Observer);
        var repeated = cadence.Take(0); Assert.False(repeated.Authority); Assert.False(repeated.Observer);
        var observerOnly = cadence.Take(.034);
        Assert.False(observerOnly.Authority); Assert.True(observerOnly.Observer);
        Assert.Equal(.1, cadence.NextAuthorityAt);
        Assert.InRange(cadence.NextObserverAt, .06666, .06667);
        var eventSameFrame = cadence.Take(.034);
        Assert.False(eventSameFrame.Authority); Assert.False(eventSameFrame.Observer);
    }

    [Theory]
    [InlineData(30)]
    [InlineData(60)]
    [InlineData(120)]
    public void IndependentCadencesStayNearTenAndThirtyHertzWithoutAccumulatingFrameDelay(int framesPerSecond)
    {
        var cadence = new BroadcastDeliveryCadence();
        int authority = 0, observer = 0;
        for (int frame = 0; frame < framesPerSecond * 10; frame++)
        {
            var ticks = cadence.Take(frame / (double)framesPerSecond);
            if (ticks.Authority) authority++;
            if (ticks.Observer) observer++;
        }
        Assert.InRange(authority, 99, 101); Assert.InRange(observer, 299, 301);
    }

    [Fact]
    public void LongStallCoalescesMissedSlotsIntoOneCurrentTickAndDoesNotEmitCatchupBurst()
    {
        var cadence = new BroadcastDeliveryCadence(); cadence.Take(5);
        var recovered = cadence.Take(20.011);
        Assert.True(recovered.Authority); Assert.True(recovered.Observer);
        Assert.True(cadence.NextAuthorityAt > 20.011); Assert.True(cadence.NextObserverAt > 20.011);
        Assert.InRange(cadence.NextAuthorityAt - 20.011, 0, BroadcastDeliveryCadence.AuthorityInterval + 1e-10);
        Assert.InRange(cadence.NextObserverAt - 20.011, 0, BroadcastDeliveryCadence.ObserverInterval + 1e-10);
        for (int i = 0; i < 1000; i++)
        {
            var duplicate = cadence.Take(20.011);
            Assert.False(duplicate.Authority); Assert.False(duplicate.Observer);
        }
    }

    [Fact]
    public void InvalidCadenceClockCannotAdvanceTimersOrCreateTicks()
    {
        var cadence = new BroadcastDeliveryCadence(); cadence.Take(10);
        var before = (cadence.NextAuthorityAt, cadence.NextObserverAt);
        foreach (double invalid in new[] { -1.0, 9.0, double.NaN, double.NegativeInfinity, double.PositiveInfinity, double.MaxValue })
        {
            var ticks = cadence.Take(invalid);
            Assert.False(ticks.Authority); Assert.False(ticks.Observer);
            Assert.Equal(before, (cadence.NextAuthorityAt, cadence.NextObserverAt));
        }
        Assert.True(cadence.Take(10.034).Observer);
    }

    [Fact]
    public void EightFrameWindowSustainsThirtyHertzModelWithMeasuredRttWhileOneFlightIsAboutFive()
    {
        static (int Sent, int Peak) Replay(bool singleFlight)
        {
            var cadence = new BroadcastDeliveryCadence(); var window = new BroadcastDeliveryWindow();
            var receipts = new Queue<(double At, ulong Sequence)>();
            int sent = 0; ulong sequence = 0;
            for (int frame = 0; frame < 600; frame++)
            {
                double now = frame / 60.0;
                while (receipts.Count > 0 && receipts.Peek().At <= now)
                {
                    var receipt = receipts.Dequeue();
                    Assert.True(window.TryAcknowledge(receipt.Sequence, now, out _, out _));
                }
                if (!cadence.Take(now).Observer || !window.CanSend || singleFlight && window.Count != 0) continue;
                Assert.True(window.RecordSent(++sequence, now));
                receipts.Enqueue((now + .194, sequence)); sent++;
                Assert.InRange(window.Count, 1, BroadcastDeliveryWindow.Capacity);
            }
            return (sent, window.PeakCount);
        }
        var current = Replay(false); var oneFlight = Replay(true);
        Assert.InRange(current.Sent, 280, 301); Assert.InRange(current.Peak, 6, 8);
        Assert.InRange(oneFlight.Sent, 40, 51); Assert.Equal(1, oneFlight.Peak);
    }
}
