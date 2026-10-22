using Actor.Presentation;

namespace Actor.Sdk.Tests;

public sealed class FogRefreshTests
{
    [Fact]
    public void MovingHumanSamplesEveryRenderedEyeRatherThanHoldingThirtyHertzHole()
    {
        double candidateDeadline = 0, legacyDeadline = 0;
        int candidate = 0, legacy = 0;
        double candidateEye = -1, legacyEye = -1, maxCandidateLag = 0, maxLegacyLag = 0;
        for (int frame = 0; frame < 120; frame++)
        {
            double now = frame / 60.0, eye = now * 2.6;
            if (FogRefreshRules.Refresh(false, false, candidate > 0, candidateEye != eye, false, now, candidateDeadline))
            { candidate++; candidateEye = eye; candidateDeadline = FogRefreshRules.NextIdleDeadline(now, candidateDeadline); }
            if (FogRefreshRules.Refresh(false, true, legacy > 0, legacyEye != eye, false, now, legacyDeadline))
            { legacy++; legacyEye = eye; legacyDeadline = now + FogRefreshRules.IdleInterval; }
            maxCandidateLag = Math.Max(maxCandidateLag, eye - candidateEye);
            maxLegacyLag = Math.Max(maxLegacyLag, eye - legacyEye);
        }
        Assert.Equal(120, candidate);
        Assert.InRange(legacy, 30, 61);
        Assert.Equal(0, maxCandidateLag);
        Assert.True(maxLegacyLag > .04);
    }

    [Fact]
    public void CornerClosureRadiusOrEpochChangeCannotWaitForIdleDeadline()
    {
        const double now = .016, future = .033;
        Assert.True(FogRefreshRules.Refresh(false, false, true, true, false, now, future));
        Assert.Equal(.1f, TemporalFogRules.Step(.1f, 3.5f, (float)now, false));
        Assert.Equal(0, TemporalFogRules.Step(0, 3.5f, (float)now, false));
        Assert.True(FogRefreshRules.Refresh(false, false, false, false, false, now, future));
        Assert.Equal(.5f, TemporalFogRules.Step(.5f, 3.5f, (float)now, true));
    }

    [Fact]
    public void EveryOutwardOpeningStepRequiresAnotherCurrentSightSample()
    {
        float displayed = .2f;
        for (int frame = 1; frame <= 120; frame++)
        {
            double time = frame / 60.0;
            // A new nearby corner closes the permitted ray while it was opening.
            float allowed = frame == 17 ? .05f : 3;
            Assert.True(FogRefreshRules.Refresh(false, false, true, false, true, time, time + .03));
            displayed = TemporalFogRules.Step(allowed, displayed, 1f / 60, false);
            Assert.InRange(displayed, 0, allowed);
            if (frame == 17) Assert.Equal(allowed, displayed);
        }
    }

    [Fact]
    public void StationaryOpeningConvergesWithoutLeavingSixtyHertzQueriesRunningForever()
    {
        float displayed = 3;
        bool opening = true;
        double deadline = 0;
        int firstSecond = 0, settledSecond = 0;
        for (int frame = 0; frame < 120; frame++)
        {
            double now = frame / 60.0;
            if (!FogRefreshRules.Refresh(false, false, true, false, opening, now, deadline)) continue;
            if (frame < 60) firstSecond++; else settledSecond++;
            deadline = FogRefreshRules.NextIdleDeadline(now, deadline);
            displayed = FogRefreshRules.FinishOpening(3.5f, TemporalFogRules.Step(3.5f, displayed, 1f / 60, false));
            Assert.InRange(displayed, 0, 3.5f);
            opening = displayed < 3.5f;
        }
        Assert.False(opening);
        Assert.Equal(3.5f, displayed);
        Assert.True(firstSecond > 30);
        Assert.InRange(settledSecond, 29, 31);
        Assert.Equal(.2f, FogRefreshRules.FinishOpening(.2f, 3.5f));
        Assert.Equal(0, FogRefreshRules.FinishOpening(float.NaN, 3.5f));
    }

