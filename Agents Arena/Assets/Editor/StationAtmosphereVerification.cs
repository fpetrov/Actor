using System;
using System.IO;
using Actor.Presentation;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.SceneManagement;

namespace Actor.Editor
{
    // Actual GPU material check, explicitly invoked by the operator. Never
    // starts Play Mode, a client, an observer, OBS, models or a public stream.
    public static class StationAtmosphereVerification
    {
        [Serializable] private sealed class Report
        {
            public string schema="actor.station-fog-seal.v1";
            public string scope="Actual URP 2D shader render with synthetic red/green geometry behind an opaque alpha1 fog quad. Not native gameplay, LOS geometry, camera ACK or performance acceptance.";
            public bool passed, shader_supported, input_changed, opaque_pixels_unchanged, reduced_motion_stable, scene_restored;
            public int width=160,height=120, opaque_samples, opaque_changed, visible_changed, animated_changed;
            public string graphics_api, failure_code="";
        }

        [MenuItem("Actor/Verify Station Fog Opaque Seal")]
        public static void VerifyFogSeal()
        {
            string directory=Path.GetFullPath("../build/atmosphere-validation");Directory.CreateDirectory(directory);
            var report=new Report();var original=SceneManager.GetActiveScene();Scene temporary=default;
            GameObject root=null;Mesh backgroundMesh=null,fogMesh=null;Material background=null,fog=null;
            RenderTexture target=null;Texture2D pixels=null;var previous=RenderTexture.active;
            float clock=Shader.GetGlobalFloat("_ActorStationTime");
            float emergency=Shader.GetGlobalFloat("_ActorStationEmergency");
            Exception failure=null;
            try
            {
                if(Application.isPlaying||EditorApplication.isPlayingOrWillChangePlaymode||SystemInfo.graphicsDeviceType==GraphicsDeviceType.Null)
                    throw new InvalidOperationException("A real graphics device in Edit Mode is required.");
                if(GraphicsSettings.currentRenderPipeline is not UniversalRenderPipelineAsset)
                    throw new InvalidOperationException("The actual project URP pipeline is required.");
                foreach(var renderer in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None))
                    if(renderer.gameObject.layer==31)throw new InvalidOperationException("Temporary QA rendering layer must be unused.");
                SocialPresentationVerification.ValidateProductionShaders(true);
                var source=Resources.Load<Material>("Art/StationFog");
                if(source==null||source.GetTexture("_Backdrop")==null||!source.shader.isSupported)
                    throw new InvalidOperationException("The production fog material and original backdrop are required.");
                report.shader_supported=true;report.graphics_api=SystemInfo.graphicsDeviceType.ToString();
                temporary=EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Additive);
                SceneManager.SetActiveScene(temporary);
                root=new GameObject("Owned Station Fog Shader QA"){hideFlags=HideFlags.HideAndDontSave};
                background=new Material(Shader.Find("Sprites/Default")){hideFlags=HideFlags.HideAndDontSave};
                fog=new Material(source){hideFlags=HideFlags.HideAndDontSave};
                backgroundMesh=Quad(-2,2);fogMesh=Quad(0,2);
                Add(root.transform,"Synthetic Background",backgroundMesh,background,0,0);
                Add(root.transform,"Opaque Fog Half",fogMesh,fog,2000,-1);
                var cameraObject=new GameObject("Owned Station Fog QA Camera"){hideFlags=HideFlags.HideAndDontSave};
                cameraObject.transform.SetParent(root.transform,false);cameraObject.transform.position=new Vector3(0,0,-10);
                var camera=cameraObject.AddComponent<Camera>();camera.enabled=false;camera.orthographic=true;
                camera.orthographicSize=1.5f;camera.aspect=(float)report.width/report.height;camera.cullingMask=1<<31;
                camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=Color.black;
                var extra=cameraObject.AddComponent<UniversalAdditionalCameraData>();
                if(extra.scriptableRenderer.GetType().Name!="Renderer2D")throw new InvalidOperationException("Actual Renderer2D is required.");
                target=new RenderTexture(report.width,report.height,24,RenderTextureFormat.ARGB32){hideFlags=HideFlags.HideAndDontSave,antiAliasing=1};
                target.Create();camera.targetTexture=target;
                pixels=new Texture2D(report.width,report.height,TextureFormat.RGB24,false){hideFlags=HideFlags.HideAndDontSave};
                Shader.SetGlobalFloat("_ActorStationTime",0);Shader.SetGlobalFloat("_ActorStationEmergency",0);
                background.color=new Color(.85f,.04f,.02f,1);Color32[] red=Render(camera,target,pixels);
                File.WriteAllBytes(Path.Combine(directory,"fog-seal.png"),pixels.EncodeToPNG());
                background.color=new Color(.02f,.85f,.04f,1);Color32[] green=Render(camera,target,pixels);
                Color32[] stable=Render(camera,target,pixels);
                Shader.SetGlobalFloat("_ActorStationTime",100);Color32[] drift=Render(camera,target,pixels);
                bool stableFrame=true;
                for(int y=8;y<report.height-8;y++)for(int x=8;x<report.width-8;x++)
                {
                    int i=y*report.width+x;bool changed=!Same(red[i],green[i]);
                    if(x>report.width/2+4)
                    {report.opaque_samples++;if(changed)report.opaque_changed++;if(!Same(green[i],drift[i]))report.animated_changed++;}
                    else if(x<report.width/2-4&&changed)report.visible_changed++;
                    stableFrame&=Same(green[i],stable[i]);
                }
                report.input_changed=report.visible_changed>100;
                report.opaque_pixels_unchanged=report.opaque_samples>100&&report.opaque_changed==0;
                report.reduced_motion_stable=stableFrame;
                report.passed=report.shader_supported&&report.input_changed&&report.opaque_pixels_unchanged&&report.reduced_motion_stable&&report.animated_changed>0;
                if(!report.passed)throw new InvalidOperationException("The actual opaque seal / visible input / drift controls did not pass.");
            }
            catch(Exception error){report.passed=false;report.failure_code=error.GetType().Name;failure=error;}
            finally
            {
                Shader.SetGlobalFloat("_ActorStationTime",clock);Shader.SetGlobalFloat("_ActorStationEmergency",emergency);
                RenderTexture.active=previous;
                if(root!=null)UnityEngine.Object.DestroyImmediate(root);
                if(target!=null){target.Release();UnityEngine.Object.DestroyImmediate(target);}
                if(pixels!=null)UnityEngine.Object.DestroyImmediate(pixels);
                if(backgroundMesh!=null)UnityEngine.Object.DestroyImmediate(backgroundMesh);
                if(fogMesh!=null)UnityEngine.Object.DestroyImmediate(fogMesh);
                if(background!=null)UnityEngine.Object.DestroyImmediate(background);
                if(fog!=null)UnityEngine.Object.DestroyImmediate(fog);
                if(temporary.IsValid())EditorSceneManager.CloseScene(temporary,true);
                if(original.IsValid()&&original.isLoaded)SceneManager.SetActiveScene(original);
                report.scene_restored=SceneManager.GetActiveScene()==original&&!temporary.isLoaded;
                report.passed&=report.scene_restored;
                File.WriteAllText(Path.Combine(directory,"fog-seal.json"),JsonUtility.ToJson(report,true)+"\n");
            }
            if(failure!=null)throw new InvalidOperationException("Station fog shader acceptance failed.",failure);
            if(!report.passed)throw new InvalidOperationException("Station fog QA did not restore its owned scene.");
            Debug.Log("ACTOR_STATION_FOG_SEAL passed=true opaque_samples="+report.opaque_samples);
        }

        private static bool Same(Color32 a,Color32 b)=>a.r==b.r&&a.g==b.g&&a.b==b.b;
        private static Color32[] Render(Camera camera,RenderTexture target,Texture2D image)
        {
            var request=new UniversalRenderPipeline.SingleCameraRequest{destination=target};
            if(!RenderPipeline.SupportsRenderRequest(camera,request))throw new InvalidOperationException("URP render request unsupported.");
            RenderPipeline.SubmitRenderRequest(camera,request);RenderTexture.active=target;
            image.ReadPixels(new Rect(0,0,target.width,target.height),0,0);image.Apply(false,false);return image.GetPixels32();
        }
        private static Mesh Quad(float left,float right)
        {
            var mesh=new Mesh{hideFlags=HideFlags.HideAndDontSave};
            mesh.vertices=new[]{new Vector3(left,-2,0),new Vector3(right,-2,0),new Vector3(right,2,0),new Vector3(left,2,0)};
            mesh.colors=new[]{Color.white,Color.white,Color.white,Color.white};
            mesh.uv=new[]{Vector2.zero,Vector2.right,Vector2.one,Vector2.up};mesh.triangles=new[]{0,1,2,0,2,3};mesh.RecalculateBounds();return mesh;
        }
        private static void Add(Transform parent,string name,Mesh mesh,Material material,int order,float z)
        {
            var root=new GameObject(name){hideFlags=HideFlags.HideAndDontSave,layer=31};root.transform.SetParent(parent,false);
            root.transform.localPosition=new Vector3(0,0,z);root.AddComponent<MeshFilter>().sharedMesh=mesh;
            var renderer=root.AddComponent<MeshRenderer>();renderer.sharedMaterial=material;renderer.sortingOrder=order;
        }
    }
}
