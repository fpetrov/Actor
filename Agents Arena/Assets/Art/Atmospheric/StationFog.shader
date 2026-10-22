Shader "Actor/Station Fog"
{
    Properties
    {
        _Backdrop("Public Space Backdrop", 2D) = "black" {}
        _DeckPlan("Immutable Authored Floor Only", 2D) = "black" {}
        _DeckPlanStrength("Public Floor Silhouette", Range(0,1)) = .65
    }
    SubShader
    {
        Tags { "Queue"="Transparent" "RenderType"="Transparent" "IgnoreProjector"="True" "RenderPipeline"="UniversalPipeline" }
        Cull Off ZWrite Off Blend SrcAlpha OneMinusSrcAlpha
        Pass
        {
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "StationBackdrop.hlsl"
            TEXTURE2D(_DeckPlan); SAMPLER(sampler_DeckPlan);
            CBUFFER_START(UnityPerMaterial)
                half _DeckPlanStrength;
            CBUFFER_END
            struct Attributes { float3 positionOS:POSITION; half4 color:COLOR; };
            struct Varyings { float4 positionCS:SV_POSITION; float2 world:TEXCOORD0; half alpha:TEXCOORD1; };
            Varyings Vert(Attributes i)
            {
                Varyings o; o.positionCS=TransformObjectToHClip(i.positionOS);
                o.world=TransformObjectToWorld(i.positionOS).xy; o.alpha=i.color.a; return o;
            }
            half4 Frag(Varyings i):SV_Target
            {
                half original = saturate(i.alpha);
                // B/C of every outside annulus triangle are exactly alpha1.
                // Noise affects only the added INWARD feather, never this seal.
                float wave = ActorMistNoise(i.world * 3.1 + float2(_ActorStationTime * .07, -_ActorStationTime * .04));
                half feather = smoothstep(0.0h, 1.0h, saturate(original + (half)(wave - .5) * .30h * original * (1.0h-original)));
                half alpha = original >= 1.0h ? 1.0h : feather;
                half3 color = ActorStationBackdrop(i.world);
                // A dim fixed floor silhouette makes the veil feel translucent
                // without revealing the scene behind it. This is the existing
                // public station-floor PNG, never a rendered gameplay texture.
                float2 deckUV=(i.world-float2(2.55,-2.925))/float2(14.1,18.65)+.5;
                half4 deck=SAMPLE_TEXTURE2D(_DeckPlan,sampler_DeckPlan,saturate(deckUV));
                half inside=(half)(step(0.0,deckUV.x)*step(deckUV.x,1.0)*step(0.0,deckUV.y)*step(deckUV.y,1.0));
                // Linear-space floor tint is deliberately low: the previous
                // values became a conspicuous grey map after sRGB conversion.
                color=lerp(color,deck.rgb*half3(.04h,.0475h,.0575h),saturate(_DeckPlanStrength)*deck.a*inside);
                // A very restrained silver-blue lip supplies depth, entirely
                // inside permitted geometry; it never reveals a hidden sprite.
                color += half3(.032h, .044h, .052h) * (4.0h * original * (1.0h-original));
                // Fog-only exposure; the visible orbital backdrop retains its
                // own palette. RGB changes never reduce the opaque alpha seal.
                return half4(color * .75h, alpha);
            }
            ENDHLSL
        }
    }
}
