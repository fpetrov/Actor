using UnityEngine;
using System.Linq;

namespace Actor.Networking
{
    public sealed class MultiplayerProbe : MonoBehaviour
    {
#if !UNITY_SERVER
        [System.Serializable] private sealed class ProbeConfig
        {
            public string roomCode, playerName, role, outputPath;
            public int relayPort;
        }
        [System.Serializable] private sealed class Check { public string name; public bool passed; public string detail; }
        [System.Serializable] private sealed class Peer
        {
            public string actorId, playerName, facing;
            public ulong clientId;
            public bool owner;
            public Vector2 position, velocity;
            public Vector3 presentationPosition;
        }
        [System.Serializable] private sealed class Report
        {
            public string role, playerName;
            public bool connected, completed;
            public float maxSpeed;
            public float visualResponseMs, authoritativeResponseMs, releaseResponseMs, idleError;
            public Peer[] peers;
            public Check[] checks;
            public string[] errors, warnings;
            public MotionSample[] trajectory;
        }
        [System.Serializable] private sealed class MotionSample
        { public float time; public string stage; public Vector3 root, visual; public Vector2 velocity; }
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
        private float visualResponseMs, authoritativeResponseMs, releaseResponseMs, idleError;
        private string stage;
        private readonly System.Collections.Generic.List<MotionSample> trajectory = new();
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
            // Release validation exercises the same ordinary owned input path.
            // Keep legacy interactive A/B probes development-only; the
            // production build runs only the explicitly requested headless P case.
            if (!Debug.isDebugBuild && (!Application.isBatchMode || value("--probe-role") != "P")) yield break;
            // The observer keeps ordinary human input. Its independent probe owns diagnostics.
            if (value("--probe-role") == "O") yield break;
            if (value("--auto-room") != null)
                config = new ProbeConfig { roomCode = value("--auto-room"), playerName = value("--player-name") ?? "Probe Player",
                    role = value("--probe-role") ?? "B", outputPath = value("--probe-output"),
                    relayPort = int.TryParse(value("--probe-relay-port"), out int relayPort) ? relayPort : 0 };
#if UNITY_EDITOR
            const string file = "Verification/multiplayer-probe.json";
            if (System.IO.File.Exists(file))
            {
                config = JsonUtility.FromJson<ProbeConfig>(System.IO.File.ReadAllText(file));
                System.IO.File.Delete(file);
            }
#endif
            if (config != null && config.role == "O") { config = null; yield break; }
            if (config == null || string.IsNullOrEmpty(config.outputPath)) yield break;
            Application.logMessageReceived += OnLog;
            var view = GetComponent<Actor.Lobby.LobbyMenuView>();
            var controller = GetComponent<Actor.Lobby.LobbyMenuController>();
            view.PlayerName = config.playerName;
            view.DirectRoomCode = config.roomCode;
            yield return new WaitForSecondsRealtime(0.5f);
            if (config.relayPort > 0)
            {
                // Explicit diagnostic-only UDP relay; public admission still comes from the real API.
                var settings = Resources.FindObjectsOfTypeAll<Actor.Lobby.LobbyClientSettings>().First();
                var api = new Actor.Lobby.LobbyApiClient(settings);
                yield return api.Join(config.roomCode, config.playerName, admission =>
                {
                    admission.host = "127.0.0.1";
                    admission.port = config.relayPort;
                    GetComponent<MatchSession>().Connect(admission);
                }, error => errors.Add(error));
            }
            else controller.JoinRoom(config.roomCode);
            float deadline = Time.realtimeSinceStartup + 65;
            while (Time.realtimeSinceStartup < deadline)
            {
                foreach (var character in FindObjectsByType<NetworkCharacter>())
                    if (character.IsSpawned && character.IsOwner) own = character;
                if (own != null && (config.role == "P" || FindObjectsByType<NetworkCharacter>().Length >= 2)) break;
                yield return new WaitForSecondsRealtime(0.2f);
            }
            if (own == null || (config.role != "P" && FindObjectsByType<NetworkCharacter>().Length < 2))
            { Add("Two clients spawned", false, "Connection timed out."); completed = true; Save(); yield break; }
            yield return new WaitForSecondsRealtime(1);
            if (config.role == "P")
            {
                yield return VerifyPrediction();
                completed = true;
                Sample(); Save();
                if (!Application.isBatchMode) ScreenCapture.CaptureScreenshot(System.IO.Path.ChangeExtension(config.outputPath, ".png"));
                Debug.Log("ACTOR_PROBE_COMPLETE role=P checks=" + checks.Count);
                yield break;
            }
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

