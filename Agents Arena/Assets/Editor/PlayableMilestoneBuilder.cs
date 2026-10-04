using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Actor.Characters;
using Actor.Presentation;
using Actor.World;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEditor.U2D.Sprites;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.Rendering.Universal;

namespace Actor.Editor
{
    public static class PlayableMilestoneBuilder
    {
        public const string ScenePath = "Assets/Scenes/MainHall.unity";
        public const string CharacterPath = "Assets/Prefabs/Characters/ActorCharacter.prefab";
        public const string CrystalPath = "Assets/Prefabs/World/Crystal.prefab";
        private const string Art = "Assets/Sprites/";

        [Serializable] private class Layout { public Sheet[] sheets; }
        [Serializable] private class Sheet { public string file; public Frame[] frames; }
        [Serializable] private class Frame { public int x, y, width, height; }

        // Authored along the floor/wall seam in the original 1254 x 1254 image (top-left origin).
        public static readonly Vector2[] FloorPixels =
        {
            new Vector2(585, 76), new Vector2(668, 76), new Vector2(668, 318),
            new Vector2(708, 356), new Vector2(794, 425), new Vector2(837, 425),
            new Vector2(913, 497), new Vector2(913, 537), new Vector2(990, 602),
            new Vector2(1038, 602), new Vector2(1038, 578), new Vector2(1216, 578),
            new Vector2(1216, 662), new Vector2(1038, 662), new Vector2(990, 688),
            new Vector2(948, 731), new Vector2(945, 787), new Vector2(862, 870),
            new Vector2(807, 870), new Vector2(706, 941), new Vector2(668, 957),
            new Vector2(668, 1148), new Vector2(584, 1148), new Vector2(584, 957),
            new Vector2(546, 939), new Vector2(447, 870), new Vector2(399, 870),
            new Vector2(312, 786), new Vector2(307, 735), new Vector2(263, 688),
            new Vector2(216, 661), new Vector2(38, 661), new Vector2(38, 579),
            new Vector2(216, 579), new Vector2(216, 601), new Vector2(266, 573),
            new Vector2(324, 539), new Vector2(337, 505), new Vector2(411, 425),
            new Vector2(458, 425), new Vector2(540, 356), new Vector2(584, 318)
        };
        public static Vector2 PixelToWorld(Vector2 p) => new Vector2((p.x - 627f) / 100f, (627f - p.y) / 100f);

        [MenuItem("ACTOR/Build first playable Main Hall")]
        public static void Build()
        {
            if (EditorApplication.isPlaying) throw new InvalidOperationException("Exit Play Mode before building.");
            foreach (string path in new[] { "Assets/Animations/Characters", "Assets/Materials", "Assets/Prefabs/Characters", "Assets/Prefabs/World" })
                Directory.CreateDirectory(path);
            AssetDatabase.Refresh();
            SliceCharacters();
            ImportSingle("main_hall_sprite.png", 100, new Vector2(0.5f, 0.5f));
            ImportSingle("main_hall_crystall_sprite.png", 700, new Vector2(0.515f, 0.08f));
            ImportSingle("map.png", 100, new Vector2(0.5f, 0.5f));
            var lit = Material("SpriteLit", "Universal Render Pipeline/2D/Sprite-Lit-Default");
            var unlit = Material("CrystalGlow", "Universal Render Pipeline/2D/Sprite-Unlit-Default");
            var contact = AssetDatabase.LoadAssetAtPath<PhysicsMaterial2D>("Assets/Materials/CharacterContact.physicsMaterial2D");
            if (contact == null)
            {
                contact = new PhysicsMaterial2D("CharacterContact") { friction = 0, bounciness = 0 };
                AssetDatabase.CreateAsset(contact, "Assets/Materials/CharacterContact.physicsMaterial2D");
            }
            AnimatorController controller = BuildAnimations();
            BuildCharacter(controller, lit, contact);
            BuildCrystal(unlit);
            BuildScene(lit);
            AssetDatabase.SaveAssets();
            Debug.Log("ACTOR: MainHall, directional clips and reusable prefabs built successfully.");
        }

