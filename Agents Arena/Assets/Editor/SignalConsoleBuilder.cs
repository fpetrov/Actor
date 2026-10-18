using System;
using System.Linq;
using Actor.Networking;
using Actor.World;
using UnityEditor;
using UnityEngine;

namespace Actor.Editor
{
    public static class SignalConsoleBuilder
    {
        public static readonly Vector2 Position = new(-2.6f, -.4f);

        public static void AddToCurrentMultiplayerScene()
        {
            var hall = GameObject.Find("Main Hall");
            if (hall == null) throw new InvalidOperationException("Signal Console requires the authored multiplayer Main Hall.");
            var root = new GameObject("Signal Console");
            root.transform.SetParent(hall.transform, false);
            root.transform.position = Position;
            root.AddComponent<SignalConsole>();
            var collider = root.AddComponent<BoxCollider2D>();
            collider.size = new Vector2(.62f, .22f); collider.offset = new Vector2(0, -.14f);
            var entity = new SerializedObject(root.AddComponent<WorldEntity>());
            entity.FindProperty("entityId").stringValue = SignalConsole.EntityId;
            entity.FindProperty("roomId").stringValue = RoomId.MainHall;
            entity.ApplyModifiedPropertiesWithoutUndo();
            var sprite = AssetDatabase.LoadAssetAtPath<Sprite>("Assets/Sprites/Rooms/GatePanel.asset");
            var material = hall.GetComponent<SpriteRenderer>().sharedMaterial;
            Panel(root.transform, "Housing", sprite, material, Vector2.zero, new Vector2(.74f, .50f), new Color(.12f, .13f, .23f), -10);
            Panel(root.transform, "Metal Rim", sprite, material, new Vector2(0, .035f), new Vector2(.65f, .37f), new Color(.35f, .36f, .49f), -9);
            Panel(root.transform, "Display", sprite, material, new Vector2(0, .07f), new Vector2(.53f, .22f), new Color(.23f, .77f, .90f), -8);
            Panel(root.transform, "Display Detail", sprite, material, new Vector2(-.06f, .07f), new Vector2(.30f, .025f), new Color(.76f, .97f, 1f), -7);
            Panel(root.transform, "Routine Button", sprite, material, new Vector2(-.12f, -.12f), new Vector2(.08f, .045f), new Color(.23f, .77f, .90f), -7);
            Panel(root.transform, "Recall Button", sprite, material, new Vector2(.12f, -.12f), new Vector2(.08f, .045f), new Color(.95f, .56f, .31f), -7);
            ValidateCurrentMultiplayerScene();
        }

        private static void Panel(Transform parent, string name, Sprite sprite, Material material, Vector2 offset,
            Vector2 size, Color color, int order)
        {
            var panel = new GameObject(name); panel.transform.SetParent(parent, false);
            panel.transform.localPosition = offset; panel.transform.localScale = new Vector3(size.x, size.y, 1);
            var renderer = panel.AddComponent<SpriteRenderer>();
            renderer.sprite = sprite; renderer.sharedMaterial = material; renderer.color = color; renderer.sortingOrder = order;
        }

        public static void ValidateCurrentMultiplayerScene()
        {
            var consoles = UnityEngine.Object.FindObjectsByType<SignalConsole>();
            if (consoles.Length != 1) throw new InvalidOperationException("Exactly one authored Signal Console is required.");
            var console = consoles[0];
            if (console.GetComponent<WorldEntity>().EntityId != SignalConsole.EntityId
                || console.GetComponent<WorldEntity>().RoomId != RoomId.MainHall
                || !UnityEngine.Object.FindObjectsByType<RoomVolume2D>().Single(r => r.Id == RoomId.MainHall).Contains(Position))
                throw new InvalidOperationException("Signal Console must have its fixed identity on Main Hall floor.");
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(MultiplayerBuild.PlayerPrefab);
            if (prefab.GetComponent<NetworkStationSignals>() == null) throw new InvalidOperationException("The ordinary player prefab must contain the signal RPC endpoint.");
            Vector2 secondPlayer = (Vector2)MatchSession.SpawnPosition(1) + prefab.GetComponent<CapsuleCollider2D>().offset;
            if (Vector2.Distance(secondPlayer, console.InteractionPosition) > SignalConsole.InteractionRadius)
                throw new InvalidOperationException("The Human observer must be able to reach the console from the normal second spawn.");
            Debug.Log("ACTOR: Signal Console identity, floor, owned endpoint and Human spawn interaction checks passed.");
        }
    }
}
