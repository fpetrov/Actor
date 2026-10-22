#if !UNITY_SERVER
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Actor.Networking;
using Actor.Protocol.Standard.Communication.V1;
using Google.Protobuf;
using UnityEngine.UIElements;

namespace Actor.UI
{
    // Own endpoint only. Claims and transport receipts have distinct meanings.
    public sealed class CommunicationPanel : IDisposable
    {
        private sealed class Outgoing
        {
            public string Correlation, Text, Channel, Recipient, MessageId = "", Status = "Pending";
            public long ExpiresAt, SentAtUnixMs, AcceptedAtUnixMs;
            public ulong ServerSequence;
        }
        private sealed class Row
        {
            public MessageReceivedEvent Incoming;
            public Outgoing Outgoing;
            public long SentAtUnixMs;
            public ulong Sequence;
            public string Key;
        }
        private readonly NetworkCooperation owner;
        private readonly List<Outgoing> sent = new();
        private readonly ScrollView log;
        private readonly DropdownField channel, recipient;
        private readonly TextField composer;
        private readonly Label receipt;
        private readonly Button send, newer;
        private readonly Toggle sensitive;
        private string signature = "", peersSignature = "", preparedScope = "";
        private bool visible, human, social;
        private int unread;
        private long receivedSeen;
        private IVisualElementScheduledItem pendingScroll;
        private long scrollRevision;
        private bool disposed;
        public readonly VisualElement Root;
        public int Unread => unread;
        public CommunicationPanel(NetworkCooperation endpoint, VisualElement parent, bool socialMode)
        {
            owner = endpoint; social = socialMode;
            Root = ArenaUI.Element("chat", parent);
            log = new ScrollView(); log.AddToClassList("chat-log"); Root.Add(log);
            log.RegisterCallback<WheelEvent>(_ => CancelPendingScroll(), TrickleDown.TrickleDown);
            log.RegisterCallback<PointerDownEvent>(_ => CancelPendingScroll(), TrickleDown.TrickleDown);
            log.RegisterCallback<KeyDownEvent>(e =>
            {
                if (e.keyCode is UnityEngine.KeyCode.UpArrow or UnityEngine.KeyCode.DownArrow
                    or UnityEngine.KeyCode.PageUp or UnityEngine.KeyCode.PageDown or UnityEngine.KeyCode.Home or UnityEngine.KeyCode.End)
                    CancelPendingScroll();
            });
            newer = ArenaUI.Button("New messages ↓", () => { ToBottom(); unread = 0; }, "quiet", Root); ArenaUI.Show(newer, false);
            var compose = ArenaUI.Element("chat-composer", Root);
            var routing = ArenaUI.Element("row wrap chat-routing", compose);
            channel = new DropdownField("Channel", new List<string>(), -1); channel.AddToClassList("channel"); routing.Add(channel);
            recipient = new DropdownField("To", new List<string>(), -1); recipient.AddToClassList("channel"); routing.Add(recipient);
            sensitive = new Toggle("Private task content"); compose.Add(sensitive); ArenaUI.Show(sensitive, !social);
            composer = ArenaUI.Field("", "", _ => UpdateSend(), 512, compose); composer.multiline = true; composer.AddToClassList("chat-compose-text");
            composer.RegisterCallback<KeyDownEvent>(e => { if (!ArenaUI.IMEComposing && (e.ctrlKey || e.commandKey) && e.keyCode == UnityEngine.KeyCode.Return) { Send(); e.StopPropagation(); } });
            var footer = ArenaUI.Element("row wrap", compose);
            receipt = ArenaUI.Text("", "chat-receipt grow", footer);
            send = ArenaUI.Button("Send", Send, "primary", footer);
            owner.PermittedEvent += OnEvent;
            channel.RegisterValueChangedCallback(_ => UpdateSend());
            recipient.RegisterValueChangedCallback(_ => UpdateSend());
        }
        public void Prepare(string actorId, string text, bool privateContent, string scope)
        {
            RefreshPeers(); channel.SetValueWithoutNotify("direct");
            var peer = owner.Peers.Peers.FirstOrDefault(p => p.ActorId == actorId);
            if (peer != null) recipient.SetValueWithoutNotify(peer.DisplayName + " · " + Short(peer.ActorId));
            composer.value = text; sensitive.value = privateContent; preparedScope = scope ?? ""; composer.Focus();
        }
        private static string Short(string id) => id.Length > 6 ? id.Substring(0, 6) : id;
        public void Tick(bool isVisible, bool isHuman)
        {
            visible = isVisible; human = isHuman;
            RefreshPeers();
            if (owner.MessageCount != receivedSeen)
            {
                if (!visible) unread += (int)Math.Max(0, owner.MessageCount - receivedSeen);
                receivedSeen = owner.MessageCount;
            }
            if (visible) unread = 0;
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            sent.RemoveAll(message => message.ExpiresAt <= now);
            var messages = owner.Inbox.Where(m => m.ExpiresAtUnixMs > now).ToArray();
            var rows = new List<Row>(messages.Length + sent.Count);
            string self = owner.GetComponent<NetworkCharacter>().AuthoritativeActorId;
            foreach (var message in messages)
            {
                // Discussion can echo to the sender. Keep its original send
                // timestamp, but show one own card with a separate receipt.
                var outgoing = message.SenderActorId == self ? sent.FirstOrDefault(s => s.Correlation == message.CorrelationId) : null;
                if (outgoing != null)
                {
                    if (message.ServerUnixMs > 0) outgoing.AcceptedAtUnixMs = message.ServerUnixMs;
                    outgoing.MessageId = message.MessageId; outgoing.ServerSequence = message.ServerSequence;
                    continue;
                }
                rows.Add(new Row { Incoming = message, SentAtUnixMs = message.ServerUnixMs,
                    Sequence = message.ServerSequence > 0 ? message.ServerSequence : ulong.MaxValue, Key = message.MessageId });
            }
            rows.AddRange(sent.Select(message => new Row { Outgoing = message,
                SentAtUnixMs = message.AcceptedAtUnixMs > 0 ? message.AcceptedAtUnixMs : message.SentAtUnixMs,
                Sequence = message.ServerSequence > 0 ? message.ServerSequence : ulong.MaxValue,
                Key = message.MessageId.Length > 0 ? message.MessageId : message.Correlation }));
            rows = rows.OrderBy(row => row.SentAtUnixMs).ThenBy(row => row.Sequence)
                .ThenBy(row => row.Key, StringComparer.Ordinal).ToList();
            string next = human + "|" + string.Join("|", rows.Select(row => row.Key + ":" + row.SentAtUnixMs + ":" + row.Sequence + ":" + row.Outgoing?.Status));
            if (next != signature)
            {
                float position = log.verticalScroller.value;
                bool bottom = position >= log.verticalScroller.highValue - 28;
                signature = next; log.Clear();
                foreach (var row in rows)
                {
                    var card = ArenaUI.Element("message", log);
                    if (row.Incoming != null)
                    {
                        var message = row.Incoming;
                        ArenaUI.Text(owner.DisplayName(message.SenderActorId) + " · " + message.ChannelId + " · player claim", "muted", card);
                        ArenaUI.Text(message.Sensitive && !human ? "[Private task content]" : message.Text, "message-content", card);
                    }
                    else
                    {
                        var message = row.Outgoing;
                        ArenaUI.Text("You · " + message.Channel + " · player claim · " + message.Status, "muted", card);
                        ArenaUI.Text(human ? message.Text : "[Own submitted message]", "message-content", card);
                    }
                }
                if (rows.Count == 0) ArenaUI.Text("No messages yet. Share what you observed; distinguish evidence from suspicion.", "muted", log);
                if (bottom) ToBottom();
                else { ScheduleScroll(position, false); ArenaUI.Show(newer, visible); }
            }
            composer.SetEnabled(human && owner.Peers.AllowedChannels.Count > 0);
            ArenaUI.Show(recipient, channel.value == "direct"); UpdateSend();
        }
        private void RefreshPeers()
        {
            string next = string.Join("|", owner.Peers.AllowedChannels) + "|" + string.Join("|", owner.Peers.Peers.Select(p => p.ActorId + p.DisplayName));
            if (next == peersSignature) return; peersSignature = next;
            string current = channel.value;
            channel.choices = owner.Peers.AllowedChannels.ToList();
            if (!channel.choices.Contains(current)) channel.SetValueWithoutNotify(channel.choices.FirstOrDefault() ?? "");
            string target = recipient.value;
            recipient.choices = owner.Peers.Peers.Where(p => p.ActorId != owner.GetComponent<NetworkCharacter>().AuthoritativeActorId)
                .Select(p => p.DisplayName + " · " + Short(p.ActorId)).ToList();
            if (!recipient.choices.Contains(target)) recipient.SetValueWithoutNotify(recipient.choices.FirstOrDefault() ?? "");
        }
        private string RecipientId()
        {
            return owner.Peers.Peers.FirstOrDefault(p => p.DisplayName + " · " + Short(p.ActorId) == recipient.value)?.ActorId ?? "";
        }
        private void UpdateSend()
        {
            int bytes = Encoding.UTF8.GetByteCount(composer.value ?? "");
            if (send == null) return;
            send.SetEnabled(human && bytes is > 0 and <= 512 && !string.IsNullOrWhiteSpace(composer.value)
                && owner.Peers.AllowedChannels.Contains(channel.value) && (channel.value != "direct" || RecipientId().Length > 0));
            receipt.text = bytes + "/512 bytes · ⌘/Ctrl+Enter";
        }
        private void Send()
        {
            UpdateSend(); if (!send.enabledSelf) return;
            string correlation = Guid.NewGuid().ToString("N");
            var command = new SendMessageCommand { MatchId = owner.Peers.MatchId, ChannelId = channel.value,
                RecipientActorId = channel.value == "direct" ? RecipientId() : "", Text = composer.value,
                CorrelationId = correlation, TtlMs = 300000,
                Sensitive = !social && sensitive.value, TaskId = !social ? preparedScope.Length > 0 ? preparedScope : owner.TaskState.TaskId : "" };
            if (!owner.Submit(command)) { ArenaUI.Toast("Message could not be submitted."); return; }
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            sent.Add(new Outgoing { Correlation = correlation, Text = command.Text, Channel = command.ChannelId,
                Recipient = command.RecipientActorId, SentAtUnixMs = now, ExpiresAt = now + command.TtlMs });
            while (sent.Count > 16) sent.RemoveAt(0);
            composer.value = ""; preparedScope = ""; composer.Focus(); ToBottom();
        }
        private void OnEvent(IMessage payload)
        {
            if (payload is not MessageDeliveryResultEvent receiptEvent) return;
            var message = sent.FirstOrDefault(s => s.Correlation == receiptEvent.CorrelationId); if (message == null) return;
            // ClientReceived carries the ACK time, not the original send time.
            // It can update the transport label without moving the message.
            if (receiptEvent.Stage == MessageDeliveryStage.ServerAccepted && receiptEvent.ServerUnixMs > 0)
                message.AcceptedAtUnixMs = receiptEvent.ServerUnixMs;
            if (receiptEvent.MessageId.Length > 0) message.MessageId = receiptEvent.MessageId;
            message.Status = receiptEvent.Stage switch
            {
                MessageDeliveryStage.ServerAccepted => "Server accepted",
                MessageDeliveryStage.ClientReceived => "Receiving client acknowledged",
                MessageDeliveryStage.Rejected => "Rejected: " + receiptEvent.ReasonCode, _ => "Pending"
            };
        }
        private void ToBottom()
        { ScheduleScroll(0, true); }
        private void CancelPendingScroll()
        { ++scrollRevision; pendingScroll?.Pause(); pendingScroll = null; }
        private void ScheduleScroll(float position, bool bottom)
        {
            CancelPendingScroll(); long revision = scrollRevision;
            pendingScroll = log.schedule.Execute(() =>
            {
                if (disposed || revision != scrollRevision) return;
                log.verticalScroller.value = bottom ? log.verticalScroller.highValue : position;
                if (bottom) ArenaUI.Show(newer, false);
                pendingScroll = null;
            });
            pendingScroll.ExecuteLater(30);
        }
        public void Dispose()
        { disposed = true; CancelPendingScroll(); owner.PermittedEvent -= OnEvent; Root.RemoveFromHierarchy(); sent.Clear(); }
    }
}
#endif
