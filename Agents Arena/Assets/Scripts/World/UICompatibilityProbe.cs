using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.UIElements;

namespace Actor.World
{
    // Explicit, offline renderer probe. Never joins a room or reads game data.
    public sealed class UICompatibilityProbe : MonoBehaviour
    {
#if !UNITY_SERVER
        [Serializable] private sealed class Result
        {
            public string schema = "actor.ui-compatibility.v1";
            public string scope = "Rendered standalone UI Toolkit/URP screen-overlay probe; no multiplayer or Human-input acceptance.";
            public bool passed, native_backdrop_supported, translucent_fallback_rendered;
            public string unity, graphics, pipeline, renderer, failure = "";
            public int width, height, baseline_frames, blur_frames;
            public float baseline_edge_energy, blur_edge_energy, baseline_frame_ms_mean, blur_frame_ms_mean;
        }
        private string directory;
        private VisualElement glass;
        private readonly List<float> baselineFrames = new(), blurFrames = new();
        private Result result;
        private readonly List<string> errors = new();

        public static bool Requested => !string.IsNullOrEmpty(Argument());
        private static string Argument()
        {
            var args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "--ui-compat-probe");
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
        }
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap()
        {
            string path = Argument();
            if (string.IsNullOrEmpty(path)) return;
            var probe = new GameObject("Offline UI Compatibility Probe").AddComponent<UICompatibilityProbe>();
            probe.directory = Path.GetFullPath(path);
        }
        private IEnumerator Start()
        {
            Directory.CreateDirectory(directory);
            Application.logMessageReceived += Log;
            Application.targetFrameRate = 60;
            QualitySettings.vSyncCount = 1;
            foreach (var view in FindObjectsByType<Actor.Lobby.LobbyMenuView>()) view.enabled = false;
            foreach (var controller in FindObjectsByType<Actor.Lobby.LobbyMenuController>()) controller.enabled = false;
            foreach (var controller in FindObjectsByType<Actor.GameAdapter.ActorControlController>()) controller.enabled = false;
            result = new Result { unity = Application.unityVersion, graphics = SystemInfo.graphicsDeviceType.ToString(),
                width = Screen.width, height = Screen.height, pipeline = GraphicsSettings.currentRenderPipeline?.GetType().Name ?? "none" };
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
            { Finish("graphics_device_missing"); yield break; }
            try { Build(); }
            catch (Exception error) { Finish("setup_" + error.GetType().Name); yield break; }
            for (int i = 0; i < 30; ++i) yield return null;
            for (int i = 0; i < 90; ++i) { yield return null; baselineFrames.Add(Time.unscaledDeltaTime * 1000); }
            yield return new WaitForEndOfFrame();
            result.baseline_edge_energy = Capture("tinted-fallback.png");
            var blur = new FilterFunction(FilterFunctionType.Blur);
            blur.AddParameter(new FilterParameter(8f));
            glass.style.backdropFilter = new List<FilterFunction> { blur };
            for (int i = 0; i < 30; ++i) yield return null;
            for (int i = 0; i < 90; ++i) { yield return null; blurFrames.Add(Time.unscaledDeltaTime * 1000); }
            yield return new WaitForEndOfFrame();
            result.blur_edge_energy = Capture("native-blur.png");
            result.baseline_frames = baselineFrames.Count; result.blur_frames = blurFrames.Count;
            result.baseline_frame_ms_mean = baselineFrames.Average(); result.blur_frame_ms_mean = blurFrames.Average();
            result.translucent_fallback_rendered = result.baseline_edge_energy > .01f;
            result.native_backdrop_supported = result.translucent_fallback_rendered
                && result.blur_edge_energy < result.baseline_edge_energy * .8f;
            result.passed = result.translucent_fallback_rendered && errors.Count == 0;
            Finish(errors.Count == 0 ? "" : "unity_error");
        }
        private void Build()
        {
            var camera = Camera.main;
            if (camera == null) camera = new GameObject("Probe Camera").AddComponent<Camera>();
            camera.orthographic = true; camera.orthographicSize = 3;
            camera.transform.position = new Vector3(0, 0, -10);
            result.renderer = camera.GetUniversalAdditionalCameraData().scriptableRenderer.GetType().Name;
            var texture = new Texture2D(256, 256, TextureFormat.RGBA32, false) { filterMode = FilterMode.Point };
            for (int y = 0; y < 256; ++y)
                for (int x = 0; x < 256; ++x)
                    texture.SetPixel(x, y, ((x / 4 + y / 4) % 2) == 0 ? new Color(.8f,.85f,.95f) : new Color(.08f,.13f,.23f));
            texture.Apply();
            var checker = new GameObject("URP Camera Backdrop Checker").AddComponent<SpriteRenderer>();
            checker.sprite = Sprite.Create(texture, new Rect(0,0,256,256), new Vector2(.5f,.5f), 32);
            checker.sharedMaterial = new Material(Shader.Find("Universal Render Pipeline/2D/Sprite-Unlit-Default")
                ?? Shader.Find("Universal Render Pipeline/2D/Sprite-Lit-Default") ?? Shader.Find("Sprites/Default"));
            var settings = Resources.Load<PanelSettings>("UI/ArenaPanelSettings");
            if (settings == null) throw new InvalidDataException("Prepare runtime UI resources before building.");
            settings = Instantiate(settings);
            settings.scaleMode = PanelScaleMode.ConstantPixelSize;
            settings.renderMode = PanelRenderMode.ScreenSpaceOverlay;
            var document = new GameObject("UI Toolkit Screen Overlay").AddComponent<UIDocument>();
            document.panelSettings = settings;
            var root = document.rootVisualElement;
            root.styleSheets.Add(Resources.Load<StyleSheet>("UI/Compatibility"));
            root.style.position = Position.Absolute; root.style.left = 0; root.style.top = 0;
            root.style.right = 0; root.style.bottom = 0;
            root.style.unityFontDefinition = FontDefinition.FromFont(Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"));
            glass = Card(root, 32, new Color(.04f,.08f,.14f,.38f), "NATIVE GLASS", "Связь установлена · Ready");
            Card(root, 432, new Color(.04f,.08f,.14f,.88f), "TINTED FALLBACK", "800 × 600 · Local UI");
        }
        private static VisualElement Card(VisualElement root, float x, Color tint, string title, string subtitle)
        {
            var card = new VisualElement(); card.style.position = Position.Absolute;
            card.style.left = x; card.style.top = 60; card.style.width = 320; card.style.height = 440;
            card.style.backgroundColor = tint;
            card.style.borderTopLeftRadius = card.style.borderTopRightRadius = 18;
            card.style.borderBottomLeftRadius = card.style.borderBottomRightRadius = 18;
            card.style.paddingLeft = 20; card.style.paddingTop = 22;
            var heading = new Label(title); heading.style.fontSize = 22; heading.style.color = Color.white;
            var description = new Label(subtitle); description.style.fontSize = 16; description.style.color = new Color(.8f,.9f,1);
            description.style.marginTop = 12;
            card.Add(heading); card.Add(description); root.Add(card); return card;
        }
        private float Capture(string name)
        {
            var image = ScreenCapture.CaptureScreenshotAsTexture();
            File.WriteAllBytes(Path.Combine(directory, name), image.EncodeToPNG());
            double energy = 0; int samples = 0;
            for (int y = 150; y < Math.Min(340, image.height - 1); ++y)
                for (int x = 60; x < Math.Min(300, image.width - 1); ++x)
                { energy += Mathf.Abs(image.GetPixel(x,y).grayscale - image.GetPixel(x+1,y).grayscale); ++samples; }
            Destroy(image); return samples == 0 ? 0 : (float)(energy / samples);
        }
        private void Log(string message, string stack, LogType type)
        { if (type is LogType.Error or LogType.Assert or LogType.Exception) errors.Add(type.ToString()); }
        private void Finish(string failure)
        {
            result.failure = failure;
            File.WriteAllText(Path.Combine(directory, "compatibility.json"), JsonUtility.ToJson(result, true));
            Application.logMessageReceived -= Log;
            Application.Quit(result.passed ? 0 : 1);
        }
#endif
    }
}
