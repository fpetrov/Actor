using Actor.Presentation;

namespace Actor.Sdk.Tests;

public sealed class NameplateLayoutRulesTests
{
    private static NameplateLayoutRules.Entry[] Entries(int count, float x, float y, float dx = 0) =>
        Enumerable.Range(0, count).Select(i => new NameplateLayoutRules.Entry
            { Id = (ulong)i, SourceIndex = i, X = x + i * dx, Y = y }).ToArray();

    private static void AssertVisibleContainedAndSeparated(NameplateLayoutRules.Entry[] values, float width, float height)
    {
        Assert.All(values, item =>
        {
            Assert.True(item.Placed);
            Assert.InRange(item.X, 0, width - NameplateLayoutRules.Width);
            Assert.InRange(item.Y, 0, height - NameplateLayoutRules.Height);
        });
        for (int i = 0; i < values.Length; i++)
        for (int j = i + 1; j < values.Length; j++)
            Assert.False(values[i].X < values[j].X + NameplateLayoutRules.Width
                && values[i].X + NameplateLayoutRules.Width > values[j].X
                && values[i].Y < values[j].Y + NameplateLayoutRules.Height
                && values[i].Y + NameplateLayoutRules.Height > values[j].Y);
    }

    [Theory]
    [InlineData(0)] // Every actor on the same projected point.
    [InlineData(45)] // Actual crowded spawn: narrower than even an ellipsized name.
    public void SixCrowdedNamesHaveSeparateScreenRects(float centerSpacing)
    {
        var values = Entries(6, 240, 340, centerSpacing);
        NameplateLayoutRules.Arrange(values, values.Length, 800, 600);
        AssertVisibleContainedAndSeparated(values, 800, 600);
        Assert.Equal(340, values[0].Y); // Uncrowded priority label retains its true anchor.
        Assert.Equal(Enumerable.Range(0, 6), values.Select(value => value.SourceIndex));
    }

    [Theory]
    [InlineData(-44, -12)]
    [InlineData(756, 588)]
    public void EdgeClusterStaysInsidePanelForAllEightSupportedPlayers(float x, float y)
    {
        var values = Entries(8, x, y);
        NameplateLayoutRules.Arrange(values, values.Length, 800, 600);
        AssertVisibleContainedAndSeparated(values, 800, 600);
    }

    [Fact]
    public void StablePublicSpawnIdIncludingZeroMakesOrderIrrelevantAndRetainsSourceBinding()
    {
        var first = Entries(6, 300, 340, 10);
        var reversed = first.Reverse().ToArray();
        NameplateLayoutRules.Arrange(first, first.Length, 800, 600);
        NameplateLayoutRules.Arrange(reversed, reversed.Length, 800, 600);
        Assert.Equal(0UL, first[0].Id); Assert.True(first[0].Placed);
        Assert.Equal(first.Select(v => (v.Id, v.SourceIndex, v.X, v.Y, v.Placed)),
            reversed.Select(v => (v.Id, v.SourceIndex, v.X, v.Y, v.Placed)));
        AssertVisibleContainedAndSeparated(first, 800, 600);
    }

    [Fact]
    public void WellSeparatedNamesRetainTheirProjectedAnchors()
    {
        var values = Entries(6, 10, 300, 125);
        NameplateLayoutRules.Arrange(values, values.Length, 800, 600);
        for (int i = 0; i < values.Length; i++)
        {
            Assert.Equal(10 + 125 * i, values[i].X);
            Assert.Equal(300, values[i].Y);
        }
    }

    [Theory]
    [InlineData(float.NaN, 100)]
    [InlineData(100, float.PositiveInfinity)]
    public void NonfiniteAnchorIsWithheldWithoutMovingOtherNames(float x, float y)
    {
        var values = Entries(2, 100, 100);
        values[0].X = x; values[0].Y = y;
        NameplateLayoutRules.Arrange(values, values.Length, 800, 600);
        Assert.False(values[0].Placed);
        Assert.True(values[1].Placed);
        Assert.Equal(100, values[1].X); Assert.Equal(100, values[1].Y);
    }

    [Theory]
    [InlineData(float.NaN, 600)]
    [InlineData(800, float.PositiveInfinity)]
    [InlineData(87, 600)]
    [InlineData(800, 21)]
    public void InvalidOrTooSmallPanelClearsPreviouslyPlacedFlags(float width, float height)
    {
        var values = Entries(6, 100, 100);
        for (int i = 0; i < values.Length; i++) values[i].Placed = true;
        NameplateLayoutRules.Arrange(values, values.Length, width, height);
        Assert.All(values, v => Assert.False(v.Placed));
    }

    [Fact]
    public void InsufficientScreenSpaceIsBoundedAndHidesExtraLabels()
    {
        var values = Entries(6, 0, 0);
        NameplateLayoutRules.Arrange(values, values.Length, 88, 22);
        Assert.True(values[0].Placed);
        Assert.All(values.Skip(1), v => Assert.False(v.Placed));
        Assert.Throws<ArgumentOutOfRangeException>(() => NameplateLayoutRules.Arrange(new NameplateLayoutRules.Entry[9], 9, 800, 600));
    }

    [Fact]
    public void ReusedBufferDoesNotKeepPreviousVisibilityOrOffsets()
    {
        var values = Entries(6, 300, 300);
        NameplateLayoutRules.Arrange(values, values.Length, 800, 600);
        values[0].X = 100; values[0].Y = 200;
        NameplateLayoutRules.Arrange(values, 1, 800, 600);
        Assert.True(values[0].Placed); Assert.Equal(100, values[0].X); Assert.Equal(200, values[0].Y);
    }
}
