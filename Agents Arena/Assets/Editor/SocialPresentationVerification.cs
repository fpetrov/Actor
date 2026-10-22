using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Actor.Presentation;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.SceneManagement;

namespace Actor.Editor
{
    // Saved static world only. This never enters Play Mode, connects a client,
    // focuses a native window, captures a desktop or reads private actor state.
    public static class SocialPresentationVerification
    {
        private const string Atlas="Assets/Sprites/map.png";
        private const int Width=800,Height=1000;
        [Serializable] private sealed class CameraSetup
        {
            public Vector2 center, world_minimum, world_maximum;
            public float orthographic_size, aspect;
        }
        [Serializable] private sealed class Report
        {
            public string schema="actor.social-static-presentation.v2";
            public string scope="Actual saved Social world, scene camera configuration and URP 2D rendering. No actors, fog, IMGUI, Human input or native-window visual acceptance.";
            public bool passed, rendered, source_map_unchanged, saved_scene_unchanged, atmospheric_art_active, legacy_atlas_inactive, all_enabled_sprites_original, bounded_art, floor_bounds_match,
                scene_setup_restored, render_request_supported, actual_renderer_2d;
            public int width=Width,height=Height,quantized_colors, enabled_sprite_count, original_texture_count, disabled_legacy_atlas_sprites, maximum_texture_dimension;
            public string graphics_api, pipeline, renderer, failure_code="", source_map_sha256, saved_scene_sha256;
            public CameraSetup camera;
        }

