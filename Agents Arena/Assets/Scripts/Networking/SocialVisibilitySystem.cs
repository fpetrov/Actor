using System;
using System.Collections.Generic;
using Unity.Netcode;
using UnityEngine;
using UnityEngine.LowLevel;

namespace Actor.Networking
{
    // The game owns policy; this component applies the same decision to NGO's
    // server-side observer sets. No role, alignment or hidden state is sent here.
    [DisallowMultipleComponent, RequireComponent(typeof(NetworkManager))]
    public sealed class SocialVisibilitySystem : MonoBehaviour
    {
        private sealed class BeforeSocialNetworkTick { }
        private const string NgoPreUpdateType = "Unity.Netcode.NetworkUpdateLoop+NetworkPreUpdate";
        private static readonly HashSet<SocialVisibilitySystem> systems = new();
        private static readonly List<SocialVisibilitySystem> systemSnapshot = new();
        private static readonly RaycastHit2D[] hits = new RaycastHit2D[128];
        private readonly HashSet<SocialVisibilityMember> members = new();
        private readonly List<SocialVisibilityMember> memberSnapshot = new();
        private readonly List<ulong> clientSnapshot = new();
        private NetworkManager manager;
        private Func<ulong, NetworkObject, bool> observerPolicy;
        private static bool hookInstalled;
        private bool refreshing;

        public Func<ulong, NetworkObject, bool> ObserverPolicy
        {
            get => observerPolicy;
            set { observerPolicy = value; }
        }
        public static bool PreNetworkHookInstalled => hookInstalled;
        public int LastPreNetworkFrame { get; private set; } = -1;
        public ulong ObserverGrants { get; private set; }
        public ulong ObserverRevocations { get; private set; }
        public ulong PolicyFailures { get; private set; }

        public static bool IsSocial(NetworkManager networkManager)
            => networkManager != null && networkManager.GetComponent<MatchSession>() is { GameMode: "SocialDeduction" };

        public static SocialVisibilitySystem Ensure(NetworkManager networkManager)
        {
            if (networkManager == null) throw new ArgumentNullException(nameof(networkManager));
            var value = networkManager.GetComponent<SocialVisibilitySystem>();
            return value != null ? value : networkManager.gameObject.AddComponent<SocialVisibilitySystem>();
        }

        private void Awake() => manager = GetComponent<NetworkManager>();
        private void OnEnable()
        {
            if (manager == null) manager = GetComponent<NetworkManager>();
            systems.Add(this);
            InstallHook();
        }
        private void Update()
        {
            // NGO installs its own loop at SubsystemRegistration. This retry also
            // handles an Editor entering Play Mode with domain reload disabled.
            if (!hookInstalled) InstallHook();
        }
        private void OnDisable()
        {
            observerPolicy = null;
            if (manager != null && manager.IsServer && manager.IsListening) RefreshNow();
            systems.Remove(this);
        }

        internal void Register(SocialVisibilityMember member) => members.Add(member);
        internal void Unregister(SocialVisibilityMember member) => members.Remove(member);

        public bool CanObserve(ulong viewerClientId, NetworkObject target)
        {
            if (target == null || manager == null || target.NetworkManager != manager) return false;
            // The owner's controller must survive death/meetings and visibility
            // changes. Its private terminal view travels on this same endpoint.
            if (viewerClientId == target.OwnerClientId || viewerClientId == NetworkManager.ServerClientId) return true;
            if (!isActiveAndEnabled || !hookInstalled || !IsSocial(manager) || !manager.IsServer || observerPolicy == null) return false;
            if (!manager.ConnectedClients.ContainsKey(viewerClientId)) return false;
            try { return observerPolicy(viewerClientId, target); }
            catch (Exception)
            {
                // Never log arbitrary policy exception text: it may contain
                // secret game state. A broken policy revokes spatial observers.
                ++PolicyFailures;
                return false;
            }
        }