        private System.Collections.IEnumerator VerifyPrediction()
        {
            Add("Client physics remains non-authoritative", !own.GetComponent<Actor.Characters.CharacterMotor2D>().enabled
                && own.GetComponent<Rigidbody2D>().bodyType == RigidbodyType2D.Kinematic);
            var commands = new Commands();
            own.SetLocalInputSource(commands);
            Vector3 visualStart = own.PresentationPosition;
            Vector3 rootStart = own.transform.position;
            float started = Time.realtimeSinceStartup;
            stage = "press";
            commands.Value = Vector2.right;
            bool visualMoved = false, rootMoved = false;
            while (Time.realtimeSinceStartup - started < 1.2f)
            {
                yield return null;
                if (!visualMoved && Vector3.Distance(visualStart, own.PresentationPosition) > 0.003f)
                { visualMoved = true; visualResponseMs = (Time.realtimeSinceStartup - started) * 1000; }
                if (!rootMoved && Vector3.Distance(rootStart, own.transform.position) > 0.003f)
                { rootMoved = true; authoritativeResponseMs = (Time.realtimeSinceStartup - started) * 1000; }
            }
            Add("Local presentation responds within two frames", visualMoved && visualResponseMs < 70, visualResponseMs.ToString("F1") + " ms");
            Add("Authoritative root still receives server movement", rootMoved, authoritativeResponseMs.ToString("F1") + " ms");
            if (config.relayPort > 0)
                Add("Prediction hides the injected network delay", authoritativeResponseMs > visualResponseMs + 100);
            commands.Value = Vector2.zero;
            stage = "release";
            started = Time.realtimeSinceStartup;
            while (own.Velocity.magnitude > 0.05f && Time.realtimeSinceStartup - started < 1)
                yield return null;
            releaseResponseMs = (Time.realtimeSinceStartup - started) * 1000;
            Add("Release decelerates locally without waiting for server", releaseResponseMs < 160, releaseResponseMs.ToString("F1") + " ms");
            yield return new WaitForSecondsRealtime(0.8f);
            idleError = Vector3.Distance(own.PresentationPosition, own.transform.position);
            Add("Idle reconciles to the server", idleError < 0.1f, idleError.ToString("F4"));
            for (int i = 0; i < 8; i++)
            {
                stage = "reversal-" + i;
                commands.Value = i % 2 == 0 ? Vector2.left : Vector2.right;
                yield return new WaitForSecondsRealtime(0.18f);
            }
            commands.Value = Vector2.zero;
            yield return new WaitForSecondsRealtime(0.8f);
            Add("Rapid reversals converge without accumulated drift", Vector3.Distance(own.PresentationPosition, own.transform.position) < 0.1f);
            commands.Value = Vector2.right;
            stage = "wall";
            yield return new WaitForSecondsRealtime(5.5f);
            Vector3 wallPosition = own.PresentationPosition;
            yield return new WaitForSecondsRealtime(1);
            Add("Predicted and authoritative wall collision agree", own.Velocity.magnitude < 0.05f
                && own.PresentationPosition.x < 6.3f && own.transform.position.x < 6.3f);
            Add("Holding into a wall does not drift or jitter", Vector3.Distance(wallPosition, own.PresentationPosition) < 0.04f);
            commands.Value = new Vector2(1, -1);
            stage = "slide";
            Vector3 slideStart = own.PresentationPosition;
            yield return new WaitForSecondsRealtime(0.6f);
            Add("Diagonal wall contact slides instead of sticking", own.PresentationPosition.y < slideStart.y - 0.3f
                && own.PresentationPosition.x < 6.3f, "start=" + slideStart + " end=" + own.PresentationPosition);
            commands.Value = Vector2.left;
            stage = "escape";
            yield return new WaitForSecondsRealtime(0.4f);
            Add("Can move away from a wall immediately", own.Velocity.x < -1);
            commands.Value = Vector2.zero;
            yield return new WaitForSecondsRealtime(0.8f);
            Add("Speed remains normalized", maxSpeed > 2.4f && maxSpeed < own.MaxSpeed + 0.05f, maxSpeed.ToString("F3"));
            own.transform.position = new Vector3(50, 50, 0);
            yield return new WaitForSecondsRealtime(1.5f);
            Add("Client root manipulation cannot become authoritative", Mathf.Abs(own.transform.position.x) < 6.3f
                && Mathf.Abs(own.PresentationPosition.x) < 6.3f);
            Add("No runtime errors or warnings", errors.Count == 0 && warnings.Count == 0);
            own.SetLocalInputSource(own.GetComponent<Actor.Characters.PlayerInputSource>());
        }

        private void Update()
        {
            if (config == null || string.IsNullOrEmpty(config.outputPath) || Time.realtimeSinceStartup < nextSample) return;
            nextSample = Time.realtimeSinceStartup + 0.25f;
            if (!completed && config.role == "P" && own != null)
                trajectory.Add(new MotionSample { time = Time.realtimeSinceStartup, stage = stage,
                    root = own.transform.position, visual = own.PresentationPosition, velocity = own.Velocity });
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
                    clientId = character.OwnerClientId, position = character.transform.position,
                    presentationPosition = character.PresentationPosition, velocity = character.Velocity, facing = character.Facing.ToString() });
                if (character.IsOwner) maxSpeed = Mathf.Max(maxSpeed, character.Velocity.magnitude);
            }
            peers = list.ToArray();
        }
        private void Save()
        {
            System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(config.outputPath));
            var report = new Report { role = config.role, playerName = config.playerName, connected = GetComponent<MatchSession>().IsConnected,
                completed = completed, peers = peers, checks = checks.ToArray(), maxSpeed = maxSpeed,
                visualResponseMs = visualResponseMs, authoritativeResponseMs = authoritativeResponseMs,
                releaseResponseMs = releaseResponseMs, idleError = idleError, errors = errors.ToArray(), warnings = warnings.ToArray(),
                trajectory = trajectory.ToArray() };
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
