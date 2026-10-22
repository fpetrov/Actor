using Actor.Networking;
using Actor.Protocol.Standard.Communication.V1;
using Actor.UI;
using Google.Protobuf;
using UnityEngine.UIElements;

// Exercise the actual production presenter. The test double only supplies its
// owned endpoint and retained UI primitives; it implements no timeline logic.
namespace UnityEngine
{
    public enum KeyCode { Return, UpArrow, DownArrow, PageUp, PageDown, Home, End }
}
namespace UnityEngine.UIElements
{
    public enum TrickleDown { NoTrickleDown, TrickleDown }
    public sealed class WheelEvent { }
    public sealed class PointerDownEvent { }
    public sealed class KeyDownEvent
    {
        public bool ctrlKey, commandKey;
        public UnityEngine.KeyCode keyCode;
        public void StopPropagation() { }
    }
    public interface IVisualElementScheduledItem
    {
        void Pause();
        void ExecuteLater(long delay);
    }
    public sealed class Scheduler
    {
        private sealed class Item(Action action) : IVisualElementScheduledItem
        {
            internal bool Active = true;
            internal void Run() { if (Active) { Active = false; action(); } }
            public void Pause() => Active = false;
            public void ExecuteLater(long delay) { }
        }
        private readonly List<Item> items = new();
        public IVisualElementScheduledItem Execute(Action action)
        { var item = new Item(action); items.Add(item); return item; }
        public void RunPending() { var pending = items.ToArray(); items.Clear(); foreach (var item in pending) item.Run(); }
    }
    public class VisualElement
    {
        private readonly Dictionary<Type, List<Delegate>> callbacks = new();
        public readonly List<VisualElement> Children = new();
        public VisualElement? parent;
        public readonly Scheduler schedule = new();
        public bool enabledSelf = true, Visible = true;
        public virtual void Clear() => Children.Clear();
        public void Add(VisualElement child) { child.parent = this; Children.Add(child); }
        public void RemoveFromHierarchy() { parent?.Children.Remove(this); parent = null; }
        public void AddToClassList(string _) { }
        public void SetEnabled(bool enabled) => enabledSelf = enabled;
        public void RegisterCallback<T>(Action<T> callback, TrickleDown propagation = TrickleDown.NoTrickleDown)
        { if (!callbacks.TryGetValue(typeof(T), out var values)) callbacks[typeof(T)] = values = new(); values.Add(callback); }
        public void Emit<T>(T value)
        { if (callbacks.TryGetValue(typeof(T), out var values)) foreach (var callback in values) ((Action<T>)callback)(value); }
    }
    public sealed class Scroller { public float value, highValue; }
    public sealed class ScrollView : VisualElement
    {
        public readonly Scroller verticalScroller = new();
        // A tree rebuild can reset/clamp the offset before the next layout.
        public override void Clear() { base.Clear(); verticalScroller.value = 0; }
    }
    public sealed class Label : VisualElement { public string text = ""; }
    public sealed class Button(Action clicked) : VisualElement
    {
        public string text = "";
        public void Click() { if (enabledSelf) clicked(); }
    }
    public sealed class DropdownField(string label, List<string> items, int index) : VisualElement
    {
        public List<string> choices = items;
        public string value = "";
        public void SetValueWithoutNotify(string text) => value = text;
        public void RegisterValueChangedCallback(Action<object> callback) { }
    }
    public sealed class Toggle(string label) : VisualElement { public bool value; }
    public sealed class TextField(Action<string> changed) : VisualElement
    {
        private string current = "";
        public bool multiline, Focused;
        public string value { get => current; set { current = value; changed(value); } }
        public void Focus() => Focused = true;
    }
}
namespace Actor.Networking
{
    public sealed class NetworkCharacter { public string AuthoritativeActorId = "self"; }
    public sealed class NetworkCooperation
    {
        public CommunicationPeersObservation Peers = new() { MatchId = "match" };
        public AgentsArena.ActorProtocol.V1.CalibrationTaskStateObservation TaskState = new();
        public readonly Queue<MessageReceivedEvent> Inbox = new();
        public readonly List<SendMessageCommand> Submitted = new();
        public long MessageCount;
        public event Action<IMessage>? PermittedEvent;
        public T GetComponent<T>() where T : class => (T)(object)new NetworkCharacter();
        public string DisplayName(string actor) => actor == "self" ? "Human" : "Codex peer";
        public bool Submit(IMessage value) { Submitted.Add(((SendMessageCommand)value).Clone()); return true; }
        public void Receive(MessageReceivedEvent value) { Inbox.Enqueue(value); ++MessageCount; PermittedEvent?.Invoke(value); }
        public void Receipt(MessageDeliveryResultEvent value) => PermittedEvent?.Invoke(value);
    }
}
namespace Actor.UI
{
    public static class ArenaUI
    {
        public static bool IMEComposing => false;
        public static VisualElement Element(string classes, VisualElement parent)
        { var result = new VisualElement(); parent.Add(result); return result; }
        public static Button Button(string text, Action clicked, string classes, VisualElement parent)
        { var result = new Button(clicked) { text = text }; parent.Add(result); return result; }
        public static Label Text(string text, string classes, VisualElement parent)
        { var result = new Label { text = text }; parent.Add(result); return result; }
        public static TextField Field(string label, string text, Action<string> changed, int limit, VisualElement parent)
        { var result = new TextField(changed); parent.Add(result); return result; }
        public static void Show(VisualElement element, bool visible) => element.Visible = visible;
        public static void Toast(string _) { }
    }
}
namespace Actor.Sdk.Tests
{
    public sealed class CommunicationPanelTests
    {
        private sealed class Fixture : IDisposable
        {
            internal readonly NetworkCooperation Owner = new();
            internal readonly CommunicationPanel Panel;
            internal readonly ScrollView Log;
            internal readonly TextField Composer;
            internal Fixture()
            {
                Owner.Peers.AllowedChannels.Add("discussion");
                Panel = new CommunicationPanel(Owner, new VisualElement(), true);
                Log = Assert.Single(Panel.Root.Children.OfType<ScrollView>());
                Composer = Assert.Single(Descendants(Panel.Root).OfType<TextField>());
                Panel.Tick(true, true);
            }
            internal string[] Content => Log.Children.Where(card => card.Children.Count == 2)
                .Select(card => ((Label)card.Children[1]).text).ToArray();
            internal string[] Headers => Log.Children.Where(card => card.Children.Count == 2)
                .Select(card => ((Label)card.Children[0]).text).ToArray();
            internal SendMessageCommand Send(string text)
            {
                Composer.value = text;
                Descendants(Panel.Root).OfType<Button>().Single(button => button.text == "Send").Click();
                return Owner.Submitted.Last();
            }
            public void Dispose() => Panel.Dispose();
        }
        private static IEnumerable<VisualElement> Descendants(VisualElement root) =>
            root.Children.SelectMany(child => new[] { child }.Concat(Descendants(child)));
        private static long Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        private static MessageReceivedEvent Message(string text, long sentAt, ulong sequence = 1,
                string id = "peer-message", string sender = "peer", string correlation = "peer-correlation") =>
            new() { Text = text, MessageId = id, SenderActorId = sender, RecipientActorId = "self", ChannelId = "discussion",
                MatchId = "match", CorrelationId = correlation, ServerUnixMs = sentAt,
                ServerSequence = sequence, ExpiresAtUnixMs = Now + 60000 };

