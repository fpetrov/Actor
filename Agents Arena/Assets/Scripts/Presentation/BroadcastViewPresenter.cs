using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using Actor.Characters;
using Actor.Networking;
using Actor.UI;
using Actor.World;
using System.Text;
using System.IO;
using System.Runtime.InteropServices;
using AgentsArena.ActorProtocol.V1;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.UIElements;

namespace Actor.Presentation
{
    // Cosmetic-only objects; never instantiates the gameplay/network prefab.
    [DefaultExecutionOrder(-20)]
    public sealed class BroadcastViewPresenter : MonoBehaviour
    {
#if !UNITY_SERVER
        private sealed class Proxy : ICharacterMotionState
        {
            internal GameObject Root; internal SpriteRenderer Sprite, Mark; internal Animator Animator; internal Label Name;
            internal BroadcastActor Actor; internal double ImpulseAt = -100;
            public Vector2 Velocity => new(Actor?.VelocityX ?? 0, Actor?.VelocityY ?? 0);
            public FacingDirection Facing => (FacingDirection)(Actor?.Facing ?? 0);
            public bool IsMoving => Velocity.sqrMagnitude > .0025f;
            public float MaxSpeed => 2.6f;
        }
        private BroadcastSession session;
        private Camera camera;
        private AtmosphericArtCatalog art;
        private readonly Dictionary<string, Proxy> actors = new();
        private sealed class BodyProxy
        {
            internal GameObject Root; internal SpriteRenderer Sprite, Mark; internal double FoldAt = -100;
        }
        private readonly Dictionary<string, BodyProxy> bodies = new();
        private readonly HashSet<string> seenAttacks = new(StringComparer.Ordinal);
        private readonly Queue<string> attackOrder = new();
        private readonly NameplateLayoutRules.Entry[] labels = new NameplateLayoutRules.Entry[8];
        private readonly List<Proxy> labelSources = new();
        private VisualElement hud, notesHud; private Label publicState;
        private readonly Label[] factLabels = new Label[3];
        private BroadcastEditorialFact[] displayedFacts = Array.Empty<BroadcastEditorialFact>();
        private readonly Dictionary<string, ulong> confirmed = new(StringComparer.Ordinal);
        private SocialFogRenderer fog;
        private string probePath; private static bool probeConsumed;
        [DllImport("libc", EntryPoint = "fchmod", SetLastError = true)] private static extern int PrivateFileMode(int descriptor, uint mode);
        private ulong applied, epoch;
        private int renderedFrame = -1;
        private bool capturePending;
        private string motionReportPath;
        private int motionForcedFrameRate;
        private double motionStarted = -1;
        private long nativeFrames, appliedFrames, ackAttempts, ackCompleted, rejectedRender, rejectedFog, rejectedSession;
        private readonly MotionIntervals renderIntervals = new(), appliedIntervals = new(), presentedIntervals = new();
        [Serializable] private sealed class MotionSummary
        {
            public long count, retained_count;
            public double mean_ms, p50_ms, p95_ms, max_ms;
        }
        private sealed class MotionIntervals
        {
            private readonly double[] samples = new double[1024];
            private long count; private double previous = -1, sum, maximum;
            internal void Record(double now)
            {
                if (previous >= 0 && now >= previous)
                {
                    double ms = (now - previous) * 1000;
                    samples[count++ % samples.Length] = ms; sum += ms; maximum = Math.Max(maximum, ms);
                }
                previous = now;
            }
            internal MotionSummary Summary()
            {
                int retained = (int)Math.Min(count, samples.Length);
                var sorted = new double[retained]; Array.Copy(samples, sorted, retained); Array.Sort(sorted);
                return new MotionSummary { count = count, retained_count = retained, mean_ms = count > 0 ? sum / count : 0, max_ms = maximum,
                    p50_ms = retained > 0 ? sorted[(int)Math.Ceiling(retained * .5) - 1] : 0,
                    p95_ms = retained > 0 ? sorted[(int)Math.Ceiling(retained * .95) - 1] : 0 };
            }
        }
        [Serializable] private sealed class MotionRenderReport
        {
            public double elapsed_seconds;
            public int forced_frame_rate, target_frame_rate, v_sync_count;
            public long native_frames, applied_frames, ack_attempts, ack_completed, rejected_render, rejected_fog, rejected_session;
            public MotionSummary render_intervals, applied_intervals, presented_intervals;
        }
        internal BroadcastView Applied { get; private set; }
        public static BroadcastViewPresenter Current { get; private set; }
        internal bool Sight(out Vector2 eye, out float radius, out ulong viewEpoch)
        {
            eye = Vector2.zero; radius = 0; viewEpoch = 0;
            if (session == null || !session.Fresh || Applied?.Camera == null) return false;
            eye = new Vector2(Applied.Camera.X, Applied.Camera.Y); radius = Applied.Camera.VisibilityRadius; viewEpoch = Applied.Scope.ViewEpoch; return true;
        }
        private void OnEnable() { Current = this; RenderPipelineManager.endCameraRendering += Rendered; }
        private void Start()
        {
            session = GetComponent<BroadcastSession>() ?? Unity.Netcode.NetworkManager.Singleton?.GetComponent<BroadcastSession>(); art = AtmosphericArtCatalog.Load();
            string requested = Environment.GetEnvironmentVariable("ACTOR_BROADCAST_PROBE_PNG");
            Environment.SetEnvironmentVariable("ACTOR_BROADCAST_PROBE_PNG", null);
            if (!string.IsNullOrEmpty(requested) && SafeProbePath(requested)) probePath = requested;
            string motion = Environment.GetEnvironmentVariable("ACTOR_BROADCAST_MOTION_REPORT");
            string motionFps = Environment.GetEnvironmentVariable("ACTOR_BROADCAST_MOTION_FPS");
            Environment.SetEnvironmentVariable("ACTOR_BROADCAST_MOTION_REPORT", null);
            Environment.SetEnvironmentVariable("ACTOR_BROADCAST_MOTION_FPS", null);
            if (SafeProofPath(motion, ".json"))
            {
                motionReportPath = motion;
                // Private acceptance can exercise real 30/60 FPS rendering.
                // Ordinary observer runs retain the selected display pacing.
                if (motionFps == "30" || motionFps == "60")
                {
                    motionForcedFrameRate = motionFps == "30" ? 30 : 60;
                    QualitySettings.vSyncCount = 0; Application.targetFrameRate = motionForcedFrameRate;
                }
            }
        }
        private void Rendered(ScriptableRenderContext _, Camera view)
        {
            if (view != camera) return;
            bool firstRender = renderedFrame != Time.frameCount; renderedFrame = Time.frameCount;
            if (motionReportPath != null && firstRender && Applied != null && session != null && session.Fresh)
            {
                double now = Time.realtimeSinceStartupAsDouble;
                if (motionStarted < 0) motionStarted = now;
                nativeFrames++; renderIntervals.Record(now);
            }
        }
        private void Update()
        {
            if (Application.isBatchMode || SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null) return;
            if (session == null || !session.Fresh || art == null)
            { Clear(); if (publicState != null) publicState.text = "Broadcast · connection unavailable"; if (notesHud != null) ArenaUI.Show(notesHud, false); return; }
            var frame = session.Received;
            camera = Camera.main; if (camera == null || !camera.isActiveAndEnabled) return;
            if (frame.Sequence != applied || frame.Scope.ViewEpoch != epoch)
            {
                if (frame.Scope.ViewEpoch != epoch) Clear();
                Apply(frame); applied = frame.Sequence; epoch = frame.Scope.ViewEpoch; Applied = frame.Clone();
                if (motionReportPath != null) { appliedFrames++; appliedIntervals.Record(Time.realtimeSinceStartupAsDouble); }
            }
            AnimatePermitted();
            ArrangeLabels();
        }
        private void LateUpdate()
        {
            // HUD text/layout must enter UI Toolkit's panel update before the
            // camera render. Fog still obtains this exact eye in LateUpdate.
            if (session == null || !session.Fresh || Applied == null || camera == null || !camera.isActiveAndEnabled) return;
            if (!capturePending && (session.Presented == null || session.Presented.View.Sequence != applied)) StartCoroutine(AcknowledgeFrame(Applied));
        }
        private void Apply(BroadcastView frame)
        {
            var follow = camera.GetComponent<CameraFollow2D>(); if (follow != null) follow.enabled = false;
            camera.orthographic = true; camera.orthographicSize = frame.Camera.OrthographicSize;
            camera.transform.position = new Vector3(frame.Camera.X, frame.Camera.Y, -10); camera.aspect = frame.Camera.Aspect;
            float target = frame.Camera.Aspect, actual = (float)Screen.width / Math.Max(1, Screen.height);
            camera.rect = actual > target ? new Rect((1 - target / actual) / 2, 0, target / actual, 1)
                : new Rect(0, (1 - actual / target) / 2, 1, actual / target);
            foreach (string key in actors.Keys.Where(k => !frame.Actors.Any(a => a.ActorId == k)).ToArray()) RemoveActor(key);
            foreach (var actor in frame.Actors)
            {
                if (!actors.TryGetValue(actor.ActorId, out var proxy))
                {
                    var root = new GameObject("Permitted broadcast avatar"); root.transform.SetParent(transform, false);
                    var visual = new GameObject("Visual"); visual.transform.SetParent(root.transform, false);
                    proxy = new Proxy { Root = root, Sprite = visual.AddComponent<SpriteRenderer>(), Animator = visual.AddComponent<Animator>() };
                    proxy.Sprite.sharedMaterial = art.paletteMaterial; proxy.Animator.runtimeAnimatorController = art.characterController;
                    proxy.Actor = actor; visual.AddComponent<CharacterAnimationView>().SetMotionState(proxy);
                    var mark = new GameObject("Public cosmetic stitch"); mark.transform.SetParent(visual.transform, false); mark.transform.localPosition = new Vector3(0, .39f, -.001f); mark.transform.localScale = Vector3.one * .45f;
                    proxy.Mark = mark.AddComponent<SpriteRenderer>(); proxy.Mark.sortingOrder = 1;
                    if (ArenaUI.WorldLayer != null) proxy.Name = ArenaUI.Text(actor.DisplayName, "world-label player-nameplate", ArenaUI.WorldLayer);
                    actors.Add(actor.ActorId, proxy);
                }
                proxy.Actor = actor; proxy.Root.transform.position = new Vector3(actor.X, actor.Y - .46f, 0);
                proxy.Sprite.enabled = true; proxy.Mark.sprite = art.identityMarks[(int)actor.CosmeticIndex];
                proxy.Mark.enabled = actor.Facing != (uint)FacingDirection.Up && actor.Facing != (uint)FacingDirection.UpLeft && actor.Facing != (uint)FacingDirection.UpRight;
                var block = new MaterialPropertyBlock(); block.SetColor("_SkinColor", SocialAvatarArt.Fabric((int)actor.CosmeticIndex)); proxy.Sprite.SetPropertyBlock(block);
            }
            foreach (string key in bodies.Keys.Where(k => !frame.Bodies.Any(b => b.BodyId == k)).ToArray()) { Destroy(bodies[key].Root); bodies.Remove(key); }
            foreach (var body in frame.Bodies)
            {
                if (!bodies.TryGetValue(body.BodyId, out var proxy))
                {
                    var root = new GameObject("Permitted broadcast body"); root.transform.SetParent(transform, false);
                    proxy = new BodyProxy { Root = root, Sprite = root.AddComponent<SpriteRenderer>() };
                    proxy.Sprite.sprite = art.folds[2]; proxy.Sprite.sharedMaterial = art.paletteMaterial; proxy.Sprite.sortingOrder = -1;
                    var markRoot = new GameObject("Public cosmetic stitch"); markRoot.transform.SetParent(root.transform, false);
                    markRoot.transform.localPosition = new Vector3(.13f, -.1f, -.001f); markRoot.transform.localScale = Vector3.one * .28f;
                    proxy.Mark = markRoot.AddComponent<SpriteRenderer>(); proxy.Mark.sortingOrder = 0; bodies.Add(body.BodyId, proxy);
                }
                proxy.Root.transform.position = new Vector3(body.X, body.Y, 0); proxy.Mark.sprite = art.identityMarks[(int)body.CosmeticIndex];
                var block = new MaterialPropertyBlock(); block.SetColor("_SkinColor", SocialAvatarArt.Fabric((int)body.CosmeticIndex)); proxy.Sprite.SetPropertyBlock(block);
            }
            foreach (var fact in frame.Facts)
            {
                long age = frame.ObservedAtUnixMs - fact.ObservedAtUnixMs;
                if (fact.Predicate != "attack-witnessed" || age < 0 || age > 640 || seenAttacks.Contains(fact.EvidenceId)
                    || !bodies.TryGetValue(fact.Label, out var body)) continue;
                seenAttacks.Add(fact.EvidenceId); attackOrder.Enqueue(fact.EvidenceId);
                while (attackOrder.Count > 256) seenAttacks.Remove(attackOrder.Dequeue());
                body.FoldAt = Time.realtimeSinceStartupAsDouble - age / 1000.0;
                if (actors.TryGetValue(fact.ActorId, out var attacker)) attacker.ImpulseAt = body.FoldAt;
            }
            DrawPublicView(frame);
        }
        private static Label ReadableLabel(VisualElement parent)
        {
            var label = ArenaUI.Text("", "", parent); label.style.fontSize = 12;
            label.style.whiteSpace = WhiteSpace.Normal; label.style.flexShrink = 0;
            label.style.paddingLeft = label.style.paddingRight = label.style.paddingTop = label.style.paddingBottom = 0;
            label.style.marginLeft = label.style.marginRight = label.style.marginTop = label.style.marginBottom = 0;
            return label;
        }
        private void DrawPublicView(BroadcastView frame)
        {
            var layer = ArenaUI.HudLayer;
            if (layer == null) return;
            if (hud == null)
            {
                hud = ArenaUI.Element("panel", layer); hud.pickingMode = PickingMode.Ignore;
                hud.style.position = Position.Absolute; hud.style.left = 16; hud.style.top = 16; hud.style.width = 260;
                publicState = ReadableLabel(hud);
                notesHud = ArenaUI.Element("panel", layer); notesHud.pickingMode = PickingMode.Ignore;
                notesHud.style.position = Position.Absolute; notesHud.style.right = 16; notesHud.style.top = 16;
                for (int i = 0; i < factLabels.Length; i++) factLabels[i] = ReadableLabel(notesHud);
            }
            ArenaUI.Show(notesHud, true);
            float available = layer.resolvedStyle.width;
            notesHud.style.width = float.IsFinite(available) && available > 0 ? Mathf.Clamp(available - 308, 180, 620) : 430;
            var text = new StringBuilder();
            text.Append("Broadcast · ").Append(frame.Phase).Append("\nTasks ").Append(frame.CrewTasksCompleted).Append('/').Append(frame.TaskThreshold);
            if (frame.PhaseEndsAtUnixMs > 0) text.Append(" · ").Append(Math.Max(0, (frame.PhaseEndsAtUnixMs - frame.ObservedAtUnixMs) / 1000)).Append("s");
            if (frame.EmergencyActive) text.Append("\nEmergency · ").Append(frame.EmergencyRoomId).Append(" · ")
                .Append(Math.Max(0, (frame.EmergencyEndsAtUnixMs - frame.ObservedAtUnixMs) / 1000)).Append("s");
            foreach (var actor in frame.Actors) text.Append("\n").Append(actor.DisplayName).Append(" · ").Append(actor.X.ToString("0.00", System.Globalization.CultureInfo.InvariantCulture))
                .Append(',').Append(actor.Y.ToString("0.00", System.Globalization.CultureInfo.InvariantCulture));
            foreach (var body in frame.Bodies) text.Append("\nBody · ").Append(body.VictimDisplayName).Append(" · ").Append(body.X.ToString("0.00", System.Globalization.CultureInfo.InvariantCulture))
                .Append(',').Append(body.Y.ToString("0.00", System.Globalization.CultureInfo.InvariantCulture));
            if (frame.Meeting != null)
            {
                foreach (var row in frame.Meeting.Participants) text.Append("\nRoll call · ").Append(row.DisplayName).Append(" · ").Append(row.Life);
                foreach (var tally in frame.Meeting.Result) text.Append("\nVotes · ").Append(tally.CandidateActorId.Length == 0 ? "Skip" : Name(frame, tally.CandidateActorId)).Append(" · ").Append(tally.Votes);
                if (frame.Meeting.Resolution.Length > 0) text.Append("\nResult · ").Append(frame.Meeting.Resolution)
                    .Append(frame.Meeting.EjectedActorId.Length > 0 ? " · " + Name(frame, frame.Meeting.EjectedActorId) : "");
            }
            if (frame.Terminal)
            {
                text.Append("\nGameOver · ").Append(frame.WinningSide).Append(" · ").Append(frame.WinReason);
                foreach (var row in frame.FinalPlayers) text.Append("\n").Append(row.DisplayName).Append(" · ").Append(row.Alignment).Append(" · ").Append(row.Life);
            }
            publicState.text = text.ToString();
            foreach (string id in confirmed.Keys.Where(id => !frame.Facts.Any(f => f.EvidenceId == id && f.Version == confirmed[id] && f.RetentionExpiresAtUnixMs > frame.ObservedAtUnixMs)).ToArray()) confirmed.Remove(id);
            displayedFacts = BroadcastPresentationRules.SelectHudFacts(frame, confirmed);
            for (int i = 0; i < factLabels.Length; i++)
            {
                var label = factLabels[i]; ArenaUI.Show(label, i < displayedFacts.Length);
                if (i < displayedFacts.Length) label.text = FactText(frame, displayedFacts[i]);
            }
        }
        private static string FactText(BroadcastView frame, BroadcastEditorialFact fact)
        {
            string time = DateTimeOffset.FromUnixTimeMilliseconds(fact.ObservedAtUnixMs).UtcDateTime.ToString("HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture) + " UTC · ";
            return time + (fact.Predicate == "discussion-claim" ? (fact.Label.Length > 0 ? fact.Label : "Player") + " claims: " + fact.Text
                : fact.Predicate == "attack-witnessed" ? "Visible attack · " + Name(frame, fact.ActorId) + " → " + Name(frame, fact.TargetActorId)
                : fact.Predicate == "activity-seen" ? Name(frame, fact.ActorId) + " · console activity " + fact.Label
                : fact.Predicate == "actor-seen" ? "Observed · " + fact.Label + " · " + fact.RoomId
                : fact.Predicate == "body-seen" ? "Observed body · " + fact.Label
                : "Public state · " + fact.Label + " · tasks " + fact.Value);
        }
        private static bool FullyVisible(Label label)
        {
            if (label?.panel == null || label.resolvedStyle.display == DisplayStyle.None || !label.visible) return false;
            Rect bounds = label.worldBound, viewport = label.panel.visualTree.worldBound;
            if (!float.IsFinite(bounds.width) || !float.IsFinite(bounds.height) || bounds.width <= 0 || bounds.height <= 0
                || !viewport.Contains(bounds.min) || !viewport.Contains(bounds.max)) return false;
            Vector2 measured = label.MeasureTextSize(label.text, label.contentRect.width, VisualElement.MeasureMode.Exactly, 0, VisualElement.MeasureMode.Undefined);
            return float.IsFinite(measured.y) && measured.y <= label.contentRect.height + 1;
        }
        private static string Name(BroadcastView frame, string actor)
            => frame.Actors.FirstOrDefault(a => a.ActorId == actor)?.DisplayName
                ?? frame.Bodies.FirstOrDefault(b => b.VictimActorId == actor)?.VictimDisplayName
                ?? frame.Meeting?.Participants.FirstOrDefault(p => p.ActorId == actor)?.DisplayName
                ?? frame.FinalPlayers.FirstOrDefault(p => p.ActorId == actor)?.DisplayName ?? "Participant " + actor.Substring(0, Math.Min(6, actor.Length));
        private void AnimatePermitted()
        {
            double now = Time.realtimeSinceStartupAsDouble;
            foreach (var actor in actors.Values)
            {
                float elapsed = (float)(now - actor.ImpulseAt);
                float impulse = !ArenaUI.ReducedMotion && elapsed >= 0 && elapsed < .18f ? Mathf.Sin(elapsed / .18f * Mathf.PI) * .08f : 0;
                actor.Sprite.transform.localScale = new Vector3(1 + impulse, 1 - impulse, 1);
            }
            foreach (var body in bodies.Values)
            {
                float elapsed = (float)(now - body.FoldAt);
                bool fold = elapsed >= 0 && elapsed < .64f;
                // The current server-filtered body AND local current LOS remain mandatory.
                Vector2 eye = new(Applied.Camera.X, Applied.Camera.Y), point = body.Root.transform.position;
                bool permitted = Vector2.Distance(eye, point) <= Applied.Camera.VisibilityRadius && SocialVisibilitySystem.LineOfSight(eye, point);
                body.Sprite.enabled = body.Mark.enabled = permitted;
                bool reduced = ArenaUI.ReducedMotion;
                body.Sprite.sprite = art.folds[fold && !reduced ? elapsed < .16f ? 0 : elapsed < .36f ? 1 : 2 : 2];
                body.Mark.enabled = permitted && (!fold || reduced || elapsed >= .36f);
                body.Root.transform.localScale = Vector3.one * (fold && !reduced ? 1 + .025f * Mathf.Sin(elapsed / .64f * Mathf.PI) : 1);
            }
        }
        private void ArrangeLabels()
        {
            var layer = ArenaUI.WorldLayer; if (layer?.panel == null || camera == null) return;
            labelSources.Clear();
            foreach (var proxy in actors.Values)
            {
                if (proxy.Name == null) continue;
                var screen = camera.WorldToScreenPoint(proxy.Root.transform.position + Vector3.up * .9f);
                var point = RuntimePanelUtils.ScreenToPanel(layer.panel, new Vector2(screen.x, Screen.height - screen.y));
                int i = labelSources.Count; labels[i] = new NameplateLayoutRules.Entry { Id = ulong.Parse(proxy.Actor.ActorId.Substring(0, 16), System.Globalization.NumberStyles.HexNumber), SourceIndex = i, X = point.x - 44, Y = point.y - 12 }; labelSources.Add(proxy);
            }
            NameplateLayoutRules.Arrange(labels, labelSources.Count, layer.resolvedStyle.width, layer.resolvedStyle.height);
            for (int i = 0; i < labelSources.Count; i++)
            { var entry = labels[i]; var label = labelSources[entry.SourceIndex].Name; ArenaUI.Show(label, entry.Placed); label.style.position = Position.Absolute; label.style.left = entry.X; label.style.top = entry.Y; label.style.width = 88; label.tooltip = labelSources[entry.SourceIndex].Actor.DisplayName; }
        }
        private IEnumerator AcknowledgeFrame(BroadcastView frame)
        {
            // Observe the render of this exact applied frame. Waiting until the
            // next Update first can replace it at a 30 Hz observer cadence.
            capturePending = true; yield return new WaitForEndOfFrame();
            if (motionReportPath != null) ackAttempts++;
            if (this != null && isActiveAndEnabled && camera != null && camera.isActiveAndEnabled && renderedFrame == Time.frameCount
                && !ArenaUI.InputCaptured && Applied != null && Applied.Sequence == frame.Sequence && Applied.Scope.Equals(frame.Scope))
            {
                fog ??= FindAnyObjectByType<SocialFogRenderer>();
                if (fog != null && fog.MatchesPresentedSight(new Vector2(frame.Camera.X, frame.Camera.Y), frame.Scope.ViewEpoch) && FullyVisible(publicState))
                {
                    var references = frame.Facts.Where(f => confirmed.TryGetValue(f.EvidenceId, out ulong version) && version == f.Version)
                        .Select(f => new BroadcastEvidenceRef { EvidenceId = f.EvidenceId, Version = f.Version }).ToList();
                    for (int i = 0; i < displayedFacts.Length; i++) if (FullyVisible(factLabels[i]))
                        references.Add(new BroadcastEvidenceRef { EvidenceId = displayedFacts[i].EvidenceId, Version = displayedFacts[i].Version });
                    if (session.MarkPresented(frame, references, (ulong)Math.Max(1, Time.frameCount), Screen.width, Screen.height))
                    {
                        if (motionReportPath != null) { ackCompleted++; presentedIntervals.Record(Time.realtimeSinceStartupAsDouble); }
                        foreach (var reference in references) confirmed[reference.EvidenceId] = reference.Version;
                        CaptureProbeOnce();
                    }
                    else if (motionReportPath != null) rejectedSession++;
                }
                else if (motionReportPath != null) rejectedFog++;
            }
            else if (motionReportPath != null) rejectedRender++;
            capturePending = false;
        }
        private static bool SafeProbePath(string path) => SafeProofPath(path, ".png");
        private static bool SafeProofPath(string path, string extension)
        {
            try
            {
                if (string.IsNullOrEmpty(path) || path.Length > 1024 || !Path.IsPathRooted(path) || Path.GetFullPath(path) != path
                    || Path.GetExtension(path).ToLowerInvariant() != extension || File.Exists(path) || Directory.Exists(path)) return false;
                var directory = new DirectoryInfo(Path.GetDirectoryName(path));
                if (!directory.Exists) return false;
                for (var parent = directory; parent != null; parent = parent.Parent)
                    if ((parent.Attributes & FileAttributes.ReparsePoint) != 0) return false;
                return true;
            }
            catch (Exception) { return false; }
        }
        private void SaveMotionReport()
        {
            string path = motionReportPath; motionReportPath = null;
            if (path == null || !SafeProofPath(path, ".json")) return;
            try
            {
                var report = new MotionRenderReport { elapsed_seconds = motionStarted >= 0 ? Math.Max(0, Time.realtimeSinceStartupAsDouble - motionStarted) : 0,
                    forced_frame_rate = motionForcedFrameRate,
                    target_frame_rate = Application.targetFrameRate, v_sync_count = QualitySettings.vSyncCount,
                    native_frames = nativeFrames, applied_frames = appliedFrames, ack_attempts = ackAttempts, ack_completed = ackCompleted,
                    rejected_render = rejectedRender, rejected_fog = rejectedFog, rejected_session = rejectedSession,
                    render_intervals = renderIntervals.Summary(), applied_intervals = appliedIntervals.Summary(), presented_intervals = presentedIntervals.Summary() };
                string transport = session != null ? JsonUtility.ToJson(session.MotionDiagnostics()) : "{}";
                byte[] bytes = Encoding.UTF8.GetBytes("{\"schema\":\"actor.broadcast-motion.v1\",\"render\":" + JsonUtility.ToJson(report) + ",\"transport\":" + transport + "}");
                if (bytes.Length > 32768) return;
                using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                if (PrivateFileMode(stream.SafeFileHandle.DangerousGetHandle().ToInt32(), 384) != 0) return;
                stream.Write(bytes, 0, bytes.Length); stream.Flush(true);
            }
            catch (Exception) { } // Optional numeric QA only; never log path or game state.
        }
        private void CaptureProbeOnce()
        {
            if (probeConsumed || probePath == null || Application.isBatchMode || SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null) return;
            probeConsumed = true; string path = probePath; probePath = null;
            Texture2D texture = null;
            try
            {
                if (!SafeProbePath(path) || Screen.width < 320 || Screen.height < 180 || (long)Screen.width * Screen.height > 3840L * 2160) return;
                texture = ScreenCapture.CaptureScreenshotAsTexture(); if (texture == null) return;
                byte[] bytes = texture.EncodeToPNG(); if (bytes == null || bytes.Length == 0 || bytes.Length > 4 * 1024 * 1024) return;
                // Controller supplies an existing private directory. CreateNew
                // refuses existing files/symlinks; never overwrite a proof.
                using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                if (PrivateFileMode(stream.SafeFileHandle.DangerousGetHandle().ToInt32(), 384) != 0) return; // 0600
                stream.Write(bytes, 0, bytes.Length); stream.Flush(true);
            }
            catch (Exception) { } // Optional private QA artifact; never log its path or pixels.
            finally { if (texture != null) Destroy(texture); }
        }
        private void RemoveActor(string key) { var proxy = actors[key]; proxy.Name?.RemoveFromHierarchy(); Destroy(proxy.Root); actors.Remove(key); }
        private void Clear() { foreach (string key in actors.Keys.ToArray()) RemoveActor(key); foreach (var body in bodies.Values) Destroy(body.Root); bodies.Clear(); Applied = null; applied = 0; }
        private void OnApplicationQuit() => SaveMotionReport();
        private void OnDisable() { SaveMotionReport(); RenderPipelineManager.endCameraRendering -= Rendered; StopAllCoroutines(); capturePending = false; Clear(); hud?.RemoveFromHierarchy(); notesHud?.RemoveFromHierarchy(); hud = notesHud = null; confirmed.Clear(); if (Current == this) Current = null; }
#else
        internal bool Sight(out Vector2 eye, out float radius, out ulong epoch) { eye = Vector2.zero; radius = 0; epoch = 0; return false; }
#endif
    }
}
