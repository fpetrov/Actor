using Actor.Networking;
using Actor.Presentation;
using Unity.Netcode;
using UnityEngine;
using System;
using System.IO;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace Actor.World
{
    // Presentation only. Server observer filtering provides actual privacy.
    public sealed class SocialFogRenderer : MonoBehaviour
    {
        [SerializeField] private float radius = 3.5f;
        [SerializeField, Range(1, 16)] private float featherPixels = StationAtmosphereRules.FeatherPixels;
#if !UNITY_SERVER
        private const int Rays = 192;
        private readonly Vector3[] vertices = new Vector3[(Rays + 1) * 3];
        private readonly Color[] colors = new Color[(Rays + 1) * 3];
        private readonly Vector2[] directions = new Vector2[Rays + 1];
        private Mesh mesh;
        private Material material;
        private MeshRenderer view;
        private Camera viewCamera;
        private double nextRefresh, previousRefresh;
        private readonly float[] displayedDistances = new float[Rays + 1];
        private bool initialized;
        private Vector2 previousEye;
        private ulong previousEpoch;
        private float previousRadius;
        private bool opening;
        private FogPerformanceCounters performance;
        private string performancePath;
        private bool legacyTiming;
        [DllImport("libc", EntryPoint = "fchmod", SetLastError = true)]
        private static extern int PrivateFileMode(int descriptor, uint mode);
        public bool MatchesPresentedSight(Vector2 eye, ulong epoch) => view != null && view.enabled
            && BroadcastPresentationRules.FogMatches(previousEpoch, previousEye.x, previousEye.y, epoch, eye.x, eye.y, initialized);
        public void Configure(float value) => radius = float.IsFinite(value) ? Mathf.Clamp(value, 1, 20) : 3.5f;
        private void Awake()
        {
            if (Application.isBatchMode || SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null)
            { enabled = false; return; }
            for (int i = 0; i <= Rays; ++i)
            {
                float angle = i * Mathf.PI * 2 / Rays;
                directions[i] = new Vector2(Mathf.Cos(angle), Mathf.Sin(angle));
            }
            string requested = Environment.GetEnvironmentVariable("ACTOR_FOG_PERF_REPORT");
            string variant = Environment.GetEnvironmentVariable("ACTOR_FOG_PERF_VARIANT");
            Environment.SetEnvironmentVariable("ACTOR_FOG_PERF_REPORT", null);
            Environment.SetEnvironmentVariable("ACTOR_FOG_PERF_VARIANT", null);
            if (SafePerformancePath(requested) && (string.IsNullOrEmpty(variant) || variant == "legacy30"))
            {
                performancePath = requested; performance = new FogPerformanceCounters();
                legacyTiming = variant == "legacy30";
            }
            mesh = new Mesh { name = "Social Visibility Fog" }; mesh.MarkDynamic();
            var atmosphere = Resources.Load<Material>("Art/StationFog");
            // Missing/unsupported cosmetic assets retain the original opaque
            // black implementation rather than removing the privacy mask.
            material = atmosphere != null && atmosphere.shader.isSupported
                ? new Material(atmosphere) : new Material(Shader.Find("Sprites/Default"));
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
            return StationAtmosphereRules.FeatherWorldWidth(pixels, orthographicSize, pixelHeight);
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
            long started = performance != null ? Stopwatch.GetTimestamp() : 0;
            double geometryMs = 0, uploadMs = 0;
            int queries = 0;
            bool refreshed = false, sightChanged = false;
            bool permitted = PresentationSight.TryGet(out Vector2 center, out float permittedRadius, out ulong epoch);
            bool observer = PresentationSight.Observer;
            view.enabled = permitted || observer;
            if (!view.enabled) { initialized = false; return; }
            try
            {
                // Revoke an old hole immediately, without any additional sight query.
                // The new camera waits for the next scheduled 30Hz mesh before ACK.
                if (initialized && (!permitted || epoch != previousEpoch))
                {
                    for (int i = 0; i <= Rays; ++i)
                    {
                        Vector2 outer = center + directions[i] * 64;
                        vertices[i * 3] = vertices[i * 3 + 1] = new Vector3(center.x, center.y, -1);
                        vertices[i * 3 + 2] = new Vector3(outer.x, outer.y, -1);
                    }
                    initialized = false; mesh.vertices = vertices; mesh.RecalculateBounds();
                }
                double time = Time.realtimeSinceStartupAsDouble;
                if (permitted) Configure(permittedRadius);
                sightChanged = center.x != previousEye.x || center.y != previousEye.y
                    || epoch != previousEpoch || radius != previousRadius;
                // Human movement/opening always obtains current-eye LOS before changing
                // the mesh. Never translate or interpolate an old visibility polygon.
                // Broadcast keeps its scheduled geometry and exact presented-eye gate.
                if (!FogRefreshRules.Refresh(observer, legacyTiming, initialized, sightChanged, opening, time, nextRefresh)) return;
                nextRefresh = observer || legacyTiming ? time + FogRefreshRules.IdleInterval
                    : FogRefreshRules.NextIdleDeadline(time, nextRefresh);
                float elapsed = (float)(time - previousRefresh); previousRefresh = time;
                bool reset = !initialized || epoch != previousEpoch || (center - previousEye).sqrMagnitude > .25f || !permitted;
                if (viewCamera == null || !viewCamera.isActiveAndEnabled) viewCamera = Camera.main;
                float feather = viewCamera != null && viewCamera.orthographic
                    ? FeatherWorldWidth(featherPixels, viewCamera.orthographicSize, viewCamera.pixelHeight) : 0;
                transform.position = Vector3.zero;
                long geometryStarted = performance != null ? Stopwatch.GetTimestamp() : 0;
                opening = false;
                for (int i = 0; i <= Rays; ++i)
                {
                    Vector2 direction = directions[i];
                    Vector2 allowed = permitted ? SocialVisibilitySystem.SightEndpoint(center, center + direction * radius) : center;
                    float allowedDistance = Vector2.Distance(center, allowed);
                    float distance = TemporalFogRules.Step(allowedDistance, displayedDistances[i], elapsed, reset);
                    if (!observer && !legacyTiming) distance = FogRefreshRules.FinishOpening(allowedDistance, distance);
                    opening |= distance < allowedDistance;
                    displayedDistances[i] = distance;
                    Vector2 inner = center + direction * distance;
                    Vector2 inset = Inset(center, inner, feather);
                    vertices[i * 3] = new Vector3(inset.x, inset.y, -1);
                    vertices[i * 3 + 1] = new Vector3(inner.x, inner.y, -1);
                    Vector2 outer = center + direction * 64;
                    vertices[i * 3 + 2] = new Vector3(outer.x, outer.y, -1);
                }
                if (performance != null) geometryMs = Milliseconds(geometryStarted);
                queries = permitted ? Rays + 1 : 0; refreshed = true;
                initialized = permitted; previousEye = center; previousEpoch = epoch; previousRadius = radius;
                long uploadStarted = performance != null ? Stopwatch.GetTimestamp() : 0;
                mesh.vertices = vertices; mesh.RecalculateBounds();
                if (performance != null) uploadMs = Milliseconds(uploadStarted);
            }
            finally
            {
                if (performance != null && permitted && !observer)
                {
                    if (viewCamera == null) viewCamera = Camera.main;
                    double pixels = viewCamera != null && viewCamera.orthographic && viewCamera.orthographicSize > 0
                        ? viewCamera.pixelHeight / (2.0 * viewCamera.orthographicSize) : 0;
                    performance.Record(Time.realtimeSinceStartupAsDouble, Milliseconds(started), geometryMs, uploadMs,
                        queries, refreshed, sightChanged, Vector2.Distance(center, previousEye) * pixels);
                }
            }
        }
        private static double Milliseconds(long started) => (Stopwatch.GetTimestamp() - started) * 1000.0 / Stopwatch.Frequency;
        private static bool SafePerformancePath(string path)
        {
            try
            {
                if (string.IsNullOrEmpty(path) || path.Length > 1024 || !Path.IsPathRooted(path)
                    || Path.GetFullPath(path) != path || Path.GetExtension(path) != ".json"
                    || File.Exists(path) || Directory.Exists(path)) return false;
                var directory = new DirectoryInfo(Path.GetDirectoryName(path));
                if (!directory.Exists) return false;
                for (var parent = directory; parent != null; parent = parent.Parent)
                    if ((parent.Attributes & FileAttributes.ReparsePoint) != 0) return false;
                return true;
            }
            catch (Exception) { return false; }
        }
        private void OnDestroy()
        {
            if (performance != null && performancePath != null)
            {
                try
                {
                    if (SafePerformancePath(performancePath))
                    {
                        using var stream = new FileStream(performancePath, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                        bool privateFile = Application.platform != RuntimePlatform.OSXPlayer && Application.platform != RuntimePlatform.LinuxPlayer
                            || PrivateFileMode(stream.SafeFileHandle.DangerousGetHandle().ToInt32(), 384) == 0;
                        if (privateFile)
                        {
                            byte[] bytes = System.Text.Encoding.UTF8.GetBytes(JsonUtility.ToJson(performance.Snapshot(legacyTiming), true));
                            stream.Write(bytes, 0, bytes.Length);
                        }
                    }
                }
                catch (Exception) { } // Optional diagnostics cannot interrupt normal cleanup.
            }
            if (mesh != null) Destroy(mesh);
            if (material != null) Destroy(material);
        }
#else
        public bool MatchesPresentedSight(Vector2 eye, ulong epoch) => false;
        public void Configure(float value) { }
#endif
    }
}
