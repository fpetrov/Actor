using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Actor.Presentation;
using Actor.World;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Actor.Editor
{
    public static class EngineeringRoomBuilder
    {
        public const string SpritePath = "Assets/Sprites/Rooms/Engineering.asset";
        private const string MapPath = "Assets/Sprites/map.png";
        private const float PixelsPerUnit = 50f;
        private static readonly Vector2 doorwayPixel = new(723, 734);
        private static readonly Vector2 doorwayWorld = new(0, -5.21f);
        public static readonly Vector2 WorldMinimum = new(-4.16f, -12.25f);
        public static readonly Vector2 WorldMaximum = new(4.16f, 2.27f);

        // All three visual pieces share the original atlas, scale and pixel-to-world
        // transform. Mesh outlines hide neighboring rooms without editing source PNGs.
        public static readonly Vector2[] MainHallFloorPixels =
        {
            new(686,382),new(698,375),new(747,375),new(762,383),new(802,389),
            new(817,386),new(851,386),new(873,393),new(888,411),new(892,446),
            new(903,457),new(903,506),new(892,517),new(892,540),new(882,559),
            new(867,573),new(846,584),new(823,589),new(785,589),new(771,596),
            new(757,608),new(747,633),new(757,649),new(763,671),new(760,710),
            new(747,729),new(747,734),new(699,734),new(699,729),
            new(685,710),new(682,676),new(690,651),new(699,633),new(691,610),
            new(677,597),new(659,589),new(622,589),new(598,585),new(577,572),
            new(560,550),new(554,531),new(554,516),new(542,503),new(542,459),
            new(554,445),new(554,415),new(558,405),new(573,391),new(591,386),
            new(627,386),new(648,389),new(683,383)
        };
        public static readonly Vector2[] FloorPixels =
        {
            new(699,734),new(747,734),new(747,782),new(775,792),
            new(809,820),new(835,846),new(837,873),new(818,903),new(789,931),
            new(750,947),new(738,960),new(707,960),new(695,947),new(650,931),
            new(623,903),new(606,876),new(606,847),new(631,821),new(662,793),new(699,782)
        };
        private static readonly Vector2[] HallVisualOutline =
        {
            new(585,360),new(660,360),new(671,373),new(689,373),new(690,360),
            new(755,360),new(756,373),new(773,374),new(803,360),new(863,360),
            new(884,371),new(905,396),new(918,423),new(931,429),new(931,536),
            new(908,548),new(891,582),new(865,608),new(825,622),new(792,638),
            new(654,638),new(629,621),new(586,605),new(557,581),new(539,548),
            new(515,534),new(515,431),new(534,425),new(540,400),new(559,379)
        };
        private static readonly Vector2[] EngineeringVisualOutline =
        {
            new(679,725),new(755,725),new(776,727),new(800,736),new(821,749),
            new(842,764),new(863,782),new(881,802),new(896,828),new(907,860),
            new(911,910),new(911,1086),new(544,1086),new(544,910),new(549,860),
            new(557,826),new(571,800),new(590,777),new(610,759),new(633,744),new(655,732)
        };

        public static Vector2 PixelToWorld(Vector2 sourcePixel)
            => doorwayWorld + new Vector2(sourcePixel.x - doorwayPixel.x, doorwayPixel.y - sourcePixel.y) / PixelsPerUnit;

        public static void ShowPassagePreview()
        {
            if (Application.isPlaying) throw new InvalidOperationException("Preview the world outside Play Mode.");
            EditorSceneManager.OpenScene(MultiplayerBuild.GameplayScene);
            var camera=UnityEngine.Object.FindObjectsByType<Camera>().Single();
            camera.transform.position=new Vector3(0,-5.3f,-10);
            camera.orthographicSize=3.4f;
            UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
        }

        public static void AddToCurrentMultiplayerScene()
        {
            if (Application.isPlaying) throw new InvalidOperationException("Prepare the world outside Play Mode.");
            var hall = GameObject.Find("Main Hall");
            if (hall == null) throw new InvalidOperationException("The authored Main Hall is required.");
            var originalCollisions = hall.transform.Find("EnvironmentCollision");
            if (originalCollisions == null) throw new InvalidOperationException("Main Hall collision root is missing.");
            UnityEngine.Object.DestroyImmediate(originalCollisions.gameObject);
            var hallRenderer = hall.GetComponent<SpriteRenderer>();
            var material = hallRenderer.sharedMaterial;
            hallRenderer.enabled = false;
            CreateVisual(hall.transform,"Visual", "Assets/Sprites/Rooms/MainHallAtlas.asset",
                new Rect(515,360,416,278),HallVisualOutline, material);
            CreateVisual(hall.transform,"South Connector", "Assets/Sprites/Rooms/SouthConnector.asset",
                new Rect(662,628,119,107),null,material);
            Vector2[] hallFloor = MainHallFloorPixels.Select(PixelToWorld).ToArray();
            // Segment 26 is the common open room boundary at y=-5.21.
            BuildWalls(hall.transform,hallFloor,26);
            CreateVolume(hall.transform,RoomId.MainHall,hallFloor);

            var engineering = new GameObject("Engineering");
            CreateVisual(engineering.transform,"Visual",SpritePath,new Rect(544,725,367,361),EngineeringVisualOutline,material);
            Vector2[] floor = FloorPixels.Select(PixelToWorld).ToArray();
            var collisions = BuildWalls(engineering.transform,floor,0);
            var housing = new GameObject("Reactor Housing");
            housing.transform.SetParent(collisions,false);
            // The glowing upper reactor disk is painted above its lower housing base.
            // Keep its collision footprint at the base so a full-height authored
            // capsule can leave the doorway and walk around either side.
            housing.transform.position = PixelToWorld(new Vector2(724,871));
            housing.AddComponent<CircleCollider2D>().radius = 0.6f;
            var serialized = new SerializedObject(housing.AddComponent<WorldEntity>());
            serialized.FindProperty("entityId").stringValue = "engineering-reactor-01";
            serialized.FindProperty("roomId").stringValue = RoomId.Engineering;
            serialized.ApplyModifiedPropertiesWithoutUndo();
            CreateVolume(engineering.transform,RoomId.Engineering,floor);
            AddGate(hall.transform,new Vector2(722.5f,375),49,0);
            AddGate(hall.transform,new Vector2(609,386),36,0);
            AddGate(hall.transform,new Vector2(834,386),34,0);
            AddGate(hall.transform,new Vector2(542,481),44,90);
            AddGate(hall.transform,new Vector2(903,481),45,90);
            AddGate(hall.transform,new Vector2(682.8f,692),35,90);
            AddGate(hall.transform,new Vector2(761.5f,692),35,90);
            AddGate(engineering.transform,new Vector2(722.5f,960),31,0);
            var signage = new GameObject("Room Wayfinding").AddComponent<RoomWayfinding2D>();
            signage.Configure(new[]
            {
                new RoomWayfinding2D.Marker { world = new Vector2(-1.9f,1.45f), text = "MAIN HALL" },
                new RoomWayfinding2D.Marker { world = new Vector2(0,-2.65f), text = "ENGINEERING  ↓" },
                new RoomWayfinding2D.Marker { world = new Vector2(0,-6.5f), text = "↑  MAIN HALL" },
                new RoomWayfinding2D.Marker { world = new Vector2(0,-9.6f), text = "ENGINEERING" }
            });
            foreach (var camera in UnityEngine.Object.FindObjectsByType<CameraFollow2D>())
            {
                camera.SetWorldBounds(WorldMinimum,WorldMaximum);
                camera.GetComponent<Camera>().orthographicSize = 3.4f;
            }
            ValidateCurrentMultiplayerScene();
        }

        private static Transform BuildWalls(Transform parent,Vector2[] floor,int openSegment)
        {
            var root = new GameObject("EnvironmentCollision");
            root.transform.SetParent(parent,false);
            for (int i=0;i<floor.Length;i++)
            {
                if (i == openSegment) continue;
                Vector2 a=floor[i], b=floor[(i+1)%floor.Length], delta=b-a;
                Vector2 outward=new Vector2(-delta.y,delta.x).normalized;
                var wall=new GameObject("Wall " + i.ToString("D2"));
                wall.transform.SetParent(root.transform,false);
                wall.transform.position=(a+b)*0.5f+outward*0.08f;
                wall.transform.rotation=Quaternion.Euler(0,0,Mathf.Atan2(delta.y,delta.x)*Mathf.Rad2Deg);
                wall.AddComponent<BoxCollider2D>().size=new Vector2(delta.magnitude+0.08f,0.16f);
            }
            return root.transform;
        }

        private static void CreateVisual(Transform parent,string name,string path,Rect sourceTopLeft,
            Vector2[] outline,Material material)
        {
            var root=new GameObject(name);
            root.transform.SetParent(parent,false);
            root.transform.position=PixelToWorld(sourceTopLeft.center);
            var renderer=root.AddComponent<SpriteRenderer>();
            renderer.sprite=GetAtlasSprite(path,sourceTopLeft,outline);
            renderer.sharedMaterial=material;
            renderer.sortingOrder=-100;
            if(outline != null)
            {
                renderer.maskInteraction=SpriteMaskInteraction.VisibleInsideMask;
                var mask=root.AddComponent<SpriteMask>();
                mask.sprite=GetSilhouetteMask(path,sourceTopLeft,outline);
                mask.isCustomRangeActive=true;
                mask.frontSortingOrder=-99;
                mask.backSortingOrder=-101;
            }
        }

        private static Sprite GetSilhouetteMask(string spritePath,Rect source,Vector2[] outline)
        {
            string basePath=Path.ChangeExtension(spritePath,null)+"Mask";
            string texturePath=basePath+"Texture.asset",maskPath=basePath+".asset";
            int width=(int)source.width,height=(int)source.height;
            var texture=AssetDatabase.LoadAssetAtPath<Texture2D>(texturePath);
            if(texture==null)
            {
                texture=new Texture2D(width,height,TextureFormat.RGBA32,false) {name=Path.GetFileNameWithoutExtension(texturePath),filterMode=FilterMode.Bilinear};
                AssetDatabase.CreateAsset(texture,texturePath);
            }
            texture.wrapMode=TextureWrapMode.Clamp;
            var pixels=new Color32[width*height];
            for(int y=0;y<height;y++)
                for(int x=0;x<width;x++)
                {
                    Vector2 p=new Vector2(source.x+x+.5f,source.yMax-y-.5f);
                    bool inside=false;
                    for(int i=0,j=outline.Length-1;i<outline.Length;j=i++)
                    {
                        var a=outline[i];var b=outline[j];
                        if((a.y>p.y)!=(b.y>p.y)&&p.x<(b.x-a.x)*(p.y-a.y)/(b.y-a.y)+a.x) inside=!inside;
                    }
                    pixels[y*width+x]=new Color32(255,255,255,inside?(byte)255:(byte)0);
                }
            texture.SetPixels32(pixels);texture.Apply();EditorUtility.SetDirty(texture);
            var existing=AssetDatabase.LoadAssetAtPath<Sprite>(maskPath);
            if(existing!=null)return existing;
            var sprite=Sprite.Create(texture,new Rect(0,0,width,height),new Vector2(.5f,.5f),PixelsPerUnit,0,SpriteMeshType.FullRect);
            sprite.name=Path.GetFileNameWithoutExtension(maskPath);
            AssetDatabase.CreateAsset(sprite,maskPath);
            return sprite;
        }

        private static Sprite GetAtlasSprite(string path,Rect sourceTopLeft,Vector2[] outline)
        {
            var texture=AssetDatabase.LoadAssetAtPath<Texture2D>(MapPath);
            if (texture == null || texture.width != 1448 || texture.height != 1086)
                throw new InvalidOperationException("The original 1448x1086 atlas is required.");
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            AssetDatabase.Refresh();
            var rect=new Rect(sourceTopLeft.x,texture.height-sourceTopLeft.yMax,sourceTopLeft.width,sourceTopLeft.height);
            var sprite=Sprite.Create(texture,rect,new Vector2(.5f,.5f),PixelsPerUnit,0,SpriteMeshType.FullRect,Vector4.zero,false);
            sprite.name=Path.GetFileNameWithoutExtension(path);
            var existing=AssetDatabase.LoadAssetAtPath<Sprite>(path);
            if (existing != null)
            {
                EditorUtility.CopySerialized(sprite,existing);
                UnityEngine.Object.DestroyImmediate(sprite);
                EditorUtility.SetDirty(existing);
                return existing;
            }
            AssetDatabase.CreateAsset(sprite,path);
            return sprite;
        }

        private static void AddGate(Transform parent,Vector2 pixel,float lengthPixels,float rotation)
        {
            const string texturePath="Assets/Sprites/Rooms/GatePanelTexture.asset";
            const string spritePath="Assets/Sprites/Rooms/GatePanel.asset";
            var sprite=AssetDatabase.LoadAssetAtPath<Sprite>(spritePath);
            if(sprite == null)
            {
                var texture=new Texture2D(1,1,TextureFormat.RGBA32,false) { name="Gate Panel",filterMode=FilterMode.Point };
                texture.SetPixel(0,0,Color.white);texture.Apply();
                AssetDatabase.CreateAsset(texture,texturePath);
                sprite=Sprite.Create(texture,new Rect(0,0,1,1),new Vector2(.5f,.5f),1);
                sprite.name="Gate Panel";AssetDatabase.CreateAsset(sprite,spritePath);
            }
            var gate=new GameObject("Sealed Gate");
            gate.transform.SetParent(parent,false);
            gate.transform.position=PixelToWorld(pixel);
            gate.transform.rotation=Quaternion.Euler(0,0,rotation);
            var panel=gate.AddComponent<SpriteRenderer>();
            panel.sprite=sprite;panel.sortingOrder=-75;panel.color=new Color(.10f,.13f,.19f);
            gate.transform.localScale=new Vector3(lengthPixels/PixelsPerUnit+.12f,.16f,1);
            var accent=new GameObject("Amber Seal");accent.transform.SetParent(gate.transform,false);
            accent.transform.localScale=new Vector3(.7f,.18f,1);
            var line=accent.AddComponent<SpriteRenderer>();line.sprite=sprite;line.sortingOrder=-74;
            line.color=new Color(.92f,.66f,.32f);
        }

        private static void CreateVolume(Transform parent,string id,Vector2[] floor)
        {
            var root=new GameObject("Room Volume");root.transform.SetParent(parent,false);
            root.AddComponent<PolygonCollider2D>();root.AddComponent<RoomVolume2D>().Configure(id,floor);
        }

        public static void ValidateCurrentMultiplayerScene()
        {
            Physics2D.SyncTransforms();
            var checks=new List<string>();
            var rooms=UnityEngine.Object.FindObjectsByType<RoomVolume2D>();
            Require(rooms.Length==2,"Two distinct room volumes",checks);
            var hall=rooms.Single(r=>r.Id==RoomId.MainHall);
            var engineering=rooms.Single(r=>r.Id==RoomId.Engineering);
            Require(hall.Contains(new Vector2(0,-.94f)),"Main Hall contains the route start",checks);
            Require(engineering.Contains(new Vector2(0,-6.54f)),"Engineering contains the route endpoint",checks);
            Require(hall.Contains(new Vector2(0,-5.20f))&&!engineering.Contains(new Vector2(0,-5.20f)),"North boundary belongs to Main Hall",checks);
            Require(engineering.Contains(new Vector2(0,-5.22f))&&!hall.Contains(new Vector2(0,-5.22f)),"South boundary belongs to Engineering",checks);
            var player=AssetDatabase.LoadAssetAtPath<GameObject>(MultiplayerBuild.PlayerPrefab);
            var capsule=player.GetComponent<CapsuleCollider2D>();
            var filter=new ContactFilter2D {useTriggers=false};filter.SetLayerMask(Physics2D.GetLayerCollisionMask(player.layer));
            var hits=new RaycastHit2D[64];
            foreach(float x in new[]{-.3f,-.2f,-.12f,0,.12f,.2f,.3f})
            {
                Vector2 start=new Vector2(x,-1.4f)+capsule.offset,end=new Vector2(x,-7)+capsule.offset;
                Require(Physics2D.CapsuleCast(start,capsule.size,capsule.direction,0,Vector2.down,filter,hits,5.603f)==0,
                    "South passage sweep x="+x,checks);
                Require(Physics2D.CapsuleCast(end,capsule.size,capsule.direction,0,Vector2.up,filter,hits,5.603f)==0,
                    "North passage sweep x="+x,checks);
            }
            foreach(float side in new[]{-1f,1f})
            {
                Vector2 a=new Vector2(side*1.8f,-1.4f)+capsule.offset,b=new Vector2(0,-3.3f)+capsule.offset;
                Vector2 delta=b-a;
                Require(Physics2D.CapsuleCast(a,capsule.size,capsule.direction,0,delta.normalized,filter,hits,delta.magnitude+.003f)==0,
                    "Diagonal hall approach from "+side,checks);
                Require(Physics2D.CapsuleCast(b,capsule.size,capsule.direction,0,-delta.normalized,filter,hits,delta.magnitude+.003f)==0,
                    "Diagonal hall return to "+side,checks);
            }
            for(int slot=0;slot<16;slot++)
            {
                Vector2 center=(Vector2)Actor.Networking.MatchSession.SpawnPosition(slot)+capsule.offset;
                Require(hall.Contains(center)&&Physics2D.OverlapCapsule(center,capsule.size,capsule.direction,0,filter,new Collider2D[16])==0,
                    "Authored spawn slot "+slot+" lies on free Main Hall floor",checks);
            }
            Require(Physics2D.CapsuleCast(new Vector2(0,-6.54f),capsule.size,capsule.direction,0,Vector2.right,filter,hits,10)>0,
                "Engineering walls block leaving the room",checks);
            Require(Physics2D.CapsuleCast(new Vector2(0,-6.54f),capsule.size,capsule.direction,0,Vector2.down,filter,hits,10)>0,
                "Reactor housing remains solid",checks);
            VerifyPhysicalTraversals(capsule,checks);
            var report=new GeometryReport {passed=true,checks=checks.ToArray(),roomBoundaryCenterY=-5.21f,
                route=new[]{new Vector2(0,-1.4f),new Vector2(0,-4.7f),new Vector2(0,-6),new Vector2(0,-7)},
                capsuleSize=capsule.size,capsuleOffset=capsule.offset};
            string output=Path.GetFullPath("../build/world-polish/geometry-validation.json");
            Directory.CreateDirectory(Path.GetDirectoryName(output));File.WriteAllText(output,JsonUtility.ToJson(report,true));
            Debug.Log("ACTOR: unified world geometry passed "+checks.Count+" checks. "+output);
        }

        private static void VerifyPhysicalTraversals(CapsuleCollider2D source,List<string> checks)
        {
            var originalMode=Physics2D.simulationMode;
            var root=new GameObject("Temporary Geometry Physics Probe");
            var body=root.AddComponent<Rigidbody2D>();body.gravityScale=0;body.constraints=RigidbodyConstraints2D.FreezeRotation;
            body.collisionDetectionMode=CollisionDetectionMode2D.Continuous;
            body.sharedMaterial=source.GetComponent<Rigidbody2D>().sharedMaterial;
            var capsule=root.AddComponent<CapsuleCollider2D>();capsule.size=source.size;capsule.offset=source.offset;capsule.direction=source.direction;
            try
            {
                Physics2D.simulationMode=SimulationMode2D.Script;
                foreach(float x in new[]{-.3f,-.2f,0,.2f,.3f})
                {
                    body.position=new Vector2(x,-1.4f);body.linearVelocity=Vector2.zero;Physics2D.SyncTransforms();
                    PhysicalWaypoint(body,new Vector2(x,-7));
                    Require(Vector2.Distance(body.position,new Vector2(x,-7))<.08f,"Actual physics reaches Engineering x="+x,checks);
                    PhysicalWaypoint(body,new Vector2(x,-1.4f));
                    Require(Vector2.Distance(body.position,new Vector2(x,-1.4f))<.08f,"Actual physics returns to Main Hall x="+x,checks);
                }
                foreach(float side in new[]{-1f,1f})
                {
                    body.position=new Vector2(side*1.8f,-1.4f);body.linearVelocity=Vector2.zero;Physics2D.SyncTransforms();
                    foreach(var goal in new[]{new Vector2(0,-3.3f),new Vector2(0,-7.25f),new Vector2(side*.9f,-7.25f),new Vector2(side*1.5f,-7.8f)})
                        PhysicalWaypoint(body,goal);
                    Require(Vector2.Distance(body.position,new Vector2(side*1.5f,-7.8f))<.08f,
                        "Human off-center Engineering approach side="+side,checks);
                    foreach(var goal in new[]{new Vector2(side*.9f,-7.25f),new Vector2(0,-7.25f),new Vector2(0,-3.3f),new Vector2(side*1.8f,-1.4f)})
                        PhysicalWaypoint(body,goal);
                    Require(Vector2.Distance(body.position,new Vector2(side*1.8f,-1.4f))<.08f,
                        "Human off-center return side="+side,checks);
                }
                body.position=new Vector2(.203f,-7.073f);body.linearVelocity=Vector2.zero;Physics2D.SyncTransforms();
                foreach(var goal in new[]{new Vector2(.203f,-7.27f),new Vector2(.9f,-7.27f),new Vector2(1.5f,-7.8f)})
                    PhysicalWaypoint(body,goal);
                Require(Vector2.Distance(body.position,new Vector2(1.5f,-7.8f))<.08f,
                    "Previously trapped Human pose can leave the doorway to the right",checks);
                var waiting=new GameObject("Stationary Multiplayer Capsule Probe");
                try
                {
                    waiting.transform.position=new Vector2(0,-7);
                    var other=waiting.AddComponent<CapsuleCollider2D>();
                    other.size=source.size;other.offset=source.offset;other.direction=source.direction;
                    Physics2D.SyncTransforms();
                    foreach(float x in new[]{-.3f,.3f})
                    {
                        body.position=new Vector2(x,-6);body.linearVelocity=Vector2.zero;Physics2D.SyncTransforms();
                        PhysicalWaypoint(body,new Vector2(x,-7.25f));
                        Require(Vector2.Distance(body.position,new Vector2(x,-7.25f))<.08f,
                            "A Human capsule can pass a waiting actor on side x="+x,checks);
                    }
                }
                finally { UnityEngine.Object.DestroyImmediate(waiting); }
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(root);
                Physics2D.simulationMode=originalMode;Physics2D.SyncTransforms();
            }
        }
        private static void PhysicalWaypoint(Rigidbody2D body,Vector2 goal)
        {
            for(int i=0;i<300&&Vector2.Distance(body.position,goal)>.025f;i++)
            {
                Vector2 delta=goal-body.position;
                body.linearVelocity=delta.normalized*Mathf.Min(2.6f,delta.magnitude/.02f);
                Physics2D.Simulate(.02f);
            }
            body.linearVelocity=Vector2.zero;Physics2D.Simulate(.02f);
        }
        private static void Require(bool passed,string name,List<string> checks)
        { if(!passed)throw new InvalidOperationException("World geometry validation failed: "+name);checks.Add(name); }
        [Serializable] private sealed class GeometryReport
        {
            public bool passed;
            public string[] checks;
            public float roomBoundaryCenterY;
            public Vector2[] route;
            public Vector2 capsuleSize,capsuleOffset;
        }
    }
}
