Shader "Actor/Station Glow"
{
    Properties { _WarmColor("Working Light",Color)=(.58,.46,.26,1) _EmergencyColor("Emergency Light",Color)=(.72,.16,.055,1) }
    SubShader
    {
        Tags { "Queue"="Transparent" "RenderType"="Transparent" "IgnoreProjector"="True" "RenderPipeline"="UniversalPipeline" }
        Cull Off ZWrite Off Blend One One
        Pass
        {
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "StationLighting.hlsl"
            struct Attributes { float3 positionOS:POSITION; float2 uv:TEXCOORD0; };
            struct Varyings { float4 positionCS:SV_POSITION; float2 uv:TEXCOORD0; };
            CBUFFER_START(UnityPerMaterial)
                half4 _WarmColor, _EmergencyColor;
            CBUFFER_END
            Varyings Vert(Attributes i)
            { Varyings o; o.positionCS=TransformObjectToHClip(i.positionOS); o.uv=i.uv*2-1; return o; }
            half4 Frag(Varyings i):SV_Target
            {
                half radius=(half)length(i.uv);
                half pool=pow(saturate(1.0h-radius),3.0h)*.28h;
                // One small inset emitter within the pool, not a huge bloom.
                half core=(1.0h-smoothstep(.11h,.18h,abs(i.uv.x)))*(1.0h-smoothstep(.018h,.045h,abs(i.uv.y)));
                half emergency=saturate(_ActorStationEmergency);
                half pulse=.84h+.16h*(half)sin(_ActorStationTime*3.14159265);
                half3 color=lerp(_WarmColor.rgb,_EmergencyColor.rgb,emergency);
                half power=lerp(1.0h,pulse,emergency);
                return half4(color*(pool+core*.8h)*power,0);
            }
            ENDHLSL
        }
    }
}
