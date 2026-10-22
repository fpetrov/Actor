Shader "Actor/Station Backdrop"
{
    Properties { _Backdrop("Public Space Backdrop", 2D) = "black" {} }
    SubShader
    {
        Tags { "Queue"="Transparent" "RenderType"="Transparent" "IgnoreProjector"="True" "RenderPipeline"="UniversalPipeline" }
        Cull Off ZWrite Off Blend Off
        Pass
        {
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "StationBackdrop.hlsl"
            struct Attributes { float3 positionOS:POSITION; };
            struct Varyings { float4 positionCS:SV_POSITION; float2 world:TEXCOORD0; };
            Varyings Vert(Attributes i)
            { Varyings o; o.positionCS=TransformObjectToHClip(i.positionOS); o.world=TransformObjectToWorld(i.positionOS).xy; return o; }
            half4 Frag(Varyings i):SV_Target { return half4(ActorStationBackdrop(i.world),1); }
            ENDHLSL
        }
    }
}
