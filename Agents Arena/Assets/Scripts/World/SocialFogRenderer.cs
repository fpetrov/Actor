using Actor.Networking;
using Unity.Netcode;
using UnityEngine;

namespace Actor.World
{
    // Presentation only. Server observer filtering provides actual privacy.
    public sealed class SocialFogRenderer : MonoBehaviour
    {
        [SerializeField] private float radius = 3.5f;
#if !UNITY_SERVER
        private const int Rays = 192;
        private readonly Vector3[] vertices = new Vector3[(Rays + 1) * 2];
        private readonly Color[] colors = new Color[(Rays + 1) * 2];
        private Mesh mesh;
        private Material material;
        private MeshRenderer view;
        private double nextRefresh;
        public void Configure(float value) => radius = float.IsFinite(value) ? Mathf.Clamp(value, 1, 20) : 3.5f;
        private void Awake()
        {
            if (Application.isBatchMode || SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null)
            { enabled = false; return; }
            mesh = new Mesh { name = "Social Visibility Fog" }; mesh.MarkDynamic();
            material = new Material(Shader.Find("Sprites/Default"));
            gameObject.AddComponent<MeshFilter>().sharedMesh = mesh;
            view = gameObject.AddComponent<MeshRenderer>(); view.sharedMaterial = material; view.sortingOrder = 2000;
            var triangles = new int[Rays * 6];
            for (int i = 0; i < colors.Length; ++i) colors[i] = Color.black;
            for (int i = 0; i < Rays; ++i)
            {
                int k = i * 6, a = i * 2;
                triangles[k] = a; triangles[k + 1] = a + 1; triangles[k + 2] = a + 2;
                triangles[k + 3] = a + 1; triangles[k + 4] = a + 3; triangles[k + 5] = a + 2;
            }
            mesh.vertices = vertices; mesh.colors = colors; mesh.triangles = triangles;
        }
        private void LateUpdate()
        {
            if (view == null) return;
            var manager = NetworkManager.Singleton;
            var owner = manager != null && manager.IsConnectedClient ? manager.SpawnManager.GetLocalPlayerObject() : null;
            view.enabled = owner != null && SocialVisibilitySystem.IsSocial(manager);
            if (!view.enabled || Time.realtimeSinceStartupAsDouble < nextRefresh) return;
            nextRefresh = Time.realtimeSinceStartupAsDouble + 1.0 / 30;
            var social = NetworkSocialMatch.LocalOwner;
            if (social != null && social.World.VisibilityRadius > 0) Configure((float)social.World.VisibilityRadius);
            Vector2 center = (Vector2)owner.GetComponent<NetworkCharacter>().PresentationPosition + owner.GetComponent<CapsuleCollider2D>().offset;
            transform.position = Vector3.zero;
            for (int i = 0; i <= Rays; ++i)
            {
                float angle = i * Mathf.PI * 2 / Rays;
                Vector2 direction = new(Mathf.Cos(angle), Mathf.Sin(angle));
                Vector2 inner = SocialVisibilitySystem.SightEndpoint(center, center + direction * radius);
                vertices[i * 2] = new Vector3(inner.x, inner.y, -1);
                Vector2 outer = center + direction * 64;
                vertices[i * 2 + 1] = new Vector3(outer.x, outer.y, -1);
            }
            mesh.vertices = vertices; mesh.RecalculateBounds();
        }
        private void OnDestroy()
        {
            if (mesh != null) Destroy(mesh);
            if (material != null) Destroy(material);
        }
#else
        public void Configure(float value) { }
#endif
    }
}
