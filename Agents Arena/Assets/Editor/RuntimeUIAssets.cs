using System.IO;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace Actor.Editor
{
    public static class RuntimeUIAssets
    {
        [MenuItem("ACTOR/Prepare runtime UI resources")]
        public static void Prepare()
        {
            const string folder = "Assets/Resources/UI";
            Directory.CreateDirectory(folder);
            // Runtime dropdowns attach to the panel root, outside UIDocument's
            // local subtree, so their shared palette must also be a panel theme.
            File.WriteAllText(folder + "/ArenaTheme.tss", "@import url(\"unity-theme://default\");\n@import url(\"Arena.uss\");\n");
            File.WriteAllText(folder + "/Compatibility.uss", ".compat-glass { backdrop-filter: blur(8px); }\n");
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            string path = folder + "/ArenaPanelSettings.asset";
            var settings = AssetDatabase.LoadAssetAtPath<PanelSettings>(path);
            if (settings == null)
            {
                settings = ScriptableObject.CreateInstance<PanelSettings>();
                AssetDatabase.CreateAsset(settings, path);
            }
            settings.themeStyleSheet = AssetDatabase.LoadAssetAtPath<ThemeStyleSheet>(folder + "/ArenaTheme.tss");
            settings.scaleMode = PanelScaleMode.ScaleWithScreenSize;
            settings.referenceResolution = new Vector2Int(800, 600);
            settings.screenMatchMode = PanelScreenMatchMode.MatchWidthOrHeight;
            settings.match = .5f;
            settings.renderMode = PanelRenderMode.ScreenSpaceOverlay;
            EditorUtility.SetDirty(settings);
            AssetDatabase.SaveAssets();
        }
    }
}
