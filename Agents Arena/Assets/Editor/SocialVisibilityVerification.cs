using System;
using System.Collections.Generic;
using System.IO;
using Actor.Networking;
using UnityEditor;
using UnityEngine;

namespace Actor.Editor
{
    public static class SocialVisibilityVerification
    {
        [Serializable] private sealed class Check { public string name; public bool passed; }
        [Serializable] private sealed class Report { public bool passed; public Check[] checks; }

        [MenuItem("Actor/Verify Social Visibility Geometry")]
        public static void Run()
        {
            var checks = new List<Check>();
            void Verify(string name, bool passed) { checks.Add(new Check { name = name, passed = passed }); }
            var wall = new GameObject("Temporary Visibility Test Wall");
            try
            {
                wall.transform.position = new Vector3(1000, 1000, 0);
                var collider = wall.AddComponent<BoxCollider2D>(); collider.size = new Vector2(.2f, 2);
                var marker = wall.AddComponent<SocialVisibilityOccluder>();
                Physics2D.SyncTransforms();
                Verify("authored_solid_wall_blocks", !SocialVisibilitySystem.LineOfSight(new Vector2(999, 1000), new Vector2(1001, 1000)));
                Verify("clear_ray_remains_visible", SocialVisibilitySystem.LineOfSight(new Vector2(999, 1002), new Vector2(1001, 1002)));
                marker.enabled = false;
                Verify("ordinary_collider_does_not_block", SocialVisibilitySystem.LineOfSight(new Vector2(999, 1000), new Vector2(1001, 1000)));
                marker.enabled = true; collider.isTrigger = true; Physics2D.SyncTransforms();
                Verify("interaction_trigger_does_not_block", SocialVisibilitySystem.LineOfSight(new Vector2(999, 1000), new Vector2(1001, 1000)));
                Verify("nonfinite_pose_is_closed", !SocialVisibilitySystem.LineOfSight(new Vector2(float.NaN, 0), Vector2.zero));
                var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(MultiplayerBuild.PlayerPrefab);
                Verify("player_prefab_has_pre_spawn_member", prefab != null && prefab.GetComponent<SocialVisibilityMember>() != null);
            }
            finally { UnityEngine.Object.DestroyImmediate(wall); }
            bool passedAll = checks.TrueForAll(check => check.passed);
            Directory.CreateDirectory("Verification");
            File.WriteAllText("Verification/social-visibility-geometry.json", JsonUtility.ToJson(new Report { passed = passedAll, checks = checks.ToArray() }, true) + "\n");
            if (!passedAll) throw new InvalidOperationException("Social visibility geometry verification failed; inspect the bounded verification report.");
            Debug.Log("ACTOR_SOCIAL_VISIBILITY_GEOMETRY passed=" + checks.Count);
        }
    }
}
