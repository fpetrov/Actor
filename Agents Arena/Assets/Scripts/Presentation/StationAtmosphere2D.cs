using Actor.Networking;
using Actor.UI;
using AgentsArena.ActorProtocol.V1;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Presentation
{
    // Ten authored, static deck lamps and one public space backdrop. No player
    // collections, roles, private tasks, network actions or visibility writes.
    [DisallowMultipleComponent, DefaultExecutionOrder(40)]
    public sealed class StationAtmosphere2D : MonoBehaviour
    {
#if !UNITY_SERVER
        private static readonly int Active = Shader.PropertyToID("_ActorStationActive"),
            Emergency = Shader.PropertyToID("_ActorStationEmergency"),
            Clock = Shader.PropertyToID("_ActorStationTime");
        private Mesh quad;
        private readonly MeshRenderer[] lamps = new MeshRenderer[StationAtmosphereRules.LampCount];
        private float emergency;
        private double animationTime;
        private bool available;
        private bool shownLamps;
        private StationAtmosphereScope lastScope;

        private void Awake()
        {
            if (Application.isBatchMode || SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null)
            { enabled = false; return; }
            var backdrop = Resources.Load<Material>("Art/StationBackdrop");
            var glow = Resources.Load<Material>("Art/StationGlow");
            if (backdrop == null || glow == null || !backdrop.shader.isSupported || !glow.shader.isSupported)
            { enabled = false; return; }
            quad = new Mesh { name = "Static Station Atmosphere Quad" };
            quad.vertices = new[] { new Vector3(-.5f,-.5f),new Vector3(.5f,-.5f),new Vector3(.5f,.5f),new Vector3(-.5f,.5f) };
            quad.uv = new[] { Vector2.zero,Vector2.right,Vector2.one,Vector2.up };
            quad.triangles = new[] { 0,1,2,0,2,3 };
            quad.RecalculateBounds();
            Create("Quiet Orbital Backdrop", new Vector2(2.55f,-2.925f), new Vector2(128,128), backdrop, -200);
            // Public authored floor coordinates, independent of the player
            // graph or anyone's current position. Pools are smaller than rooms.
            var positions = new[] { new Vector2(-2.35f,-.45f),new Vector2(2.22f,.25f),
                new Vector2(-1.2f,-6.9f),new Vector2(.9f,-8.75f),
                new Vector2(5.65f,4.25f),new Vector2(7.3f,3.0f),
                new Vector2(4.5f,-6.6f),new Vector2(6.15f,-7.65f),
                new Vector2(6.44f,.8f),new Vector2(3.8f,-4.33f) };
            for (int i=0;i<lamps.Length;i++)
            {
                lamps[i]=Create("Static Service Lamp "+(i+1),positions[i],new Vector2(1.5f,1.25f),glow,-60);
                lamps[i].enabled=false;
            }
            available = true;
            Shader.SetGlobalFloat(Active,1);
        }

        private void OnEnable(){if(available)Shader.SetGlobalFloat(Active,1);}

        private MeshRenderer Create(string label, Vector2 position, Vector2 scale, Material material, int order)
        {
            var root = new GameObject(label); root.transform.SetParent(transform,false);
            root.transform.position = new Vector3(position.x,position.y,0);
            root.transform.localScale = new Vector3(scale.x,scale.y,1);
            root.AddComponent<MeshFilter>().sharedMesh=quad;
            var renderer=root.AddComponent<MeshRenderer>(); renderer.sharedMaterial=material; renderer.sortingOrder=order;
            renderer.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; renderer.receiveShadows=false;
            renderer.lightProbeUsage=UnityEngine.Rendering.LightProbeUsage.Off;
            renderer.reflectionProbeUsage=UnityEngine.Rendering.ReflectionProbeUsage.Off;
            return renderer;
        }

        private void Update()
        {
            if (!available) return;
            bool permitted=PresentationSight.TryGet(out _,out _,out ulong epoch,true);
            bool active=false;
            StationAtmosphereScope scope=default;
            if (permitted && PresentationSight.Observer)
            {
                // Exactly the public frame already applied to this camera.
                // PresentationSight supplies its existing freshness gate.
                var frame=BroadcastViewPresenter.Current?.Applied;
                var session=NetworkManager.Singleton?.GetComponent<BroadcastSession>();
                permitted=frame?.Scope!=null&&session!=null&&session.Fresh&&frame.Scope.ViewEpoch==epoch
                    &&BroadcastPresentationRules.SameIdentity(frame.Scope,session.ExpectedScope);
                if(permitted)
                {
                    scope=new StationAtmosphereScope(frame.Scope.MatchId,frame.Scope.ConnectionGeneration,epoch,true,
                        frame.Scope.BroadcastSessionId,frame.Scope.ObserverId);
                    active=frame.EmergencyActive&&frame.Phase!=SocialPhase.GameOver;
                }
            }
            else if (permitted)
            {
                // Use the same current endpoint as PresentationSight, not a
                // previous LocalOwner reference during a reconnect/rematch.
                var own=NetworkManager.Singleton?.SpawnManager?.GetLocalPlayerObject()?.GetComponent<NetworkSocialMatch>();
                permitted=own!=null&&own.HasOwnState&&own.IsOwner&&own.IsClient
                    &&StationAtmosphereRules.SamePublicMatch(own.State.MatchId,own.Emergency.MatchId);
                if(permitted)
                {
                    scope=new StationAtmosphereScope(own.State.MatchId,own.Own.ConnectionGeneration,epoch,false);
                    active=own.Emergency.Active&&own.State.Phase!=SocialPhase.GameOver;
                }
            }
            permitted&=scope.Valid;
            // Cosmetic envelopes cannot carry an old match, reconnect, camera
            // epoch or audience's emergency into the next presentation scope.
            bool reset=!scope.Matches(lastScope);
            lastScope=permitted?scope:default;
            emergency=StationAtmosphereRules.EmergencyEnvelope(emergency,permitted,active,Time.unscaledDeltaTime,reset);
            Shader.SetGlobalFloat(Emergency,emergency);
            // The existing accessibility setting freezes the CURRENT cosmetic
            // phase. No reset, catch-up burst or periodic clock wrap is needed.
            if(!ArenaUI.ReducedMotion)animationTime+=Mathf.Clamp(Time.unscaledDeltaTime,0,.1f);
            Shader.SetGlobalFloat(Clock,(float)animationTime);
            if(shownLamps!=permitted)
            {shownLamps=permitted;for (int i=0;i<lamps.Length;i++)lamps[i].enabled=permitted;}
        }

        private void OnDisable()
        {
            Shader.SetGlobalFloat(Active,0); Shader.SetGlobalFloat(Emergency,0); Shader.SetGlobalFloat(Clock,0);
            shownLamps=false;
            emergency=0;lastScope=default;
            for (int i=0;i<lamps.Length;i++) if(lamps[i]!=null)lamps[i].enabled=false;
        }
        private void OnDestroy(){if(quad!=null)Destroy(quad);}
#endif
    }
}
