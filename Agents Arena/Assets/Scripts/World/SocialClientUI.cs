using System;
using System.Linq;
using System.Text;
using Actor.Characters;
using Actor.Networking;
using Actor.Protocol.Standard.Communication.V1;
using AgentsArena.ActorProtocol.V1;
using UnityEngine;

namespace Actor.World
{
    // All data comes from the local owner's permitted endpoint. In particular,
    // the meeting roster never reads hidden avatars or the server authority.
    public sealed class SocialClientUI : MonoBehaviour
    {
#if !UNITY_SERVER
        private NetworkSocialMatch owner;
        private NetworkCooperation communication;
        private PlayerInputSource keyboard;
        private bool details, chat;
        private string text = "", recipient = "", channel = "";
        private Vector2 scroll, chatScroll;
        private GUIStyle body, heading;
        private long Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        private void Awake()
        { owner = GetComponent<NetworkSocialMatch>(); communication = GetComponent<NetworkCooperation>(); keyboard = GetComponent<PlayerInputSource>(); }
        private void OnGUI()
        {
            if (!owner.IsSpawned || !owner.IsOwner || !owner.IsClient || !SocialVisibilitySystem.IsSocial(owner.NetworkManager) || !owner.HasOwnState) return;
            if (body == null)
            { body = new GUIStyle(GUI.skin.label) { fontSize = 13, wordWrap = true }; heading = new GUIStyle(body) { fontSize = 16, fontStyle = FontStyle.Bold }; }
            var state = owner.State; var own = owner.Own;
            float width = 330;
            GUILayout.BeginArea(new Rect(18, 108, width, 88), GUI.skin.box);
            GUILayout.Label("Social Deduction · " + state.Phase, heading);
            GUILayout.Label(owner.Human ? own.Life + " · " + (own.Assigned ? own.Alignment + " / " + own.Duty : "Awaiting assignment") : "Runtime controlled · " + own.Life, body);
            long end = state.PhaseEndsAtUnixMs > 0 ? state.PhaseEndsAtUnixMs : state.EndsAtUnixMs;
            GUILayout.Label("Tasks " + state.CrewTasksCompleted + "/" + state.TaskThreshold + (end > 0 ? " · " + Seconds(end) + "s" : " · 4–6 ready players required"), body);
            GUILayout.EndArea();
            if (state.Phase == SocialPhase.GameOver) { DrawPostgame(); return; }
            if (!owner.Human) { keyboard.TextEntryFocused = false; return; }
            GUILayout.BeginArea(new Rect(18, 206, 330, Mathf.Min(220, Screen.height - 226)), GUI.skin.box);
            if (state.Phase == SocialPhase.WaitingForPlayers)
            { GUILayout.Label("Each participant must confirm readiness.", body); if (GUILayout.Button(own.Ready ? "Not ready" : "Ready")) owner.Submit(new SocialReadyCommand { Context = owner.Context(), Ready = !own.Ready }); }
            if (state.Phase == SocialPhase.Exploration && own.Life == SocialLife.Alive)
            {
                if (own.Alignment == SocialAlignment.Impostor) GUILayout.Label("Kill cooldown: " + Seconds(own.KillReadyAtUnixMs) + "s", body);
                if (owner.Emergency.Active) GUILayout.Label("Life support emergency · " + Seconds(owner.Emergency.EndsAtUnixMs) + "s", heading);
                if (GUILayout.Button(details ? "Close tasks / nearby actions" : "Tasks / nearby actions")) { details = !details; chat = false; }
                if (owner.ActiveWorkId.Length > 0 && GUILayout.Button("Cancel physical work")) owner.Submit(new SocialCancelWorkCommand { Context = owner.Context(), WorkId = owner.ActiveWorkId });
            }
            if (own.Life == SocialLife.Alive && communication.Peers.AllowedChannels.Count > 0)
                if (GUILayout.Button(chat ? "Close messages" : "Messages (" + communication.Inbox.Count + ")")) { chat = !chat; details = false; }
            GUILayout.Label(owner.LastAction, body);
            if (own.Life != SocialLife.Alive) GUILayout.Label("You are out of play. Waiting for the team result.", body);
            GUILayout.EndArea();
            if (chat && own.Life == SocialLife.Alive) DrawChat();
            else if (state.Phase is SocialPhase.Discussion or SocialPhase.Voting or SocialPhase.VoteResolution) DrawMeeting();
            else if (details && own.Life == SocialLife.Alive) DrawActions();
            keyboard.TextEntryFocused = chat && own.Life == SocialLife.Alive
                && GUI.GetNameOfFocusedControl() == "social-chat";
        }
        private string Seconds(long end) => Math.Max(0, (end - Now + 999) / 1000).ToString();
        private void DrawActions()
        {
            GUILayout.BeginArea(new Rect(360, 130, Mathf.Min(422, Screen.width - 378), Screen.height - 148), GUI.skin.box);
            scroll = GUILayout.BeginScrollView(scroll);
            GUILayout.Label("Physical tasks", heading);
            foreach (var task in owner.Tasks.Tasks)
            {
                GUILayout.Label(task.TaskId + " · " + task.RoomId + (task.Completed ? " · complete" : ""), body);
                if (!task.Completed && owner.CaptureWorld().Interactables.Any(t => t.EntityId == task.TerminalEntityId)
                    && GUILayout.Button("Work at " + task.TaskId)) owner.Submit(new SocialWorkTaskCommand { Context = owner.Context(), TaskId = task.TaskId });
            }
            foreach (var terminal in owner.CaptureWorld().Interactables)
            {
                if (terminal.Kind == "meeting" && GUILayout.Button("Call meeting at console")) owner.Submit(new SocialCallMeetingCommand { Context = owner.Context() });
                if (terminal.Kind == "life-support")
                {
                    if (owner.Emergency.Active && owner.Own.Alignment == SocialAlignment.Crew && GUILayout.Button("Repair life support"))
                        owner.Submit(new SocialRepairEmergencyCommand { Context = owner.Context(), EmergencyId = owner.Emergency.EmergencyId });
                    if (!owner.Emergency.Active && owner.Own.Alignment == SocialAlignment.Impostor && GUILayout.Button("Sabotage life support"))
                        owner.Submit(new SocialSabotageCommand { Context = owner.Context(), TerminalEntityId = terminal.EntityId });
                }
            }
            foreach (var body in owner.CaptureWorld().Bodies)
                if (GUILayout.Button("Report body · " + body.VictimDisplayName)) owner.Submit(new SocialReportBodyCommand { Context = owner.Context(), BodyId = body.BodyId });
            if (owner.Own.Alignment == SocialAlignment.Impostor)
                foreach (var actor in owner.CaptureWorld().Actors)
                    if (GUILayout.Button("Kill nearby · " + actor.DisplayName)) owner.Submit(new SocialKillCommand { Context = owner.Context(), TargetActorId = actor.ActorId });
            GUILayout.Label("Move close enough to interact. The server validates range and sight.", body);
            GUILayout.EndScrollView(); GUILayout.EndArea();
        }
        private void DrawMeeting()
        {
            GUILayout.BeginArea(new Rect(360, 130, Mathf.Min(422, Screen.width - 378), Screen.height - 148), GUI.skin.box);
            scroll = GUILayout.BeginScrollView(scroll);
            GUILayout.Label(owner.State.Phase == SocialPhase.Discussion ? "Discuss observations in messages" : "One final ballot · no role reveal", heading);
            foreach (var person in owner.Meeting.Participants)
            {
                GUILayout.Label(person.DisplayName + " · " + person.Life, body);
                if (owner.State.Phase == SocialPhase.Voting && person.Life == SocialLife.Alive && owner.Own.Life == SocialLife.Alive && !owner.Meeting.OwnBallotSubmitted
                    && GUILayout.Button("Vote " + person.DisplayName)) Vote(person.ActorId, false);
            }
            if (owner.State.Phase == SocialPhase.Voting && owner.Own.Life == SocialLife.Alive && !owner.Meeting.OwnBallotSubmitted && GUILayout.Button("Skip")) Vote("", true);
            if (owner.Meeting.OwnBallotSubmitted) GUILayout.Label("Your ballot is final.", body);
            if (owner.Meeting.Resolution.Length > 0) GUILayout.Label("Result: " + owner.Meeting.Resolution, heading);
            foreach (var tally in owner.Meeting.Result) GUILayout.Label((tally.CandidateActorId.Length == 0 ? "Skip" : communication.DisplayName(tally.CandidateActorId)) + ": " + tally.Votes, body);
            GUILayout.EndScrollView(); GUILayout.EndArea();
        }
        private void Vote(string candidate, bool skip) => owner.Submit(new SocialCastVoteCommand { Context = owner.Context(), MeetingId = owner.Meeting.MeetingId, CandidateActorId = candidate, Skip = skip });
        private void DrawChat()
        {
            float w = Mathf.Min(422, Screen.width - 378);
            GUILayout.BeginArea(new Rect(360, 130, w, Screen.height - 148), GUI.skin.box);
            GUILayout.Label("Authorized messages · " + communication.LastAction, heading);
            chatScroll = GUILayout.BeginScrollView(chatScroll, GUILayout.Height(Math.Max(80, Screen.height - 355)));
            foreach (var message in communication.Inbox.Where(m => m.ExpiresAtUnixMs > Now))
                GUILayout.Label(communication.DisplayName(message.SenderActorId) + ": " + message.Text, body);
            GUILayout.EndScrollView();
            var channels = communication.Peers.AllowedChannels.ToArray();
            if (!channels.Contains(channel)) channel = channels.FirstOrDefault() ?? "";
            if (channels.Length > 0 && GUILayout.Button("Channel: " + channel)) channel = channels[(Array.IndexOf(channels, channel) + 1) % channels.Length];
            var peers = communication.Peers.Peers.ToArray();
            if (!peers.Any(p => p.ActorId == recipient)) recipient = peers.FirstOrDefault()?.ActorId ?? "";
            if (channel == "direct" && peers.Length > 0 && GUILayout.Button("To: " + communication.DisplayName(recipient)))
                recipient = peers[(Array.FindIndex(peers, p => p.ActorId == recipient) + 1) % peers.Length].ActorId;
            GUI.SetNextControlName("social-chat"); text = GUILayout.TextField(text, 512);
            bool controlsEnabled = GUI.enabled;
            GUI.enabled = controlsEnabled && channel.Length > 0 && !string.IsNullOrWhiteSpace(text)
                && Encoding.UTF8.GetByteCount(text) <= 512 && (channel != "direct" || recipient.Length > 0);
            if (channel.Length > 0 && GUILayout.Button("Send · " + Encoding.UTF8.GetByteCount(text) + "/512 bytes"))
            {
                communication.Submit(new SendMessageCommand { MatchId = owner.State.MatchId, ChannelId = channel, RecipientActorId = channel == "direct" ? recipient : "",
                    Text = text, CorrelationId = Guid.NewGuid().ToString("N"), TtlMs = 60000 });
                text = ""; GUI.FocusControl(null);
            }
            GUI.enabled = controlsEnabled;
            GUILayout.EndArea();
        }
        private void DrawPostgame()
        {
            var summary = owner.Postgame.Summary;
            if (summary == null) return;
            GUILayout.BeginArea(new Rect(18, 206, Screen.width - 36, Screen.height - 224), GUI.skin.box);
            GUILayout.Label(summary.WinningSide + " · " + summary.WinReason, heading);
            foreach (var player in summary.Players)
                GUILayout.Label(player.DisplayName + " · " + player.Alignment + " / " + player.Duty + " · team " + (player.TeamWon ? "won" : "did not win") + " · " + (player.Survived ? "survived" : player.Life.ToString()), body);
            GUILayout.EndArea(); keyboard.TextEntryFocused = false;
        }
        private void OnDisable() { if (keyboard != null) keyboard.TextEntryFocused = false; }
#endif
    }
}
