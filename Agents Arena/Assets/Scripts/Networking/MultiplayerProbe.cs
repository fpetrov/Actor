using UnityEngine;
using System.Linq;

namespace Actor.Networking
{
    public sealed class MultiplayerProbe : MonoBehaviour
    {
#if (UNITY_EDITOR || DEVELOPMENT_BUILD) && !UNITY_SERVER
        [System.Serializable] private sealed class ProbeConfig
        {
            public string roomCode, playerName, role, outputPath;
        }
        [System.Serializable] private sealed class Check { public string name; public bool passed; public string detail; }
        [System.Serializable] private sealed class Peer
        {
            public string actorId, playerName, facing;
            public ulong clientId;
            public bool owner;
            public Vector2 position, velocity;
        }
        [System.Serializable] private sealed class Report
        {
            public string role, playerName;
            public bool connected, completed;
            public float maxSpeed;
            public Peer[] peers;
            public Check[] checks;
            public string[] errors, warnings;
        }
        private sealed class Commands : Actor.Characters.IMovementInputSource
        {
            public Vector2 Value;
            public Vector2 ReadMovement() => Value;
        }
        private ProbeConfig config;
        private readonly System.Collections.Generic.List<Check> checks = new();
        private readonly System.Collections.Generic.List<string> errors = new(), warnings = new();
        private NetworkCharacter own;
        private float nextSample, maxSpeed;
        private bool completed;
        private Peer[] peers = System.Array.Empty<Peer>();

        private System.Collections.IEnumerator Start()
        {
            string[] args = System.Environment.GetCommandLineArgs();
            string value(string key)
            {
                int index = System.Array.IndexOf(args, key);
                return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
            }
            if (value("--auto-room") != null)
                config = new ProbeConfig { roomCode = value("--auto-room"), playerName = value("--player-name") ?? "Probe Player",
                    role = value("--probe-role") ?? "B", outputPath = value("--probe-output") };
#if UNITY_EDITOR
            const string file = "Verification/multiplayer-probe.json";
            if (System.IO.File.Exists(file))
            {
                config = JsonUtility.FromJson<ProbeConfig>(System.IO.File.ReadAllText(file));
                System.IO.File.Delete(file);
            }
#endif
            if (config == null || string.IsNullOrEmpty(config.outputPath)) yield break;
            Application.logMessageReceived += OnLog;
            var view = GetComponent<Actor.Lobby.LobbyMenuView>();
            var controller = GetComponent<Actor.Lobby.LobbyMenuController>();
            view.PlayerName = config.playerName;
            view.DirectRoomCode = config.roomCode;
            yield return new WaitForSecondsRealtime(0.5f);
            controller.JoinRoom(config.roomCode);
            float deadline = Time.realtimeSinceStartup + 65;
            while (Time.realtimeSinceStartup < deadline)
            {
                foreach (var character in FindObjectsByType<NetworkCharacter>())
                    if (character.IsSpawned && character.IsOwner) own = character;
                if (own != null && FindObjectsByType<NetworkCharacter>().Length >= 2) break;
                yield return new WaitForSecondsRealtime(0.2f);
            }
            if (own == null || FindObjectsByType<NetworkCharacter>().Length < 2)
            { Add("Two clients spawned", false, "Connection timed out."); completed = true; Save(); yield break; }
            yield return new WaitForSecondsRealtime(1);
            Sample();
            Add("Two clients spawned", peers.Length >= 2);
            Add("Unique player identities and replicated names", System.Linq.Enumerable.Distinct(System.Linq.Enumerable.Select(peers, p => p.actorId)).Count() == peers.Length
                && System.Linq.Enumerable.All(peers, p => !string.IsNullOrEmpty(p.playerName)));
            Add("Client physics is non-authoritative", !own.GetComponent<Actor.Characters.CharacterMotor2D>().enabled && own.GetComponent<Rigidbody2D>().bodyType == RigidbodyType2D.Kinematic);
            var commands = new Commands();
            own.SetLocalInputSource(commands);
            Vector2 initial = own.transform.position;
            commands.Value = config.role == "A" ? Vector2.right : new Vector2(1, -1);
            yield return new WaitForSecondsRealtime(config.role == "A" ? 5.5f : 0.7f);
            Add("Owned input moves an independent character", Vector2.Distance(initial, own.transform.position) > 0.3f);
            if (config.role == "A")
                Add("Authoritative wall blocks continued intent", own.Velocity.magnitude < 0.05f && own.transform.position.x < 6.3f,
                    own.transform.position.ToString());
            commands.Value = Vector2.zero;
            yield return new WaitForSecondsRealtime(0.6f);
            Add("Release stops the character", own.Velocity.magnitude < 0.05f);
            Add("Network speed stays normalized", maxSpeed > 2.4f && maxSpeed <= own.MaxSpeed + 0.05f, maxSpeed.ToString("F3"));
            if (config.role == "A")
            {
                own.transform.position = new Vector3(50, 50, 0);
                yield return new WaitForSecondsRealtime(1.5f);
                Add("Client transform manipulation is corrected", Vector2.Distance(own.transform.position, new Vector2(50, 50)) > 20
                    && Mathf.Abs(own.transform.position.x) < 6.3f && Mathf.Abs(own.transform.position.y) < 6.3f,
                    own.transform.position.ToString());
            }
            else
            {
                yield return new WaitForSecondsRealtime(7);
                Add("Remote authoritative movement is visible", System.Linq.Enumerable.Any(peers, p => !p.owner && p.position.x > 1.5f));
            }
            own.SetLocalInputSource(own.GetComponent<Actor.Characters.PlayerInputSource>());
            Add("No runtime errors", errors.Count == 0);
            completed = true;
            Sample(); Save();
            ScreenCapture.CaptureScreenshot(System.IO.Path.ChangeExtension(config.outputPath, ".png"));
            Debug.Log("ACTOR_PROBE_COMPLETE role=" + config.role + " checks=" + checks.Count);
        }

