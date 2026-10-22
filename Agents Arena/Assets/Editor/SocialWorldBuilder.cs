using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Actor.Networking;
using Actor.Presentation;
using Actor.World;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Actor.Editor
{
    public static class SocialWorldBuilder
    {
        public const string SocialScene = "Assets/Scenes/SocialDeduction.unity";
        private const string Atlas = "Assets/Sprites/map.png";
        private const float PassageWidth = 1.15f;
        private static readonly Vector2 Offset = new(0, .46f);
        private static readonly Vector2[] LoopCenters =
        {
            new(3.30f,-.15f), new(6.44f,-.15f), new(6.44f,-3.03f),
            new(5.64f,-4.33f), new(2.04f,-4.33f), new(.50f,-4.37f)
        };
        [Serializable] private sealed class GeometryReport { public bool passed; public string[] checks; public SocialWorldDefinition.Node[] nodes; }
        [Serializable] private sealed class LayoutFloor { public string room_id; public Vector2[] physical_polygon; }
        [Serializable] private sealed class LayoutNode { public string id, room_id; public Vector2 root_position; }
        [Serializable] private sealed class LayoutTerminal { public string entity_id, room_id, kind, task_id; public Vector2 interaction_point, approach_point; public float radius; }
        [Serializable] private sealed class Layout
        {
            public int version = 1;
            public Vector2 capsule_offset, capsule_size;
            public LayoutFloor[] floors;
            public LayoutNode[] nodes;
            public SocialWorldDefinition.Edge[] edges;
            public LayoutTerminal[] terminals;
        }

        // Root invokes this on its saved Social clone. It never opens or saves
        // the legacy scene, rewrites the atlas PNG or modifies an existing prefab.
        public static void AddToCurrentSocialScene()
        {
            if (Application.isPlaying) throw new InvalidOperationException("Author Social geometry outside Play Mode.");
            if (UnityEngine.SceneManagement.SceneManager.GetActiveScene().path != SocialScene)
                throw new InvalidOperationException("Save/open the separate SocialDeduction scene before authoring it.");
            var hall = GameObject.Find("Main Hall"); var engineering = GameObject.Find("Engineering");
            if (hall == null || engineering == null) throw new InvalidOperationException("The authored atlas hall and Engineering are required.");
            if (GameObject.Find("Social World") != null) throw new InvalidOperationException("Author Social geometry from a fresh legacy clone.");
            var material = hall.GetComponent<SpriteRenderer>().sharedMaterial;
            RemoveLegacyOnlyObjects();
            foreach (var parent in new[] { hall.transform, engineering.transform })
            {
                foreach (string name in new[] { "Visual", "South Connector" })
                { var child = parent.Find(name); if (child != null) UnityEngine.Object.DestroyImmediate(child.gameObject); }
                var collision = parent.Find("EnvironmentCollision");
                if (collision != null)
                {
                    var reactor = collision.Find("Reactor Housing");
                    if (reactor != null) { reactor.SetParent(engineering.transform, true); reactor.gameObject.AddComponent<SocialVisibilityOccluder>(); }
                    UnityEngine.Object.DestroyImmediate(collision.gameObject);
                }
            }
            // These two formerly sealed exits now join the real eastern loop.
            foreach (var renderer in hall.GetComponentsInChildren<SpriteRenderer>())
                if (renderer != null && renderer.name == "Sealed Gate" && (Vector2.Distance(renderer.transform.position, EngineeringRoomBuilder.PixelToWorld(new Vector2(903,481))) < .1f
                    || Vector2.Distance(renderer.transform.position, EngineeringRoomBuilder.PixelToWorld(new Vector2(761.5f,692))) < .1f))
                    UnityEngine.Object.DestroyImmediate(renderer.gameObject);
            var root = new GameObject("Social World");
            FullAtlas(root.transform, material);
            var floors = new List<Vector2[]> { EngineeringRoomBuilder.MainHallFloorPixels.Select(EngineeringRoomBuilder.PixelToWorld).ToArray(),
                EngineeringRoomBuilder.FloorPixels.Select(EngineeringRoomBuilder.PixelToWorld).ToArray() };
            var corridorFloors = new List<Vector2[]>();
            for (int i = 1; i < LoopCenters.Length; ++i) corridorFloors.Add(Strip(LoopCenters[i - 1], LoopCenters[i], PassageWidth));
            corridorFloors.Add(Strip(new Vector2(6.44f,-.15f), new Vector2(6.44f,2.85f), PassageWidth));
            corridorFloors.Add(Strip(new Vector2(5.34f,-4.33f), new Vector2(5.34f,-5.95f), PassageWidth));
            // Butt-ended rectangles leave a tiny triangular hole at a turn.
            // Authored square landings provide actual capsule clearance there,
            // with the same square floor art and collision footprint.
            foreach (var point in LoopCenters.Concat(new[] { new Vector2(5.34f,-4.33f) }))
                corridorFloors.Add(Strip(point-Vector2.right*PassageWidth*.5f,point+Vector2.right*PassageWidth*.5f,PassageWidth));
            floors.AddRange(corridorFloors);
            foreach (var floor in corridorFloors)
            {
                Volume(root.transform, "east-corridor", floor);
                FloorStrip(root.transform, floor, material);
            }
            Vector2[] security = Pixels(new[] { new Vector2(958,240),new(978,220),new(1106,220),new(1130,242),new(1130,340),new(1110,353),new(978,353),new(958,338) });
            Vector2[] storage = Pixels(new[] { new Vector2(900,770),new(919,751),new(1048,751),new(1068,770),new(1068,863),new(1048,884),new(920,884),new(900,863) });
            floors.Add(security); floors.Add(storage);
            Volume(root.transform, "security", security); Volume(root.transform, "storage", storage);
            var boundary=BuildUnionWalls(root.transform, floors);
            DecorateCorridorBoundary(root.transform,boundary,floors,new[]{floors[0],floors[1],security,storage},material);
            CreateTerminal(root.transform, "meeting-main-hall", "main-hall", "meeting", "", new Vector2(-1.3f,-.4f), new Vector2(-1.5f,-1.9f), material);
            CreateTerminal(root.transform, "life-support-engineering", "engineering", "life-support", "", new Vector2(.8f,-6.6f), new Vector2(.3f,-7.15f), material);
            CreateTerminal(root.transform, "task-main-panel", "main-hall", "task", "main-panel", new Vector2(1.5f,.4f), new Vector2(1.5f,-1), material);
            CreateTerminal(root.transform, "task-engineering-systems", "engineering", "task", "engineering-systems", new Vector2(-1.7f,-8.7f), new Vector2(-.95f,-9.16f), material);
            CreateTerminal(root.transform, "task-security-records", "security", "task", "security-records", new Vector2(7.2f,3.7f), new Vector2(6.35f,3.24f), material);
            CreateTerminal(root.transform, "task-storage-inventory", "storage", "task", "storage-inventory", new Vector2(5.9f,-7.3f), new Vector2(5,-7.76f), material);
            root.AddComponent<SocialWorldDefinition>().Configure(Nodes(), Edges(), Offset);
            new GameObject("Social Fog").AddComponent<SocialFogRenderer>();
            foreach (var camera in UnityEngine.Object.FindObjectsByType<CameraFollow2D>())
            { camera.SetWorldBounds(new Vector2(-4.5f,-12.25f),new Vector2(9.6f,6.4f)); camera.GetComponent<Camera>().orthographicSize = 3.4f; }
            ValidateCurrentSocialScene();
            ExportLayout();
            AtmosphericWorldArt.Apply(root.transform,boundary,floors);
        }
        private static void RemoveLegacyOnlyObjects()
        {
            foreach (var component in UnityEngine.Object.FindObjectsByType<CooperativeTerminal2D>()) UnityEngine.Object.DestroyImmediate(component.gameObject);
            foreach (var component in UnityEngine.Object.FindObjectsByType<IncidentTerminal2D>()) UnityEngine.Object.DestroyImmediate(component.gameObject);
            foreach (var component in UnityEngine.Object.FindObjectsByType<SignalConsole>()) UnityEngine.Object.DestroyImmediate(component.gameObject);
            foreach (var component in UnityEngine.Object.FindObjectsByType<CooperativeSession>()) UnityEngine.Object.DestroyImmediate(component.gameObject);
            foreach (var component in UnityEngine.Object.FindObjectsByType<IncidentSession>()) UnityEngine.Object.DestroyImmediate(component.gameObject);
            foreach (var component in UnityEngine.Object.FindObjectsByType<RoomWayfinding2D>()) UnityEngine.Object.DestroyImmediate(component.gameObject);
        }
        private static Vector2[] Pixels(IEnumerable<Vector2> values) => values.Select(EngineeringRoomBuilder.PixelToWorld).ToArray();
        private static Vector2[] Strip(Vector2 a, Vector2 b, float width)
        { Vector2 normal = new Vector2(-(b-a).y,(b-a).x).normalized * width * .5f; return new[] {a+normal,b+normal,b-normal,a-normal}; }
        private static void Volume(Transform parent, string id, Vector2[] polygon)
        { var value = new GameObject(id + " Floor Volume"); value.transform.SetParent(parent,false); value.AddComponent<PolygonCollider2D>(); value.AddComponent<RoomVolume2D>().Configure(id,polygon); }
        private static CompositeCollider2D BuildUnionWalls(Transform parent, IEnumerable<Vector2[]> floors)
        {
            var root = new GameObject("Opaque Floor Boundary"); root.transform.SetParent(parent,false);
            root.AddComponent<Rigidbody2D>().bodyType = RigidbodyType2D.Static;
            var composite = root.AddComponent<CompositeCollider2D>(); composite.geometryType = CompositeCollider2D.GeometryType.Outlines;
            composite.generationType = CompositeCollider2D.GenerationType.Manual; composite.edgeRadius = .03f;
            foreach (var floor in floors)
            {
                var source = new GameObject("Floor Union Source"); source.transform.SetParent(root.transform,false);
                var shape = source.AddComponent<PolygonCollider2D>(); shape.SetPath(0,floor); shape.compositeOperation = Collider2D.CompositeOperation.Merge;
            }
            composite.GenerateGeometry(); root.AddComponent<SocialVisibilityOccluder>();
            return composite;
        }
        private static Sprite Slice(string path, Rect source)
        {
            var sprite = AssetDatabase.LoadAssetAtPath<Sprite>(path); if (sprite != null) return sprite;
            var texture = AssetDatabase.LoadAssetAtPath<Texture2D>(Atlas);
            sprite = Sprite.Create(texture,new Rect(source.x,texture.height-source.y-source.height,source.width,source.height),new Vector2(.5f,.5f),50,0,SpriteMeshType.FullRect);
            sprite.name = Path.GetFileNameWithoutExtension(path); AssetDatabase.CreateAsset(sprite,path); return sprite;
        }
        private static void FullAtlas(Transform parent, Material material)
        {
            var texture = AssetDatabase.LoadAssetAtPath<Texture2D>(Atlas);
            var root = new GameObject("Unmodified Ship Atlas"); root.transform.SetParent(parent,false);
            root.transform.position = EngineeringRoomBuilder.PixelToWorld(new Vector2(texture.width*.5f,texture.height*.5f));
            var renderer = root.AddComponent<SpriteRenderer>(); renderer.sprite = Slice("Assets/Sprites/Rooms/SocialAtlas.asset",new Rect(0,0,texture.width,texture.height));
            renderer.sharedMaterial = material; renderer.sortingOrder = -100;
        }
        private static void FloorStrip(Transform parent, Vector2[] polygon, Material material)
        {
            Vector2 a=(polygon[0]+polygon[3])*.5f,b=(polygon[1]+polygon[2])*.5f,delta=b-a;
            var root = new GameObject("Atlas Corridor Floor"); root.transform.SetParent(parent,false); root.transform.position=(a+b)*.5f;
            root.transform.rotation=Quaternion.Euler(0,0,Mathf.Atan2(delta.y,delta.x)*Mathf.Rad2Deg);
            var renderer=root.AddComponent<SpriteRenderer>(); renderer.sprite=Slice("Assets/Sprites/Rooms/SocialCorridorPlating.asset",new Rect(994,265,32,32));
            renderer.sharedMaterial=material; renderer.sortingOrder=-90; renderer.drawMode=SpriteDrawMode.Tiled; renderer.size=new Vector2(delta.magnitude,PassageWidth);
            renderer.color=new Color(.80f,.78f,.88f);
        }
        private static bool InsideFloor(Vector2 point,Vector2[] polygon)
        {
            bool inside=false;
            for(int i=0,j=polygon.Length-1;i<polygon.Length;j=i++)
            {
                Vector2 a=polygon[i],b=polygon[j];
                if((a.y>point.y)!=(b.y>point.y)&&point.x<(b.x-a.x)*(point.y-a.y)/(b.y-a.y)+a.x)inside=!inside;
            }
            return inside;
        }
        private static void FlatPanel(Transform parent,string name,Sprite sprite,Material material,Vector2 center,Vector2 size,Color color,int order,float angle=0)
        {
            var root=new GameObject(name);root.transform.SetParent(parent,false);root.transform.localPosition=center;
            root.transform.localRotation=Quaternion.Euler(0,0,angle);root.transform.localScale=new Vector3(size.x,size.y,1);
            var renderer=root.AddComponent<SpriteRenderer>();renderer.sprite=sprite;renderer.sharedMaterial=material;
            renderer.color=color;renderer.sortingOrder=order;
        }
        private static void DecorateCorridorBoundary(Transform parent,CompositeCollider2D boundary,IReadOnlyList<Vector2[]> allFloors,
            IReadOnlyList<Vector2[]> originalRooms,Material material)
        {
            var root=new GameObject("Authored Corridor Hull Trim");root.transform.SetParent(parent,false);
            var sprite=AssetDatabase.LoadAssetAtPath<Sprite>("Assets/Sprites/Rooms/GatePanel.asset");
            // Only the external union contour receives trim. There are no
            // crossing lines at tile overlaps, loop branches or room openings.
            for(int path=0;path<boundary.pathCount;++path)
            {
                var points=new Vector2[boundary.GetPathPointCount(path)];boundary.GetPath(path,points);
                for(int i=0;i<points.Length;++i)
                {
                    Vector2 a=boundary.transform.TransformPoint(points[i]),b=boundary.transform.TransformPoint(points[(i+1)%points.Length]);
                    Vector2 delta=b-a;if(delta.sqrMagnitude<.0004f)continue;
                    Vector2 middle=(a+b)*.5f,inward=new Vector2(-delta.y,delta.x).normalized;
                    if(!allFloors.Any(floor=>InsideFloor(middle+inward*.04f,floor)))inward=-inward;
                    // Preserve the original detailed room walls and doorway art.
                    if(originalRooms.Any(floor=>InsideFloor(middle+inward*.04f,floor)))continue;
                    float angle=Mathf.Atan2(delta.y,delta.x)*Mathf.Rad2Deg,length=delta.magnitude+.04f;
                    FlatPanel(root.transform,"Hull Shadow",sprite,material,middle-inward*.035f,new Vector2(length,.29f),new Color(.025f,.03f,.055f,.88f),-85,angle);
                    FlatPanel(root.transform,"Navy Hull Edge",sprite,material,middle+inward*.015f,new Vector2(length,.16f),new Color(.105f,.12f,.19f),-83,angle);
                    FlatPanel(root.transform,"Inner Metal Rim",sprite,material,middle+inward*.085f,new Vector2(length,.045f),new Color(.36f,.35f,.46f),-82,angle);
                    FlatPanel(root.transform,"Soft Rim Highlight",sprite,material,middle+inward*.105f,new Vector2(length,.013f),new Color(.53f,.49f,.60f),-81,angle);
                    if(delta.magnitude>1.35f)
                        FlatPanel(root.transform,"Inset Warm Service Light",sprite,material,middle+inward*.06f,new Vector2(.22f,.018f),new Color(.9f,.70f,.43f),-80,angle);
                }
            }
        }
        private static void CreateTerminal(Transform parent,string id,string room,string kind,string task,Vector2 point,Vector2 approach,Material material)
        {
            var root = new GameObject(id); root.transform.SetParent(parent,false); root.transform.position=point;
            root.AddComponent<SocialTerminal2D>().Configure(id,room,kind,task,approach);
            root.AddComponent<BoxCollider2D>().size=new Vector2(.42f,.22f);
            var world = new SerializedObject(root.AddComponent<WorldEntity>()); world.FindProperty("entityId").stringValue=id;world.FindProperty("roomId").stringValue=room;world.ApplyModifiedPropertiesWithoutUndo();
            var sprite=AssetDatabase.LoadAssetAtPath<Sprite>("Assets/Sprites/Rooms/GatePanel.asset");
            Color accent=kind=="meeting"?new Color(.86f,.52f,.18f):kind=="life-support"?new Color(.2f,.75f,.8f):new Color(.46f,.58f,.78f);
            // The same layered metal housing vocabulary as the existing M6
            // console, with an inset display and keys instead of a flat marker.
            FlatPanel(root.transform,"Console Ground Shadow",sprite,material,new Vector2(.025f,-.105f),new Vector2(.61f,.26f),new Color(.025f,.025f,.055f,.6f),-7);
            FlatPanel(root.transform,"Console Housing",sprite,material,Vector2.zero,new Vector2(.56f,.40f),new Color(.115f,.125f,.20f),-6);
            FlatPanel(root.transform,"Bevel Metal Face",sprite,material,new Vector2(0,.025f),new Vector2(.49f,.30f),new Color(.38f,.37f,.48f),-5);
            FlatPanel(root.transform,"Display Recess",sprite,material,new Vector2(0,.065f),new Vector2(.39f,.18f),new Color(.065f,.08f,.13f),-4);
            FlatPanel(root.transform,"Status Display",sprite,material,new Vector2(0,.075f),new Vector2(.32f,.105f),accent,-3);
            FlatPanel(root.transform,"Display Readout",sprite,material,new Vector2(-.025f,.085f),new Vector2(.19f,.012f),new Color(.77f,.9f,.95f),-2);
            FlatPanel(root.transform,"Inset Key Left",sprite,material,new Vector2(-.12f,-.08f),new Vector2(.07f,.028f),accent*.7f,-3);
            FlatPanel(root.transform,"Inset Key Right",sprite,material,new Vector2(.12f,-.08f),new Vector2(.07f,.028f),new Color(.63f,.64f,.72f),-3);
        }
        public static SocialWorldDefinition.Node[] Nodes()
        {
            SocialWorldDefinition.Node N(string id,string room,float x,float y)=>new(){id=id,roomId=room,rootPosition=new Vector2(x,y)};
            return new[]{N("hall","main-hall",0,-1.4f),N("hall-east","main-hall",2.8f,-.61f),N("east-door","east-corridor",3.6f,-.61f),N("east-hub","east-corridor",6.44f,-.61f),
                N("security-gate","east-corridor",6.44f,1.91f),N("security","security",6.44f,3.31f),N("loop-north","east-corridor",6.44f,-3.49f),N("loop-corner","east-corridor",5.64f,-4.79f),
                N("storage-branch","east-corridor",5.34f,-4.79f),N("loop-return","east-corridor",2.04f,-4.79f),N("stem-east","east-corridor",.78f,-4.83f),N("hall-stem","main-hall",0,-4.83f),
                N("engineering-door","engineering",0,-6),N("engineering","engineering",0,-7),N("storage-gate","east-corridor",5.34f,-5.69f),N("storage","storage",5.34f,-7.29f),
                N("meeting-main-hall","main-hall",-1.5f,-1.9f),N("task-main-panel","main-hall",1.5f,-1),N("life-support-engineering","engineering",.3f,-7.15f),
                N("engineering-west","engineering",-1.2f,-7.6f),N("task-engineering-systems","engineering",-.95f,-9.16f),
                N("task-security-records","security",6.35f,3.24f),N("task-storage-inventory","storage",5,-7.76f)};
        }
        public static SocialWorldDefinition.Edge[] Edges()
        {
            return new[]{("hall","hall-east"),("hall-east","east-door"),("east-door","east-hub"),("east-hub","security-gate"),("security-gate","security"),("east-hub","loop-north"),
                ("loop-north","loop-corner"),("loop-corner","storage-branch"),("storage-branch","loop-return"),("loop-return","stem-east"),("stem-east","hall-stem"),("hall-stem","hall"),
                ("hall-stem","engineering-door"),("engineering-door","engineering"),("storage-branch","storage-gate"),("storage-gate","storage"),
                ("hall","meeting-main-hall"),("hall","task-main-panel"),("engineering","life-support-engineering"),("engineering","engineering-west"),
                ("engineering-west","task-engineering-systems"),("security","task-security-records"),("storage","task-storage-inventory")}.Select(pair=>new SocialWorldDefinition.Edge{from=pair.Item1,to=pair.Item2}).ToArray();
        }
        public static void ExportLayout()
        {
            var capsule=AssetDatabase.LoadAssetAtPath<GameObject>(MultiplayerBuild.PlayerPrefab).GetComponent<CapsuleCollider2D>();
            var layout=new Layout {capsule_offset=capsule.offset,capsule_size=capsule.size,
                floors=UnityEngine.Object.FindObjectsByType<RoomVolume2D>().OrderBy(r=>r.Id).ThenBy(r=>r.GetComponent<PolygonCollider2D>().GetPath(0)[0].x).ThenBy(r=>r.GetComponent<PolygonCollider2D>().GetPath(0)[0].y).Select(r=>new LayoutFloor{room_id=r.Id,
                    physical_polygon=r.GetComponent<PolygonCollider2D>().GetPath(0).Select(p=>(Vector2)r.transform.TransformPoint(p)).ToArray()}).ToArray(),
                nodes=Nodes().Select(n=>new LayoutNode{id=n.id,room_id=n.roomId,root_position=n.rootPosition}).ToArray(),edges=Edges(),
                terminals=UnityEngine.Object.FindObjectsByType<SocialTerminal2D>().OrderBy(t=>t.EntityId).Select(t=>new LayoutTerminal{entity_id=t.EntityId,room_id=t.RoomId,kind=t.Kind,task_id=t.TaskId,
                    interaction_point=t.InteractionPoint,approach_point=t.ApproachPoint,radius=t.Radius}).ToArray()};
            Directory.CreateDirectory("Assets/World");File.WriteAllText("Assets/World/SocialLayout.json",JsonUtility.ToJson(layout,true)+"\n");AssetDatabase.ImportAsset("Assets/World/SocialLayout.json");
        }
        [MenuItem("Actor/Verify Social World Geometry")]
        public static void ValidateCurrentSocialScene()
        {
            // RoomVolume2D is intentionally a runtime MonoBehaviour, without
            // ExecuteAlways. Edit-mode scene authoring does not invoke its
            // OnEnable registration, so exercise that exact runtime lifecycle
            // while testing TryResolve, then release the temporary registry.
            var roomVolumes=UnityEngine.Object.FindObjectsByType<RoomVolume2D>();
            bool editorLifecycle=!Application.isPlaying;
            if(editorLifecycle)foreach(var room in roomVolumes)room.SendMessage("OnEnable",SendMessageOptions.RequireReceiver);
            try { ValidateRegisteredSocialScene(); }
            finally
            {
                if(editorLifecycle)foreach(var room in roomVolumes)
                    if(room!=null)room.SendMessage("OnDisable",SendMessageOptions.RequireReceiver);
            }
        }
        private static void ValidateRegisteredSocialScene()
        {
            Physics2D.SyncTransforms();var checks=new List<string>();
            void Require(bool value,string name){if(!value)throw new InvalidOperationException("Social geometry: "+name);checks.Add(name);}
            var prefab=AssetDatabase.LoadAssetAtPath<GameObject>(MultiplayerBuild.PlayerPrefab);var capsule=prefab.GetComponent<CapsuleCollider2D>();
            Require(capsule.offset==Offset,"root/capsule center mapping remains explicit");
            var filter=new ContactFilter2D{useTriggers=false};filter.SetLayerMask(Physics2D.GetLayerCollisionMask(prefab.layer));
            var hits=new RaycastHit2D[64];var overlap=new Collider2D[32];var nodes=Nodes();var byId=nodes.ToDictionary(n=>n.id);
            foreach(var node in nodes)
            {
                int count=Physics2D.OverlapCapsule(node.rootPosition+Offset,capsule.size,capsule.direction,0,filter,overlap);
                Require(count==0,"free authored graph node "+node.id+" blockers="+string.Join(",",overlap.Take(count).Where(c=>c!=null).Select(c=>c.name).Distinct()));
            }
            foreach(var edge in Edges())foreach(float side in new[]{-.08f,0,.08f})
            {
                Vector2 a=byId[edge.from].rootPosition+Offset,b=byId[edge.to].rootPosition+Offset,delta=b-a;
                Vector2 normal=new Vector2(-delta.y,delta.x).normalized*side;
                int forward=Physics2D.CapsuleCast(a+normal,capsule.size,capsule.direction,0,delta.normalized,filter,hits,delta.magnitude+.003f);
                Require(forward==0,"free actual capsule edge "+edge.from+"/"+edge.to+" offset "+side+" blockers="+string.Join(",",hits.Take(forward).Where(h=>h.collider!=null).Select(h=>h.collider.name).Distinct()));
                int reverse=Physics2D.CapsuleCast(b+normal,capsule.size,capsule.direction,0,-delta.normalized,filter,hits,delta.magnitude+.003f);
                Require(reverse==0,"free reverse capsule edge "+edge.to+"/"+edge.from+" offset "+side+" blockers="+string.Join(",",hits.Take(reverse).Where(h=>h.collider!=null).Select(h=>h.collider.name).Distinct()));
            }
            var rooms=UnityEngine.Object.FindObjectsByType<RoomVolume2D>().Select(r=>r.Id).Distinct().ToArray();
            Require(new[]{"main-hall","engineering","east-corridor","security","storage"}.All(rooms.Contains),"four rooms plus real alternate corridor");
            Require(!SocialVisibilitySystem.LineOfSight(new Vector2(0,-.94f),new Vector2(6.44f,3.77f)),"real walls occlude cross-room sight");
            Require(SocialVisibilitySystem.LineOfSight(new Vector2(4.2f,-.15f),new Vector2(6,-.15f)),"open corridor preserves sight");
            foreach(var terminal in UnityEngine.Object.FindObjectsByType<SocialTerminal2D>())
            {
                Require(byId.ContainsKey(terminal.EntityId) && byId[terminal.EntityId].rootPosition==terminal.ApproachPoint,"terminal approach is on swept public graph "+terminal.EntityId);
                Require(Physics2D.OverlapCapsule(terminal.ApproachPoint+Offset,capsule.size,capsule.direction,0,filter,overlap)==0,"free terminal approach "+terminal.EntityId);
                Require(Vector2.Distance(terminal.ApproachPoint+Offset,terminal.InteractionPoint)<=terminal.Radius,"physical interaction radius "+terminal.EntityId);
                bool resolved=RoomVolume2D.TryResolve(terminal.ApproachPoint+Offset,null,out string actualRoom);
                Require(resolved&&actualRoom==terminal.RoomId,"actual authoritative terminal room "+terminal.EntityId+" resolved="+(actualRoom??"none"));
                Require(SocialVisibilitySystem.LineOfSight(terminal.ApproachPoint+Offset,terminal.InteractionPoint),"unobstructed terminal interaction sight "+terminal.EntityId);
            }
            string output=Path.GetFullPath("../build/social-world/geometry.json");Directory.CreateDirectory(Path.GetDirectoryName(output));
            File.WriteAllText(output,JsonUtility.ToJson(new GeometryReport{passed=true,checks=checks.ToArray(),nodes=nodes},true)+"\n");
            Debug.Log("ACTOR_SOCIAL_WORLD_GEOMETRY passed="+checks.Count);
        }
    }
}
