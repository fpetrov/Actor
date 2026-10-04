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
        static MilestoneEditorCommands() => EditorApplication.update += Poll;

        private static void Poll()
        {
            if (EditorApplication.timeSinceStartup < nextPoll || EditorApplication.isCompiling || EditorApplication.isUpdating) return;
            nextPoll = EditorApplication.timeSinceStartup + 0.5;
            string request = Path.Combine(Folder, "editor-command.txt");
            if (!File.Exists(request)) return;
            string command = File.ReadAllText(request).Trim();
            File.Delete(request);
            try
            {
                switch (command)
                {
                    case "build": PlayableMilestoneBuilder.Build(); break;
                    case "verify": PlayableMilestoneVerification.Start(); break;
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
