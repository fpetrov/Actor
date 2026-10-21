using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Actor.Characters;
using Unity.Collections;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    // Explicit trusted-process compatibility fixture. It never runs in an
    // ordinary match and does not expose a client-callable visibility actuator.
    public sealed class SocialVisibilityProbe : MonoBehaviour
    {
#if UNITY_SERVER || UNITY_EDITOR || DEVELOPMENT_BUILD
        private const string StageMessage = "actor.visibility-spike.stage.v1";
        private const string ResultMessage = "actor.visibility-spike.result.v1";
        [Serializable] private sealed class Check { public string name; public bool passed; }
        [Serializable] private sealed class Report
        {
            public string fixture = "ngo-2.13.3-visibility", role;
            public bool completed, passed, preNetworkHookInstalled;
            public int stages, duplicateSpawnWarnings, initialForeignSpawns;
            public ulong observerGrants, observerRevocations, policyFailures;
            public Check[] checks;
        }
        private sealed class Movement : IMovementInputSource
        {
            public Vector2 ReadMovement() => new(Mathf.Sin(Time.realtimeSinceStartup * 1.5f), 0);
        }
        private readonly List<Check> checks = new();
        private readonly HashSet<ulong> acknowledgements = new();
        private NetworkManager manager;
        private SocialVisibilitySystem visibility;
        private NetworkObject fixtureBody;
        private NetworkObject ownerObject;
        private NetworkCharacter owner;
        private string role, output;
        private int stage = -1, expectedPlayers, expectedBodies, stagesPassed, duplicateSpawnWarnings;
        private int initialForeignSpawns;
        private Vector2 expectedBodyPosition;
        private double stageReceived, nextReply, nextSave;
        private bool finished, registered, finalStageAcknowledged;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap()
        {
            if (Argument("--visibility-spike-role") == null) return;
            var host = new GameObject("NGO Visibility Compatibility Probe");
            DontDestroyOnLoad(host);
            host.AddComponent<SocialVisibilityProbe>();
        }
        private static string Argument(string key)
        {
            var args = Environment.GetCommandLineArgs(); int index = Array.IndexOf(args, key);
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
        }
        private IEnumerator Start()
        {
            role = Argument("--visibility-spike-role"); output = Argument("--visibility-spike-output");
            if (role is not ("server" or "A" or "B" or "C") || string.IsNullOrEmpty(output))
            { Finish(false, "valid_explicit_fixture_arguments"); yield break; }
            Application.targetFrameRate = 60;
            Application.logMessageReceived += OnLog;
            double deadline = Time.realtimeSinceStartupAsDouble + 90;
            while (Time.realtimeSinceStartupAsDouble < deadline)
            {
                manager = NetworkManager.Singleton;
                if (manager != null && manager.IsListening && manager.CustomMessagingManager != null) break;
                yield return null;
            }
            if (manager == null || !manager.IsListening || !SocialVisibilitySystem.IsSocial(manager))
            { Finish(false, "social_network_session_started"); yield break; }
            visibility = SocialVisibilitySystem.Ensure(manager);
            if (role == "server")
            {
                if (!manager.IsServer) { Finish(false, "server_role_matches_process"); yield break; }
                manager.CustomMessagingManager.RegisterNamedMessageHandler(ResultMessage, OnResult);
                registered = true;
                visibility.ObserverPolicy = ProbePolicy;
                yield return ServerStages();
            }
            else
            {
                if (!manager.IsClient || manager.IsServer) { Finish(false, "client_role_matches_process"); yield break; }
                manager.CustomMessagingManager.RegisterNamedMessageHandler(StageMessage, OnStage);
                registered = true;
            }
        }
        private bool ProbePolicy(ulong viewer, NetworkObject target)
        {
            if (stage < 0) return false;
            if (!target.IsPlayerObject)
                return target == fixtureBody && (stage == 28 || stage == 26 && PlayerRole(viewer) == "A");
            if (stage == 0 || stage == 4 || stage >= 6 && stage <= 25 && stage % 2 == 0) return false;
            if (stage == 2 && PlayerRole(viewer) == "A" && target.GetComponent<NetworkCharacter>()?.PlayerName == "Visibility-B") return false;
            return true;
        }
        private string PlayerRole(ulong client)
        {
            if (!manager.ConnectedClients.TryGetValue(client, out var peer) || peer.PlayerObject == null) return "";
            string name = peer.PlayerObject.GetComponent<NetworkCharacter>()?.PlayerName;
            return name != null && name.StartsWith("Visibility-", StringComparison.Ordinal) ? name.Substring(11) : "";
        }
        private IEnumerator ServerStages()
        {
            double deadline = Time.realtimeSinceStartupAsDouble + 90;
            while (Time.realtimeSinceStartupAsDouble < deadline && !AllFixtureClients()) yield return null;
            if (!AllFixtureClients()) { Finish(false, "three_approved_fixture_clients"); yield break; }
            for (int next = 0; next <= 29; ++next)
            {
                stage = next; acknowledgements.Clear();
                if (next == 26)
                {
                    var body = Instantiate(manager.NetworkConfig.PlayerPrefab, new Vector3(1.3f, -1.4f, 0), Quaternion.identity);
                    fixtureBody = body.GetComponent<NetworkObject>();
                    body.GetComponent<SocialVisibilityMember>().Configure(SocialVisibilityKind.Body);
                    // The exact same registered prefab exercises manual Spawn,
                    // without granting any observers from OnNetworkSpawn.
                    fixtureBody.Spawn();
                }
                if (next == 27 && fixtureBody != null)
                {
                    visibility.RefreshNow();
                    fixtureBody.GetComponent<Rigidbody2D>().position = new Vector2(-1.3f, -1.4f);
                }
                if (next == 29 && fixtureBody != null) { fixtureBody.Despawn(); fixtureBody = null; }
                if (next == 4)
                {
                    // A Show followed by Hide in one frame must cancel the spawn.
                    visibility.RefreshNow(); stage = 3; visibility.RefreshNow(); stage = next;
                }
                visibility.RefreshNow();
                double started = Time.realtimeSinceStartupAsDouble, lastStageSend = -10;
                deadline = started + 8;
                while (Time.realtimeSinceStartupAsDouble < deadline)
                {
                    if (!AllFixtureClients()) { Finish(false, "fixture_clients_remain_connected"); yield break; }
                    if (Time.realtimeSinceStartupAsDouble - lastStageSend >= .25)
                    {
                        lastStageSend = Time.realtimeSinceStartupAsDouble;
                        foreach (ulong peer in manager.ConnectedClientsIds)
                        {
                            if (PlayerRole(peer) is not ("A" or "B" or "C")) continue;
                            ExpectedCounts(peer, out int players, out int bodies);
                            using var writer = new FastBufferWriter(24, Allocator.Temp);
                            writer.WriteValueSafe(stage); writer.WriteValueSafe(players); writer.WriteValueSafe(bodies);
                            // Only a currently permitted body sample is provided
                            // for the fresh-spawn check. Reports omit coordinates.
                            writer.WriteValueSafe(bodies > 0 && fixtureBody != null ? (Vector2)fixtureBody.transform.position : Vector2.zero);
                            manager.CustomMessagingManager.SendNamedMessage(StageMessage, peer, writer);
                        }
                    }
                    if (acknowledgements.Count == 3 && Time.realtimeSinceStartupAsDouble - started >= .65) break;
                    yield return null;
                }
                bool observers = ServerObserversMatch();
                checks.Add(new Check { name = "stage_" + next + "_server_observers", passed = observers });
                checks.Add(new Check { name = "stage_" + next + "_three_client_spawned_sets", passed = acknowledgements.Count == 3 });
                if (!observers || acknowledgements.Count != 3) { Finish(false, "bounded_stage_completion"); yield break; }
                ++stagesPassed; Save();
            }
            Finish(true, "all_visibility_stages");
            foreach (ulong peer in manager.ConnectedClientsIds)
            {
                if (PlayerRole(peer) is not ("A" or "B" or "C")) continue;
                using var writer = new FastBufferWriter(24, Allocator.Temp);
                writer.WriteValueSafe(30); writer.WriteValueSafe(0); writer.WriteValueSafe(0); writer.WriteValueSafe(Vector2.zero);
                manager.CustomMessagingManager.SendNamedMessage(StageMessage, peer, writer);
            }
        }
        private bool AllFixtureClients()
        {
            var found = new HashSet<string>();
            foreach (ulong id in manager.ConnectedClientsIds)
            { string value = PlayerRole(id); if (value is "A" or "B" or "C") found.Add(value); }
            return found.Count == 3;
        }
        private bool ServerObserversMatch()
        {
            foreach (var target in manager.SpawnManager.SpawnedObjectsList)
            {
                if (target.GetComponent<SocialVisibilityMember>() == null) continue;
                foreach (ulong peer in manager.ConnectedClientsIds)
                {
                    if (PlayerRole(peer) is not ("A" or "B" or "C")) continue;
                    bool permitted = target.OwnerClientId == peer || ProbePolicy(peer, target);
                    if (target.IsNetworkVisibleTo(peer) != permitted) return false;
                }
            }
            return SocialVisibilitySystem.PreNetworkHookInstalled && visibility.LastPreNetworkFrame == Time.frameCount && visibility.PolicyFailures == 0;
        }
        private void ExpectedCounts(ulong peer, out int players, out int bodies)
        {
            players = bodies = 0;
            foreach (var target in manager.SpawnManager.SpawnedObjectsList)
            {
                if (target.GetComponent<SocialVisibilityMember>() == null || !(target.OwnerClientId == peer || ProbePolicy(peer, target))) continue;
                if (target.IsPlayerObject) ++players; else ++bodies;
            }
        }
        private void OnStage(ulong sender, FastBufferReader reader)
        {
            if (sender != NetworkManager.ServerClientId || finished) return;
            reader.ReadValueSafe(out int incoming); reader.ReadValueSafe(out int players); reader.ReadValueSafe(out int bodies);
            reader.ReadValueSafe(out Vector2 bodyPosition);
            if (incoming == 30)
            {
                Finish(finalStageAcknowledged && stagesPassed == 30, "server_confirmed_all_client_stages");
                return;
            }
            if (incoming < 0 || incoming > 29 || players < 1 || players > 3 || bodies < 0 || bodies > 1) return;
            if (incoming != stage)
            {
                if (stage >= 0) { ++stagesPassed; checks.Add(new Check { name = "stage_" + stage + "_spawned_set_and_owner", passed = true }); }
                stage = incoming; stageReceived = Time.realtimeSinceStartupAsDouble;
            }
            expectedPlayers = players; expectedBodies = bodies; expectedBodyPosition = bodyPosition;
        }
        private void OnResult(ulong sender, FastBufferReader reader)
        {
            reader.ReadValueSafe(out int incoming); reader.ReadValueSafe(out bool passed);
            if (incoming == stage && passed && PlayerRole(sender) is "A" or "B" or "C") acknowledgements.Add(sender);
        }
        private void Update()
        {
            if (finished || manager == null || !manager.IsClient || manager.IsServer) return;
            if (stage < 0)
            {
                foreach (var target in manager.SpawnManager.SpawnedObjectsList)
                    if (target.IsPlayerObject && !target.IsOwner && target.GetComponent<SocialVisibilityMember>() != null) ++initialForeignSpawns;
                return;
            }
            var local = manager.SpawnManager.GetLocalPlayerObject();
            if (local != null && owner == null)
            {
                owner = local.GetComponent<NetworkCharacter>(); ownerObject = local;
                local.GetComponent<PlayerInputSource>().enabled = false;
                owner.SetLocalInputSource(new Movement());
            }
            if (Time.realtimeSinceStartupAsDouble >= nextSave) { nextSave = Time.realtimeSinceStartupAsDouble + .5; Save(); }
            if (Time.realtimeSinceStartupAsDouble - stageReceived < .35 || Time.realtimeSinceStartupAsDouble < nextReply) return;
            int players = 0, bodies = 0; bool freshBody = expectedBodies == 0;
            foreach (var target in manager.SpawnManager.SpawnedObjectsList)
            {
                if (target.GetComponent<SocialVisibilityMember>() == null) continue;
                if (target.IsPlayerObject) ++players;
                else { ++bodies; freshBody = Vector2.Distance(target.transform.position, expectedBodyPosition) < .25f; }
            }
            bool valid = players == expectedPlayers && bodies == expectedBodies && freshBody && local != null && local.IsOwner
                && ReferenceEquals(local, ownerObject) && owner != null && owner.HasAuthoritativeSnapshot
                && owner.AuthoritativeSnapshotAge <= .5f && duplicateSpawnWarnings == 0 && initialForeignSpawns == 0;
            if (!valid) return;
            nextReply = Time.realtimeSinceStartupAsDouble + .2;
            using var writer = new FastBufferWriter(8, Allocator.Temp);
            writer.WriteValueSafe(stage); writer.WriteValueSafe(valid);
            manager.CustomMessagingManager.SendNamedMessage(ResultMessage, NetworkManager.ServerClientId, writer);
            if (stage == 29 && !finalStageAcknowledged)
            {
                ++stagesPassed; checks.Add(new Check { name = "stage_29_spawned_set_and_owner", passed = true });
                finalStageAcknowledged = true;
            }
        }
        private void LateUpdate()
        {
            // The scene's normal Social session may initialize after this
            // process-level fixture. Reassert only in the explicit server
            // diagnostic process, after its ordinary Update initialization.
            if (role == "server" && registered && !finished && visibility != null)
            { visibility.ObserverPolicy = ProbePolicy; visibility.RefreshNow(); }
        }
        private void OnLog(string message, string stackTrace, LogType type)
        {
            if ((type == LogType.Warning || type == LogType.Error) && message.IndexOf("already spawned", StringComparison.OrdinalIgnoreCase) >= 0)
                ++duplicateSpawnWarnings;
        }
        private void Finish(bool passed, string check)
        {
            if (finished) return;
            checks.Add(new Check { name = check, passed = passed }); finished = true; Save();
            Debug.Log("ACTOR_VISIBILITY_PROBE role=" + role + " passed=" + passed + " stages=" + stagesPassed);
            // Keep clients connected until the server receives the final reliable
            // acknowledgement; the external bounded supervisor owns processes.
        }
        private void Save()
        {
            if (string.IsNullOrEmpty(output)) return;
            string path = Path.GetFullPath(output); Directory.CreateDirectory(Path.GetDirectoryName(path));
            var report = new Report { role = role, completed = finished, passed = finished && checks.All(c => c.passed),
                preNetworkHookInstalled = SocialVisibilitySystem.PreNetworkHookInstalled, stages = stagesPassed,
                duplicateSpawnWarnings = duplicateSpawnWarnings, initialForeignSpawns = initialForeignSpawns,
                observerGrants = visibility != null ? visibility.ObserverGrants : 0,
                observerRevocations = visibility != null ? visibility.ObserverRevocations : 0,
                policyFailures = visibility != null ? visibility.PolicyFailures : 0, checks = checks.ToArray() };
            File.WriteAllText(path, JsonUtility.ToJson(report, true) + "\n");
        }
        private void OnDestroy()
        {
            Application.logMessageReceived -= OnLog;
            if (!registered || manager == null || manager.CustomMessagingManager == null) return;
            manager.CustomMessagingManager.UnregisterNamedMessageHandler(role == "server" ? ResultMessage : StageMessage);
        }
#endif
    }
}
