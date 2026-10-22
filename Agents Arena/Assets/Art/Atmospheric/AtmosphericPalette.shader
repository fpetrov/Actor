Shader "Actor/Atmospheric Palette"
{
    Properties { [PerRendererData] _MainTex("Sprite",2D)="white" {} _SkinColor("Fabric",Color)=(.58,.71,.64,1) }
    SubShader
    {
        Tags { "Queue"="Transparent" "RenderType"="Transparent" "IgnoreProjector"="True" "CanUseSpriteAtlas"="True" "RenderPipeline"="UniversalPipeline" }
        Cull Off ZWrite Off Blend SrcAlpha OneMinusSrcAlpha
        Pass
        {
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #include "Packages/com.unity.render-pipelines.universal/Shaders/2D/Include/Core2D.hlsl"
            #pragma multi_compile_instancing
            struct Attributes { float3 positionOS:POSITION; float2 uv:TEXCOORD0; half4 color:COLOR; UNITY_VERTEX_INPUT_INSTANCE_ID };
            struct Varyings { float4 positionCS:SV_POSITION; float2 uv:TEXCOORD0; half4 color:COLOR; };
            TEXTURE2D(_MainTex); SAMPLER(sampler_MainTex);
            CBUFFER_START(UnityPerMaterial) half4 _SkinColor; CBUFFER_END
            Varyings Vert(Attributes i) { UNITY_SETUP_INSTANCE_ID(i); SetUpSpriteInstanceProperties(); Varyings o; o.positionCS=TransformObjectToHClip(UnityFlipSprite(i.positionOS,unity_SpriteProps.xy)); o.uv=i.uv; o.color=i.color*unity_SpriteColor; return o; }
            half4 Frag(Varyings i):SV_Target
            {
                half4 c=SAMPLE_TEXTURE2D(_MainTex,sampler_MainTex,i.uv);
                // Magenta is a dedicated editable fabric mask, never an identity signal.
                half mask=step(c.g,.025h)*step(.08h,c.r)*step(.08h,c.b)*step(abs(c.r-c.b),.06h);
                c.rgb=lerp(c.rgb,_SkinColor.rgb*c.r,mask);
                return c*i.color;
            }
            ENDHLSL
        }
    }
}
