using System;
using System.IO;
using System.Linq;
using Actor.Characters;
using Actor.Lobby;
using Actor.Networking;
using Actor.Presentation;
using Unity.Netcode;
using Unity.Netcode.Components;
using Unity.Netcode.Transports.UTP;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Actor.Editor
{
    public static class MultiplayerBuild
    {
        public const string LobbyScene = "Assets/Scenes/Lobby.unity";
        public const string GameplayScene = "Assets/Scenes/MainHallMultiplayer.unity";
        public const string SocialScene = "Assets/Scenes/SocialDeduction.unity";
        public const string BodyPrefab = "Assets/Prefabs/Characters/SocialBody.prefab";
        public const string PlayerPrefab = "Assets/Prefabs/Characters/NetworkCharacter.prefab";

        [MenuItem("ACTOR/Prepare multiplayer assets")]
        public static void Prepare()
        {
            if (EditorApplication.isPlaying) throw new InvalidOperationException("Exit Play Mode before preparing assets.");
            var original = EditorSceneManager.OpenScene(PlayableMilestoneBuilder.ScenePath);
            var source = UnityEngine.Object.FindObjectsByType<ActorCharacter>().Single();
            var player = UnityEngine.Object.Instantiate(source.gameObject);
            if (PrefabUtility.IsPartOfPrefabInstance(player))
                PrefabUtility.UnpackPrefabInstance(player, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
            player.name = "NetworkCharacter";
            player.transform.position = Vector3.zero;
            var actor = player.GetComponent<ActorCharacter>();
            var identity = new SerializedObject(actor);
            identity.FindProperty("actorId").stringValue = "";
            identity.FindProperty("initialRoomId").stringValue = "main-hall";
            identity.ApplyModifiedPropertiesWithoutUndo();
            actor.enabled = false;
            player.GetComponent<CharacterMotor2D>().enabled = false;
            player.GetComponent<PlayerInputSource>().enabled = false;
            player.AddComponent<NetworkObject>();
            var transform = player.AddComponent<NetworkTransform>();
            transform.AuthorityMode = NetworkTransform.AuthorityModes.Server;
            transform.SyncPositionZ = false;
            transform.SyncRotAngleX = transform.SyncRotAngleY = transform.SyncRotAngleZ = false;
            transform.SyncScaleX = transform.SyncScaleY = transform.SyncScaleZ = false;
            transform.UseUnreliableDeltas = true;
            transform.Interpolate = true;
            transform.PositionThreshold = 0.001f;
            player.AddComponent<NetworkRigidbody2D>();
            player.AddComponent<NetworkCharacter>();
            player.AddComponent<SocialVisibilityMember>();
            player.AddComponent<NetworkSocialMatch>();
            player.AddComponent<Actor.World.SocialClientUI>();
            player.AddComponent<NetworkNameplate>();
            player.AddComponent<NetworkRoomMembership>();
            player.AddComponent<NetworkStationSignals>();
            player.AddComponent<NetworkCooperation>();
            player.AddComponent<Actor.World.CooperativeClientUI>();
            player.AddComponent<NetworkInvestigation>();
            player.AddComponent<Actor.World.IncidentClientUI>();
            PrefabUtility.SaveAsPrefabAsset(player, PlayerPrefab);
            var corpse = new GameObject("Social Body");
            corpse.AddComponent<NetworkObject>();
            corpse.AddComponent<SocialVisibilityMember>().Configure(SocialVisibilityKind.Body);
            corpse.AddComponent<SocialBody>();
            var corpseView = new GameObject("Body Visual"); corpseView.transform.SetParent(corpse.transform, false);
            var corpseRenderer = corpseView.AddComponent<SpriteRenderer>();
            var characterRenderer = player.GetComponentInChildren<SpriteRenderer>();
            corpseRenderer.sprite = characterRenderer.sprite; corpseRenderer.sharedMaterial = characterRenderer.sharedMaterial;
            corpseRenderer.color = new Color(.6f,.65f,.7f); corpseRenderer.sortingOrder = 2;
            corpseView.transform.rotation = Quaternion.Euler(0,0,90);
            PrefabUtility.SaveAsPrefabAsset(corpse, BodyPrefab); UnityEngine.Object.DestroyImmediate(corpse);
            UnityEngine.Object.DestroyImmediate(player);
            UnityEngine.Object.DestroyImmediate(source.gameObject);
            var camera = UnityEngine.Object.FindObjectsByType<Camera>().Single();
            camera.GetComponent<CameraFollow2D>().Follow(null);
            EngineeringRoomBuilder.AddToCurrentMultiplayerScene();
            SignalConsoleBuilder.AddToCurrentMultiplayerScene();
            CooperativeWorldBuilder.AddToCurrentMultiplayerScene();
            IncidentWorldBuilder.AddToCurrentMultiplayerScene();
            EngineeringRoomBuilder.ValidateCurrentMultiplayerScene();
            EditorSceneManager.SaveScene(original, GameplayScene);
            EditorSceneManager.SaveScene(original, SocialScene);
            SocialWorldBuilder.AddToCurrentSocialScene();
            File.Copy("Assets/World/SocialLayout.json", "../runtime/actor_agents_arena/social_layout.json", true);
            var social = new GameObject("Social Match Authority").AddComponent<SocialMatchSession>();
            Set(social, "bodyPrefab", AssetDatabase.LoadAssetAtPath<GameObject>(BodyPrefab));
            EditorSceneManager.SaveScene(original, SocialScene);

            string settingsPath = "Assets/Settings/LobbyClient.asset";
            var settings = AssetDatabase.LoadAssetAtPath<LobbyClientSettings>(settingsPath);
            if (settings == null)
            {
                settings = ScriptableObject.CreateInstance<LobbyClientSettings>();
                AssetDatabase.CreateAsset(settings, settingsPath);
            }
            string apiUrl = Environment.GetEnvironmentVariable("ACTOR_API_URL");
            if (string.IsNullOrWhiteSpace(apiUrl) && File.Exists("Verification/client-api-url.txt"))
                apiUrl = File.ReadAllText("Verification/client-api-url.txt").Trim();
            if (!string.IsNullOrWhiteSpace(apiUrl)) settings.apiBaseUrl = apiUrl;
            settings.gameVersion = "0.8.0";
            EditorUtility.SetDirty(settings);
            string listPath = "Assets/Settings/NetworkPrefabs.asset";
            var list = AssetDatabase.LoadAssetAtPath<NetworkPrefabsList>(listPath);
            if (list == null) { list = ScriptableObject.CreateInstance<NetworkPrefabsList>(); AssetDatabase.CreateAsset(list, listPath); }
            foreach (var existing in list.PrefabList.ToArray()) list.Remove(existing);
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(PlayerPrefab);
            list.Add(new NetworkPrefab { Prefab = prefab });
            list.Add(new NetworkPrefab { Prefab = AssetDatabase.LoadAssetAtPath<GameObject>(BodyPrefab) });
            EditorUtility.SetDirty(list);

            var lobby = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var root = new GameObject("Arena Session");
            var manager = root.AddComponent<NetworkManager>();
            var transport = root.AddComponent<UnityTransport>();
            manager.NetworkConfig.NetworkTransport = transport;
            manager.NetworkConfig.NetworkTopology = NetworkTopologyTypes.ClientServer;
            manager.NetworkConfig.TickRate = 30;
            manager.NetworkConfig.ConnectionApproval = true;
            manager.NetworkConfig.EnableSceneManagement = true;
            manager.NetworkConfig.PlayerPrefab = prefab;
            manager.NetworkConfig.AutoSpawnPlayerPrefabClientSide = false;
            manager.NetworkConfig.ClientConnectionBufferTimeout = 15;
            manager.NetworkConfig.Prefabs.NetworkPrefabsLists.Clear();
            manager.NetworkConfig.Prefabs.NetworkPrefabsLists.Add(list);
            var cameraRoot = new GameObject("Lobby Camera");
            cameraRoot.transform.SetParent(root.transform, false);
            var lobbyCamera = cameraRoot.AddComponent<Camera>();
            lobbyCamera.clearFlags = CameraClearFlags.SolidColor;
            lobbyCamera.backgroundColor = new Color(0.035f, 0.045f, 0.075f);
            cameraRoot.AddComponent<AudioListener>();
            var session = root.AddComponent<MatchSession>();
            Set(session, "settings", settings); Set(session, "lobbyCamera", lobbyCamera);
            var view = root.AddComponent<LobbyMenuView>();
            var controller = root.AddComponent<LobbyMenuController>();
            Set(controller, "settings", settings); Set(controller, "view", view); Set(controller, "session", session);
            root.AddComponent<MultiplayerProbe>();
            root.AddComponent<ActorObserverProbe>();
            EditorSceneManager.SaveScene(lobby, LobbyScene);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(LobbyScene, true), new EditorBuildSettingsScene(GameplayScene, true), new EditorBuildSettingsScene(SocialScene, true) };
            PlayerSettings.runInBackground = true;
            PlayerSettings.bundleVersion = settings.gameVersion;
            PlayerSettings.insecureHttpOption = InsecureHttpOption.AlwaysAllowed;
            PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Standalone, "com.actor.agentsarena");
            AssetDatabase.SaveAssets();
            Debug.Log("ACTOR: multiplayer assets prepared from the current Main Hall without modifying the original scene.");
        }

        private static void Set(UnityEngine.Object target, string name, UnityEngine.Object value)
        {
            var serialized = new SerializedObject(target);
            serialized.FindProperty(name).objectReferenceValue = value;
            serialized.ApplyModifiedPropertiesWithoutUndo();
        }

        public static void BuildLinuxDedicatedServer()
        {
            PlayerSettings.SetScriptingBackend(NamedBuildTarget.Server, ScriptingImplementation.Mono2x);
            Build(new BuildPlayerOptions
            {
                scenes = new[] { LobbyScene, GameplayScene, SocialScene }, target = BuildTarget.StandaloneLinux64,
                subtarget = (int)StandaloneBuildSubtarget.Server,
                locationPathName = Output("../build/agents-arena-server/AgentsArenaServer"),
                options = BuildOptions.None
            });
        }
        public static void BuildMacClient()
        {
            PlayerSettings.SetScriptingBackend(NamedBuildTarget.Standalone, ScriptingImplementation.Mono2x);
            Build(new BuildPlayerOptions
            {
                scenes = new[] { LobbyScene, GameplayScene, SocialScene }, target = BuildTarget.StandaloneOSX,
                subtarget = (int)StandaloneBuildSubtarget.Player,
                locationPathName = Output("../build/agents-arena-client/AgentsArena.app"),
                options = BuildOptions.Development
            });
        }
        private static string Output(string fallback)
        {
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "-actor-output");
            return Path.GetFullPath(index >= 0 && index + 1 < args.Length ? args[index + 1] : fallback);
        }
        private static void Build(BuildPlayerOptions options)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(options.locationPathName));
            BuildReport report = BuildPipeline.BuildPlayer(options);
            if (report.summary.result != BuildResult.Succeeded)
                throw new InvalidOperationException("Build failed: " + report.summary.result + ", errors=" + report.summary.totalErrors);
            Debug.Log("ACTOR build complete: " + options.locationPathName);
        }
    }
}
