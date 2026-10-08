using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class GrpcSmokeEditor
{
    public static async void Run()
    {
        string token = File.ReadAllText(GrpcSmoke.Argument("--spike-token-file")).Trim();
        var result = await GrpcSmoke.Run(GrpcSmoke.Argument("--spike-endpoint", "http://127.0.0.1:50061"), token);
        File.WriteAllText(GrpcSmoke.Argument("--spike-report"), JsonUtility.ToJson(result, true));
        EditorApplication.Exit(result.completed && result.checks.All(check => check.passed) ? 0 : 1);
    }
    public static void Build()
    {
        PlayerSettings.SetScriptingBackend(NamedBuildTarget.Standalone, ScriptingImplementation.Mono2x);
        PlayerSettings.SetApiCompatibilityLevel(NamedBuildTarget.Standalone, ApiCompatibilityLevel.NET_Standard);
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        new GameObject("Camera").AddComponent<Camera>();
        EditorSceneManager.SaveScene(scene, "Assets/Smoke.unity");
        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { "Assets/Smoke.unity" }, target = BuildTarget.StandaloneOSX,
            locationPathName = GrpcSmoke.Argument("--spike-output"), options = BuildOptions.Development
        });
        if (report.summary.result != BuildResult.Succeeded) throw new System.InvalidOperationException("Spike player build failed.");
    }
}
