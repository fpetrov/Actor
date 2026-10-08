using UnityEngine;

namespace Actor.Networking
{
    // Development diagnostics only. The observed client retains its ordinary keyboard
    // input path; this component never submits movement or replaces an input source.
    public sealed class ActorObserverProbe : MonoBehaviour
    {
#if (UNITY_EDITOR || DEVELOPMENT_BUILD) && !UNITY_SERVER
        [System.Serializable] private sealed class Check
        { public string name, detail; public bool passed; }
        [System.Serializable] private sealed class Peer
        {
            public string actorId, playerName, roomId;
            public ulong clientId;
            public uint roomRevision;
            public bool owner, keyboardEnabled, motorEnabled;
            public Vector3 root, visual;
            public Vector2 velocity;
        }
        [System.Serializable] private sealed class MotionSample
        {
            public float elapsed;
            public string stage, roomId;
            public uint roomRevision;
            public Vector3 observerRoot, remoteRoot;
            public Vector2 remoteVelocity;
        }
        [System.Serializable] private sealed class RoomEvent
        {
            public float elapsed;
            public string previousRoom, currentRoom;
            public uint revision;
            public Vector3 remoteRoot;
        }
        [System.Serializable] private sealed class Report
        {
            public string role = "O", playerName, observedPlayer, roomCode, stage, recordedAtUtc;
            public bool connected, completed, passed;
            public float elapsed, remoteDisplacement, observerDisplacement, peakRemoteSpeed;
            public Peer[] peers;
            public MotionSample[] trajectory;
            public RoomEvent[] roomEvents;
            public Check[] checks;
            public string[] errors, warnings, screenshots;
        }

        private readonly System.Collections.Generic.List<Check> checks = new();
        private readonly System.Collections.Generic.List<MotionSample> trajectory = new();
        private readonly System.Collections.Generic.List<RoomEvent> roomEvents = new();
        private readonly System.Collections.Generic.List<string> errors = new(), warnings = new(), screenshots = new();
        private string output, playerName, observedPlayer, roomCode, stage = "connecting";
        private NetworkCharacter own, remote;
        private NetworkRoomMembership remoteMembership;
        private Vector3 ownStart, remoteStart;
        private float started, nextSample, nextSave, remoteDisplacement, observerDisplacement, peakRemoteSpeed;
        private uint initialRevision;
        private bool active, observing, completed, enteredEngineering, returnedMainHall;
        private bool finiteSpeed = true, humanInputEnabled = true, captureScreenshots;
        private Peer[] peers = System.Array.Empty<Peer>();

