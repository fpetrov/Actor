using System;
using System.Collections.Generic;
using System.Linq;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using Unity.Collections;
using Unity.Netcode;
using UnityEngine;

namespace Actor.Networking
{
    // Named messages are deliberately separate from every player owner RPC.
    // Observers have no NetworkObject; only the authenticated registry can send.
    [RequireComponent(typeof(NetworkManager))]
    public sealed class BroadcastSession : MonoBehaviour
    {
        private const string ViewMessage = "actor.broadcast.view.v1", ReceiptMessage = "actor.broadcast.received.v1",
            PresentedMessage = "actor.broadcast.presented.v1", SelectMessage = "actor.broadcast.select.v1", ResultMessage = "actor.broadcast.result.v1";
        private const int MaxBytes = 49152;
        private NetworkManager manager;
        private MatchSession match;
        private bool registered;
        private readonly Dictionary<ulong, BroadcastViewProjector> projectors = new();
        private readonly Dictionary<ulong, ulong> waiting = new();
        private readonly Dictionary<ulong, double> requestAt = new();
        public BroadcastScope ExpectedScope { get; private set; }
        public BroadcastView Received { get; private set; }
        public BroadcastPresentedViewObservation Presented { get; private set; }
        private double receivedAt = -100, presentedAt = -100;
        private readonly Dictionary<string, ulong> nativeKnown = new(StringComparer.Ordinal);
        private string nativeSignature = ""; private ulong nativeSemantic;
        public bool PresentedFresh => Fresh && Presented != null && Time.realtimeSinceStartupAsDouble - presentedAt <= .5;
        public bool Fresh => Received != null && manager != null && manager.IsConnectedClient && Time.realtimeSinceStartupAsDouble - receivedAt <= .5;
        public event Action<BroadcastPresentedViewObservation> FramePresented;
        public event Action<BroadcastViewSelectionResultEvent> SelectionResult;
        public static BroadcastSession Ensure(NetworkManager value)
            => value.GetComponent<BroadcastSession>() ?? value.gameObject.AddComponent<BroadcastSession>();
        private void Awake() { manager = GetComponent<NetworkManager>(); match = GetComponent<MatchSession>(); }
        internal void Expect(BroadcastScope scope) { ExpectedScope = scope.Clone(); Received = null; Presented = null; receivedAt = -100; nativeKnown.Clear(); nativeSignature = ""; nativeSemantic = 0; }
        private void Update()
        {
            if (manager == null || manager.CustomMessagingManager == null) return;
            if (manager.IsListening && !registered)
            {
                registered = true;
                var messages = manager.CustomMessagingManager;
                messages.RegisterNamedMessageHandler(ViewMessage, ReceiveView); messages.RegisterNamedMessageHandler(ReceiptMessage, ReceiveReceipt);
                messages.RegisterNamedMessageHandler(PresentedMessage, ReceivePresented); messages.RegisterNamedMessageHandler(SelectMessage, ReceiveSelection);
                messages.RegisterNamedMessageHandler(ResultMessage, ReceiveResult); manager.OnClientDisconnectCallback += Disconnected;
            }
            if (!manager.IsListening && registered) Unregister();
            if (!Fresh || Time.realtimeSinceStartupAsDouble - presentedAt > .5) Presented = null;
        }
        private static byte[] Read(FastBufferReader reader, int limit)
        {
            if (!reader.TryBeginRead(sizeof(int))) return null;
            reader.ReadValueSafe(out int size);
            if (size < 1 || size > limit || reader.Length - reader.Position != size) return null;
            var bytes = new byte[size]; reader.ReadBytesSafe(ref bytes, size); return bytes;
        }
        private void Send(string channel, ulong recipient, IMessage value)
        {
            byte[] bytes = value.ToByteArray(); if (bytes.Length > MaxBytes) return;
            using var writer = new FastBufferWriter(bytes.Length + 4, Allocator.Temp, MaxBytes + 4);
            writer.WriteValueSafe(bytes.Length); writer.WriteBytesSafe(bytes);
            manager.CustomMessagingManager.SendNamedMessage(channel, recipient, writer, NetworkDelivery.ReliableFragmentedSequenced);
        }
        private void ReceiveView(ulong sender, FastBufferReader reader)
        {
            if (!manager.IsClient || sender != NetworkManager.ServerClientId || ExpectedScope == null || !match.IsBroadcastObserver) return;
            var bytes = Read(reader, MaxBytes); if (bytes == null) return;
            try
            {
                var value = BroadcastView.Parser.ParseFrom(bytes);
                if (!BroadcastPresentationRules.ValidView(value, ExpectedScope) || Received != null
                    && (value.Scope.ViewEpoch < Received.Scope.ViewEpoch || value.Scope.ViewEpoch == Received.Scope.ViewEpoch && value.Sequence <= Received.Sequence)) return;
                if (value.Scope.ViewEpoch != ExpectedScope.ViewEpoch) ExpectedScope = value.Scope.Clone();
                if (Received == null || Received.Scope.ViewEpoch != value.Scope.ViewEpoch) Presented = null;
                Received = value; receivedAt = Time.realtimeSinceStartupAsDouble;
                using var writer = new FastBufferWriter(8, Allocator.Temp); writer.WriteValueSafe(value.Sequence);
                manager.CustomMessagingManager.SendNamedMessage(ReceiptMessage, NetworkManager.ServerClientId, writer);
            }
            catch (InvalidProtocolBufferException) { }
        }
        private void ReceiveReceipt(ulong sender, FastBufferReader reader)
        {
            if (!manager.IsServer || !match.IsObserver(sender) || !reader.TryBeginRead(8) || reader.Length != 8) return;
            reader.ReadValueSafe(out ulong sequence);
            if (waiting.TryGetValue(sender, out ulong expected) && expected == sequence) waiting.Remove(sender);
        }
        private void ReceivePresented(ulong sender, FastBufferReader reader)
        {
            if (!manager.IsServer || !match.IsObserver(sender) || !projectors.TryGetValue(sender, out var projector)) return;
            var bytes = Read(reader, 8192); if (bytes == null) return;
            try { projector.ConfirmPresented(BroadcastPresentationAck.Parser.ParseFrom(bytes), DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()); }
            catch (InvalidProtocolBufferException) { }
        }
        private void ReceiveSelection(ulong sender, FastBufferReader reader)
        {
            if (!manager.IsServer || !match.IsObserver(sender) || !projectors.TryGetValue(sender, out var projector)) return;
            double time = Time.realtimeSinceStartupAsDouble;
            if (requestAt.TryGetValue(sender, out double previous) && time - previous < .5) return;
            requestAt[sender] = time;
            var bytes = Read(reader, 1024); if (bytes == null) return;
            try { Send(ResultMessage, sender, projector.Select(BroadcastSelectViewCommand.Parser.ParseFrom(bytes), DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())); }
            catch (InvalidProtocolBufferException) { }
        }
        private void ReceiveResult(ulong sender, FastBufferReader reader)
        {
            if (!manager.IsClient || !match.IsBroadcastObserver || sender != NetworkManager.ServerClientId) return;
            var bytes = Read(reader, 1024); if (bytes == null) return;
            try
            {
                var result = BroadcastViewSelectionResultEvent.Parser.ParseFrom(bytes);
                if (BroadcastPresentationRules.SameIdentity(result.Scope, ExpectedScope)) SelectionResult?.Invoke(result);
            }
            catch (InvalidProtocolBufferException) { }
        }
        internal void Publish(SocialMatchStateObservation state, SocialEmergencyStateObservation emergency, SocialMeetingObservation meeting,
            BroadcastViewProjector.Actor[] actors, BroadcastViewProjector.Body[] bodies, SocialMatchSummary summary, long now)
        {
            if (!manager.IsServer || !registered) return;
            foreach (var binding in match.ObserverBindings)
            {
                if (!manager.ConnectedClients.ContainsKey(binding.Client)) continue;
                if (!projectors.TryGetValue(binding.Client, out var projector))
                    projectors[binding.Client] = projector = new BroadcastViewProjector(binding.Scope, (x, y, tx, ty) => SocialVisibilitySystem.LineOfSight(new Vector2(x, y), new Vector2(tx, ty)), now);
                if (waiting.ContainsKey(binding.Client)) continue; // one wire frame in flight, never grow a reliable queue.
                var view = projector.Build(state, emergency, meeting, actors, bodies, summary, now);
                if (view.CalculateSize() > MaxBytes) continue;
                waiting[binding.Client] = view.Sequence; Send(ViewMessage, binding.Client, view);
            }
        }
        internal ulong[] AttackWitnesses(float ax, float ay, float vx, float vy, string attacker, string victim, long now)
            => projectors.Where(p => p.Value.Witnesses(attacker, victim, ax, ay, vx, vy, now)).Select(p => p.Key).ToArray();
        internal void AttackCompleted(ulong[] witnesses, string attacker, string victim, string bodyId, long now)
        {
            string id = bodyId;
            foreach (ulong client in witnesses) if (projectors.TryGetValue(client, out var p)) p.Note("attack-witnessed", id, attacker, victim, "", "", now, 0, bodyId);
        }
        internal void VisibleActivity(float x, float y, string actor, string terminal, string activity, long now)
        {
            string id = Guid.NewGuid().ToString("N");
            foreach (var p in projectors.Values) if (p.SeenActorRecently(actor, now) && p.Permits(x, y)) p.Note("activity-seen", id, actor, "", "", "", now, 2, activity);
        }
        internal void PublicDiscussion(string id, string actor, string name, string text, long now)
        { foreach (var p in projectors.Values) p.Note("discussion-claim", id, actor, "", text, "", now, 2, name); }
        public bool Select(BroadcastSelectViewCommand command)
        {
            if (!Fresh || command == null || command.CalculateSize() > 1024 || !BroadcastObserverRules.SameScope(command.Scope, Received.Scope)) return false;
            Send(SelectMessage, NetworkManager.ServerClientId, command); return true;
        }
        internal bool MarkPresented(BroadcastView frame, IEnumerable<BroadcastEvidenceRef> references, ulong frameCounter, int width, int height)
        {
            if (!Fresh || !BroadcastPresentationRules.SameIdentity(frame?.Scope, ExpectedScope) || Received.Sequence != frame.Sequence || !Received.Scope.Equals(frame.Scope)
                || Application.isBatchMode || SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null) return false;
            var ack = new BroadcastPresentationAck { Scope = frame.Scope.Clone(), RenderedSequence = frame.Sequence, FrameCounter = frameCounter,
                PresentedAtMonotonicMs = Time.realtimeSinceStartupAsDouble * 1000, Width = (uint)width, Height = (uint)height };
            ack.PresentedEvidence.Add(references.Select(r => r.Clone()));
            if (!BroadcastPresentationRules.CanAcknowledge(frame, ack, false)) return false;
            var cognitive = BroadcastPresentationRules.ForCognition(frame, ack, nativeKnown);
            string signature = BroadcastPresentationRules.SemanticSignature(cognitive);
            if (signature != nativeSignature) { nativeSignature = signature; ++nativeSemantic; }
            cognitive.SemanticRevision = nativeSemantic;
            foreach (var reference in ack.PresentedEvidence) nativeKnown[reference.EvidenceId] = reference.Version;
            foreach (string id in nativeKnown.Keys.Where(id => !frame.Facts.Any(f => f.EvidenceId == id) && !frame.RevokedEvidenceIds.Contains(id)).ToArray()) nativeKnown.Remove(id);
            presentedAt = Time.realtimeSinceStartupAsDouble;
            Presented = new BroadcastPresentedViewObservation { View = cognitive, Presentation = ack };
            Send(PresentedMessage, NetworkManager.ServerClientId, ack); FramePresented?.Invoke(Presented.Clone()); return true;
        }
        private void Disconnected(ulong client)
        { projectors.Remove(client); waiting.Remove(client); requestAt.Remove(client); if (manager.IsClient && client == manager.LocalClientId) { Received = null; Presented = null; ExpectedScope = null; } }
        private void Unregister()
        {
            if (!registered) return; registered = false;
            if (manager != null) { manager.OnClientDisconnectCallback -= Disconnected; var messages = manager.CustomMessagingManager;
                if (messages != null) foreach (var name in new[] { ViewMessage, ReceiptMessage, PresentedMessage, SelectMessage, ResultMessage }) messages.UnregisterNamedMessageHandler(name); }
            projectors.Clear(); waiting.Clear(); requestAt.Clear(); Received = null; Presented = null;
        }
        private void OnDestroy() => Unregister();
    }
}