    [Fact]
    public void IdleDeadlineDoesNotAccumulateAnExtraRenderFrameOnEveryJitteredTick()
    {
        double deadline = 0, legacyDeadline = 0;
        int updates = 0, legacy = 0;
        for (int frame = 0; frame < 625; frame++)
        {
            double now = frame * .016;
            if (FogRefreshRules.Refresh(false, false, true, false, false, now, deadline))
            { updates++; deadline = FogRefreshRules.NextIdleDeadline(now, deadline); }
            if (FogRefreshRules.Refresh(false, true, true, false, false, now, legacyDeadline))
            { legacy++; legacyDeadline = now + FogRefreshRules.IdleInterval; }
        }
        Assert.InRange(updates, 299, 301);
        Assert.InRange(legacy, 208, 210);
        Assert.False(FogRefreshRules.Refresh(false, false, true, false, false, .01, .02));
    }

    [Fact]
    public void BroadcastAndExplicitMeasuredBaselineKeepScheduledRefreshOnly()
    {
        Assert.False(FogRefreshRules.Refresh(true, false, false, true, true, .01, .02));
        Assert.False(FogRefreshRules.Refresh(false, true, false, true, true, .01, .02));
        Assert.True(FogRefreshRules.Refresh(true, false, true, false, false, .02, .02));
    }

    [Fact]
    public void PauseDoesNotCreateCatchupQueriesOrDelayTheNextIdleDeadline()
    {
        double next = FogRefreshRules.NextIdleDeadline(10, .01);
        Assert.InRange(next, 10.03, 10.034);
        Assert.False(FogRefreshRules.Refresh(false, false, true, false, false, 10.001, next));
        Assert.Equal(.3, FogRefreshRules.NextIdleDeadline(.1, .3));
        Assert.Throws<ArgumentOutOfRangeException>(() => FogRefreshRules.NextIdleDeadline(double.NaN, 0));
    }

    [Fact]
    public void DiagnosticsHaveBoundedRecentPercentilesButRetainActualWholeRunTotals()
    {
        var counters = new FogPerformanceCounters();
        counters.Record(0, 50, 40, 10, 193, true, true, 7);
        for (int i = 1; i <= FogPerformanceCounters.Capacity; i++)
            counters.Record(i / 60.0, .2, .1, .1, 193, true, true, 0);
        var report = counters.Snapshot(false);
        Assert.Equal(FogPerformanceCounters.Capacity + 1, report.active_frames);
        Assert.Equal(report.active_frames * 193, report.actual_sight_queries);
        Assert.Equal(FogPerformanceCounters.Capacity, report.fog_cpu_ms.count);
        Assert.Equal(.2, report.fog_cpu_ms.p99);
        Assert.Equal(.2, report.fog_cpu_ms.max);
        Assert.Equal(50, report.max_fog_cpu_ms_all_frames);
        Assert.Equal(0, report.eye_lag_pixels.max);
        Assert.Equal("per-render-moving", report.timing);
        Assert.Contains("recent 4096", report.scope);
    }

    [Fact]
    public void InvalidDiagnosticSamplesDoNotCreateFakeQueriesRatesOrTimings()
    {
        var counters = new FogPerformanceCounters();
        counters.Record(double.NaN, .1, .1, 0, 193, true, false, 0);
        counters.Record(1, -1, .1, 0, 193, true, false, 0);
        counters.Record(1, .1, .1, 0, 194, true, false, 0);
        Assert.Equal(0, counters.Snapshot(false).active_frames);
        counters.Record(1, .1, .1, 0, 193, true, false, 0);
        counters.Record(.5, .1, .1, 0, 193, true, false, 0);
        Assert.Equal(1, counters.Snapshot(true).active_frames);
        Assert.Equal(0, counters.Snapshot(true).duration_seconds);
        Assert.Equal("legacy30", counters.Snapshot(true).timing);
    }
}
