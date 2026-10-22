using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Actor.Presentation;
using UnityEditor;
using UnityEngine;

namespace Actor.Editor
{
    public static class AtmosphericArtImporter
    {
        private const string Art = "Assets/Art/Atmospheric/Generated/";
        [Serializable] private sealed class Placement { public string sprite; public float x,y,pivot_x,pivot_y; }
        [Serializable] private sealed class PlacementFile { public Placement[] landmarks; }
        private const string CatalogPath = "Assets/Resources/Art/Atmospheric.asset";
        private static readonly string[] Directions = { "Down", "Up", "Left", "Right", "DownRight", "UpRight" };
        public static readonly string[] CueNames = { "Hover", "Click", "Message", "TaskAccepted", "TaskCompleted", "Meeting", "VoteSubmitted", "Emergency", "Death", "KillImpact", "GameOver" };
        [MenuItem("ACTOR/Prepare Atmospheric Art")]
        public static void Prepare()
        {
            AssetDatabase.Refresh();
            Directory.CreateDirectory("Assets/Resources/Art");
            Directory.CreateDirectory("Assets/Art/Atmospheric/Animation");
            var placements=JsonUtility.FromJson<PlacementFile>(File.ReadAllText(Art+"landmarks.json"));
            if(placements?.landmarks==null||placements.landmarks.Length!=3)throw new InvalidOperationException("Expected three authored wall landmarks.");
            foreach (string file in Directory.GetFiles(Art, "*.png"))
            {
                if (Path.GetFileNameWithoutExtension(file).StartsWith("surveyor-", StringComparison.Ordinal)) Sheet(file);
                else
                {
                    var placement=placements.landmarks.FirstOrDefault(p=>p.sprite==Path.GetFileNameWithoutExtension(file));
                    Vector2 pivot=placement!=null?new Vector2(placement.pivot_x,placement.pivot_y):file.Contains("terminal-")?new Vector2(.5f,.25f)
                        :file.EndsWith("reactor.png",StringComparison.Ordinal)?new Vector2(.5f,.44f):new Vector2(.5f,.5f);
                    Single(file,file.Contains("fold-")?320:file.Contains("mark-")?160:100,pivot);
                }
            }
            foreach (string file in Directory.GetFiles(Art,"*.wav"))
            {
                var importer=(AudioImporter)AssetImporter.GetAtPath(file);
                importer.forceToMono=true; importer.loadInBackground=false;
                var sample=importer.defaultSampleSettings; sample.loadType=AudioClipLoadType.DecompressOnLoad;
                sample.compressionFormat=AudioCompressionFormat.PCM; sample.sampleRateSetting=AudioSampleRateSetting.PreserveSampleRate;
                importer.defaultSampleSettings=sample; importer.SaveAndReimport();
            }
            var catalog=AssetDatabase.LoadAssetAtPath<AtmosphericArtCatalog>(CatalogPath);
            if (catalog==null) { catalog=ScriptableObject.CreateInstance<AtmosphericArtCatalog>(); AssetDatabase.CreateAsset(catalog,CatalogPath); }
            string materialPath="Assets/Art/Atmospheric/Palette.mat";
            var material=AssetDatabase.LoadAssetAtPath<Material>(materialPath);
            var shader=Shader.Find("Actor/Atmospheric Palette"); if(shader==null)throw new InvalidOperationException("Atmospheric palette shader missing.");
            if(material==null){material=new Material(shader);AssetDatabase.CreateAsset(material,materialPath);}else material.shader=shader;
            catalog.paletteMaterial=material; catalog.floor=Sprite("station-floor");catalog.wall=Sprite("wall");catalog.reactor=Sprite("reactor");catalog.serviceLight=Sprite("service-light");
            catalog.landmarks=placements.landmarks.Select(p=>new AtmosphericArtCatalog.Landmark{sprite=Sprite(p.sprite),world=new Vector2(p.x,p.y)}).ToArray();
            catalog.terminals=new[]{"meeting","task","records","storage","life-support"}.Select(x=>Sprite("terminal-"+x)).ToArray();
            catalog.folds=Enumerable.Range(0,3).Select(i=>Sprite("fold-"+i)).ToArray();
            catalog.identityMarks=Enumerable.Range(0,6).Select(i=>Sprite("mark-"+i)).ToArray();
            catalog.cues=CueNames.Select(x=>AssetDatabase.LoadAssetAtPath<AudioClip>(Art+x+".wav")).ToArray();
            var original=AssetDatabase.LoadAssetAtPath<RuntimeAnimatorController>("Assets/Animations/Characters/Character.controller");
            if(original==null)throw new InvalidOperationException("Original six-direction controller missing.");
            string controllerPath="Assets/Art/Atmospheric/Animation/Surveyor.overrideController";
            var controller=AssetDatabase.LoadAssetAtPath<AnimatorOverrideController>(controllerPath);
            if(controller==null){controller=new AnimatorOverrideController(original);AssetDatabase.CreateAsset(controller,controllerPath);} else controller.runtimeAnimatorController=original;
            var replacements=new List<KeyValuePair<AnimationClip,AnimationClip>>();
            foreach(string direction in Directions)
            {
                Sprite[] sprites=AssetDatabase.LoadAllAssetsAtPath(Art+"surveyor-"+direction.ToLowerInvariant()+".png").OfType<Sprite>().OrderBy(s=>s.name,StringComparer.Ordinal).ToArray();
                if(sprites.Length!=8)throw new InvalidOperationException("Expected eight surveyor poses for "+direction);
                foreach(bool walk in new[]{false,true})
                {
                    string name=(walk?"Walk_":"Idle_")+direction,path="Assets/Art/Atmospheric/Animation/"+name+".anim";
                    var clip=AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
                    if(clip==null){clip=new AnimationClip{name=name};AssetDatabase.CreateAsset(clip,path);}clip.frameRate=10;
                    var keys=walk?sprites.Select((sprite,i)=>new ObjectReferenceKeyframe{time=i*.1f,value=sprite}).ToList():new List<ObjectReferenceKeyframe>{new(){time=0,value=sprites[0]}};
                    keys.Add(new ObjectReferenceKeyframe{time=walk?.8f:1,value=sprites[0]});
                    AnimationUtility.SetObjectReferenceCurve(clip,new EditorCurveBinding{path="",type=typeof(SpriteRenderer),propertyName="m_Sprite"},keys.ToArray());
                    var settings=AnimationUtility.GetAnimationClipSettings(clip);settings.loopTime=true;AnimationUtility.SetAnimationClipSettings(clip,settings);EditorUtility.SetDirty(clip);
                    var old=original.animationClips.FirstOrDefault(x=>x.name==name);if(old==null)throw new InvalidOperationException("Missing original animation "+name);
                    replacements.Add(new KeyValuePair<AnimationClip,AnimationClip>(old,clip));
                }
            }
            controller.ApplyOverrides(replacements);catalog.characterController=controller;
            EditorUtility.SetDirty(material);EditorUtility.SetDirty(controller);EditorUtility.SetDirty(catalog);AssetDatabase.SaveAssets();
            ApplyPrefabCosmetics(catalog);
            Debug.Log("ACTOR Atmospheric art: 6 directions / 48 frames, 3 fold poses, 6 cosmetic marks, 11 original cues; original controller/prefab untouched.");
        }
        private static void ApplyPrefabCosmetics(AtmosphericArtCatalog catalog)
        {
            // MonoBehaviours only: NGO behaviour order, motor, capsule, original
            // offline prefab and the base Animator controller are untouched.
            foreach(string path in new[]{MultiplayerBuild.PlayerPrefab,MultiplayerBuild.BodyPrefab})
            {
                if(AssetDatabase.LoadAssetAtPath<GameObject>(path)==null)continue;
                var prefab=PrefabUtility.LoadPrefabContents(path);
                try
                {
                    if(path==MultiplayerBuild.PlayerPrefab)
                    {if(prefab.GetComponent<SocialAvatarArt>()==null)prefab.AddComponent<SocialAvatarArt>();}
                    else
                    {
                        if(prefab.GetComponent<SocialBodyArt>()==null)prefab.AddComponent<SocialBodyArt>();
                        var view=prefab.GetComponentInChildren<SpriteRenderer>();
                        view.sprite=catalog.folds[2];view.sharedMaterial=catalog.paletteMaterial;view.color=Color.white;view.sortingOrder=-1;
                        view.transform.localRotation=Quaternion.identity;view.transform.localPosition=Vector3.zero;view.transform.localScale=Vector3.one;
                    }
                    PrefabUtility.SaveAsPrefabAsset(prefab,path);
                }
                finally{PrefabUtility.UnloadPrefabContents(prefab);}
            }
        }
        private static Sprite Sprite(string name)=>AssetDatabase.LoadAssetAtPath<Sprite>(Art+name+".png")??throw new InvalidOperationException("Missing generated sprite "+name);
        private static void Common(TextureImporter importer,float ppu)
        {
            importer.textureType=TextureImporterType.Sprite;importer.spritePixelsPerUnit=ppu;importer.alphaIsTransparency=true;
            importer.mipmapEnabled=false;importer.filterMode=FilterMode.Bilinear;importer.textureCompression=TextureImporterCompression.Uncompressed;
            importer.maxTextureSize=2048;importer.wrapMode=TextureWrapMode.Clamp;
        }
        private static void Single(string file,float ppu,Vector2 pivot)
        {
            var importer=(TextureImporter)AssetImporter.GetAtPath(file);Common(importer,ppu);importer.spriteImportMode=SpriteImportMode.Single;
            var settings=new TextureImporterSettings();importer.ReadTextureSettings(settings);settings.spriteAlignment=(int)SpriteAlignment.Custom;settings.spritePivot=pivot;settings.spriteMeshType=SpriteMeshType.FullRect;importer.SetTextureSettings(settings);importer.SaveAndReimport();
        }
        private static void Sheet(string file)
        {
            var importer=(TextureImporter)AssetImporter.GetAtPath(file);Common(importer,320);importer.spriteImportMode=SpriteImportMode.Multiple;
#pragma warning disable CS0618
            importer.spritesheet=Enumerable.Range(0,8).Select(i=>new SpriteMetaData{name=Path.GetFileNameWithoutExtension(file)+"_"+i.ToString("D2"),rect=new Rect(i*240,0,240,350),alignment=(int)SpriteAlignment.Custom,pivot=new Vector2(.5f,8f/350)}).ToArray();
#pragma warning restore CS0618
            var settings=new TextureImporterSettings();importer.ReadTextureSettings(settings);settings.spriteMeshType=SpriteMeshType.FullRect;importer.SetTextureSettings(settings);
            importer.SaveAndReimport();
        }
    }
}