        private System.Collections.IEnumerator Start()
        {
            string[] args = System.Environment.GetCommandLineArgs();
            string value(string key)
            {
                int index = System.Array.IndexOf(args, key);
                return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
            }
            if (value("--probe-role") != "O") yield break;
            roomCode = value("--auto-room");
            output = value("--probe-output");
            if (string.IsNullOrWhiteSpace(roomCode) || string.IsNullOrWhiteSpace(output)) yield break;
            output = System.IO.Path.GetFullPath(output);
            System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(output));
            playerName = value("--player-name") ?? "Human Observer";
            observedPlayer = value("--observed-player") ?? "Agent Pilot";
            float timeout = float.TryParse(value("--probe-timeout"), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out float parsed) && float.IsFinite(parsed)
                ? Mathf.Clamp(parsed, 5, 600) : 120;
            captureScreenshots = System.Array.IndexOf(args, "--probe-no-screenshots") < 0;
            started = Time.realtimeSinceStartup;
            active = true;
            Application.logMessageReceived += OnLog;
            Save();
            var view = GetComponent<Actor.Lobby.LobbyMenuView>();
            var controller = GetComponent<Actor.Lobby.LobbyMenuController>();
            view.PlayerName = playerName;
            view.DirectRoomCode = roomCode;
            yield return new WaitForSecondsRealtime(0.5f);
            controller.JoinRoom(roomCode);
            float deadline = started + timeout;
            while (Time.realtimeSinceStartup < deadline)
            {
                foreach (var character in FindObjectsByType<NetworkCharacter>())
                {
                    if (!character.IsSpawned) continue;
                    if (character.IsOwner) own = character;
                    else if (character.PlayerName == observedPlayer) remote = character;
                }
                if (own != null && remote != null) break;
                yield return new WaitForSecondsRealtime(0.1f);
            }
            if (own == null || remote == null)
            {
                Add("Human observer and named remote actor spawn", false, "Timed out: " + view.Status);
                Finish(); yield break;
            }
            remoteMembership = remote.GetComponent<NetworkRoomMembership>();
            if (remoteMembership == null)
            {
                Add("Remote actor exposes authoritative room membership", false);
                Finish(); yield break;
            }
            ownStart = own.transform.position;
            remoteStart = remote.transform.position;
            initialRevision = remoteMembership.Revision;
            remoteMembership.RoomChanged += OnRoomChanged;
            Add("Remote actor initially belongs to Main Hall", remoteMembership.CurrentRoomId == Actor.World.RoomId.MainHall);
            observing = true;
            stage = "main-hall";
            Sample(); Save();
            RequestScreenshot("initial");
            Debug.Log("ACTOR_OBSERVER_READY room=" + roomCode + " observedPlayer=" + observedPlayer);
            while (!returnedMainHall && Time.realtimeSinceStartup < deadline)
            {
                if (remote == null || !remote.IsSpawned || own == null || !own.IsSpawned)
                {
                    Add("Both actors remain connected through the round trip", false);
                    Finish(); yield break;
                }
                yield return null;
            }
            if (!returnedMainHall)
            {
                Add("Remote Engineering round trip completes before timeout", false);
                Finish(); yield break;
            }
            // Keep enough final frames for interpolated roots, stop intent and screenshots.
            yield return new WaitForSecondsRealtime(1);
            Sample();
            Add("Two multiplayer actors retain unique stable identities", UniqueIdentities());
            Add("Observed actor is remote and independent of the Human observer", !remote.IsOwner
                && remote.OwnerClientId != own.OwnerClientId
                && remote.GetComponent<Actor.Characters.ActorCharacter>().ActorId != own.GetComponent<Actor.Characters.ActorCharacter>().ActorId);
            Add("Replicated authoritative root movement is visible", remoteDisplacement > 0.5f,
                remoteDisplacement.ToString("F3") + " units");
            Add("Replicated movement speed remains finite and bounded", finiteSpeed && peakRemoteSpeed <= remote.MaxSpeed + 0.1f,
                peakRemoteSpeed.ToString("F3") + " units/s");
            Add("Server room state reaches Engineering and returns to Main Hall", enteredEngineering && returnedMainHall
                && remoteMembership.CurrentRoomId == Actor.World.RoomId.MainHall);
            bool ordered = roomEvents.Count == 2
                && roomEvents[0].previousRoom == Actor.World.RoomId.MainHall && roomEvents[0].currentRoom == Actor.World.RoomId.Engineering
                && roomEvents[1].previousRoom == Actor.World.RoomId.Engineering && roomEvents[1].currentRoom == Actor.World.RoomId.MainHall
                && roomEvents[0].revision > initialRevision && roomEvents[1].revision > roomEvents[0].revision;
            Add("Server RoomChanged events preserve order and advancing revisions", ordered, roomEvents.Count + " events");
            Add("Human keyboard input remains enabled", humanInputEnabled);
            Add("Observer physics remains server authoritative", !own.GetComponent<Actor.Characters.CharacterMotor2D>().enabled
                && own.GetComponent<Rigidbody2D>().bodyType == RigidbodyType2D.Kinematic);
            Add("No runtime errors or warnings", errors.Count == 0 && warnings.Count == 0);
            RequestScreenshot("complete");
            yield return new WaitForSecondsRealtime(0.4f);
            Finish();
        }

        private void Update()
        {
            if (!active || completed) return;
            if (observing && own != null && remote != null && own.IsSpawned && remote.IsSpawned)
            {
                string room = remoteMembership.CurrentRoomId;
                if (!enteredEngineering && room == Actor.World.RoomId.Engineering)
                { enteredEngineering = true; stage = "engineering"; RequestScreenshot("engineering"); }
                if (enteredEngineering && !returnedMainHall && room == Actor.World.RoomId.MainHall)
                { returnedMainHall = true; stage = "returned-main-hall"; RequestScreenshot("return"); }
                var velocity = remote.Velocity;
                finiteSpeed &= float.IsFinite(velocity.x) && float.IsFinite(velocity.y);
                if (finiteSpeed) peakRemoteSpeed = Mathf.Max(peakRemoteSpeed, velocity.magnitude);
                remoteDisplacement = Mathf.Max(remoteDisplacement, Vector3.Distance(remoteStart, remote.transform.position));
                observerDisplacement = Mathf.Max(observerDisplacement, Vector3.Distance(ownStart, own.transform.position));
                humanInputEnabled &= own.GetComponent<Actor.Characters.PlayerInputSource>().enabled;
                if (Time.realtimeSinceStartup >= nextSample)
                {
                    nextSample = Time.realtimeSinceStartup + 0.1f;
                    trajectory.Add(new MotionSample { elapsed = Time.realtimeSinceStartup - started, stage = stage,
                        roomId = room, roomRevision = remoteMembership.Revision, observerRoot = own.transform.position,
                        remoteRoot = remote.transform.position, remoteVelocity = velocity });
                    Sample();
                }
            }
            if (Time.realtimeSinceStartup >= nextSave)
            { nextSave = Time.realtimeSinceStartup + 0.5f; Save(); }
        }

