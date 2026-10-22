using System;
using System.Collections.Generic;
using System.Linq;
using Actor.Characters;
using Actor.Lobby;
using Actor.World;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.UIElements;

namespace Actor.UI
{
    // One retained overlay for every player-facing flow. World labels only use
    // already-permitted objects; this host never reads game authority/state.
    [DefaultExecutionOrder(-100)]
    public sealed class ArenaUI : MonoBehaviour
    {
#if !UNITY_SERVER
        private static ArenaUI instance;
        private UIDocument document;
        private PanelSettings settings;
        private VisualElement world, hud, screens, modal, shade;
        private Label notification;
        private double noticeUntil;
        private int textFocus;
        private Keyboard imeKeyboard;
        private bool imeComposing;
        private bool hasModal;
        private VisualElement previousFocus;
        public static VisualElement WorldLayer => Ensure()?.world;
        public static VisualElement HudLayer => Ensure()?.hud;
        public static VisualElement ScreenLayer => Ensure()?.screens;
        public static bool InputCaptured => instance != null && (instance.textFocus > 0 || instance.hasModal || instance.DropdownFocused());
        public static event Action SettingsChanged;
        public static event Action EscapeRequested;
        public static bool IMEComposing => instance != null && instance.imeComposing;
        public static float MasterVolume => PlayerPrefs.GetFloat("Arena.Master", .8f);
        public static float MusicVolume => PlayerPrefs.GetFloat("Arena.Music", .35f);
        public static float EffectsVolume => PlayerPrefs.GetFloat("Arena.Effects", .65f);
        public static bool ReducedMotion => PlayerPrefs.GetInt("Arena.ReducedMotion", 0) != 0;
        public static bool Blur => PlayerPrefs.GetInt("Arena.Blur", 1) != 0;
        public static float UIScale => PlayerPrefs.GetFloat("Arena.UIScale", 1);
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap() { if (!UICompatibilityProbe.Requested) Ensure(); }
        private static ArenaUI Ensure()
        {
            if (!Application.isPlaying || UICompatibilityProbe.Requested || Application.isBatchMode && SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null) return null;
            if (instance != null) return instance;
            var root = new GameObject("ACTOR Retained UI"); DontDestroyOnLoad(root);
            instance = root.AddComponent<ArenaUI>(); return instance;
        }
        private void Awake()
        {
            instance = this;
            settings = Instantiate(Resources.Load<PanelSettings>("UI/ArenaPanelSettings"));
            settings.scaleMode = PanelScaleMode.ConstantPixelSize;
            settings.scale = UIScale;
            document = gameObject.AddComponent<UIDocument>(); document.panelSettings = settings;
            document.sortingOrder = 100;
            var root = document.rootVisualElement; root.name = "arena-root";
            root.focusable = true; root.tabIndex = -1;
            root.styleSheets.Add(Resources.Load<StyleSheet>("UI/Arena"));
            var font = Resources.Load<Font>("UI/Fonts/NotoSans-Regular");
            root.style.unityFontDefinition = FontDefinition.FromFont(font != null ? font : Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"));
            world = Layer(root, "world-layer"); hud = Layer(root, "hud-layer");
            screens = Layer(root, "screen-layer"); modal = Layer(root, "modal-layer");
            shade = new VisualElement { name = "modal-shade" }; shade.AddToClassList("modal-shade");
            notification = Text("", "toast"); notification.pickingMode = PickingMode.Ignore; root.Add(notification); Show(notification, false);
            root.RegisterCallback<FocusInEvent>(e => { if (e.target is VisualElement v && InTextField(v)) textFocus = 1; });
            root.RegisterCallback<FocusOutEvent>(e => { textFocus = 0; imeComposing = false; });
            root.RegisterCallback<KeyDownEvent>(e =>
            {
                if (e.keyCode == KeyCode.Escape && !imeComposing)
                {
                    if (hasModal) CloseModal();
                    else { root.Focus(); textFocus = 0; EscapeRequested?.Invoke(); }
                    e.StopImmediatePropagation();
                }
                else if (e.keyCode == KeyCode.Tab && hasModal)
                {
                    var controls = modal.Query<VisualElement>().ToList().Where(v => v.focusable && v.enabledInHierarchy && v.resolvedStyle.display != DisplayStyle.None
                        && (v is Button || v is TextField || v is Toggle || v is Slider || v is DropdownField)).ToList();
                    if (controls.Count == 0) return;
                    var focused = root.focusController?.focusedElement as VisualElement;
                    int index = controls.FindIndex(v => v == focused || focused != null && v.Contains(focused));
                    controls[(index + (e.shiftKey ? controls.Count - 1 : 1)) % controls.Count].Focus();
                    e.StopImmediatePropagation(); e.PreventDefault();
                }
            }, TrickleDown.TrickleDown);
            AudioListener.volume = MasterVolume;
            var arguments = Environment.GetCommandLineArgs();
            if (!arguments.Contains("-screen-fullscreen") && PlayerPrefs.HasKey("Arena.Windowed"))
                Screen.fullScreenMode = PlayerPrefs.GetInt("Arena.Windowed", 1) != 0 ? FullScreenMode.Windowed : FullScreenMode.FullScreenWindow;
            if (!arguments.Contains("-screen-width") && !arguments.Contains("-screen-height") && PlayerPrefs.HasKey("Arena.Width"))
                Screen.SetResolution(PlayerPrefs.GetInt("Arena.Width", 800), PlayerPrefs.GetInt("Arena.Height", 600), Screen.fullScreenMode);
        }
        private static bool InTextField(VisualElement target)
        { for (var p = target; p != null; p = p.parent) if (p is TextField) return true; return false; }
        private bool DropdownFocused()
        {
            // Panel-root popup keyboard navigation must not also steer the actor.
            for (var p = document.rootVisualElement.focusController?.focusedElement as VisualElement; p != null; p = p.parent)
                if (p.ClassListContains("unity-base-dropdown")) return true;
            return false;
        }
        private static VisualElement Layer(VisualElement root, string name)
        { var layer = new VisualElement { name = name, pickingMode = PickingMode.Ignore }; layer.AddToClassList("layer"); root.Add(layer); return layer; }
        private void Composition(UnityEngine.InputSystem.LowLevel.IMECompositionString value) => imeComposing = value.Count > 0;
        private void TextCommitted(char value) => imeComposing = false;
        private void BindKeyboard()
        {
            if (imeKeyboard == Keyboard.current) return;
            if (imeKeyboard != null) { imeKeyboard.onIMECompositionChange -= Composition; imeKeyboard.onTextInput -= TextCommitted; }
            imeKeyboard = Keyboard.current;
            if (imeKeyboard != null) { imeKeyboard.onIMECompositionChange += Composition; imeKeyboard.onTextInput += TextCommitted; }
            imeComposing = false;
        }
        private void OnApplicationFocus(bool focused)
        {
            imeComposing = false;
            textFocus = document?.rootVisualElement.focusController?.focusedElement is VisualElement field && InTextField(field) ? 1 : 0;
        }
        private void Update()
        {
            BindKeyboard();
            if (notification != null && Time.realtimeSinceStartupAsDouble > noticeUntil) Show(notification, false);
            // A detached/hidden field cannot keep the keyboard captured.
            if (textFocus != 0 && document.rootVisualElement.focusController?.focusedElement is VisualElement field
                && (field.panel == null || field.resolvedStyle.display == DisplayStyle.None)) textFocus = 0;
        }
        public static VisualElement Element(string classes = "", VisualElement parent = null)
        { var e = new VisualElement(); Classes(e, classes); parent?.Add(e); return e; }
        public static Label Text(string value, string classes = "", VisualElement parent = null)
        { var e = new Label(value) { enableRichText = false }; Classes(e, classes); parent?.Add(e); return e; }
        public static Button Button(string value, Action action, string classes = "", VisualElement parent = null)
        {
            var e = new Button(() => { Actor.Presentation.ArenaAudio.PlayUI(Actor.Presentation.ArenaAudio.Cue.Click); action?.Invoke(); }) { text = value };
            e.RegisterCallback<PointerEnterEvent>(_ => { if (e.enabledInHierarchy) Actor.Presentation.ArenaAudio.PlayUI(Actor.Presentation.ArenaAudio.Cue.Hover); });
            Classes(e, classes); parent?.Add(e); return e;
        }
        public static TextField Field(string label, string value, Action<string> changed, int maxLength = 512, VisualElement parent = null)
        {
            var e = new TextField(label) { value = value ?? "", maxLength = maxLength, isDelayed = false };
            e.RegisterValueChangedCallback(v => changed(v.newValue)); parent?.Add(e); return e;
        }
        public static void Classes(VisualElement e, string classes)
        { foreach (string c in classes.Split(' ', StringSplitOptions.RemoveEmptyEntries)) e.AddToClassList(c); }
        public static void Show(VisualElement element, bool shown)
        {
            if (element == null) return;
            element.style.display = shown ? DisplayStyle.Flex : DisplayStyle.None;
            if (!shown && instance != null && instance.document.rootVisualElement.focusController?.focusedElement is VisualElement focused && element.Contains(focused))
            { instance.document.rootVisualElement.Focus(); instance.textFocus = 0; }
        }
        public static VisualElement Panel(string title, string classes = "", VisualElement parent = null)
        {
            var card = Element("panel " + classes, parent); Text(title, "heading", card); ApplyGlass(card); return card;
        }
        public static void ApplyGlass(VisualElement element)
        {
            element.EnableInClassList("glass", Blur);
            // USS retains the shader in builds; fallback has identical contrast.
        }
        public static void WorldLabel(VisualElement label, Vector3 position, float width = 160)
        {
            var host = Ensure(); if (host == null || label == null) return;
            if (host.world.panel == null) { Show(label, false); return; }
            var camera = Camera.main;
            if (camera == null) { Show(label, false); return; }
            Vector3 p = camera.WorldToScreenPoint(position);
            bool shown = p.z > 0 && p.x >= 0 && p.x <= Screen.width && p.y >= 0 && p.y <= Screen.height;
            Show(label, shown); if (!shown) return;
            Vector2 panel = RuntimePanelUtils.ScreenToPanel(host.world.panel, new Vector2(p.x, Screen.height - p.y));
            label.style.position = Position.Absolute; label.style.width = width;
            label.style.left = panel.x - width / 2; label.style.top = panel.y - 12;
            label.pickingMode = PickingMode.Ignore; label.AddToClassList("world-label");
        }
        public static void Toast(string message)
        {
            var host = Ensure(); if (host == null) return;
            host.notification.text = message; Show(host.notification, true);
            host.noticeUntil = Time.realtimeSinceStartupAsDouble + 4;
        }
        public static VisualElement OpenModal(string title)
        {
            var host = Ensure(); if (host == null) return null;
            var restoreFocus = host.hasModal ? host.previousFocus : host.document.rootVisualElement.focusController?.focusedElement as VisualElement;
            CloseModal(); host.previousFocus = restoreFocus;
            host.hasModal = true; host.textFocus = 0; host.modal.Add(host.shade);
            var card = Panel(title, "modal-card", host.shade);
            var close = Button("Close", CloseModal, "quiet modal-close", card);
            close.schedule.Execute(() => close.Focus());
            return card;
        }
        public static void CloseModal()
        {
            if (instance == null) return;
            instance.document.rootVisualElement.Focus(); instance.textFocus = 0;
            instance.modal.Clear(); instance.shade.Clear(); instance.hasModal = false;
            if (instance.previousFocus?.panel != null) instance.previousFocus.Focus();
            instance.previousFocus = null;
        }
        public static void Settings()
        {
            var card = OpenModal("Settings"); if (card == null) return;
            var scroll = new ScrollView(); card.Add(scroll);
            Slider(scroll, "Master volume", "Arena.Master", .8f, 0, 1);
            Slider(scroll, "Music", "Arena.Music", .35f, 0, 1);
            Slider(scroll, "Effects", "Arena.Effects", .65f, 0, 1);
            Slider(scroll, "Interface scale", "Arena.UIScale", 1, .85f, 1.25f);
            Toggle(scroll, "Background blur", "Arena.Blur", true);
            Toggle(scroll, "Reduced motion", "Arena.ReducedMotion", false);
            var window = new Toggle("Windowed") { value = !Screen.fullScreen };
            window.RegisterValueChangedCallback(e => { Screen.fullScreenMode = e.newValue ? FullScreenMode.Windowed : FullScreenMode.FullScreenWindow; PlayerPrefs.SetInt("Arena.Windowed", e.newValue ? 1 : 0); PlayerPrefs.Save(); }); scroll.Add(window);
            var sizes = new List<string> { "800 × 600", "1280 × 720", "1920 × 1080", "2560 × 1080" };
            int selectedSize = sizes.IndexOf(Screen.width + " × " + Screen.height);
            var resolution = new DropdownField("Resolution", sizes, Math.Max(0, selectedSize));
            resolution.RegisterValueChangedCallback(e =>
            {
                int index = sizes.IndexOf(e.newValue);
                int[] widths = { 800, 1280, 1920, 2560 }, heights = { 600, 720, 1080, 1080 };
                Screen.SetResolution(widths[index], heights[index], Screen.fullScreenMode);
                PlayerPrefs.SetInt("Arena.Width", widths[index]); PlayerPrefs.SetInt("Arena.Height", heights[index]); PlayerPrefs.Save();
            }); scroll.Add(resolution);
            Text("Settings are local. The multiplayer match continues while this menu is open.", "muted", scroll);
        }
        private static void Slider(VisualElement parent, string title, string key, float initial, float low, float high)
        {
            var slider = new Slider(title, low, high) { value = PlayerPrefs.GetFloat(key, initial), showInputField = true }; parent.Add(slider);
            slider.RegisterValueChangedCallback(e => { PlayerPrefs.SetFloat(key, e.newValue); Changed(); });
        }
        private static void Toggle(VisualElement parent, string title, string key, bool initial)
        {
            var toggle = new Toggle(title) { value = PlayerPrefs.GetInt(key, initial ? 1 : 0) != 0 }; parent.Add(toggle);
            toggle.RegisterValueChangedCallback(e => { PlayerPrefs.SetInt(key, e.newValue ? 1 : 0); Changed(); });
        }
        private static void Changed()
        {
            PlayerPrefs.Save(); AudioListener.volume = MasterVolume;
            if (instance != null) { instance.settings.scale = UIScale; instance.document.rootVisualElement.Query<VisualElement>(className: "panel").ForEach(ApplyGlass); }
            SettingsChanged?.Invoke();
        }
        public static void Tutorial()
        {
            var card = OpenModal("Welcome to Agents Arena"); if (card == null) return;
            var scroll = new ScrollView(); card.Add(scroll);
            Text("Move with WASD or arrow keys. E opens nearby tasks and actions; T opens messages. Text entry captures the keyboard.", "body", scroll);
            Text("Ready up with 4–6 players. Your assignment is private. Crew complete physical work and investigate; an Impostor blends in, sabotages and eliminates nearby targets.", "body", scroll);
            Text("Only what you can currently see is available. A player message is a claim, not proof. Report a visible body or use the meeting console. Discuss, select a candidate or Skip, then confirm one final ballot.", "body", scroll);
            Text("After death you can wait for the result. GameOver reveals the authorised team summary; a new round starts in a fresh room.", "body", scroll);
            Text("Human play needs only this application. ACTOR Runtime is an optional local agent connection.", "muted", scroll);
            Button("Got it", () => { PlayerPrefs.SetInt("Arena.TutorialSeen", 1); PlayerPrefs.Save(); CloseModal(); }, "primary", scroll);
        }
        private void OnDestroy()
        {
            if (imeKeyboard != null) { imeKeyboard.onIMECompositionChange -= Composition; imeKeyboard.onTextInput -= TextCommitted; }
            if (instance == this) instance = null; if (settings != null) Destroy(settings);
        }
#endif
    }
}
