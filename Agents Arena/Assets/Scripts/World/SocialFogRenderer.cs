using Actor.Networking;
using Actor.Presentation;
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
        private double nextRefresh, previousRefresh;
        private readonly float[] displayedDistances = new float[Rays + 1];
        private bool initialized;
        private Vector2 previousEye;
        private ulong previousEpoch;
        public bool MatchesPresentedSight(Vector2 eye, ulong epoch) => view != null && view.enabled
            && BroadcastPresentationRules.FogMatches(previousEpoch, previousEye.x, previousEye.y, epoch, eye.x, eye.y, initialized);
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
            bool permitted = PresentationSight.TryGet(out Vector2 center, out float permittedRadius, out ulong epoch);
            view.enabled = permitted || PresentationSight.Observer;
            if (!view.enabled) { initialized = false; return; }
            // Revoke an old hole immediately, without any additional sight query.
            // The new camera waits for the next scheduled 30Hz mesh before ACK.
            if (initialized && (!permitted || epoch != previousEpoch))
            {
                for (int i = 0; i <= Rays; ++i)
                {
                    float angle = i * Mathf.PI * 2 / Rays;
                    Vector2 outer = center + new Vector2(Mathf.Cos(angle), Mathf.Sin(angle)) * 64;
                    vertices[i * 3] = vertices[i * 3 + 1] = new Vector3(center.x, center.y, -1);
                    vertices[i * 3 + 2] = new Vector3(outer.x, outer.y, -1);
                }
                initialized = false; mesh.vertices = vertices; mesh.RecalculateBounds();
            }
            if (Time.realtimeSinceStartupAsDouble < nextRefresh) return;
            double time = Time.realtimeSinceStartupAsDouble;
            nextRefresh = time + 1.0 / 30;
            float elapsed = (float)(time - previousRefresh); previousRefresh = time;
            bool reset = !initialized || epoch != previousEpoch || (center - previousEye).sqrMagnitude > .25f || !permitted;
            if (permitted) Configure(permittedRadius);
            if (viewCamera == null || !viewCamera.isActiveAndEnabled) viewCamera = Camera.main;
            float feather = viewCamera != null && viewCamera.orthographic
                ? FeatherWorldWidth(featherPixels, viewCamera.orthographicSize, viewCamera.pixelHeight) : 0;
            transform.position = Vector3.zero;
            for (int i = 0; i <= Rays; ++i)
            {
                float angle = i * Mathf.PI * 2 / Rays;
                Vector2 direction = new(Mathf.Cos(angle), Mathf.Sin(angle));
                Vector2 allowed = permitted ? SocialVisibilitySystem.SightEndpoint(center, center + direction * radius) : center;
                float distance = TemporalFogRules.Step(Vector2.Distance(center, allowed), displayedDistances[i], elapsed, reset);
                displayedDistances[i] = distance;
                Vector2 inner = center + direction * distance;
                Vector2 inset = Inset(center, inner, feather);
                vertices[i * 3] = new Vector3(inset.x, inset.y, -1);
                vertices[i * 3 + 1] = new Vector3(inner.x, inner.y, -1);
                Vector2 outer = center + direction * 64;
                vertices[i * 3 + 2] = new Vector3(outer.x, outer.y, -1);
            }
            initialized = permitted; previousEye = center; previousEpoch = epoch;
            mesh.vertices = vertices; mesh.RecalculateBounds();
        }
        private void OnDestroy()
        {
            if (mesh != null) Destroy(mesh);
            if (material != null) Destroy(material);
        }
#else
        public bool MatchesPresentedSight(Vector2 eye, ulong epoch) => false;
        public void Configure(float value) { }
#endif
    }
}