        private void OnRoomChanged(string previous, string current, uint revision)
        {
            if (completed) return;
            roomEvents.Add(new RoomEvent { elapsed = Time.realtimeSinceStartup - started, previousRoom = previous,
                currentRoom = current, revision = revision, remoteRoot = remote.transform.position });
            Debug.Log("ACTOR_OBSERVER_ROOM_CHANGED " + previous + " -> " + current + " revision=" + revision);
        }

        private void Sample()
        {
            var list = new System.Collections.Generic.List<Peer>();
            foreach (var character in FindObjectsByType<NetworkCharacter>())
            {
                if (!character.IsSpawned) continue;
                var actor = character.GetComponent<Actor.Characters.ActorCharacter>();
                var membership = character.GetComponent<NetworkRoomMembership>();
                list.Add(new Peer { actorId = actor.ActorId, playerName = character.PlayerName, clientId = character.OwnerClientId,
                    owner = character.IsOwner, root = character.transform.position, visual = character.PresentationPosition,
                    velocity = character.Velocity, roomId = membership != null ? membership.CurrentRoomId : actor.RoomId,
                    roomRevision = membership != null ? membership.Revision : 0,
                    keyboardEnabled = character.GetComponent<Actor.Characters.PlayerInputSource>().enabled,
                    motorEnabled = character.GetComponent<Actor.Characters.CharacterMotor2D>().enabled });
            }
            peers = list.ToArray();
        }

        private bool UniqueIdentities()
        {
            if (peers.Length < 2) return false;
            var ids = new System.Collections.Generic.HashSet<string>();
            foreach (var peer in peers)
                if (string.IsNullOrEmpty(peer.actorId) || string.IsNullOrEmpty(peer.playerName) || !ids.Add(peer.actorId)) return false;
            return true;
        }

        private void RequestScreenshot(string name)
        {
            if (!captureScreenshots) return;
            string path = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(output),
                System.IO.Path.GetFileNameWithoutExtension(output) + "-" + name + ".png");
            screenshots.Add(path);
            StartCoroutine(CaptureAfterRendering(path));
        }

        private System.Collections.IEnumerator CaptureAfterRendering(string path)
        {
            yield return new WaitForSecondsRealtime(0.25f);
            yield return new WaitForEndOfFrame();
            ScreenCapture.CaptureScreenshot(path);
        }

        private void Add(string name, bool passed, string detail = "")
            => checks.Add(new Check { name = name, passed = passed, detail = detail });

        private void Finish()
        {
            if (completed) return;
            completed = true;
            if (remoteMembership != null) remoteMembership.RoomChanged -= OnRoomChanged;
            Application.logMessageReceived -= OnLog;
            Save();
            Debug.Log("ACTOR_OBSERVER_COMPLETE passed=" + AllChecksPassed() + " checks=" + checks.Count);
        }

        private bool AllChecksPassed()
        {
            if (checks.Count == 0) return false;
            foreach (var check in checks) if (!check.passed) return false;
            return errors.Count == 0 && warnings.Count == 0;
        }

        private void Save()
        {
            if (output == null) return;
            var report = new Report { playerName = playerName, observedPlayer = observedPlayer, roomCode = roomCode, stage = stage,
                recordedAtUtc = System.DateTime.UtcNow.ToString("O"), connected = GetComponent<MatchSession>().IsConnected,
                completed = completed, passed = completed && AllChecksPassed(), elapsed = Time.realtimeSinceStartup - started,
                remoteDisplacement = remoteDisplacement, observerDisplacement = observerDisplacement, peakRemoteSpeed = peakRemoteSpeed,
                peers = peers, trajectory = trajectory.ToArray(), roomEvents = roomEvents.ToArray(), checks = checks.ToArray(),
                errors = errors.ToArray(), warnings = warnings.ToArray(), screenshots = screenshots.ToArray() };
            System.IO.File.WriteAllText(output, JsonUtility.ToJson(report, true));
        }

        private void OnLog(string message, string stack, LogType type)
        {
            if (!active || completed) return;
            if (type == LogType.Error || type == LogType.Exception || type == LogType.Assert) errors.Add(message);
            if (type == LogType.Warning) warnings.Add(message);
        }

        private void OnDestroy()
        {
            Application.logMessageReceived -= OnLog;
            if (remoteMembership != null) remoteMembership.RoomChanged -= OnRoomChanged;
        }
#endif
    }
}
