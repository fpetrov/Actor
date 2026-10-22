#ifndef ACTOR_STATION_BACKDROP_INCLUDED
#define ACTOR_STATION_BACKDROP_INCLUDED

#include "StationLighting.hlsl"
TEXTURE2D(_Backdrop); SAMPLER(sampler_Backdrop);

float ActorMistNoise(float2 p)
{
    // A small continuous field: no CPU texture uploads or simulation particles.
    return .5 + .22 * sin(p.x * 1.13 + sin(p.y * .83))
        + .18 * sin(p.y * 1.41 - p.x * .39)
        + .10 * sin(p.x * 2.31 + p.y * 1.73);
}

half3 ActorStationBackdrop(float2 world)
{
    // The same world mapping is used by the background AND the opaque fog.
    // Authored public art only; never samples the camera/color/depth buffer.
    float2 uv = (world - float2(2.55, -2.925)) / float2(64, 36) + .5;
    half3 space = SAMPLE_TEXTURE2D(_Backdrop, sampler_Backdrop, saturate(uv)).rgb;
    float2 drift = float2(_ActorStationTime * .018, -_ActorStationTime * .012);
    half mist = (half)saturate(ActorMistNoise(world * .53 + drift));
    return space * .62h + lerp(half3(.008h, .015h, .023h), half3(.021h, .033h, .044h), mist);
}

#endif