        private void Update()
        {
            if (config == null || string.IsNullOrEmpty(config.outputPath) || Time.realtimeSinceStartup < nextSample) return;
            nextSample = Time.realtimeSinceStartup + 0.25f;
            Sample(); Save();
        }
        private void Sample()
        {
            var list = new System.Collections.Generic.List<Peer>();
            foreach (var character in FindObjectsByType<NetworkCharacter>())
            {
                if (!character.IsSpawned) continue;
                var state = character.GetComponent<Actor.Characters.ActorCharacter>().CaptureState();
                list.Add(new Peer { actorId = state.ActorId, playerName = character.PlayerName, owner = character.IsOwner,
                    clientId = character.OwnerClientId, position = character.transform.position, velocity = character.Velocity, facing = character.Facing.ToString() });
                if (character.IsOwner) maxSpeed = Mathf.Max(maxSpeed, character.Velocity.magnitude);
            }
            peers = list.ToArray();
        }
        private void Save()
        {
            System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(config.outputPath));
            var report = new Report { role = config.role, playerName = config.playerName, connected = GetComponent<MatchSession>().IsConnected,
                completed = completed, peers = peers, checks = checks.ToArray(), maxSpeed = maxSpeed, errors = errors.ToArray(), warnings = warnings.ToArray() };
            System.IO.File.WriteAllText(config.outputPath, JsonUtility.ToJson(report, true));
        }
        private void Add(string name, bool passed, string detail = "") => checks.Add(new Check { name = name, passed = passed, detail = detail });
        private void OnLog(string message, string stack, LogType type)
        {
            if (type == LogType.Error || type == LogType.Exception || type == LogType.Assert) errors.Add(message);
            if (type == LogType.Warning) warnings.Add(message);
        }
        private void OnDestroy() => Application.logMessageReceived -= OnLog;
#endif
    }
}