        [Fact]
        public void HumanSendPrecedesLaterCodexReplyAndDelayedOlderIncomingKeepsItsSendOrder()
        {
            using var f = new Fixture();
            long beforeSend = Now;
            f.Send("Human first");
            f.Owner.Receive(Message("Codex later", Now + 1000));
            f.Panel.Tick(true, true);
            Assert.Equal(new[] { "Human first", "Codex later" }, f.Content);
            f.Owner.Receive(Message("Earlier but delayed", beforeSend - 1, 2, "delayed"));
            f.Panel.Tick(true, true);
            Assert.Equal(new[] { "Earlier but delayed", "Human first", "Codex later" }, f.Content);
        }

        [Fact]
        public void ReceivingClientAckUpdatesTransportLabelWithoutMovingAnOldMessage()
        {
            using var f = new Fixture(); var request = f.Send("Own claim"); long time = Now;
            f.Owner.Receipt(new() { CorrelationId = request.CorrelationId, MessageId = "own-message",
                Stage = MessageDeliveryStage.ServerAccepted, ServerUnixMs = time + 100 });
            f.Owner.Receive(Message("Reply", time + 200)); f.Panel.Tick(true, true);
            Assert.Equal(new[] { "Own claim", "Reply" }, f.Content);
            Assert.Contains("player claim · Server accepted", f.Headers[0]);
            f.Owner.Receipt(new() { CorrelationId = request.CorrelationId, MessageId = "own-message",
                Stage = MessageDeliveryStage.ClientReceived, ServerUnixMs = time + 5000 });
            f.Panel.Tick(true, true);
            Assert.Equal(new[] { "Own claim", "Reply" }, f.Content);
            Assert.Contains("player claim · Receiving client acknowledged", f.Headers[0]);
            Assert.DoesNotContain("read", f.Headers[0], StringComparison.OrdinalIgnoreCase);
        }

