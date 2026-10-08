using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Actor.Presentation;
using Actor.World;
using UnityEditor;
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
        public static readonly Vector2 WorldMinimum = new(-6.27f, -12.25f);
        public static readonly Vector2 WorldMaximum = new(6.27f, 6.27f);

        // The existing map's lower central room, with a north-facing entrance.
        // Coordinates follow its visible floor/wall seam, in top-left source pixels.
        // Segment 0 is the open shared doorway and intentionally has no wall.
        public static readonly Vector2[] FloorPixels =
        {
            new(701.5f, 734), new(743.5f, 734), new(743.5f, 790),
            new(775, 792), new(809, 820), new(835, 846), new(837, 873),
            new(818, 903), new(789, 931), new(750, 947), new(738, 960),
            new(707, 960), new(695, 947), new(650, 931), new(623, 903),
            new(606, 876), new(606, 847), new(631, 821), new(662, 793),
            new(701.5f, 790)
        };

        public static Vector2 PixelToWorld(Vector2 sourcePixel)
            => doorwayWorld + new Vector2(sourcePixel.x - doorwayPixel.x, doorwayPixel.y - sourcePixel.y) / PixelsPerUnit;

        public static void AddToCurrentMultiplayerScene()
        {
            if (Application.isPlaying) throw new InvalidOperationException("Prepare the world outside Play Mode.");
            var hall = GameObject.Find("Main Hall");
            if (hall == null) throw new InvalidOperationException("The current Main Hall is required when preparing Engineering.");
            var endWall = hall.transform.Find("EnvironmentCollision/Wall 21");
            if (endWall == null) throw new InvalidOperationException("The authored south corridor end wall was not found.");
            UnityEngine.Object.DestroyImmediate(endWall.gameObject);
            CreateVolume(hall.transform, RoomId.MainHall,
                PlayableMilestoneBuilder.FloorPixels.Select(PlayableMilestoneBuilder.PixelToWorld).ToArray());

            var engineering = new GameObject("Engineering");
            var visual = new GameObject("Visual");
            visual.transform.SetParent(engineering.transform, false);
            // Source rect: top-left (544,725), size 367x361 in the unchanged 1448x1086 map.
            visual.transform.position = PixelToWorld(new Vector2(727.5f, 905.5f));
            var renderer = visual.AddComponent<SpriteRenderer>();
            renderer.sprite = GetEngineeringSprite();
            renderer.sharedMaterial = hall.GetComponent<SpriteRenderer>().sharedMaterial;
            renderer.sortingOrder = -100;
            var collisions = new GameObject("EnvironmentCollision");
            collisions.transform.SetParent(engineering.transform, false);
            Vector2[] floor = FloorPixels.Select(PixelToWorld).ToArray();
            for (int i = 1; i < floor.Length; i++)
            {
                Vector2 a = floor[i], b = floor[(i + 1) % floor.Length];
                Vector2 delta = b - a;
                Vector2 outward = new Vector2(-delta.y, delta.x).normalized;
                var wall = new GameObject("Wall " + i.ToString("D2"));
                wall.transform.SetParent(collisions.transform, false);
                wall.transform.position = (a + b) * 0.5f + outward * 0.08f;
                wall.transform.rotation = Quaternion.Euler(0, 0, Mathf.Atan2(delta.y, delta.x) * Mathf.Rad2Deg);
                wall.AddComponent<BoxCollider2D>().size = new Vector2(delta.magnitude + 0.12f, 0.16f);
            }
            var housing = new GameObject("Reactor Housing");
            housing.transform.SetParent(collisions.transform, false);
            housing.transform.position = PixelToWorld(new Vector2(724, 853));
            housing.AddComponent<CircleCollider2D>().radius = 0.6f;
            var entity = housing.AddComponent<WorldEntity>();
            var serialized = new SerializedObject(entity);
            serialized.FindProperty("entityId").stringValue = "engineering-reactor-01";
            serialized.FindProperty("roomId").stringValue = RoomId.Engineering;
            serialized.ApplyModifiedPropertiesWithoutUndo();
            CreateVolume(engineering.transform, RoomId.Engineering, floor);
            foreach (var camera in UnityEngine.Object.FindObjectsByType<CameraFollow2D>())
                camera.SetWorldBounds(WorldMinimum, WorldMaximum);
            ValidateCurrentMultiplayerScene();
        }

        public static void ValidateCurrentMultiplayerScene()
        {
            Physics2D.SyncTransforms();
            var checks = new List<string>();
            var rooms = UnityEngine.Object.FindObjectsByType<RoomVolume2D>();
            Require(rooms.Length == 2, "Two distinct room volumes", checks);
            var mainHall = rooms.Single(room => room.Id == RoomId.MainHall);
            var engineering = rooms.Single(room => room.Id == RoomId.Engineering);
            Require(mainHall.Contains(new Vector2(0, -0.94f)), "Main Hall contains the initial route center", checks);
            Require(engineering.Contains(new Vector2(0, -6.54f)), "Engineering contains the final route center", checks);
            Require(mainHall.Contains(new Vector2(0, -5.20f)) && !engineering.Contains(new Vector2(0, -5.20f)),
                "North side of doorway belongs to Main Hall", checks);
            Require(engineering.Contains(new Vector2(0, -5.22f)) && !mainHall.Contains(new Vector2(0, -5.22f)),
                "South side of doorway belongs to Engineering", checks);
            var player = AssetDatabase.LoadAssetAtPath<GameObject>(MultiplayerBuild.PlayerPrefab);
            var capsule = player.GetComponent<CapsuleCollider2D>();
            Require(player.GetComponent<Actor.Networking.NetworkRoomMembership>() != null,
                "Network player has authoritative room membership", checks);
            var filter = new ContactFilter2D { useTriggers = false };
            filter.SetLayerMask(Physics2D.GetLayerCollisionMask(player.layer));
            var hits = new RaycastHit2D[32];
            Vector2 start = new Vector2(0, -1.4f) + capsule.offset;
            Vector2 end = new Vector2(0, -7f) + capsule.offset;
            int blockers = Physics2D.CapsuleCast(start, capsule.size, capsule.direction, 0, Vector2.down,
                filter, hits, Vector2.Distance(start, end) + 0.003f);
            Require(blockers == 0, "Authored player capsule can traverse MainHall to Engineering", checks);
            blockers = Physics2D.CapsuleCast(end, capsule.size, capsule.direction, 0, Vector2.up,
                filter, hits, Vector2.Distance(start, end) + 0.003f);
            Require(blockers == 0, "Authored player capsule can return to MainHall", checks);
            blockers = Physics2D.CapsuleCast(end, capsule.size, capsule.direction, 0, Vector2.right,
                filter, hits, 10f);
            Require(blockers > 0, "Engineering wall blocks passage out of the floor", checks);
            blockers = Physics2D.CapsuleCast(end, capsule.size, capsule.direction, 0, Vector2.down,
                filter, hits, 10f);
            Require(blockers > 0, "Engineering reactor housing remains a physical obstacle", checks);
            var report = new GeometryReport { checks = checks.ToArray(), passed = true };
            string output = Path.GetFullPath("../build/engineering-design/geometry-validation.json");
            Directory.CreateDirectory(Path.GetDirectoryName(output));
            File.WriteAllText(output, JsonUtility.ToJson(report, true));
            Debug.Log("ACTOR: Engineering geometry validation passed " + checks.Count + " checks. " + output);
        }

        private static void Require(bool passed, string description, List<string> checks)
        {
            if (!passed) throw new InvalidOperationException("Engineering geometry validation failed: " + description);
            checks.Add(description);
        }

        [Serializable] private sealed class GeometryReport
        {
            public bool passed;
            public string[] checks;
        }

        private static void CreateVolume(Transform parent, string id, Vector2[] floor)
        {
            var root = new GameObject("Room Volume");
            root.transform.SetParent(parent, false);
            root.AddComponent<PolygonCollider2D>();
            root.AddComponent<RoomVolume2D>().Configure(id, floor);
        }

        private static Sprite GetEngineeringSprite()
        {
            var existing = AssetDatabase.LoadAssetAtPath<Sprite>(SpritePath);
            if (existing != null) return existing;
            var texture = AssetDatabase.LoadAssetAtPath<Texture2D>(MapPath);
            if (texture == null || texture.width != 1448 || texture.height != 1086)
                throw new InvalidOperationException("The original 1448x1086 map sprite is required.");
            Directory.CreateDirectory(Path.GetDirectoryName(SpritePath));
            AssetDatabase.Refresh();
            var sprite = Sprite.Create(texture, new Rect(544, 0, 367, 361), new Vector2(0.5f, 0.5f),
                PixelsPerUnit, 0, SpriteMeshType.FullRect, Vector4.zero, false);
            sprite.name = "Engineering";
            AssetDatabase.CreateAsset(sprite, SpritePath);
            return sprite;
        }
    }
}
