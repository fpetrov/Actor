#ifndef ACTOR_STATION_LIGHTING_INCLUDED
#define ACTOR_STATION_LIGHTING_INCLUDED

// Set only by the client presentation host from its permitted public state.
// Zero defaults leave legacy/offline scenes unchanged.
float _ActorStationActive;
float _ActorStationEmergency;
float _ActorStationTime;

half3 ActorStationLighting(half3 color)
{
    half active = saturate(_ActorStationActive);
    half emergency = saturate(_ActorStationEmergency);
    half3 ambient = lerp(half3(1, 1, 1), half3(.90h, .94h, .98h), active);
    ambient *= lerp(half3(1, 1, 1), half3(.42h, .49h, .60h), emergency);
    return color * ambient;
}

#endif