        private static void ConfigureTexture(TextureImporter importer, int ppu)
        {
            importer.textureType = TextureImporterType.Sprite;
            importer.spritePixelsPerUnit = ppu;
            importer.filterMode = FilterMode.Bilinear;
            importer.textureCompression = TextureImporterCompression.Uncompressed;
            importer.mipmapEnabled = false;
            importer.alphaIsTransparency = true;
            importer.wrapMode = TextureWrapMode.Clamp;
            importer.maxTextureSize = 2048;
            importer.spriteImportMode = SpriteImportMode.Multiple;
            var settings = new TextureImporterSettings();
            importer.ReadTextureSettings(settings);
            settings.spriteMeshType = SpriteMeshType.FullRect;
            settings.spriteGenerateFallbackPhysicsShape = false;
            importer.SetTextureSettings(settings);
            foreach (string platform in new[] { "Standalone", "Android", "iPhone" })
                importer.ClearPlatformTextureSettings(platform);
        }

        private static void SliceCharacters()
        {
            Layout layout = JsonUtility.FromJson<Layout>(File.ReadAllText("Assets/Editor/CharacterSliceLayout.json"));
            var factories = new SpriteDataProviderFactories();
            factories.Init();
            foreach (Sheet sheet in layout.sheets)
            {
                var importer = (TextureImporter)AssetImporter.GetAtPath(Art + sheet.file);
                ConfigureTexture(importer, 320);
                var provider = factories.GetSpriteEditorDataProviderFromObject(importer);
                provider.InitSpriteEditorDataProvider();
                var old = provider.GetSpriteRects().ToDictionary(r => r.name, r => r.spriteID);
                var rects = sheet.frames.Select((frame, i) =>
                {
                    string name = Path.GetFileNameWithoutExtension(sheet.file) + "_frame_" + i.ToString("D2");
                    return new SpriteRect
                    {
                        name = name, spriteID = old.TryGetValue(name, out var id) ? id : GUID.Generate(),
                        rect = new Rect(frame.x, frame.y, frame.width, frame.height),
                        alignment = SpriteAlignment.Custom, pivot = new Vector2(0.5f, 8f / 350f)
                    };
                }).ToArray();
                provider.SetSpriteRects(rects);
                provider.GetDataProvider<ISpriteNameFileIdDataProvider>().SetNameFileIdPairs(
                    rects.Select(r => new SpriteNameFileIdPair(r.name, r.spriteID)));
                provider.Apply();
                importer.SaveAndReimport();
            }
        }

        private static void ImportSingle(string file, int ppu, Vector2 pivot)
        {
            var importer = (TextureImporter)AssetImporter.GetAtPath(Art + file);
            ConfigureTexture(importer, ppu);
            importer.spriteImportMode = SpriteImportMode.Single;
            var settings = new TextureImporterSettings();
            importer.ReadTextureSettings(settings);
            settings.spriteAlignment = (int)SpriteAlignment.Custom;
            settings.spritePivot = pivot;
            importer.SetTextureSettings(settings);
            importer.SaveAndReimport();
        }

        private static Material Material(string name, string shader)
        {
            string path = "Assets/Materials/" + name + ".mat";
            var material = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (material != null) return material;
            material = new Material(Shader.Find(shader)) { name = name };
            AssetDatabase.CreateAsset(material, path);
            return material;
        }

