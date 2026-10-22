using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace Actor.Editor
{
    // Fixed editor-only operations allow repeatable validation in the already open Editor.
    [InitializeOnLoad]
    public static class MilestoneEditorCommands
    {
        private static readonly string Folder = Path.GetFullPath("Verification");
        private static double nextPoll;
        private static string refreshedCommand;
        static MilestoneEditorCommands() => EditorApplication.update += Poll;

        private static void VerifySavedSocialScene()
        {
            var setup = UnityEditor.SceneManagement.EditorSceneManager.GetSceneManagerSetup();
            for (int i = 0; i < UnityEngine.SceneManagement.SceneManager.sceneCount; ++i)
                if (UnityEngine.SceneManagement.SceneManager.GetSceneAt(i).isDirty)
                    throw new InvalidOperationException("Save scene changes before geometry validation.");
            try
            {
                UnityEditor.SceneManagement.EditorSceneManager.OpenScene(MultiplayerBuild.SocialScene);
                SocialWorldBuilder.ValidateCurrentSocialScene();
            }
            finally { UnityEditor.SceneManagement.EditorSceneManager.RestoreSceneManagerSetup(setup); }
        }

        private static void Poll()
        {
            if (EditorApplication.timeSinceStartup < nextPoll || EditorApplication.isCompiling || EditorApplication.isUpdating) return;
            nextPoll = EditorApplication.timeSinceStartup + 0.5;
            string request = Path.Combine(Folder, "editor-command.txt");
            if (!File.Exists(request)) return;
            string command = File.ReadAllText(request).Trim();
            // A background Editor may not import external edits until focused.
            // Keep the request across import/domain reload, then execute on a
            // later update with matching Editor and Player assembly layouts.
            if (refreshedCommand != command)
            {
                refreshedCommand = command;
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                return;
            }
            File.Delete(request);
            refreshedCommand = null;
            try
            {
                switch (command)
                {
                    case "build": PlayableMilestoneBuilder.Build(); break;
                    case "verify": PlayableMilestoneVerification.Start(); break;
                    case "prepare-multiplayer": MultiplayerBuild.Prepare(); break;
                    case "preview-world-passage": EngineeringRoomBuilder.ShowPassagePreview(); break;
                    case "build-client": MultiplayerBuild.BuildMacClient(); break;
                    case "build-client-qa": MultiplayerBuild.BuildMacQAClient(); break;
                    case "prepare-release": MultiplayerBuild.PrepareReleaseSettings(); break;
                    case "prepare-art": AtmosphericArtImporter.Prepare(); break;
                    case "prepare-ui": RuntimeUIAssets.Prepare(); break;
                    case "build-server": MultiplayerBuild.BuildLinuxDedicatedServer(); break;
                    case "verify-social-world": VerifySavedSocialScene(); break;
                    case "render-social-world": SocialPresentationVerification.RenderWorld(); break;
                    case "verify-visibility": SocialVisibilityVerification.Run(); break;
                    case "verify-prediction": MovementPredictionVerification.Run(); break;
                    case "status":
                        File.WriteAllText(Path.Combine(Folder, "status.txt"), PlayableMilestoneVerification.Status);
                        break;
                    case "stop": EditorApplication.isPlaying = false; break;
                    default: throw new ArgumentException("Unknown editor command: " + command);
                }
                File.WriteAllText(Path.Combine(Folder, "editor-result.txt"), command + ": OK\n");
            }
            catch (Exception error)
            {
                Debug.LogException(error);
                File.WriteAllText(Path.Combine(Folder, "editor-result.txt"), error.ToString());
            }
        }
    }
}
