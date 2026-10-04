using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Actor.Characters;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;

namespace Actor.Editor
{
    [InitializeOnLoad]
    public static class PlayableMilestoneVerification
    {
        [Serializable] private class Check { public string name; public bool passed; public string detail; }
        [Serializable] private class Report { public string unityVersion; public string utcTime; public Check[] checks; public string[] errors; public string[] warnings; }
        private sealed class Commands : IMovementInputSource
        {
            public Vector2 Value;
            public Vector2 ReadMovement() => Value;
        }
        private static readonly List<Check> checks = new List<Check>();
        private static readonly List<string> errors = new List<string>();
        private static readonly List<string> warnings = new List<string>();
        private static IEnumerator routine;
        private static double waitUntil;
        private static double nextProgress;
        public static string Status => $"playing={EditorApplication.isPlaying}, routine={routine != null}, time={Time.time}, checks={checks.Count}";
        private static Keyboard keyboard;
        private static string Folder => Path.GetFullPath("Verification");

        static PlayableMilestoneVerification()
        {
            EditorApplication.playModeStateChanged += OnPlayMode;
        }

        [MenuItem("ACTOR/Verify playable in Play Mode")]
        public static void Start()
        {
            if (EditorApplication.isPlaying) throw new InvalidOperationException("Exit Play Mode before verification.");
            EditorSceneManager.OpenScene(PlayableMilestoneBuilder.ScenePath);
            Directory.CreateDirectory(Folder);
            SessionState.SetBool("ACTOR.Verification", true);
            EditorApplication.isPlaying = true;
        }

        private static void OnPlayMode(PlayModeStateChange state)
        {
            if (state == PlayModeStateChange.EnteredPlayMode && SessionState.GetBool("ACTOR.Verification", false))
            {
                SessionState.SetBool("ACTOR.Verification", false);
                checks.Clear(); errors.Clear(); warnings.Clear();
                Application.logMessageReceived += OnLog;
                routine = Run();
                File.WriteAllText(Path.Combine(Folder, "progress.txt"), "Entered Play Mode\n");
                waitUntil = 0;
                EditorApplication.update += Tick;
            }
            if (state == PlayModeStateChange.ExitingPlayMode) Finish(false);
        }

        private static void OnLog(string message, string stack, LogType type)
        {
            if (type == LogType.Error || type == LogType.Exception || type == LogType.Assert) errors.Add(message);
            if (type == LogType.Warning) warnings.Add(message);
        }

        private static void Tick()
        {
            if (!EditorApplication.isPlaying) return;
            EditorApplication.QueuePlayerLoopUpdate();
            if (EditorApplication.timeSinceStartup > nextProgress)
            {
                nextProgress = EditorApplication.timeSinceStartup + 1;
                File.WriteAllText(Path.Combine(Folder, "progress.txt"), Status);
            }
            if (Time.time < waitUntil) return;
            try
            {
                if (!routine.MoveNext()) { Finish(true); return; }
                waitUntil = Time.time + (routine.Current is float seconds ? seconds : 0f);
            }
            catch (Exception error)
            {
                errors.Add(error.ToString());
                Finish(true);
            }
        }

        private static void Finish(bool write)
        {
            EditorApplication.update -= Tick;
            Application.logMessageReceived -= OnLog;
            if (keyboard != null) { InputSystem.RemoveDevice(keyboard); keyboard = null; }
            routine = null;
            if (!write) return;
            File.WriteAllText(Path.Combine(Folder, "playmode-report.json"), JsonUtility.ToJson(new Report
            {
                unityVersion = Application.unityVersion,
                utcTime = DateTime.UtcNow.ToString("O"), checks = checks.ToArray(),
                errors = errors.ToArray(), warnings = warnings.ToArray()
            }, true));
            int failures = checks.Count(c => !c.passed) + errors.Count;
            Debug.Log($"ACTOR Play Mode verification: {checks.Count} checks, {failures} failures, {warnings.Count} warnings. See Verification/playmode-report.json.");
        }

        private static void CheckResult(string name, bool passed, string detail = "")
            => checks.Add(new Check { name = name, passed = passed, detail = detail });