        private static AnimatorController BuildAnimations()
        {
            string path = "Assets/Animations/Characters/Character.controller";
            var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(path);
            if (controller == null) controller = AnimatorController.CreateAnimatorControllerAtPath(path);
            var machine = controller.layers[0].stateMachine;
            foreach (var state in machine.states) machine.RemoveState(state.state);
            var directions = new Dictionary<string, string>
            {
                { "Down", "bottom" }, { "Up", "top" }, { "Left", "left" }, { "Right", "right" },
                { "UpRight", "right_top" }, { "DownRight", "right_bottom" }
            };
            int row = 0;
            foreach (var pair in directions)
            {
                Sprite[] sprites = AssetDatabase.LoadAllAssetsAtPath(Art + "hero_sprites_" + pair.Value + ".png")
                    .OfType<Sprite>().OrderBy(s => s.name).ToArray();
                if (sprites.Length != 8) throw new InvalidOperationException("Expected 8 complete poses for " + pair.Key);
                foreach (bool walking in new[] { false, true })
                {
                    string name = (walking ? "Walk_" : "Idle_") + pair.Key;
                    string clipPath = "Assets/Animations/Characters/" + name + ".anim";
                    var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath);
                    if (clip == null)
                    {
                        clip = new AnimationClip { name = name };
                        AssetDatabase.CreateAsset(clip, clipPath);
                    }
                    clip.frameRate = 10;
                    var keys = walking
                        ? sprites.Select((sprite, i) => new ObjectReferenceKeyframe { time = i / 10f, value = sprite }).ToList()
                        : new List<ObjectReferenceKeyframe> { new ObjectReferenceKeyframe { time = 0, value = sprites[1] } };
                    keys.Add(new ObjectReferenceKeyframe { time = walking ? 0.8f : 1f, value = walking ? sprites[0] : sprites[1] });
                    AnimationUtility.SetObjectReferenceCurve(clip, new EditorCurveBinding
                    {
                        path = "", type = typeof(SpriteRenderer), propertyName = "m_Sprite"
                    }, keys.ToArray());
                    var settings = AnimationUtility.GetAnimationClipSettings(clip);
                    settings.loopTime = true;
                    AnimationUtility.SetAnimationClipSettings(clip, settings);
                    var state = machine.AddState(name, new Vector3(walking ? 360 : 80, row * 75));
                    state.motion = clip;
                    state.writeDefaultValues = false;
                    if (name == "Idle_Down") machine.defaultState = state;
                    EditorUtility.SetDirty(clip);
                }
                row++;
            }
            EditorUtility.SetDirty(controller);
            return controller;
        }

        private static void Set(UnityEngine.Object target, string name, UnityEngine.Object value)
        {
            var so = new SerializedObject(target);
            so.FindProperty(name).objectReferenceValue = value;
            so.ApplyModifiedPropertiesWithoutUndo();
        }
        private static void SetString(UnityEngine.Object target, string name, string value)
        {
            var so = new SerializedObject(target);
            so.FindProperty(name).stringValue = value;
            so.ApplyModifiedPropertiesWithoutUndo();
        }

        private static void BuildCharacter(AnimatorController controller, Material lit, PhysicsMaterial2D contact)
        {
            var root = new GameObject("ActorCharacter");
            var body = root.AddComponent<Rigidbody2D>();
            body.gravityScale = 0;
            body.constraints = RigidbodyConstraints2D.FreezeRotation;
            body.interpolation = RigidbodyInterpolation2D.Interpolate;
            body.collisionDetectionMode = CollisionDetectionMode2D.Continuous;
            body.sharedMaterial = contact;
            var feet = root.AddComponent<CapsuleCollider2D>();
            feet.direction = CapsuleDirection2D.Horizontal;
            feet.size = new Vector2(0.34f, 0.18f);
            feet.offset = new Vector2(0, 0.09f);
            var motor = root.AddComponent<CharacterMotor2D>();
            var input = root.AddComponent<PlayerInputSource>();
            Set(input, "actions", AssetDatabase.LoadAssetAtPath<InputActionAsset>("Assets/Settings/InputSystem_Actions.inputactions"));
            var actor = root.AddComponent<ActorCharacter>();
            Set(actor, "inputSource", input);
            var visual = new GameObject("Visual");
            visual.transform.SetParent(root.transform, false);
            var renderer = visual.AddComponent<SpriteRenderer>();
            renderer.sprite = AssetDatabase.LoadAllAssetsAtPath(Art + "hero_sprites_bottom.png").OfType<Sprite>()
                .OrderBy(s => s.name).ElementAt(1);
            renderer.sharedMaterial = lit;
            renderer.spriteSortPoint = SpriteSortPoint.Pivot;
            var animator = visual.AddComponent<Animator>();
            animator.runtimeAnimatorController = controller;
            animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            Set(visual.AddComponent<CharacterAnimationView>(), "motor", motor);
            PrefabUtility.SaveAsPrefabAsset(root, CharacterPath);
            UnityEngine.Object.DestroyImmediate(root);
        }