        [Fact]
        public void OwnDiscussionEchoHasOneCardAndEqualTimestampPeersUseAuthoritativeSequence()
        {
            using var f = new Fixture(); var request = f.Send("Own broadcast"); long time = Now;
            f.Owner.Receive(Message("Peer second", time + 100, 3, "second"));
            f.Owner.Receive(Message("Own broadcast", time + 100, 1, "own", "self", request.CorrelationId));
            f.Owner.Receive(Message("Peer first", time + 100, 2, "first"));
            f.Panel.Tick(true, true);
            Assert.Equal(new[] { "Own broadcast", "Peer first", "Peer second" }, f.Content);
            Assert.StartsWith("You · discussion · player claim", f.Headers[0]);
        }

        [Fact]
        public void ExpiredIncomingAndPrivateContentStillRespectTtlAndControlMode()
        {
            using var f = new Fixture(); f.Send("Own private text");
            var expired = Message("Expired", Now - 2000); expired.ExpiresAtUnixMs = Now - 1;
            f.Owner.Receive(expired);
            var sensitive = Message("Private authorization", Now + 1000, 2, "private"); sensitive.Sensitive = true;
            f.Owner.Receive(sensitive); f.Panel.Tick(true, false);
            Assert.Equal(new[] { "[Own submitted message]", "[Private task content]" }, f.Content);
            f.Panel.Tick(true, true);
            Assert.Equal(new[] { "Own private text", "Private authorization" }, f.Content);
        }

        [Fact]
        public void AManualScrollCancelsDelayedAutoBottomInsteadOfOverridingTheReader()
        {
            using var f = new Fixture(); f.Log.verticalScroller.highValue = f.Log.verticalScroller.value = 500;
            f.Owner.Receive(Message("New message", Now)); f.Panel.Tick(true, true);
            f.Log.verticalScroller.value = 120; f.Log.Emit(new WheelEvent());
            f.Log.schedule.RunPending(); Assert.Equal(120, f.Log.verticalScroller.value);
        }

        [Fact]
        public void RebuildRestoresReadingOffsetWithoutTakingComposerFocus()
        {
            using var f = new Fixture(); f.Log.verticalScroller.highValue = 500; f.Log.verticalScroller.value = 80;
            f.Owner.Receive(Message("New message", Now)); f.Panel.Tick(true, true);
            Assert.Equal(0, f.Log.verticalScroller.value); // Rebuild/layout reset.
            f.Log.schedule.RunPending(); Assert.Equal(80, f.Log.verticalScroller.value);
            Assert.False(f.Composer.Focused);
            Assert.True(Descendants(f.Panel.Root).OfType<Button>().Single(button => button.text == "New messages ↓").Visible);
        }

        [Fact]
        public void ScrollbarInteractionAndDisposalBothInvalidateScheduledScroll()
        {
            var f = new Fixture(); f.Log.verticalScroller.highValue = f.Log.verticalScroller.value = 500;
            f.Owner.Receive(Message("Message", Now)); f.Panel.Tick(true, true);
            f.Log.verticalScroller.value = 90; f.Log.Emit(new PointerDownEvent());
            f.Log.schedule.RunPending(); Assert.Equal(90, f.Log.verticalScroller.value);
            f.Log.verticalScroller.value = 500;
            f.Owner.Receive(Message("Next", Now + 1, 2, "next")); f.Panel.Tick(true, true);
            f.Dispose(); f.Log.verticalScroller.value = 50;
            f.Log.schedule.RunPending(); Assert.Equal(50, f.Log.verticalScroller.value);
        }
    }
}
