using Actor.Networking;
using Unity.Netcode;
using UnityEngine;

namespace Actor.World
{
    // Presentation only. Server observer filtering provides actual privacy.
    public sealed class SocialFogRenderer : MonoBehaviour
    {
        [SerializeField] private float radius = 3.5f;
        [SerializeField, Range(1, 3)] private float featherPixels = 2;
#if !UNITY_SERVER
        private const int Rays = 192;
        private readonly Vector3[] vertices = new Vector3[(Rays + 1) * 3];
        private readonly Color[] colors = new Color[(Rays + 1) * 3];
        private Mesh mesh;
        private Material material;
        private MeshRenderer view;
        private Camera viewCamera;
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
            SetColors(colors);
            mesh.vertices = vertices; mesh.colors = colors; mesh.triangles = CreateTriangles();
        }
        private static void SetColors(Color[] values)
        {
            for (int i = 0; i < values.Length; ++i) values[i] = i % 3 == 0 ? Color.clear : Color.black;
        }
        private static int[] CreateTriangles()
        {
            var triangles = new int[Rays * 12];
            for (int i = 0; i < Rays; ++i)
            {
                int k = i * 12, a = i * 3;
                // B/C preserve the original fully opaque annulus, unchanged.
                triangles[k] = a + 1; triangles[k + 1] = a + 2; triangles[k + 2] = a + 4;
                triangles[k + 3] = a + 2; triangles[k + 4] = a + 5; triangles[k + 5] = a + 4;
                // Only add darkness INSIDE the old visible polygon: A(alpha0) -> B(alpha1).
                triangles[k + 6] = a; triangles[k + 7] = a + 1; triangles[k + 8] = a + 3;
                triangles[k + 9] = a + 1; triangles[k + 10] = a + 4; triangles[k + 11] = a + 3;
            }
            return triangles;
        }
        private static float FeatherWorldWidth(float pixels, float orthographicSize, int pixelHeight)
        {
            if (!float.IsFinite(pixels) || !float.IsFinite(orthographicSize) || orthographicSize <= 0 || pixelHeight <= 0) return 0;
            float width = 2 * orthographicSize / pixelHeight * Mathf.Clamp(pixels, 1, 3);
            return float.IsFinite(width) ? width : 0;
        }
        private static Vector2 Inset(Vector2 center, Vector2 endpoint, float width)
        {
            if (!float.IsFinite(width) || width <= 0) return endpoint;
            Vector2 delta = endpoint - center;
            float distance = delta.magnitude;
            if (distance <= .000001f) return center;
            if (!float.IsFinite(distance)) return endpoint;
            return center + delta * Mathf.Clamp01(1 - width / distance);
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
            if (viewCamera == null || !viewCamera.isActiveAndEnabled) viewCamera = Camera.main;
            float feather = viewCamera != null && viewCamera.orthographic
                ? FeatherWorldWidth(featherPixels, viewCamera.orthographicSize, viewCamera.pixelHeight) : 0;
            transform.position = Vector3.zero;
            for (int i = 0; i <= Rays; ++i)
            {
                float angle = i * Mathf.PI * 2 / Rays;
                Vector2 direction = new(Mathf.Cos(angle), Mathf.Sin(angle));
                Vector2 inner = SocialVisibilitySystem.SightEndpoint(center, center + direction * radius);
                Vector2 inset = Inset(center, inner, feather);
                vertices[i * 3] = new Vector3(inset.x, inset.y, -1);
                vertices[i * 3 + 1] = new Vector3(inner.x, inner.y, -1);
                Vector2 outer = center + direction * 64;
                vertices[i * 3 + 2] = new Vector3(outer.x, outer.y, -1);
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
