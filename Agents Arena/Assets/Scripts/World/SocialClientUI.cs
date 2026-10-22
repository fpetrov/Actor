using System;
using System.Linq;
using Actor.Networking;
using Actor.UI;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using UnityEngine;
#if !UNITY_SERVER
using UnityEngine.InputSystem;
using UnityEngine.UIElements;
#endif

namespace Actor.World
{
    // Retained presenter over the local owner's targeted observation endpoint.
    public sealed class SocialClientUI : MonoBehaviour
    {
#if !UNITY_SERVER
        private NetworkSocialMatch owner;
        private NetworkCooperation communication;
        private VisualElement hud, toolbar, actionSheet, actionList, meeting, meetingRoster, meetingMain, ballot, candidateGrid, result, death, waiting, work;
        private Label title, assignment, progress, countdown, cooldown, meetingTitle, meetingTime, voteStatus, workTime;
        private Button ready, messages, confirm, skip, meetingMessages;
        private CommunicationPanel chat;
        private VisualElement chatSheet;
        private string waitingSignature = "", workSignature = "";
        private string actionSignature = "", meetingSignature = "", summarySignature = "", selection, shownMeeting = "";
        private string feedbackSignature = "";
        private bool detailsOpen, chatOpen, votePending, meetingChat;
        private SocialPhase shownPhase;
        private SocialLife shownLife;
        private long Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        private void Awake() { owner = GetComponent<NetworkSocialMatch>(); communication = GetComponent<NetworkCooperation>(); }
        private void Build()
        {
            if (ArenaUI.HudLayer == null) return;
            hud = ArenaUI.Panel("", "hud social-hud", ArenaUI.HudLayer);
            hud.Clear(); // The compact card has its own two rows, not an empty panel heading.
            var identityRow = ArenaUI.Element("social-hud-row", hud);
            title = ArenaUI.Text("", "social-hud-phase", identityRow);
            assignment = ArenaUI.Text("", "social-hud-assignment", identityRow);
            var metricsRow = ArenaUI.Element("social-hud-row", hud);
            progress = ArenaUI.Text("", "social-hud-metric", metricsRow);
            countdown = ArenaUI.Text("", "social-hud-metric social-hud-clock", metricsRow);
            cooldown = ArenaUI.Text("", "social-hud-metric social-hud-cooldown", metricsRow);
            toolbar = ArenaUI.Element("hud-actions", ArenaUI.HudLayer);
            ready = ArenaUI.Button("Ready", () => owner.Submit(new SocialReadyCommand { Context = owner.Context(), Ready = !owner.Own.Ready }), "primary", toolbar);
            ArenaUI.Button("E · Actions", () => { detailsOpen = !detailsOpen; chatOpen = false; }, "", toolbar);
            messages = ArenaUI.Button("T · Chat", () => { chatOpen = !chatOpen; detailsOpen = false; }, "", toolbar);
            ArenaUI.Button("Rules", ShowRules, "quiet", toolbar);
            actionSheet = ArenaUI.Panel("Tasks & nearby actions", "action-sheet", ArenaUI.ScreenLayer);
            work = ArenaUI.Element("participant", actionSheet);
            work.style.flexShrink = 0; // Current work choices remain above the scrolling task list.
            var scroll = new ScrollView(); scroll.style.flexGrow = 1; scroll.style.minHeight = 0;
            actionSheet.Add(scroll); actionList = scroll.contentContainer;
            chatSheet = ArenaUI.Panel("Communications", "action-sheet", ArenaUI.ScreenLayer);
            ArenaUI.Text("Messages are player claims. Receipts describe transport only.", "muted", chatSheet);
            chat = new CommunicationPanel(communication, chatSheet, true);
            meeting = ArenaUI.Panel("", "full-screen", ArenaUI.ScreenLayer);
            var heading = ArenaUI.Element("row spaced", meeting);
            meetingTitle = ArenaUI.Text("", "heading grow", heading); meetingTime = ArenaUI.Text("", "eyebrow", heading);
            meetingMessages = ArenaUI.Button("Messages", () => meetingChat = !meetingChat, "quiet", heading);
            ArenaUI.Text("Separate what you saw from what someone told you. Alignment stays private.", "muted", meeting);
            var columns = ArenaUI.Element("meeting-columns", meeting);
            meetingRoster = ArenaUI.Element("meeting-roster", columns);
            meetingMain = ArenaUI.Element("meeting-main", columns);
            ballot = ArenaUI.Element("grow", meetingMain);
            var candidates = new ScrollView(); candidates.style.flexGrow = 1; ballot.Add(candidates);
            candidateGrid = ArenaUI.Element("candidate-grid", candidates);
            var footer = ArenaUI.Element("ballot-footer row", ballot);
            voteStatus = ArenaUI.Text("Select a participant or Skip.", "muted grow", footer);
            skip = ArenaUI.Button("Skip", () => { selection = ""; meetingSignature = ""; }, "quiet gap", footer);
            confirm = ArenaUI.Button("Confirm ballot", ConfirmVote, "primary", footer);
            result = ArenaUI.Panel("Expedition complete", "full-screen", ArenaUI.ScreenLayer);
            death = ArenaUI.Panel("You are out of play", "death", ArenaUI.ScreenLayer);
            ArenaUI.Text("Your movement and actions have stopped. Your team's result will appear here when the match ends.", "body", death);
            ArenaUI.Button("Wait for result", () => ArenaUI.Toast("Waiting for the authoritative team result."), "primary", death);
            ArenaUI.Button("Return to rooms", Leave, "quiet", death);
            waiting = ArenaUI.Element("social-hud-roster", hud);
            owner.PermittedEvent += OnEvent;
            ArenaUI.EscapeRequested += Escape;
        }
        private void Update()
        {
            bool active = owner != null && owner.IsSpawned && owner.IsOwner && owner.IsClient && SocialVisibilitySystem.IsSocial(owner.NetworkManager) && owner.HasOwnState;
            if (!active) { SetVisible(false); return; }
            if (hud == null) Build(); if (hud == null) return;
            var vm = new SocialViewModel(owner, Now);
            if (shownLife != owner.Own.Life)
            {
                shownLife = owner.Own.Life;
                if (!vm.Alive) { ArenaUI.CloseModal(); detailsOpen = chatOpen = false; }
            }
            if (shownPhase != vm.Phase)
            {
                shownPhase = vm.Phase; actionSignature = meetingSignature = ""; meetingChat = vm.Phase == SocialPhase.Discussion;
                if (vm.Meeting || vm.Terminal || !vm.Alive) { chatOpen = detailsOpen = false; ArenaUI.CloseModal(); }
            }
            if (shownMeeting != owner.Meeting.MeetingId)
            { shownMeeting = owner.Meeting.MeetingId; selection = null; votePending = false; meetingSignature = ""; }
            bool boarding = vm.Phase == SocialPhase.WaitingForPlayers;
            title.text = boarding ? "Waiting" : vm.Phase == SocialPhase.Exploration ? "Explore" : "Preparing";
            title.tooltip = vm.Heading; assignment.text = vm.Assignment; assignment.tooltip = vm.Assignment;
            progress.text = boarding ? SeatRange() + " seats" : "Tasks " + owner.State.CrewTasksCompleted + "/" + owner.State.TaskThreshold;
            countdown.text = boarding ? owner.Waiting.ReadyCount + "/" + owner.Waiting.Players.Count + " ready" : vm.Countdown;
            bool showCooldown = owner.Emergency.Active || owner.Own.Alignment == SocialAlignment.Impostor && vm.Human;
            cooldown.text = owner.Emergency.Active ? "LIFE SUPPORT · " + SocialViewModel.Seconds(owner.Emergency.EndsAtUnixMs, Now) + "s" : vm.Cooldown;
            cooldown.tooltip = cooldown.text; ArenaUI.Show(cooldown, showCooldown);
            if (feedbackSignature != owner.LastAction)
            {
                feedbackSignature = owner.LastAction;
                if (vm.Human && !string.IsNullOrEmpty(feedbackSignature)) ArenaUI.Toast(Feedback(feedbackSignature));
            }
            ArenaUI.Show(hud, !vm.Meeting && !vm.Terminal);
            ArenaUI.Show(toolbar, !vm.Meeting && !vm.Terminal && vm.Alive && vm.Human);
            ArenaUI.Show(ready, vm.Phase == SocialPhase.WaitingForPlayers);
            ready.text = owner.Own.Ready ? "✓ Ready" : "Ready";
            ready.tooltip = owner.Own.Ready ? "Undo readiness" : "Confirm that you are ready to begin";
            ArenaUI.Show(actionSheet, detailsOpen && vm.Human && vm.Alive && vm.Phase == SocialPhase.Exploration);
            ArenaUI.Show(chatSheet, chatOpen && vm.Human && vm.Alive && !vm.Meeting && !vm.Terminal);
            ArenaUI.Show(meeting, vm.Meeting); ArenaUI.Show(result, vm.Terminal);
            ArenaUI.Show(death, !vm.Alive && !vm.Meeting && !vm.Terminal);
            messages.text = "T · Chat" + (chat.Unread > 0 ? " (" + chat.Unread + ")" : "");
            chat.Tick(vm.Meeting && meetingChat || chatOpen, vm.Human && vm.Alive);
            if (vm.Meeting)
            {
                if (chat.Root.parent != meetingMain) meetingMain.Add(chat.Root);
                meetingTitle.text = vm.Heading; meetingTime.text = vm.Countdown;
                ArenaUI.Show(meetingMessages, vm.Phase == SocialPhase.Voting);
                meetingMessages.text = meetingChat ? "Back to ballot" : "Messages" + (chat.Unread > 0 ? " (" + chat.Unread + ")" : "");
                ArenaUI.Show(meetingRoster, meetingChat);
                ArenaUI.Show(chat.Root, meetingChat);
                RenderMeeting(vm);
            }
            else { if (chat.Root.parent != chatSheet) chatSheet.Add(chat.Root); ArenaUI.Show(chat.Root, true); }
            if (vm.Terminal) RenderSummary();
            if (detailsOpen && vm.Alive) { RenderActions(); RenderWork(); }
            if (vm.Phase == SocialPhase.WaitingForPlayers) RenderWaiting(); else { waiting.Clear(); waitingSignature = ""; }
            if (!vm.Human || !vm.Alive || ArenaUI.InputCaptured || Keyboard.current == null || !Application.isFocused) return;
            if (Keyboard.current.tKey.wasPressedThisFrame && !vm.Meeting) { chatOpen = !chatOpen; detailsOpen = false; }
            if (Keyboard.current.eKey.wasPressedThisFrame && vm.Phase == SocialPhase.Exploration) { detailsOpen = !detailsOpen; chatOpen = false; }
            if (Keyboard.current.escapeKey.wasPressedThisFrame) { detailsOpen = chatOpen = false; }
        }
        private void RenderWaiting()
        {
            string next = owner.Waiting.ToString(); if (next == waitingSignature) return; waitingSignature = next;
            waiting.Clear();
            foreach (var player in owner.Waiting.Players.Take(6))
            {
                var label = ArenaUI.Text((player.Ready ? "✓ " : "○ ") + player.DisplayName, "social-hud-player", waiting);
                label.tooltip = player.DisplayName + (player.Connected ? player.Ready ? " · ready" : " · not ready" : " · reconnecting");
            }
        }
        private void ShowRules()
        {
            var card = ArenaUI.OpenModal("Expedition rules"); if (card == null) return;
            ArenaUI.Text(SeatRange() + " players confirm readiness before assignments are made. Your alignment is private.", "body", card);
            ArenaUI.Text("Crew win by completing " + owner.State.TaskThreshold + " station tasks or ejecting all Impostors. An Impostor wins by living parity or unrepaired life support.", "body", card);
            ArenaUI.Text("Eliminate cooldown: " + owner.State.KillCooldownMs / 1000f + "s. Physical range and line of sight are checked by the server.", "body", card);
            ArenaUI.Text("Messages are player claims. Discuss what you observed, then confirm one final ballot or Skip.", "body", card);
            ArenaUI.Button("How to play", ArenaUI.Tutorial, "quiet", card);
        }
        private string SeatRange() => owner.Waiting.MinPlayers > 0 && owner.Waiting.MaxPlayers >= owner.Waiting.MinPlayers
            ? owner.Waiting.MinPlayers + "–" + owner.Waiting.MaxPlayers : "4–6";
        private void RenderWork()
        {
            var state = owner.TaskWork;
            ArenaUI.Show(work, state.WorkId.Length > 0);
            string remaining = state.Mechanic == SocialTaskMechanic.MaintenanceHold
                ? "Remain nearby · " + SocialViewModel.Seconds(state.DeadlineUnixMs, Now) + "s remaining"
                : Now < state.NotBeforeUnixMs ? "Sampling / settling · ready in " + SocialViewModel.Seconds(state.NotBeforeUnixMs, Now) + "s"
                : "Choose before the timer expires · " + SocialViewModel.Seconds(state.DeadlineUnixMs, Now) + "s";
            if (workTime != null) workTime.text = remaining;
            string next = state.ToString() + "|" + (Now >= state.NotBeforeUnixMs);
            if (next == workSignature) return; workSignature = next; work.Clear(); workTime = null;
            if (state.WorkId.Length == 0) return;
            ArenaUI.Text(TaskTitle(owner.Tasks.Tasks.FirstOrDefault(t => t.TaskId == state.TaskId)), "heading", work);
            workTime = ArenaUI.Text(remaining, "muted", work);
            if (state.Mechanic == SocialTaskMechanic.MaintenanceHold)
                ArenaUI.Text("Stay beside the terminal until server completion. Moving away cancels physical work.", "body", work);
            else
            {
                ArenaUI.Text("Step " + state.StepIndex + " / " + state.StepCount, "muted", work);
                SocialTaskChoice[] choices;
                if (state.Mechanic == SocialTaskMechanic.CircuitCalibration)
                {
                    ArenaUI.Text("Select the indicated circuit: " + state.RequiredChoice, "body", work);
                    choices = new[] { SocialTaskChoice.Alpha, SocialTaskChoice.Beta, SocialTaskChoice.Gamma };
                }
                else
                {
                    ArenaUI.Text("Sensor sample A: " + state.SampleA + " · B: " + (state.SampleBReady ? state.SampleB.ToString() : "sampling…"), "body", work);
                    choices = new[] { SocialTaskChoice.Rising, SocialTaskChoice.Stable, SocialTaskChoice.Falling };
                }
                var row = ArenaUI.Element("row wrap", work);
                foreach (var choice in choices)
                    ArenaUI.Button(choice.ToString(), () => owner.Submit(new SocialTaskStepCommand { Context = owner.Context(), WorkId = state.WorkId, StepRevision = state.StepRevision, Choice = choice }), "grow", row)
                        .SetEnabled(Now >= state.NotBeforeUnixMs && (state.Mechanic != SocialTaskMechanic.SensorObservation || state.SampleBReady));
            }
            ArenaUI.Text("Server validates timing, position and this exact step. Starting is not completion.", "muted", work);
        }
        private static string RoomName(string id) => id switch { "main-hall" => "Main Hall", "engineering" => "Engineering", "security" => "Security", "reactor" => "Reactor", "storage" => "Storage", _ => id.Replace('-', ' ') };
        private static string TaskTitle(SocialTask task) => task == null ? "Current station work" : (task.Mechanic switch
        { SocialTaskMechanic.CircuitCalibration => "Calibrate circuits", SocialTaskMechanic.SensorObservation => "Observe sensors", _ => "Maintain station" }) + " · " + RoomName(task.RoomId);
        private static string TaskInstruction(SocialTask task) => task.Mechanic switch
        { SocialTaskMechanic.CircuitCalibration => task.StepCount + " interlocked circuit selections", SocialTaskMechanic.SensorObservation => "Compare two samples " + task.SampleDelayMs / 1000f + "s apart", _ => "Remain beside the panel for " + task.WorkDurationMs / 1000f + "s" };
        private static string Feedback(string code) => code switch
        {
            "out_of_range" => "Move closer to interact.", "not_visible" => "That target is no longer in sight.",
            "kill_cooldown" => "Eliminate is still cooling down.", "work_started" => "Work started. Stay near the panel.",
            "work_completed" => "Station work complete.", "task_completed" => "Station work complete.",
            "crew_tasks" => "Crew completed the required station work", "crew_parity" => "Impostor reached parity",
            "impostor_parity" => "Impostor reached parity", "impostor_eliminated" => "The Impostor was eliminated",
            "timeout" => "The expedition timer expired", "all_crew_eliminated" => "Crew were eliminated",
            "match_timeout" => "The expedition timer expired", "waiting_timeout" => "Not enough ready participants",
            "all_players_lost" => "The expedition ended after all participants disconnected",
            "tasks_completed" => "Crew completed the required station work", "parity" => "Impostor reached parity",
            "impostors_eliminated" => "The Impostor was eliminated", "sabotage_expired" => "Life support failed",
            _ => (code ?? "").Replace('_', ' ')
        };
        private float Distance(float x, float y) => Vector2.Distance(GetComponent<CapsuleCollider2D>().bounds.center, new Vector2(x, y));
        private void RenderActions()
        {
            var world = owner.CaptureWorld();
            string next = owner.Tasks.ServerRevision + "|" + owner.Own.ServerRevision + "|" + owner.ActiveWorkId + "|" + owner.Emergency.ServerRevision + "|" + (owner.Own.KillReadyAtUnixMs <= Now)
                + "|" + string.Join("|", world.Interactables.Select(t => t.EntityId + (Distance(t.X, t.Y) <= t.Radius))) + "|" + string.Join("|", world.Bodies.Select(b => b.BodyId + (Distance(b.X, b.Y) <= owner.State.BodyReportDistance))) + "|" + string.Join("|", world.Actors.Select(a => a.ActorId + (Distance(a.X, a.Y) <= owner.State.KillDistance)));
            if (next == actionSignature) return; actionSignature = next; actionList.Clear();
            foreach (var task in owner.Tasks.Tasks)
            {
                var row = ArenaUI.Element("participant", actionList);
                ArenaUI.Text(TaskTitle(task), "body", row);
                ArenaUI.Text(task.Completed ? "✓ Complete" : TaskInstruction(task), "muted", row);
                if (!task.Completed)
                {
                    var button = ArenaUI.Button("Work at terminal", () => owner.Submit(new SocialWorkTaskCommand { Context = owner.Context(), TaskId = task.TaskId }), "", row);
                    button.SetEnabled(world.Interactables.Any(t => t.EntityId == task.TerminalEntityId && Distance(t.X, t.Y) <= t.Radius) && owner.ActiveWorkId.Length == 0);
                }
            }
            if (owner.ActiveWorkId.Length > 0)
                ArenaUI.Button("Cancel current work", () => owner.Submit(new SocialCancelWorkCommand { Context = owner.Context(), WorkId = owner.ActiveWorkId }), "quiet", actionList);
            foreach (var terminal in world.Interactables)
            {
                if (terminal.Kind == "meeting") ArenaUI.Button("Call meeting · console", () => owner.Submit(new SocialCallMeetingCommand { Context = owner.Context() }), "", actionList);
                if (terminal.Kind != "life-support") continue;
                if (owner.Emergency.Active && owner.Own.Alignment == SocialAlignment.Crew)
                    ArenaUI.Button("Repair life support", () => owner.Submit(new SocialRepairEmergencyCommand { Context = owner.Context(), EmergencyId = owner.Emergency.EmergencyId }), "primary", actionList);
                if (!owner.Emergency.Active && owner.Own.Alignment == SocialAlignment.Impostor)
                    ArenaUI.Button("Sabotage life support", () => owner.Submit(new SocialSabotageCommand { Context = owner.Context(), TerminalEntityId = terminal.EntityId }), "danger", actionList);
            }
            foreach (var body in world.Bodies)
                ArenaUI.Button("Report body · " + body.VictimDisplayName, () => owner.Submit(new SocialReportBodyCommand { Context = owner.Context(), BodyId = body.BodyId }), "danger", actionList).SetEnabled(Distance(body.X, body.Y) <= owner.State.BodyReportDistance);
            if (owner.Own.Alignment == SocialAlignment.Impostor)
                foreach (var actor in world.Actors)
                    ArenaUI.Button("Eliminate nearby · " + actor.DisplayName, () => owner.Submit(new SocialKillCommand { Context = owner.Context(), TargetActorId = actor.ActorId }), "danger", actionList).SetEnabled(Distance(actor.X, actor.Y) <= owner.State.KillDistance && owner.Own.KillReadyAtUnixMs <= Now);
            ArenaUI.Text("Approach the physical terminal, body or player. The server checks range, sight and cooldown.", "muted", actionList);
        }
        private void RenderMeeting(SocialViewModel vm)
        {
            bool voting = vm.Phase == SocialPhase.Voting;
            ArenaUI.Show(ballot, voting && !meetingChat || vm.Phase == SocialPhase.VoteResolution);
            string next = owner.Meeting.PhaseRevision + "|" + owner.Meeting.OwnBallotSubmitted + "|" + selection + "|" + votePending + "|" + owner.Meeting.Resolution
                + "|" + string.Join("|", owner.Meeting.Participants.Select(p => p.ActorId + p.Life));
            if (next != meetingSignature)
            {
                meetingSignature = next; meetingRoster.Clear(); candidateGrid.Clear();
                foreach (var person in owner.Meeting.Participants)
                {
                    var row = ArenaUI.Element("participant", meetingRoster);
                    ArenaUI.Text(person.DisplayName, "body", row).tooltip = person.DisplayName;
                    ArenaUI.Text(person.Life.ToString(), "muted", row);
                    if (!voting) continue;
                    var candidate = ArenaUI.Button(person.DisplayName + "\n" + person.Life, () => { selection = person.ActorId; meetingSignature = ""; }, "candidate" + (selection == person.ActorId ? " selected" : ""), candidateGrid);
                    candidate.tooltip = person.DisplayName;
                    candidate.SetEnabled(vm.Human && vm.Alive && person.Life == SocialLife.Alive && !owner.Meeting.OwnBallotSubmitted && !votePending);
                }
                if (owner.Meeting.Resolution.Length > 0)
                {
                    ArenaUI.Text(owner.Meeting.Resolution == "ejected" ? Name(owner.Meeting.EjectedActorId) + " was ejected. Alignment remains private." : "No one was ejected.", "heading", candidateGrid);
                    foreach (var tally in owner.Meeting.Result) ArenaUI.Text((tally.CandidateActorId.Length == 0 ? "Skip" : Name(tally.CandidateActorId)) + " · " + tally.Votes, "body", candidateGrid);
                }
            }
            skip.SetEnabled(voting && vm.Human && vm.Alive && !owner.Meeting.OwnBallotSubmitted && !votePending);
            skip.EnableInClassList("selected", selection == "");
            voteStatus.text = owner.Meeting.OwnBallotSubmitted ? "Your ballot is final." : votePending ? "Submitting ballot…" : selection == null ? "Select a participant or Skip." : "Selected: " + (selection.Length == 0 ? "Skip" : Name(selection));
            confirm.SetEnabled(voting && vm.Human && vm.Alive && selection != null && !owner.Meeting.OwnBallotSubmitted && !votePending);
        }
        private string Name(string actor) => owner.Meeting.Participants.FirstOrDefault(p => p.ActorId == actor)?.DisplayName ?? communication.DisplayName(actor);
        private void ConfirmVote()
        {
            if (selection == null || votePending || owner.Meeting.OwnBallotSubmitted || owner.State.Phase != SocialPhase.Voting) return;
            votePending = owner.Submit(new SocialCastVoteCommand { Context = owner.Context(), MeetingId = owner.Meeting.MeetingId, CandidateActorId = selection, Skip = selection.Length == 0 });
        }
        private void OnEvent(IMessage value)
        {
            if (value is SocialActionResultEvent action && action.Action == SocialAction.CastVote)
            { votePending = false; if (!action.Accepted) ArenaUI.Toast("Ballot rejected: " + action.ReasonCode); }
        }
        private void RenderSummary()
        {
            var summary = owner.Postgame.Summary; if (summary == null || summary.MatchId == summarySignature) return;
            summarySignature = summary.MatchId; result.Clear();
            bool noWinner = summary.WinningSide == SocialAlignment.Unspecified;
            ArenaUI.Text(noWinner ? "Expedition ended · no winner" : summary.WinningSide + " victory", "title", result);
            ArenaUI.Text(Feedback(summary.WinReason) + " · " + summary.DurationMs / 1000 + "s", "muted", result);
            var scroll = new ScrollView(); result.Add(scroll);
            foreach (var player in summary.Players)
                ArenaUI.Text(player.DisplayName + " · " + player.Alignment + " / " + player.Duty + "\n" + (noWinner ? "No team result" : player.TeamWon ? "Team won" : "Team lost") + " · " + (player.Survived ? "Survived" : player.Life.ToString()), "result-row", scroll);
            ArenaUI.Text(summary.Kills + " eliminations · " + summary.BodyReports + " reports · " + summary.Meetings + " meetings · " + summary.Votes + " ballots", "muted", result);
            var actions = ArenaUI.Element("row", result);
            ArenaUI.Button("New expedition", () => { FindAnyObjectByType<Actor.Lobby.LobbyMenuView>()?.RequestFreshRound(); }, "primary grow gap", actions);
            ArenaUI.Button("Return to rooms", Leave, "grow", actions);
        }
        private void Escape() { detailsOpen = chatOpen = false; if (owner.State.Phase == SocialPhase.Voting) meetingChat = false; }
        private static void Leave() => FindAnyObjectByType<Actor.Lobby.LobbyMenuView>()?.Leave();
        private void SetVisible(bool visible)
        { foreach (var panel in new[] { hud, toolbar, actionSheet, chatSheet, meeting, result, death }) ArenaUI.Show(panel, visible); }
        private void OnDisable()
        {
            ArenaUI.EscapeRequested -= Escape;
            if (owner != null) owner.PermittedEvent -= OnEvent;
            chat?.Dispose(); chat = null;
            foreach (var panel in new[] { hud, toolbar, actionSheet, chatSheet, meeting, result, death }) panel?.RemoveFromHierarchy();
            hud = toolbar = actionSheet = chatSheet = meeting = result = death = null;
        }
        private void OnDestroy() => OnDisable();
#endif
    }
}
