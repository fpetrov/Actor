using System;
using System.Linq;
using Actor.Networking;
using Actor.World;
using UnityEditor;
using UnityEngine;

namespace Actor.Editor
{
    public static class CooperativeWorldBuilder
    {
        public static void AddToCurrentMultiplayerScene()
        {
            Create(GameObject.Find("Main Hall").transform, CooperativeTerminalKind.Diagnostic,
                new Vector2(1.3f, -.4f), new Vector2(.42f, .20f), new Color(.18f, .75f, .80f));
            Create(GameObject.Find("Engineering").transform, CooperativeTerminalKind.Calibration,
                new Vector2(.8f, -6.0f), new Vector2(.16f, .44f), new Color(.80f, .52f, .95f));
            new GameObject("Authoritative Cooperative Session").AddComponent<CooperativeSession>();
            Validate();
        }
        private static void Create(Transform parent, CooperativeTerminalKind kind, Vector2 position, Vector2 colliderSize, Color accent)
        {
            var root = new GameObject(kind == CooperativeTerminalKind.Diagnostic ? "Diagnostic Terminal" : "Calibration Console");
            root.transform.SetParent(parent, false); root.transform.position = position;
            root.AddComponent<CooperativeTerminal2D>().Configure(kind);
            var collider = root.AddComponent<BoxCollider2D>(); collider.size = colliderSize;
            collider.offset = kind == CooperativeTerminalKind.Diagnostic ? new Vector2(0, -.14f) : Vector2.zero;
            var data = new SerializedObject(root.AddComponent<WorldEntity>());
            data.FindProperty("entityId").stringValue = kind == CooperativeTerminalKind.Diagnostic ? "main-hall-diagnostic-01" : "engineering-calibration-01";
            data.FindProperty("roomId").stringValue = kind == CooperativeTerminalKind.Diagnostic ? RoomId.MainHall : RoomId.Engineering;
            data.ApplyModifiedPropertiesWithoutUndo();
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
            var terminals = UnityEngine.Object.FindObjectsByType<CooperativeTerminal2D>();
            if (terminals.Length != 2 || UnityEngine.Object.FindObjectsByType<CooperativeSession>().Length != 1)
                throw new InvalidOperationException("Two terminals and one authoritative scene session are required.");
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(MultiplayerBuild.PlayerPrefab);
            var capsule = prefab.GetComponent<CapsuleCollider2D>(); Physics2D.SyncTransforms();
            var filter = new ContactFilter2D { useTriggers = false }; filter.SetLayerMask(Physics2D.GetLayerCollisionMask(prefab.layer));
            foreach (var pose in new[] { new Vector2(.9f, -2.4f), new Vector2(0, -1.4f), new Vector2(0, -7) })
                if (Physics2D.OverlapCapsule(pose + capsule.offset, capsule.size, capsule.direction, 0, filter, new Collider2D[32]) != 0)
                    throw new InvalidOperationException("Cooperative interaction pose is obstructed: " + pose);
            var diagnostic = terminals.Single(t => t.Kind == CooperativeTerminalKind.Diagnostic);
            var calibration = terminals.Single(t => t.Kind == CooperativeTerminalKind.Calibration);
            if (Vector2.Distance(new Vector2(.9f, -2.4f) + capsule.offset, diagnostic.InteractionPoint) > diagnostic.Radius
                || Vector2.Distance(new Vector2(0, -7) + capsule.offset, calibration.InteractionPoint) > calibration.Radius)
                throw new InvalidOperationException("Installed navigation poses must reach both terminals.");
            Debug.Log("ACTOR: cooperative terminal identities, three physical poses and interaction distances passed.");
        }
    }
}
