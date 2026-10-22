Shader "Actor/Static Surface Vision"
{
    Properties { [PerRendererData] _MainTex("Sprite",2D)="white" {} }
    SubShader
    {
        Tags { "Queue"="Transparent" "RenderType"="Transparent" "IgnoreProjector"="True" "RenderPipeline"="UniversalPipeline" }
        Cull Off ZWrite Off Blend SrcAlpha OneMinusSrcAlpha
        Pass
        {
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #include "Packages/com.unity.render-pipelines.universal/Shaders/2D/Include/Core2D.hlsl"
            #pragma multi_compile_instancing
            struct Attributes {float3 positionOS:POSITION;float2 uv:TEXCOORD0;half4 color:COLOR;UNITY_VERTEX_INPUT_INSTANCE_ID};
            struct Varyings {float4 positionCS:SV_POSITION;float2 uv:TEXCOORD0;float2 world:TEXCOORD1;half4 color:COLOR;};
            TEXTURE2D(_MainTex);SAMPLER(sampler_MainTex);
            float4 _SurfaceEye,_SurfaceStart,_SurfaceEnd;
            Varyings Vert(Attributes i)
            {
                UNITY_SETUP_INSTANCE_ID(i);SetUpSpriteInstanceProperties();Varyings o;
                float3 localPosition=UnityFlipSprite(i.positionOS,unity_SpriteProps.xy);
                o.positionCS=TransformObjectToHClip(localPosition);o.world=TransformObjectToWorld(localPosition).xy;
                o.uv=i.uv;o.color=i.color*unity_SpriteColor;return o;
            }
            half4 Frag(Varyings i):SV_Target
            {
                // Wall art is a vertical face, not a traversable floor object.
                // Its physical support is the corresponding front-edge segment.
                float2 support=lerp(_SurfaceStart.xy,_SurfaceEnd.xy,saturate(i.uv.x));
                // East-facing cargo uses its sprite's vertical axis instead.
                if(abs(_SurfaceEnd.y-_SurfaceStart.y)>abs(_SurfaceEnd.x-_SurfaceStart.x))
                    support=lerp(_SurfaceStart.xy,_SurfaceEnd.xy,saturate(i.uv.y));
                float2 visiblePosition=lerp(i.world,support,_SurfaceEye.w);
                // Edit-mode art review has no local observer. Runtime renderers
                // are disabled until the component installs a positive radius.
                if(_SurfaceEye.z>0)
                {
                    float2 delta=visiblePosition-_SurfaceEye.xy;
                    clip(_SurfaceEye.z*_SurfaceEye.z-dot(delta,delta));
                }
                return SAMPLE_TEXTURE2D(_MainTex,sampler_MainTex,i.uv)*i.color;
            }
            ENDHLSL
        }
    }
}
