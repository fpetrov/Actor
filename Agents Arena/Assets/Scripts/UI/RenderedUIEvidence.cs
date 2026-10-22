using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Actor.Networking;
using UnityEngine;
#if !UNITY_SERVER
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using UnityEngine.InputSystem;
#endif

namespace Actor.UI
{
    // Explicit native QA opt-in. Captures the actual rendered framebuffer;
    // does not set state, click controls, join rooms or reveal authority data.
    public sealed class RenderedUIEvidence : MonoBehaviour
    {
#if !UNITY_SERVER
        [Serializable] private sealed class Evidence
        {
            public string schema = "actor.rendered-ui.v2", scope = "Actual rendered standalone frame intervals after two-second warmup, restricted to the current phase and resolution; not hardware presentation timings or Human acceptance by itself.";
            public string phase, screenshot, unity, graphics, version;
            public int width, height, frames, focused_frames;
            public double sample_seconds, mean_fps, frame_ms_p50, frame_ms_p95, frame_ms_p99;
            public bool headless, fullscreen;
        }
        private string directory;
        private readonly List<double> intervals = new();
        private int focused, captures;
        private double started;
        private int sampleWidth, sampleHeight;
        private string samplePhase;
        private bool pending;
        [Serializable] private sealed class PresentationFrame
        {
            public string screenshot, phase, audio_status;
            public double target_seconds, elapsed_seconds;
            public long captured_at_unix_ms;
            public int width, height, framebuffer_width, framebuffer_height, audio_samples, audio_sample_rate;
            public bool focused, fullscreen, camera_present, camera_orthographic, audio_measured;
            public float camera_x, camera_y, camera_z, camera_size, camera_aspect, camera_field_of_view;
            public double audio_rms, audio_peak;
        }
        [Serializable] private sealed class PresentationEvidence
        {
            public string schema = "actor.rendered-presentation.v1";
            public string scope = "Actual framebuffer samples after an own permitted presentation event; no authority/identity lookup. PNG encoding and disk I/O perturb frame timing. Audio values are local DSP samples, not proof of human audibility.";
            public string unity, graphics, version, status = "capturing";
            public int event_index;
            public bool audio_requested;
            public PresentationFrame[] frames = Array.Empty<PresentationFrame>();
        }
        private sealed class PresentationRun
        {
            internal NetworkSocialMatch owner;
            internal string match;
            internal double started;
            internal Coroutine routine;
            internal PresentationEvidence evidence;
            internal readonly List<PresentationFrame> frames = new();
        }
        private static readonly double[] PresentationOffsets = { .04, .18, .34, .62, .80 };
        private readonly List<PresentationRun> presentationRuns = new();
        private readonly HashSet<string> presentationIds = new(StringComparer.Ordinal);
        private NetworkSocialMatch presentationOwner;
        private bool capturePresentations, captureAudio, presentationOutputFailed;
        private int presentationEvents;
        private readonly float[] audioSamples = new float[1024];
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap()
        {
            var args = Environment.GetCommandLineArgs(); int index = Array.IndexOf(args, "--ui-evidence");
            if (index < 0 || index + 1 >= args.Length || Application.isBatchMode || SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null) return;
            var root = new GameObject("Opt-in rendered UI evidence"); DontDestroyOnLoad(root);
            var evidence = root.AddComponent<RenderedUIEvidence>(); evidence.directory = Path.GetFullPath(args[index + 1]);
            Directory.CreateDirectory(evidence.directory); evidence.started = Time.realtimeSinceStartupAsDouble;
            evidence.capturePresentations = args.Contains("--ui-capture-presentations");
            evidence.captureAudio = evidence.capturePresentations && args.Contains("--ui-capture-audio-metrics");
            if (args.Contains("--ui-capture-initial")) evidence.StartCoroutine(evidence.Initial());
        }
        private IEnumerator Initial() { yield return new WaitForSecondsRealtime(3); yield return Capture(); }
        private void Update()
        {
            if (capturePresentations) BindPresentations();
            string phase = CurrentPhase();
            if (sampleWidth != Screen.width || sampleHeight != Screen.height || samplePhase != phase)
            {
                sampleWidth = Screen.width; sampleHeight = Screen.height; samplePhase = phase;
                intervals.Clear(); focused = 0; started = Time.realtimeSinceStartupAsDouble;
            }
            if (Time.realtimeSinceStartupAsDouble - started > 2 && intervals.Count < 36000)
            { intervals.Add(Time.unscaledDeltaTime * 1000); if (Application.isFocused) ++focused; }
            if (!pending && Application.isFocused && Keyboard.current?.f12Key.wasPressedThisFrame == true) StartCoroutine(Capture());
        }
        private IEnumerator Capture()
        {
            if (pending) yield break; pending = true;
            yield return new WaitForEndOfFrame();
            string phase = CurrentPhase();
            string stem = (++captures).ToString("D3") + "-" + phase + "-" + Screen.width + "x" + Screen.height;
            var texture = ScreenCapture.CaptureScreenshotAsTexture(); File.WriteAllBytes(Path.Combine(directory, stem + ".png"), texture.EncodeToPNG()); Destroy(texture);
            var sorted = intervals.OrderBy(x => x).ToArray();
            double total = intervals.Sum() / 1000;
            var result = new Evidence { phase = phase, screenshot = stem + ".png", unity = Application.unityVersion, version = Application.version,
                graphics = SystemInfo.graphicsDeviceType.ToString(), width = Screen.width, height = Screen.height, fullscreen = Screen.fullScreen,
                frames = intervals.Count, focused_frames = focused, sample_seconds = total, mean_fps = total > 0 ? intervals.Count / total : 0,
                frame_ms_p50 = Percentile(sorted, .5), frame_ms_p95 = Percentile(sorted, .95), frame_ms_p99 = Percentile(sorted, .99), headless = false };
            File.WriteAllText(Path.Combine(directory, stem + ".json"), JsonUtility.ToJson(result, true)); pending = false;
        }
        private void BindPresentations()
        {
            var next = NetworkSocialMatch.LocalOwner;
            if (ReferenceEquals(next, presentationOwner)) return;
            UnbindPresentations("owner_changed"); presentationOwner = next;
            if (presentationOwner != null) presentationOwner.PermittedEvent += OnPresentation;
        }
        private void OnPresentation(IMessage value)
        {
            if (presentationOutputFailed || presentationEvents >= 4 || value is not SocialKillPresentationEvent signal
                || presentationOwner == null || !presentationOwner.IsSpawned || !presentationOwner.IsOwner || !presentationOwner.IsClient
                || signal.MatchId != presentationOwner.State.MatchId || signal.RecipientActorId != presentationOwner.Self
                || !Guid.TryParseExact(signal.PresentationId, "N", out _) || !presentationIds.Add(signal.PresentationId)) return;
            var run = new PresentationRun { owner = presentationOwner, match = signal.MatchId, started = Time.realtimeSinceStartupAsDouble,
                evidence = new PresentationEvidence { event_index = ++presentationEvents, unity = Application.unityVersion,
                    graphics = SystemInfo.graphicsDeviceType.ToString(), version = Application.version, audio_requested = captureAudio } };
            presentationRuns.Add(run); WritePresentation(run);
            if (presentationOutputFailed) { FinishPresentation(run, "output_failed"); return; }
            run.routine = StartCoroutine(CapturePresentation(run));
        }
        private IEnumerator CapturePresentation(PresentationRun run)
        {
            foreach (double target in PresentationOffsets)
            {
                while (Time.realtimeSinceStartupAsDouble - run.started < target) yield return null;
                yield return new WaitForEndOfFrame();
                if (!OwnPresentationStillBound(run)) { FinishPresentation(run, "owner_changed"); yield break; }
                if (Time.realtimeSinceStartupAsDouble - run.started > 3) { FinishPresentation(run, "capture_deadline_exceeded"); yield break; }
                if (!CapturePresentationFrame(run, target)) { FinishPresentation(run, "capture_failed"); yield break; }
                WritePresentation(run);
                if (presentationOutputFailed) { FinishPresentation(run, "output_failed"); yield break; }
            }
            FinishPresentation(run, "completed");
        }
        private bool OwnPresentationStillBound(PresentationRun run) => isActiveAndEnabled && run.owner != null
            && ReferenceEquals(run.owner, presentationOwner) && run.owner.IsSpawned && run.owner.IsOwner && run.owner.IsClient
            && run.owner.State.MatchId == run.match;
        private bool CapturePresentationFrame(PresentationRun run, double target)
        {
            Texture2D texture = null;
            try
            {
                var camera = Camera.main;
                var frame = new PresentationFrame { target_seconds = target, elapsed_seconds = Time.realtimeSinceStartupAsDouble - run.started,
                    captured_at_unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), phase = CurrentPhase(), width = Screen.width,
                    height = Screen.height, focused = Application.isFocused, fullscreen = Screen.fullScreen, camera_present = camera != null };
                if (camera != null)
                {
                    var position = camera.transform.position;
                    frame.camera_x = position.x; frame.camera_y = position.y; frame.camera_z = position.z;
                    frame.camera_orthographic = camera.orthographic; frame.camera_size = camera.orthographicSize;
                    frame.camera_aspect = camera.aspect; frame.camera_field_of_view = camera.fieldOfView;
                }
                MeasureAudio(frame, camera);
                frame.elapsed_seconds = Time.realtimeSinceStartupAsDouble - run.started;
                frame.captured_at_unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                texture = ScreenCapture.CaptureScreenshotAsTexture();
                if (texture == null) return false;
                frame.framebuffer_width = texture.width; frame.framebuffer_height = texture.height;
                frame.screenshot = "presentation-" + run.evidence.event_index.ToString("D2") + "-frame-" + (run.frames.Count + 1).ToString("D2") + ".png";
                File.WriteAllBytes(Path.Combine(directory, frame.screenshot), texture.EncodeToPNG());
                run.frames.Add(frame); return true;
            }
            catch (Exception) { return false; }
            finally { if (texture != null) Destroy(texture); }
        }
        private void MeasureAudio(PresentationFrame frame, Camera camera)
        {
            frame.audio_status = captureAudio ? "camera_listener_unavailable" : "not_requested";
            var listener = camera != null ? camera.GetComponent<AudioListener>() : null;
            if (!captureAudio || listener == null || !listener.isActiveAndEnabled) return;
            try
            {
                AudioListener.GetOutputData(audioSamples, 0);
                double sum = 0, peak = 0;
                foreach (float sample in audioSamples)
                {
                    if (!float.IsFinite(sample)) { frame.audio_status = "nonfinite_samples"; return; }
                    sum += (double)sample * sample; peak = Math.Max(peak, Math.Abs(sample));
                }
                frame.audio_samples = audioSamples.Length; frame.audio_sample_rate = AudioSettings.outputSampleRate;
                frame.audio_rms = Math.Sqrt(sum / audioSamples.Length); frame.audio_peak = peak;
                frame.audio_measured = true; frame.audio_status = "local_dsp_samples";
            }
            catch (Exception) { frame.audio_status = "sampling_unavailable"; }
        }
        private void WritePresentation(PresentationRun run)
        {
            run.evidence.frames = run.frames.ToArray();
            try { File.WriteAllText(Path.Combine(directory, "presentation-" + run.evidence.event_index.ToString("D2") + ".json"), JsonUtility.ToJson(run.evidence, true)); }
            catch (Exception) { presentationOutputFailed = true; }
        }
        private void FinishPresentation(PresentationRun run, string status)
        { run.evidence.status = status; WritePresentation(run); presentationRuns.Remove(run); }
        private void UnbindPresentations(string reason)
        {
            if (!ReferenceEquals(presentationOwner, null)) presentationOwner.PermittedEvent -= OnPresentation;
            presentationOwner = null;
            foreach (var run in presentationRuns.ToArray())
            {
                if (run.routine != null) StopCoroutine(run.routine);
                FinishPresentation(run, reason);
            }
        }
        private void OnDisable() => UnbindPresentations("collector_disabled");
        private void OnDestroy() => UnbindPresentations("collector_destroyed");
        private static double Percentile(double[] values, double fraction) => values.Length == 0 ? 0 : values[(int)Math.Min(values.Length - 1, Math.Ceiling(values.Length * fraction) - 1)];
        private static string CurrentPhase() => NetworkSocialMatch.LocalOwner != null ? NetworkSocialMatch.LocalOwner.State.Phase.ToString() : "Lobby";
#endif
    }
}
