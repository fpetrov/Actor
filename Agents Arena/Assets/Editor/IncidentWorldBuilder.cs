using System;
using System.Linq;
using Actor.Networking;
using Actor.World;
using UnityEditor;
using UnityEngine;

namespace Actor.Editor
{
    public static class IncidentWorldBuilder
    {
        public static void AddToCurrentMultiplayerScene()
        {
            Create(GameObject.Find("Main Hall").transform, IncidentTerminalKind.Security,
                new Vector2(-1.3f, -.4f), new Vector2(.42f, .20f), new Color(.45f, .68f, .92f));
            Create(GameObject.Find("Engineering").transform, IncidentTerminalKind.Cooling,
                new Vector2(-.8f, -6), new Vector2(.16f, .44f), new Color(.24f, .82f, .77f));
            new GameObject("Authoritative Incident Session").AddComponent<IncidentSession>();
            Validate();
        }
        private static void Create(Transform parent, IncidentTerminalKind kind, Vector2 position, Vector2 colliderSize, Color accent)
        {
            var root = new GameObject(kind == IncidentTerminalKind.Security ? "Security Records Terminal" : "Cooling Control");
            root.transform.SetParent(parent, false); root.transform.position = position;
            var terminal = root.AddComponent<IncidentTerminal2D>(); terminal.Configure(kind);
            var collider = root.AddComponent<BoxCollider2D>(); collider.size = colliderSize;
            collider.offset = kind == IncidentTerminalKind.Security ? new Vector2(0, -.14f) : Vector2.zero;
            var entity = new SerializedObject(root.AddComponent<WorldEntity>());
            entity.FindProperty("entityId").stringValue = terminal.EntityId;
            entity.FindProperty("roomId").stringValue = terminal.RoomId; entity.ApplyModifiedPropertiesWithoutUndo();
            var sprite = AssetDatabase.LoadAssetAtPath<Sprite>("Assets/Sprites/Rooms/GatePanel.asset");
            var material = GameObject.Find("Main Hall").GetComponent<SpriteRenderer>().sharedMaterial;
            Panel(root.transform, "Housing", new Vector2(.58f, .50f), Vector2.zero, new Color(.14f, .14f, .23f), sprite, material, -10);
            Panel(root.transform, "Metal Rim", new Vector2(.50f, .39f), new Vector2(0, .025f), new Color(.38f, .37f, .48f), sprite, material, -9);
            Panel(root.transform, "Display", new Vector2(.40f, .23f), new Vector2(0, .055f), accent, sprite, material, -8);
            Panel(root.transform, "Readout", new Vector2(.24f, .022f), new Vector2(-.02f, .075f), new Color(.92f, .97f, 1), sprite, material, -7);
            Panel(root.transform, "Status Indicator", new Vector2(.06f, .045f), new Vector2(.14f, -.13f), accent, sprite, material, -7);
        }
        private static void Panel(Transform parent, string name, Vector2 size, Vector2 position, Color color, Sprite sprite, Material material, int order)
        {
            var child = new GameObject(name); child.transform.SetParent(parent, false);
            child.transform.localPosition = position; child.transform.localScale = new Vector3(size.x, size.y, 1);
            var renderer = child.AddComponent<SpriteRenderer>(); renderer.sprite = sprite; renderer.sharedMaterial = material;
            renderer.color = color; renderer.sortingOrder = order;
        }
        public static void Validate()
        {
            var terminals = UnityEngine.Object.FindObjectsByType<IncidentTerminal2D>();
            if (terminals.Length != 2 || UnityEngine.Object.FindObjectsByType<IncidentSession>().Length != 1)
                throw new InvalidOperationException("Two investigation terminals and one incident session are required.");
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(MultiplayerBuild.PlayerPrefab);
            var capsule = prefab.GetComponent<CapsuleCollider2D>(); Physics2D.SyncTransforms();
            var filter = new ContactFilter2D { useTriggers = false }; filter.SetLayerMask(Physics2D.GetLayerCollisionMask(prefab.layer));
            foreach (var pose in new[] { new Vector2(-1.5f, -1.9f), new Vector2(-.3f, -7.15f), new Vector2(.3f, -7.15f) })
                if (Physics2D.OverlapCapsule(pose + capsule.offset, capsule.size, capsule.direction, 0, filter, new Collider2D[32]) != 0)
                    throw new InvalidOperationException("Investigation approach is obstructed: " + pose);
            var security = terminals.Single(t => t.Kind == IncidentTerminalKind.Security);
            var cooling = terminals.Single(t => t.Kind == IncidentTerminalKind.Cooling);
            if (Vector2.Distance(new Vector2(-1.5f, -1.9f) + capsule.offset, security.InteractionPoint) > security.Radius
                || Vector2.Distance(new Vector2(-.3f, -7.15f) + capsule.offset, cooling.InteractionPoint) > cooling.Radius
                || Vector2.Distance(new Vector2(.3f, -7.15f) + capsule.offset, cooling.InteractionPoint) > cooling.Radius)
                throw new InvalidOperationException("Investigation approaches must reach their actual terminals.");
            Debug.Log("ACTOR: investigation identities, three free approach poses and physical ranges passed.");
        }
    }
}