        private static void BuildCrystal(Material unlit)
        {
            var root = new GameObject("Crystal");
            var entity = root.AddComponent<WorldEntity>();
            var renderer = root.AddComponent<SpriteRenderer>();
            renderer.sprite = AssetDatabase.LoadAssetAtPath<Sprite>(Art + "main_hall_crystall_sprite.png");
            renderer.sharedMaterial = unlit;
            renderer.spriteSortPoint = SpriteSortPoint.Pivot;
            var collider = root.AddComponent<CapsuleCollider2D>();
            collider.direction = CapsuleDirection2D.Horizontal;
            collider.size = new Vector2(0.95f, 0.35f);
            collider.offset = new Vector2(0, 0.1f);
            var glow = new GameObject("Crystal Light");
            glow.transform.SetParent(root.transform, false);
            glow.transform.localPosition = new Vector3(0, 0.55f, 0);
            var light = glow.AddComponent<Light2D>();
            light.lightType = Light2D.LightType.Point;
            light.color = new Color(0.55f, 0.75f, 1f);
            light.intensity = 0.4f;
            light.blendStyleIndex = 1;
            light.pointLightInnerRadius = 0.35f;
            light.pointLightOuterRadius = 2.3f;
            PrefabUtility.SaveAsPrefabAsset(root, CrystalPath);
            UnityEngine.Object.DestroyImmediate(root);
        }

        private static void BuildScene(Material lit)
        {
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var hall = new GameObject("Main Hall");
            var render = hall.AddComponent<SpriteRenderer>();
            render.sprite = AssetDatabase.LoadAssetAtPath<Sprite>(Art + "main_hall_sprite.png");
            render.sharedMaterial = lit;
            render.sortingOrder = -100;
            var collisions = new GameObject("EnvironmentCollision");
            collisions.transform.SetParent(hall.transform, false);
            for (int i = 0; i < FloorPixels.Length; i++)
            {
                Vector2 a = PixelToWorld(FloorPixels[i]);
                Vector2 b = PixelToWorld(FloorPixels[(i + 1) % FloorPixels.Length]);
                Vector2 delta = b - a;
                Vector2 outward = new Vector2(-delta.y, delta.x).normalized;
                var wall = new GameObject("Wall " + i.ToString("D2"));
                wall.transform.SetParent(collisions.transform, false);
                wall.transform.position = (a + b) * 0.5f + outward * 0.08f;
                wall.transform.rotation = Quaternion.Euler(0, 0, Mathf.Atan2(delta.y, delta.x) * Mathf.Rad2Deg);
                var collider = wall.AddComponent<BoxCollider2D>();
                collider.size = new Vector2(delta.magnitude + 0.12f, 0.16f);
            }
            var crystal = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(CrystalPath));
            crystal.transform.position = new Vector3(0, 0.2f, 0);
            SetString(crystal.GetComponent<WorldEntity>(), "entityId", "main-hall-crystal-01");
            SetString(crystal.GetComponent<WorldEntity>(), "roomId", "main-hall");
            var character = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(CharacterPath));
            character.name = "Local Character";
            character.transform.position = new Vector3(-0.9f, -1.25f, 0);
            SetString(character.GetComponent<ActorCharacter>(), "actorId", "main-hall-local-01");
            SetString(character.GetComponent<ActorCharacter>(), "initialRoomId", "main-hall");
            PrefabUtility.RecordPrefabInstancePropertyModifications(character.GetComponent<ActorCharacter>());
            PrefabUtility.RecordPrefabInstancePropertyModifications(crystal.GetComponent<WorldEntity>());
            var cameraRoot = new GameObject("Main Camera") { tag = "MainCamera" };
            cameraRoot.transform.position = new Vector3(0, -0.8f, -10);
            var camera = cameraRoot.AddComponent<Camera>();
            camera.orthographic = true;
            camera.orthographicSize = 4.1f;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.035f, 0.045f, 0.075f);
            camera.nearClipPlane = 0.1f;
            camera.farClipPlane = 50;
            camera.allowMSAA = false;
            camera.transparencySortMode = UnityEngine.TransparencySortMode.CustomAxis;
            camera.transparencySortAxis = Vector3.up;
            cameraRoot.AddComponent<UniversalAdditionalCameraData>();
            cameraRoot.AddComponent<AudioListener>();
            Set(cameraRoot.AddComponent<CameraFollow2D>(), "target", character.transform);
            var global = new GameObject("Global Light 2D").AddComponent<Light2D>();
            global.lightType = Light2D.LightType.Global;
            global.color = new Color(0.97f, 0.98f, 1f);
            global.intensity = 0.95f;
            EditorSceneManager.SaveScene(scene, ScenePath);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
            Selection.activeGameObject = character;
            SceneView.lastActiveSceneView?.Frame(new Bounds(Vector3.zero, new Vector3(12.5f, 12.5f, 0)), false);
        }
    }
}