        // Call after an authoritative phase/life change and before its deliveries.
        // It is also invoked after physics, immediately before NGO's PreUpdate.
        public void RefreshNow()
        {
            if (refreshing || manager == null || !manager.IsServer || !manager.IsListening || !IsSocial(manager)) return;
            long started = System.Diagnostics.Stopwatch.GetTimestamp();
            int visibleObserverPairs = 0;
            refreshing = true;
            try
            {
                memberSnapshot.Clear();
                foreach (var member in members) if (member != null && member.IsSpawned) memberSnapshot.Add(member);
                clientSnapshot.Clear();
                foreach (ulong client in manager.ConnectedClientsIds) clientSnapshot.Add(client);
                foreach (var member in memberSnapshot)
                {
                    var target = member.NetworkObject;
                    foreach (ulong client in clientSnapshot)
                    {
                        if (client == NetworkManager.ServerClientId) continue;
                        bool visible = target.IsNetworkVisibleTo(client);
                        bool permitted = CanObserve(client, target);
                        if (visible && !permitted)
                        { target.NetworkHide(client); ++ObserverRevocations; }
                        else if (!visible && permitted)
                        { target.NetworkShow(client); ++ObserverGrants; }
                        if (target.IsNetworkVisibleTo(client)) ++visibleObserverPairs;
                    }
                }
            }
            finally
            {
                refreshing = false;
                double elapsedMilliseconds = (System.Diagnostics.Stopwatch.GetTimestamp() - started) * 1000.0
                    / System.Diagnostics.Stopwatch.Frequency;
                SocialMatchMetrics.RecordVisibility(elapsedMilliseconds, visibleObserverPairs);
            }
        }

        // Only authored opaque geometry blocks sight. Characters, bodies and
        // interaction triggers do not accidentally become walls or role oracles.
        public static bool LineOfSight(Vector2 from, Vector2 to)
        {
            if (!float.IsFinite(from.x) || !float.IsFinite(from.y) || !float.IsFinite(to.x) || !float.IsFinite(to.y)) return false;
            var filter = new ContactFilter2D { useTriggers = false };
            int count = Physics2D.Linecast(from, to, filter, hits);
            if (count == hits.Length) return false;
            for (int i = 0; i < count; ++i)
            {
                var hit = hits[i].collider;
                if (hit != null && hit.GetComponentInParent<SocialVisibilityOccluder>() is { isActiveAndEnabled: true }) return false;
            }
            return true;
        }

        public static Vector2 SightEndpoint(Vector2 from, Vector2 to)
        {
            if (!float.IsFinite(from.x) || !float.IsFinite(from.y) || !float.IsFinite(to.x) || !float.IsFinite(to.y)) return from;
            int count = Physics2D.Linecast(from, to, new ContactFilter2D { useTriggers = false }, hits);
            if (count == hits.Length) return from;
            float closest = Vector2.Distance(from, to);
            for (int i = 0; i < count; ++i)
                if (hits[i].collider != null && hits[i].collider.GetComponentInParent<SocialVisibilityOccluder>() is { isActiveAndEnabled: true })
                    closest = Mathf.Min(closest, hits[i].distance);
            return from + (to - from).normalized * Mathf.Max(0, closest - .015f);
        }

        private static void BeforeNetworkTick()
        {
            systemSnapshot.Clear();
            foreach (var system in systems) if (system != null) systemSnapshot.Add(system);
            foreach (var system in systemSnapshot)
            {
                system.LastPreNetworkFrame = Time.frameCount;
                system.RefreshNow();
            }
        }

        public static bool InstallHook()
        {
            var loop = PlayerLoop.GetCurrentPlayerLoop();
            RemoveHook(ref loop);
            hookInstalled = InsertHook(ref loop);
            PlayerLoop.SetPlayerLoop(loop);
            return hookInstalled;
        }
        private static bool InsertHook(ref PlayerLoopSystem parent)
        {
            var children = parent.subSystemList;
            if (children == null) return false;
            for (int i = 0; i < children.Length; ++i)
            {
                if (children[i].type?.FullName == NgoPreUpdateType)
                {
                    var replacement = new PlayerLoopSystem[children.Length + 1];
                    Array.Copy(children, 0, replacement, 0, i);
                    replacement[i] = new PlayerLoopSystem { type = typeof(BeforeSocialNetworkTick), updateDelegate = BeforeNetworkTick };
                    Array.Copy(children, i, replacement, i + 1, children.Length - i);
                    parent.subSystemList = replacement;
                    return true;
                }
                var child = children[i];
                if (InsertHook(ref child)) { children[i] = child; return true; }
            }
            return false;
        }
        private static void RemoveHook(ref PlayerLoopSystem parent)
        {
            if (parent.subSystemList == null) return;
            var replacement = new List<PlayerLoopSystem>(parent.subSystemList.Length);
            foreach (var entry in parent.subSystemList)
            {
                if (entry.type == typeof(BeforeSocialNetworkTick)) continue;
                var child = entry;
                RemoveHook(ref child);
                replacement.Add(child);
            }
            parent.subSystemList = replacement.ToArray();
        }
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
        private static void ResetStatics()
        {
            systems.Clear(); systemSnapshot.Clear(); hookInstalled = false;
            var loop = PlayerLoop.GetCurrentPlayerLoop(); RemoveHook(ref loop); PlayerLoop.SetPlayerLoop(loop);
        }
    }
}