        [MenuItem("Actor/Render Static Social World")]
        public static void RenderWorld()
        {
            string directory=Path.GetFullPath("../build/social-world");Directory.CreateDirectory(directory);
            string imagePath=Path.Combine(directory,"presentation.png");
            if(File.Exists(imagePath))File.Delete(imagePath);
            var report=new Report();var setup=EditorSceneManager.GetSceneManagerSetup();
            GameObject temporaryCamera=null;RenderTexture target=null;Texture2D image=null;
            RenderTexture previousTarget=RenderTexture.active;
            bool changedScenes=false;Exception failure=null;
            string mapBefore=null,sceneBefore=null;
            try
            {
                if(Application.isPlaying||EditorApplication.isPlayingOrWillChangePlaymode)
                    throw new InvalidOperationException("Static presentation requires Edit Mode.");
                // Never discard or silently save the user's unsaved scene work.
                for(int i=0;i<SceneManager.sceneCount;++i)
                {
                    var scene=SceneManager.GetSceneAt(i);
                    if(scene.isDirty||scene.isLoaded&&string.IsNullOrEmpty(scene.path))
                        throw new InvalidOperationException("Static presentation requires saved scene state.");
                }
                if(SystemInfo.graphicsDeviceType==GraphicsDeviceType.Null)
                    throw new InvalidOperationException("A real Editor graphics device is required.");
                mapBefore=Hash(Atlas);sceneBefore=Hash(SocialWorldBuilder.SocialScene);
                EditorSceneManager.OpenScene(SocialWorldBuilder.SocialScene,OpenSceneMode.Single);changedScenes=true;
                var source=UnityEngine.Object.FindObjectsByType<Camera>().Single(camera=>camera.GetComponent<CameraFollow2D>()!=null);
                var follow=new SerializedObject(source.GetComponent<CameraFollow2D>());
                Vector2 minimum=follow.FindProperty("roomMin").vector2Value,maximum=follow.FindProperty("roomMax").vector2Value;
                var atlas=AssetDatabase.LoadAssetAtPath<Texture2D>(Atlas);
                var art=AtmosphericArtCatalog.Load();
                var originals=GameObject.Find("Original Atmospheric Environment");
                var sprites=UnityEngine.Object.FindObjectsByType<SpriteRenderer>(FindObjectsSortMode.None);
                var enabled=sprites.Where(r=>r.enabled&&r.gameObject.activeInHierarchy&&r.sprite!=null).ToArray();
                report.enabled_sprite_count=enabled.Length;
                report.atmospheric_art_active=art!=null&&originals!=null&&originals.activeInHierarchy&&enabled.Any(r=>r.sprite==art.floor);
                report.legacy_atlas_inactive=atlas!=null&&!enabled.Any(r=>r.sprite.texture==atlas);
                report.disabled_legacy_atlas_sprites=sprites.Count(r=>r.sprite!=null&&r.sprite.texture==atlas&&!r.enabled);
                report.all_enabled_sprites_original=enabled.Length>0&&enabled.All(r=>AssetDatabase.GetAssetPath(r.sprite.texture).StartsWith("Assets/Art/Atmospheric/Generated/",StringComparison.Ordinal));
                var textures=enabled.Select(r=>r.sprite.texture).Distinct().ToArray();
                report.original_texture_count=textures.Length;
                report.maximum_texture_dimension=textures.Select(t=>Math.Max(t.width,t.height)).DefaultIfEmpty(0).Max();
                report.bounded_art=enabled.Length<=1024&&textures.Length<=16&&report.maximum_texture_dimension<=2048;
                var floor=enabled.FirstOrDefault(r=>r.sprite==art?.floor);
                report.floor_bounds_match=floor!=null&&Vector2.Distance(floor.bounds.min,minimum)<.002f&&Vector2.Distance(floor.bounds.max,maximum)<.002f;
                if(!report.atmospheric_art_active||!report.legacy_atlas_inactive||!report.all_enabled_sprites_original||!report.bounded_art||!report.floor_bounds_match)
                    throw new InvalidOperationException("Expected bounded original Atmospheric art over the unchanged Social world bounds.");
                if(UnityEngine.Object.FindObjectsByType<Actor.Networking.NetworkCharacter>().Length!=0)
                    throw new InvalidOperationException("Static presentation must contain no actor instances.");
                if(GraphicsSettings.currentRenderPipeline is not UniversalRenderPipelineAsset asset)
                    throw new InvalidOperationException("The actual project URP asset is required.");
                report.pipeline=asset.name;report.graphics_api=SystemInfo.graphicsDeviceType.ToString();
                temporaryCamera=new GameObject("Temporary Static Social QA Camera"){hideFlags=HideFlags.HideAndDontSave};
                var camera=temporaryCamera.AddComponent<Camera>();camera.CopyFrom(source);camera.enabled=false;
                var extra=temporaryCamera.AddComponent<UniversalAdditionalCameraData>();
                var sourceExtra=source.GetComponent<UniversalAdditionalCameraData>();
                if(sourceExtra==null)throw new InvalidOperationException("Expected the scene's actual URP camera configuration.");
                EditorUtility.CopySerialized(sourceExtra,extra);
                report.renderer=extra.scriptableRenderer.GetType().Name;
                report.actual_renderer_2d=report.renderer=="Renderer2D";
                if(!report.actual_renderer_2d)throw new InvalidOperationException("Expected the actual 2D renderer.");
                camera.orthographic=true;camera.aspect=(float)Width/Height;camera.rect=new Rect(0,0,1,1);
                camera.transform.position=new Vector3((minimum.x+maximum.x)*.5f,(minimum.y+maximum.y)*.5f,source.transform.position.z);
                camera.transform.rotation=source.transform.rotation;
                camera.orthographicSize=Mathf.Max((maximum.y-minimum.y)*.5f,(maximum.x-minimum.x)*.5f/camera.aspect)+.3f;
                report.camera=new CameraSetup{center=camera.transform.position,world_minimum=minimum,world_maximum=maximum,
                    orthographic_size=camera.orthographicSize,aspect=camera.aspect};
                target=new RenderTexture(Width,Height,24,RenderTextureFormat.ARGB32){hideFlags=HideFlags.HideAndDontSave,
                    antiAliasing=1,name="Static Social QA Render Target"};target.Create();camera.targetTexture=target;
                var request=new UniversalRenderPipeline.SingleCameraRequest{destination=target};
                report.render_request_supported=RenderPipeline.SupportsRenderRequest(camera,request);
                if(!report.render_request_supported)throw new InvalidOperationException("The installed URP render request is not available.");
                RenderPipeline.SubmitRenderRequest(camera,request);
                RenderTexture.active=target;
                image=new Texture2D(Width,Height,TextureFormat.RGB24,false){hideFlags=HideFlags.HideAndDontSave};
                image.ReadPixels(new Rect(0,0,Width,Height),0,0);image.Apply(false,false);
                // A blank/failed render is not successful visual evidence.
                report.quantized_colors=image.GetPixels32().Where((_,index)=>index%23==0)
                    .Select(pixel=>(pixel.r>>4)<<8|(pixel.g>>4)<<4|(pixel.b>>4)).Distinct().Count();
                if(report.quantized_colors<16)throw new InvalidOperationException("The render target is unexpectedly flat.");
                File.WriteAllBytes(imagePath,image.EncodeToPNG());report.rendered=true;
            }
            catch(Exception error){failure=error;report.failure_code=error.GetType().Name;}
            finally
            {
                RenderTexture.active=previousTarget;
                if(temporaryCamera!=null)UnityEngine.Object.DestroyImmediate(temporaryCamera);
                if(target!=null){target.Release();UnityEngine.Object.DestroyImmediate(target);}
                if(image!=null)UnityEngine.Object.DestroyImmediate(image);
                try
                {
                    if(changedScenes)EditorSceneManager.RestoreSceneManagerSetup(setup);
                    var after=EditorSceneManager.GetSceneManagerSetup();
                    report.scene_setup_restored=setup.Length==after.Length&&setup.Zip(after,(a,b)=>a.path==b.path&&a.isLoaded==b.isLoaded&&a.isActive==b.isActive).All(value=>value);
                    report.source_map_sha256=Hash(Atlas);report.saved_scene_sha256=Hash(SocialWorldBuilder.SocialScene);
                    report.source_map_unchanged=mapBefore!=null&&mapBefore==report.source_map_sha256;
                    report.saved_scene_unchanged=sceneBefore!=null&&sceneBefore==report.saved_scene_sha256;
                }
                catch(Exception error){if(failure==null)failure=error;report.failure_code=error.GetType().Name;}
                report.passed=report.rendered&&report.source_map_unchanged&&report.saved_scene_unchanged&&report.atmospheric_art_active&&report.legacy_atlas_inactive&&report.all_enabled_sprites_original&&report.bounded_art&&report.floor_bounds_match
                    &&report.scene_setup_restored&&report.render_request_supported&&report.actual_renderer_2d&&failure==null;
                File.WriteAllText(Path.Combine(directory,"presentation.json"),JsonUtility.ToJson(report,true)+"\n");
            }
            if(failure!=null)throw new InvalidOperationException("Static Social presentation rendering failed ("+report.failure_code+").",failure);
            Debug.Log("ACTOR_SOCIAL_STATIC_PRESENTATION rendered=800x1000 passed="+report.passed);
        }
        private static string Hash(string path)
        {
            using var hash=SHA256.Create();
            return BitConverter.ToString(hash.ComputeHash(File.ReadAllBytes(path))).Replace("-","").ToLowerInvariant();
        }
    }
}
