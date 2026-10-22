using Actor.Presentation;

namespace Actor.Sdk.Tests;

public sealed class StaticSurfaceRulesTests
{
    [Fact] public void WallSurfaceRequiresTheVisibleFrontNotAnOutsideSpriteCenter()
    {
        var queries=new List<(float x,float y)>();
        bool Clear(float x,float y)=>y<5.07f;
        Assert.True(StaticSurfaceRules.Segment(6.4f,3.7f,3.5f,5.24f,5.01f,7.56f,5.01f,0,-1,
            (x,y)=>{queries.Add((x,y));return Clear(x,y);}));
        Assert.Equal(5,queries.Count);
        Assert.All(queries,p=>Assert.InRange(p.y,5.009f,5.011f));
        // The original sprite is on the far side of the same opaque wall.
        Assert.False(Clear(6.4f,5.09f));
    }
    [Fact] public void BackSideCannotSeeWallArtEvenWithAnUnobstructedQuery()
    {
        Assert.False(StaticSurfaceRules.Segment(0,1,3,-1,0,1,0,0,-1,(_,_)=>true));
        Assert.False(StaticSurfaceRules.Segment(0,0,3,-1,0,1,0,0,-1,(_,_)=>true));
    }
    [Fact] public void AnyBlockedFrontSampleSuppressesTheEntireDecorativeFace()
    {
        for(int blocked=0;blocked<5;++blocked)
        {
            int sample=0,selected=blocked;
            Assert.False(StaticSurfaceRules.Segment(0,-1,3,-1,0,1,0,0,-1,(_,_)=>sample++!=selected));
        }
    }
    [Fact] public void ALongSurfaceMayIntersectRangeWhileItsCenterIsOutside()
    {
        Assert.True(StaticSurfaceRules.Segment(-2,-.5f,1,-2,0,2,0,0,-1,(_,_)=>true));
        Assert.False(StaticSurfaceRules.Segment(-2,-1.01f,1,-2,0,2,0,0,-1,(_,_)=>true));
        // The gate permits a partial face; the sprite shader separately clips
        // each fragment against this same radius at its physical support point.
    }
    [Fact] public void VerticalCargoFrontUsesTheSameVisibilityAndRangeRules()
    {
        Assert.True(StaticSurfaceRules.Segment(5,-6.8f,2,6.84f,-7.7f,6.84f,-6.02f,-1,0,(_,_)=>true));
        Assert.False(StaticSurfaceRules.Segment(7,-6.8f,2,6.84f,-7.7f,6.84f,-6.02f,-1,0,(_,_)=>true));
        Assert.False(StaticSurfaceRules.Segment(4,-6.8f,2,6.84f,-7.7f,6.84f,-6.02f,-1,0,(_,_)=>true));
    }
    [Fact] public void ReactorBoundsRequireAllCornersAndCenterToHaveStaticSight()
    {
        for(int blocked=0;blocked<5;++blocked)
        {
            int sample=0,selected=blocked;
            Assert.False(StaticSurfaceRules.Bounds(-1.04f,-8.69f,3.5f,-.78f,-8.83f,.82f,-6.83f,(_,_)=>sample++!=selected));
        }
        Assert.True(StaticSurfaceRules.Bounds(-1.04f,-8.69f,3.5f,-.78f,-8.83f,.82f,-6.83f,(_,_)=>true));
        Assert.False(StaticSurfaceRules.Bounds(-5,-8,3.5f,-.78f,-8.83f,.82f,-6.83f,(_,_)=>true));
    }
    [Theory]
    [InlineData(float.NaN,0,1)] [InlineData(0,float.PositiveInfinity,1)]
    [InlineData(0,0,float.NaN)] [InlineData(0,0,float.PositiveInfinity)]
    [InlineData(0,0,0)] [InlineData(0,0,-1)]
    public void NonfiniteOrNonpositiveObserverGeometryFailsClosed(float x,float y,float radius)
    {
        Assert.False(StaticSurfaceRules.Segment(x,y,radius,-1,1,1,1,0,-1,(_,_)=>true));
        Assert.False(StaticSurfaceRules.Bounds(x,y,radius,-1,-1,1,1,(_,_)=>true));
    }
    [Fact] public void MalformedSurfaceAndMissingQueriesFailClosed()
    {
        Assert.False(StaticSurfaceRules.Segment(0,-1,3,0,0,0,0,0,-1,(_,_)=>true));
        Assert.False(StaticSurfaceRules.Segment(0,-1,3,-1,0,1,0,0,0,(_,_)=>true));
        Assert.False(StaticSurfaceRules.Segment(0,-1,3,-1,0,1,0,0,-1,null!));
        Assert.False(StaticSurfaceRules.Bounds(0,-1,3,1,0,-1,1,(_,_)=>true));
        Assert.False(StaticSurfaceRules.Bounds(0,-1,3,-1,0,1,1,null!));
    }
}