        private static void Reset(Rigidbody2D body, Vector2 position)
        {
            body.position = position;
            body.linearVelocity = Vector2.zero;
            Physics2D.SyncTransforms();
        }

        private static IEnumerator Run()
        {
            yield return 0.5f;
            var actor = UnityEngine.Object.FindObjectsByType<ActorCharacter>().Single();
            var motor = actor.GetComponent<CharacterMotor2D>();
            var body = actor.GetComponent<Rigidbody2D>();
            var feet = actor.GetComponent<Collider2D>();
            var renderer = actor.GetComponentInChildren<SpriteRenderer>();
            var animator = actor.GetComponentInChildren<Animator>();
            var input = actor.GetComponent<PlayerInputSource>();
            var commands = new Commands();
            actor.SetInputSource(commands);
            var origin = new Vector2(-1.6f, -1.25f);
            Vector2[] directions = { Vector2.down, new Vector2(1,-1), Vector2.right,
                Vector2.one, Vector2.up, new Vector2(-1,1), Vector2.left, -Vector2.one };
            var assets = new[] { "bottom", "right_bottom", "right", "right_top", "top", "right_top", "left", "right_bottom" };
            CheckResult("Authored actor and room IDs", actor.ActorId == "main-hall-local-01" && actor.RoomId == "main-hall");
            CheckResult("Physics configuration", body.gravityScale == 0 && body.interpolation == RigidbodyInterpolation2D.Interpolate
                && body.collisionDetectionMode == CollisionDetectionMode2D.Continuous && body.freezeRotation);
            for (int i = 0; i < directions.Length; i++)
            {
                Reset(body, origin);
                commands.Value = directions[i];
                yield return 0.04f;
                float initialSpeed = motor.Velocity.magnitude;
                CheckResult("Acceleration " + (FacingDirection)i, initialSpeed > 0 && initialSpeed < motor.MaxSpeed,
                    initialSpeed.ToString("F3"));
                string firstFrame = renderer.sprite.name;
                yield return 0.32f;
                var state = actor.CaptureState();
                CheckResult("Normalized speed " + (FacingDirection)i, Mathf.Abs(state.Velocity.magnitude - motor.MaxSpeed) < 0.03f,
                    state.Velocity.magnitude.ToString("F3"));
                CheckResult("Walk facing " + (FacingDirection)i, state.Facing == (FacingDirection)i && state.IsMoving
                    && renderer.sprite.name.Contains("hero_sprites_" + assets[i] + "_frame_")
                    && renderer.flipX == (i == 5 || i == 7)
                    && renderer.sprite.name != firstFrame, renderer.sprite.name);
                commands.Value = Vector2.zero;
                yield return 0.04f;
                CheckResult("Deceleration " + (FacingDirection)i, motor.Velocity.magnitude < motor.MaxSpeed && motor.Velocity.magnitude > 0);
                yield return 0.16f;
                state = actor.CaptureState();
                var canonical = i == 5 ? FacingDirection.UpRight : i == 7 ? FacingDirection.DownRight : (FacingDirection)i;
                CheckResult("Idle facing " + (FacingDirection)i, !state.IsMoving && state.Facing == (FacingDirection)i
                    && animator.GetCurrentAnimatorStateInfo(0).IsName("Idle_" + canonical)
                    && renderer.flipX == (i == 5 || i == 7));
                ScreenCapture.CaptureScreenshot(Path.Combine(Folder, "idle-" + (FacingDirection)i + ".png"));
                yield return 0.1f;
            }

            // The real Input System action path is exercised with a temporary keyboard device.
            actor.SetInputSource(input);
            keyboard = InputSystem.AddDevice<Keyboard>("ACTOR Verification Keyboard");
            Key[][] keys = { new[]{Key.S}, new[]{Key.D,Key.S}, new[]{Key.D}, new[]{Key.D,Key.W},
                new[]{Key.W}, new[]{Key.A,Key.W}, new[]{Key.A}, new[]{Key.A,Key.S} };
            for (int i = 0; i < keys.Length; i++)
            {
                Reset(body, origin);
                InputSystem.QueueStateEvent(keyboard, new KeyboardState(keys[i]));
                yield return 0.25f;
                CheckResult("Keyboard action " + (FacingDirection)i, motor.Facing == (FacingDirection)i
                    && Mathf.Abs(motor.Velocity.magnitude - motor.MaxSpeed) < 0.03f,
                    $"velocity={motor.Velocity}, input={input.ReadMovement()}, focused={Application.isFocused}");
                InputSystem.QueueStateEvent(keyboard, new KeyboardState());
                yield return 0.15f;
            }
            InputSystem.RemoveDevice(keyboard);
            keyboard = null;
            actor.SetInputSource(commands);

            var points = PlayableMilestoneBuilder.FloorPixels;
            for (int i = 0; i < points.Length; i++)
            {
                Vector2 a = PlayableMilestoneBuilder.PixelToWorld(points[i]);
                Vector2 b = PlayableMilestoneBuilder.PixelToWorld(points[(i + 1) % points.Length]);
                Vector2 delta = b - a;
                Vector2 outward = new Vector2(-delta.y, delta.x).normalized;
                Vector2 midpoint = (a + b) / 2;
                commands.Value = Vector2.zero;
                Reset(body, midpoint - outward * 0.42f - Vector2.up * 0.09f);
                yield return 0.04f;
                commands.Value = outward;
                yield return 0.55f;
                float distance = Vector2.Dot(body.position + Vector2.up * 0.09f - midpoint, outward);
                CheckResult("Wall segment " + i.ToString("D2"), distance <= -0.06f,
                    "Signed distance to floor boundary: " + distance.ToString("F3"));
                commands.Value = Vector2.zero;
                yield return 0.12f;
            }

            var crystal = UnityEngine.Object.FindObjectsByType<Actor.World.WorldEntity>().Single();
            var crystalCollider = crystal.GetComponent<Collider2D>();
            Vector2 crystalCenter = (Vector2)crystal.transform.position + Vector2.up * 0.1f;
            foreach (Vector2 direction in new[] { Vector2.up, Vector2.down, Vector2.left, Vector2.right })
            {
                Reset(body, crystalCenter + direction * 0.95f - Vector2.up * 0.09f);
                commands.Value = -direction;
                yield return 0.65f;
                float plane = Vector2.Dot(body.position + Vector2.up * 0.09f - crystalCenter, direction);
                float separation = feet.Distance(crystalCollider).distance;
                // The physics solver allows contact penetration within its configured tolerance.
                CheckResult("Crystal collision " + direction, plane > 0.24f && separation >= -Physics2D.defaultContactOffset,
                    $"plane={plane:F3}, separation={separation:F4}");
                commands.Value = Vector2.zero;
                yield return 0.15f;
            }

            // Slide along the long corridor walls under simultaneous forward/outward commands.
            foreach (float side in new[] { -1f, 1f })
            {
                Reset(body, new Vector2(side * 0.15f, 3.35f));
                commands.Value = new Vector2(side, 1);
                yield return 0.7f;
                CheckResult("Corridor wall slide " + side, body.position.y > 4.25f && Mathf.Abs(body.position.x) < 0.29f,
                    body.position.ToString());
                commands.Value = Vector2.zero;
                yield return 0.15f;
            }
            Reset(body, new Vector2(0, 4.65f));
            yield return 0.85f;
            var camera = Camera.main;
            CheckResult("Camera follows and clamps", Mathf.Abs(camera.transform.position.y - 2.17f) < 0.08f,
                camera.transform.position.ToString());
            Reset(body, new Vector2(-0.9f, -1.25f));
            commands.Value = Vector2.down;
            yield return 0.04f;
            commands.Value = Vector2.zero;
            yield return 0.5f;
            actor.SetInputSource(input);
            var lights = UnityEngine.Object.FindObjectsByType<UnityEngine.Rendering.Universal.Light2D>();
            CheckResult("Global and crystal 2D lights", lights.Length == 2
                && lights.Any(l => l.lightType == UnityEngine.Rendering.Universal.Light2D.LightType.Global)
                && lights.Any(l => l.lightType == UnityEngine.Rendering.Universal.Light2D.LightType.Point));
            ScreenCapture.CaptureScreenshot(Path.Combine(Folder, "main-hall-playmode.png"));
            yield return 0.1f;
            CheckResult("No runtime errors or warnings", errors.Count == 0 && warnings.Count == 0);
        }
    }
}
