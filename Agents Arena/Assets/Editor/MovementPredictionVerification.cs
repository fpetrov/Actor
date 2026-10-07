using System;
using System.Collections.Generic;
using System.IO;
using Actor.Characters;
using Actor.Networking;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Actor.Editor
{
    public static class MovementPredictionVerification
    {
        [Serializable] private sealed class Check { public string name; public bool passed; public string detail; }
        [Serializable] private sealed class Report { public Check[] checks; }

        // Isolated, remote coordinates keep these query tests away from authored scene colliders.
        public static void Run()
        {
            var checks = new List<Check>();
            var character = new GameObject("Prediction verification capsule");
            var wall = new GameObject("Prediction verification wall");
            Vector2 origin = new(1000, 1000);
            try
            {
                character.transform.position = origin;
                var body = character.AddComponent<Rigidbody2D>();
                body.bodyType = RigidbodyType2D.Kinematic;
                var capsule = character.AddComponent<CapsuleCollider2D>();
                capsule.size = new Vector2(0.28f, 0.81f);
                capsule.offset = new Vector2(0, 0.46f);
                var motor = character.AddComponent<CharacterMotor2D>();
                motor.enabled = false;
                wall.transform.position = origin + Vector2.right * 0.55f;
                var box = wall.AddComponent<BoxCollider2D>();
                box.size = new Vector2(0.1f, 10);
                Physics2D.SyncTransforms();
                float now = Time.unscaledTime;
                var prediction = new OwnerMovementPrediction(motor, capsule, origin);
                prediction.Advance(Vector2.right, now + 0.02f);
                CheckResult("Input moves presentation before any server snapshot", prediction.Position.x > origin.x);
                CheckResult("Prediction cannot move the network body", body.position == origin);
                for (int i = 2; i <= 20; i++) prediction.Advance(Vector2.right, now + i * 0.02f);
                CheckResult("Swept capsule stops at static wall", prediction.Position.x < origin.x + 0.365f
                    && prediction.Position.x > origin.x + 0.3f && prediction.Velocity.magnitude < 0.01f);
                var sliding = new OwnerMovementPrediction(motor, capsule, origin);
                for (int i = 1; i <= 20; i++) sliding.Advance(new Vector2(1, -1).normalized, now + i * 0.02f);
                CheckResult("Wall contact preserves tangential movement", sliding.Position.y < origin.y - 0.4f
                    && sliding.Position.x < origin.x + 0.365f, (sliding.Position - origin) + " velocity=" + sliding.Velocity);
                var touching = new OwnerMovementPrediction(motor, capsule, origin + Vector2.right * 0.36f);
                for (int i = 1; i <= 15; i++) touching.Advance(new Vector2(1, -1).normalized, now + i * 0.02f);
                CheckResult("Authoritative contact pose can slide on first frame", touching.Position.y < origin.y - 0.2f,
                    (touching.Position - origin) + " velocity=" + touching.Velocity);
                Vector2 beforeStall = prediction.Position;
                prediction.Advance(Vector2.left, now + 1);
                CheckResult("Snapshot timeout freezes prediction", prediction.Position == beforeStall && prediction.Velocity == Vector2.zero);
                Vector2 recovered = origin + Vector2.right * 0.1f;
                prediction.Reconcile(2, 0, 0, recovered, Vector2.zero, FacingDirection.Down, now + 1.1f);
                CheckResult("Recovery removes visual lead", prediction.Position == recovered && prediction.RenderedPosition == recovered);
                prediction.Reconcile(1, 0, 0, origin, Vector2.zero, FacingDirection.Down, now + 1.2f);
                CheckResult("Out-of-order snapshots cannot roll back the owner", prediction.Position == recovered);

                var replay = new OwnerMovementPrediction(motor, capsule, origin);
                replay.Record(1, Vector2.right, now);
                replay.Reconcile(uint.MaxValue, 1, 0.02f, origin, Vector2.zero, FacingDirection.Right, now + 0.12f);
                CheckResult("Unacknowledged input replays from server pose", replay.Position.x > origin.x + 0.08f
                    && replay.Position.x < origin.x + 0.2f);
                replay.Reconcile(0, 999, 0, recovered, Vector2.zero, FacingDirection.Down, now + 0.13f);
                CheckResult("Snapshot sequence wrap accepts the next packet", replay.Position == recovered);
                replay.Reconcile(uint.MaxValue, 999, 0, origin, Vector2.zero, FacingDirection.Down, now + 0.14f);
                CheckResult("Sequence wrap still rejects old packets", replay.Position == recovered);
                var bounded = new OwnerMovementPrediction(motor, capsule, origin - Vector2.right * 5);
                bounded.Record(1, Vector2.right, now);
                bounded.Record(2, Vector2.zero, now + 0.48f);
                bounded.Reconcile(1, 1, 0, origin - Vector2.right * 5, Vector2.right * motor.MaxSpeed,
                    FacingDirection.Right, now + 0.6f);
                CheckResult("Bounded replay preserves a recent release under high latency", bounded.Velocity.magnitude < 0.05f);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(character);
                UnityEngine.Object.DestroyImmediate(wall);
            }
            string path = Path.GetFullPath("../build/verification/prediction-query-tests.json");
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, JsonUtility.ToJson(new Report { checks = checks.ToArray() }, true));
            if (checks.Exists(check => !check.passed)) throw new InvalidOperationException("Movement prediction verification failed: " + path);
            Debug.Log("ACTOR: " + checks.Count + " movement prediction checks passed.");

            void CheckResult(string name, bool passed, string detail = "") => checks.Add(new Check { name = name, passed = passed, detail = detail });
        }

        public static void PlayMultiplayer()
        {
            EditorSceneManager.OpenScene(MultiplayerBuild.LobbyScene);
            EditorApplication.isPlaying = true;
        }
    }
}
