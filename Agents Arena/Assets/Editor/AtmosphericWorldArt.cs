using System;
using System.Collections.Generic;
using System.Linq;
using Actor.Presentation;
using Actor.World;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Actor.Editor
{
    // Rendering is authored from the collision union, never the other way round.
    // No collider, room, route, terminal or capsule is created or changed here.
    public static class AtmosphericWorldArt
    {
        public static void Apply(Transform world,CompositeCollider2D boundary,IReadOnlyList<Vector2[]> floors)
        {
            AtmosphericArtImporter.Prepare();
            var art=AtmosphericArtCatalog.Load();if(art==null)throw new InvalidOperationException("Atmospheric catalog not imported.");
            foreach(var root in SceneManager.GetActiveScene().GetRootGameObjects())
                foreach(var renderer in root.GetComponentsInChildren<SpriteRenderer>(true))renderer.enabled=false;
            var presentation=new GameObject("Original Atmospheric Environment");presentation.transform.SetParent(world,false);
            var parent=presentation.transform;
            Image(parent,"Original Floor Union",art.floor,new Vector2(2.55f,-2.925f),art.paletteMaterial,-100);
            for(int p=0;p<boundary.pathCount;++p)
            {
                var points=new Vector2[boundary.GetPathPointCount(p)];boundary.GetPath(p,points);
                for(int i=0;i<points.Length;++i)
                {
                    Vector2 a=boundary.transform.TransformPoint(points[i]),b=boundary.transform.TransformPoint(points[(i+1)%points.Length]);
                    Vector2 delta=b-a;if(delta.magnitude<.02f)continue;Vector2 middle=(a+b)*.5f,normal=new Vector2(-delta.y,delta.x).normalized;
                    if(!floors.Any(floor=>Inside(middle+normal*.06f,floor)))normal=-normal;
                    var renderer=Image(parent,"Layered Hull",art.wall,middle-normal*.11f,art.paletteMaterial,-70);
                    renderer.transform.rotation=Quaternion.Euler(0,0,Mathf.Atan2(delta.y,delta.x)*Mathf.Rad2Deg);
                    renderer.transform.localScale=new Vector3((delta.magnitude+.035f)/art.wall.bounds.size.x,1,1);
                    renderer.flipY=Vector2.Dot(new Vector2(-delta.y,delta.x).normalized,normal)<0;
                    if(delta.magnitude>1.8f)
                    {
                        var light=Image(parent,"Warm Service Inset",art.serviceLight,middle-normal*.055f,art.paletteMaterial,-68);
                        light.transform.rotation=renderer.transform.rotation;light.transform.localScale=new Vector3(.45f,.45f,1);
                    }
                }
            }
            foreach(var terminal in UnityEngine.Object.FindObjectsByType<SocialTerminal2D>(FindObjectsSortMode.None))
            {
                int index=terminal.Kind=="meeting"?0:terminal.Kind=="life-support"?4:terminal.RoomId=="security"?2:terminal.RoomId=="storage"?3:1;
                var renderer=Image(parent,"Original "+terminal.EntityId,art.terminals[index],terminal.transform.position,art.paletteMaterial,-3);
                renderer.transform.localScale=Vector3.one*.72f;
            }
            if(art.landmarks==null||art.landmarks.Length!=3)throw new InvalidOperationException("Expected exactly three immutable wall landmarks.");
            var surfaceMaterial=SurfaceMaterial();
            foreach(var landmark in art.landmarks)
            {
                Vector2 start,end,normal;
                switch(landmark.sprite.name)
                {
                    case "landmark-hall-directory": start=new(-2.9f,1.48f);end=new(-1.1f,1.63f);normal=Vector2.down;break;
                    case "landmark-security-records": start=new(5.24f,5.01f);end=new(7.56f,5.01f);normal=Vector2.down;break;
                    case "landmark-storage-cargo": start=new(6.84f,-7.70f);end=new(6.84f,-6.02f);normal=Vector2.left;break;
                    default: throw new InvalidOperationException("Unrecognized static wall landmark.");
                }
                // The decorative sprite extrudes above/beside an opaque wall.
                // Its visible physical front is strictly inside the floor union.
                for(int i=0;i<5;++i)
                    if(!floors.Any(floor=>Inside(Vector2.Lerp(start,end,i/4f),floor)))
                        throw new InvalidOperationException("Static landmark front must be inside the authored floor.");
                var renderer=Image(parent,landmark.sprite.name,landmark.sprite,landmark.world,surfaceMaterial,-67);
                renderer.gameObject.AddComponent<StaticSurfaceArt>().Configure(start,end,normal);
            }
            var reactor=GameObject.Find("Reactor Housing");
            if(reactor==null||!reactor.TryGetComponent<CircleCollider2D>(out var housing)
                ||reactor.GetComponent<Actor.Networking.SocialVisibilityOccluder>()==null)
                throw new InvalidOperationException("The unchanged opaque reactor housing is required.");
            var reactorArt=Image(parent,"Coolant Containment",art.reactor,reactor.transform.position,surfaceMaterial,-4);
            reactorArt.gameObject.AddComponent<StaticSurfaceArt>().ConfigureHousing(housing);
            if(presentation.GetComponentsInChildren<StaticSurfaceArt>().Length!=4)
                throw new InvalidOperationException("Static surface presentation is bounded to four immutable landmarks.");
            var wayfinding=presentation.AddComponent<RoomWayfinding2D>();
            wayfinding.Configure(new[]{new RoomWayfinding2D.Marker{world=new Vector2(0,1.3f),text="MAIN HALL"},
                new RoomWayfinding2D.Marker{world=new Vector2(0,-10.3f),text="ENGINEERING"},
                new RoomWayfinding2D.Marker{world=new Vector2(6.44f,4.75f),text="SECURITY"},
                new RoomWayfinding2D.Marker{world=new Vector2(5.34f,-8.25f),text="STORAGE"}});
            foreach(var camera in UnityEngine.Object.FindObjectsByType<Camera>(FindObjectsSortMode.None))camera.backgroundColor=new Color(23/255f,31/255f,41/255f,1);
            EditorUtility.SetDirty(presentation);
        }
        private static SpriteRenderer Image(Transform parent,string name,Sprite sprite,Vector2 point,Material material,int order)
        {
            var root=new GameObject(name);root.transform.SetParent(parent,false);root.transform.position=point;
            var renderer=root.AddComponent<SpriteRenderer>();renderer.sprite=sprite;renderer.sharedMaterial=material;renderer.sortingOrder=order;return renderer;
        }
        private static Material SurfaceMaterial()
        {
            const string path="Assets/Art/Atmospheric/StaticSurface.mat";
            var shader=AssetDatabase.LoadAssetAtPath<Shader>("Assets/Art/Atmospheric/StaticSurface.shader");
            if(shader==null)throw new InvalidOperationException("Static surface shader is not imported.");
            var material=AssetDatabase.LoadAssetAtPath<Material>(path);
            if(material==null){material=new Material(shader);AssetDatabase.CreateAsset(material,path);}else material.shader=shader;
            EditorUtility.SetDirty(material);return material;
        }
        private static bool Inside(Vector2 point,IReadOnlyList<Vector2> polygon)
        {
            bool inside=false;for(int i=0,j=polygon.Count-1;i<polygon.Count;j=i++)
            {var a=polygon[i];var b=polygon[j];if((a.y>point.y)!=(b.y>point.y)&&point.x<(b.x-a.x)*(point.y-a.y)/(b.y-a.y)+a.x)inside=!inside;}
            return inside;
        }
    }
}
