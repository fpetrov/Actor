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
        private sealed class Transport
        {
            internal readonly BroadcastDeliveryWindow Window = new();
            internal double LastSentAt = -1;
        }
        private readonly Dictionary<ulong, Transport> transports = new();
        private readonly Dictionary<ulong, double> requestAt = new();
        public BroadcastScope ExpectedScope { get; private set; }
        public BroadcastView Received { get; private set; }
        public BroadcastPresentedViewObservation Presented { get; private set; }
        private double receivedAt = -100, presentedAt = -100;
        private readonly Dictionary<string, ulong> nativeKnown = new(StringComparer.Ordinal);
        private string nativeSignature = ""; private ulong nativeSemantic;
        private readonly Samples buildMs = new(), serializeMs = new(), sendIntervalsMs = new(), receiptRttMs = new(),
            receiveIntervalsMs = new(), sourceIntervalsMs = new(), presentedIntervalsMs = new(), receiveToPresentedMs = new();
        private long observerTicks, zeroObserverTicks, viewsBuilt, viewsSent, payloadBytes, windowFull, oversize, sendFailures, receiptsAccepted, receiptsRejected, retiredFrames,
            serverPresentedAccepted, serverPresentedRejected, clientMessages, clientAccepted, clientRejected, clientPresented;
        private int windowPeak;
        private double firstReceivedAt = -1, lastReceivedAt = -1, firstPresentedAt = -1, lastPresentedAt = -1;
        // Numeric-only on-demand diagnostics; no per-frame sorting or file I/O.
        // Negative timing/rate values with count zero mean unobserved, not zero latency.
        [Serializable] internal sealed class Distribution
        {
            public long count_all;
            public int count_window;
            public double p50_ms = -1, p95_ms = -1, max_window_ms = -1, max_all_ms = -1;
        }
        private sealed class Samples
        {
            private readonly double[] values = new double[1024];
            private long count;
            private double maximum = -1;
            internal void Add(double value)
            {
                if (!double.IsFinite(value) || value < 0) return;
                values[(int)(count++ % values.Length)] = value; maximum = Math.Max(maximum, value);
            }
            internal Distribution Snapshot()
            {
                int length = (int)Math.Min(count, values.Length);
                var result = new Distribution { count_all = count, count_window = length, max_all_ms = maximum };
                if (length == 0) return result;
                var sorted = new double[length]; Array.Copy(values, sorted, length); Array.Sort(sorted);
                result.p50_ms = sorted[Math.Max(0, (int)Math.Ceiling(length * .5) - 1)];
                result.p95_ms = sorted[Math.Max(0, (int)Math.Ceiling(length * .95) - 1)]; result.max_window_ms = sorted[length - 1];
                return result;
            }
        }
        [Serializable] internal sealed class MotionSnapshot
        {
            public long server_observer_due_ticks, server_no_observer_ticks, server_views_built, server_views_sent, server_view_protobuf_bytes, server_window_full_skips, server_oversize,
                server_send_failures, server_receipts_accepted, server_receipts_rejected, server_frames_retired,
                server_presented_accepted, server_presented_rejected, client_view_messages, client_views_accepted, client_views_rejected, client_presented;
            public int server_inflight_current_total, server_inflight_current_max, server_window_peak_per_transport;
            public double client_receive_span_seconds = -1, client_receive_mean_hz = -1, client_presented_span_seconds = -1, client_presented_mean_hz = -1;
            public Distribution server_build_ms, server_serialize_ms, server_send_interval_ms, server_receipt_rtt_ms,
                client_receive_interval_ms, client_source_observed_interval_ms, client_presented_interval_ms, client_receive_to_presented_ms;
        }
        internal MotionSnapshot MotionDiagnostics()
        {
            int total = 0, largest = 0;
            foreach (var transport in transports.Values) { total += transport.Window.Count; largest = Math.Max(largest, transport.Window.Count); }
            double receiveSpan = clientAccepted > 1 ? lastReceivedAt - firstReceivedAt : -1;
            double presentedSpan = clientPresented > 1 ? lastPresentedAt - firstPresentedAt : -1;
            return new MotionSnapshot { server_observer_due_ticks = observerTicks, server_no_observer_ticks = zeroObserverTicks,
                server_views_built = viewsBuilt, server_views_sent = viewsSent, server_view_protobuf_bytes = payloadBytes,
                server_window_full_skips = windowFull, server_oversize = oversize, server_send_failures = sendFailures,
                server_receipts_accepted = receiptsAccepted, server_receipts_rejected = receiptsRejected, server_frames_retired = retiredFrames,
                server_presented_accepted = serverPresentedAccepted, server_presented_rejected = serverPresentedRejected,
                client_view_messages = clientMessages, client_views_accepted = clientAccepted, client_views_rejected = clientRejected, client_presented = clientPresented,
                server_inflight_current_total = total, server_inflight_current_max = largest, server_window_peak_per_transport = windowPeak,
                client_receive_span_seconds = receiveSpan, client_receive_mean_hz = receiveSpan > 0 ? (clientAccepted - 1) / receiveSpan : -1,
                client_presented_span_seconds = presentedSpan, client_presented_mean_hz = presentedSpan > 0 ? (clientPresented - 1) / presentedSpan : -1,
                server_build_ms = buildMs.Snapshot(), server_serialize_ms = serializeMs.Snapshot(), server_send_interval_ms = sendIntervalsMs.Snapshot(),
                server_receipt_rtt_ms = receiptRttMs.Snapshot(), client_receive_interval_ms = receiveIntervalsMs.Snapshot(),
                client_source_observed_interval_ms = sourceIntervalsMs.Snapshot(), client_presented_interval_ms = presentedIntervalsMs.Snapshot(),
                client_receive_to_presented_ms = receiveToPresentedMs.Snapshot() };
        }
        internal bool HasDeliveryCapacity
        {
            get => AvailableDelivery(false);
        }
        internal bool BeginObserverTick() => AvailableDelivery(true);
        private bool AvailableDelivery(bool recordTick)
        {
            if (manager == null || !manager.IsServer || !registered || match == null) return false;
            bool available = false; int connected = 0;
            foreach (var binding in match.ObserverBindings)
            {
                if (!manager.ConnectedClients.ContainsKey(binding.Client)) continue;
                ++connected;
                if (transports.TryGetValue(binding.Client, out var transport) && !transport.Window.CanSend)
                { if (recordTick) ++windowFull; }
                else available = true;
            }
            if (recordTick) { ++observerTicks; if (connected == 0) ++zeroObserverTicks; }
            return available;
        }
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
            ++clientMessages;
            var bytes = Read(reader, MaxBytes); if (bytes == null) { ++clientRejected; return; }
            try
            {
                var value = BroadcastView.Parser.ParseFrom(bytes);
                if (!BroadcastPresentationRules.ValidView(value, ExpectedScope) || Received != null
                    && (value.Scope.ViewEpoch < Received.Scope.ViewEpoch || value.Scope.ViewEpoch == Received.Scope.ViewEpoch && value.Sequence <= Received.Sequence)) { ++clientRejected; return; }
                double arrivedAt = Time.realtimeSinceStartupAsDouble;
                if (lastReceivedAt >= 0) receiveIntervalsMs.Add((arrivedAt - lastReceivedAt) * 1000);
                if (Received != null) sourceIntervalsMs.Add(value.ObservedAtUnixMs - Received.ObservedAtUnixMs);
                if (firstReceivedAt < 0) firstReceivedAt = arrivedAt;
                lastReceivedAt = arrivedAt; ++clientAccepted;
                if (value.Scope.ViewEpoch != ExpectedScope.ViewEpoch) ExpectedScope = value.Scope.Clone();
                if (Received == null || Received.Scope.ViewEpoch != value.Scope.ViewEpoch) Presented = null;
                Received = value; receivedAt = arrivedAt;
                using var writer = new FastBufferWriter(8, Allocator.Temp); writer.WriteValueSafe(value.Sequence);
                manager.CustomMessagingManager.SendNamedMessage(ReceiptMessage, NetworkManager.ServerClientId, writer);
            }
            catch (InvalidProtocolBufferException) { ++clientRejected; }
        }
        private void ReceiveReceipt(ulong sender, FastBufferReader reader)
        {
            // NGO's named-message reader retains the already-consumed hash
            // prefix. Validate the unread receipt, not the entire buffer.
            if (!manager.IsServer || !match.IsObserver(sender)) return;
            if (reader.Length - reader.Position != sizeof(ulong) || !reader.TryBeginRead(sizeof(ulong))) { ++receiptsRejected; return; }
            reader.ReadValueSafe(out ulong sequence);
            // Receipt frees transport ownership only. It never calls the
            // projector's presented-fact commit or changes a camera epoch.
            if (transports.TryGetValue(sender, out var transport)
                && transport.Window.TryAcknowledge(sequence, Time.realtimeSinceStartupAsDouble, out int retired, out double rtt))
            { ++receiptsAccepted; retiredFrames += retired; receiptRttMs.Add(rtt * 1000); }
            else ++receiptsRejected;
        }
        private void ReceivePresented(ulong sender, FastBufferReader reader)
        {
            if (!manager.IsServer || !match.IsObserver(sender) || !projectors.TryGetValue(sender, out var projector)) return;
            var bytes = Read(reader, 8192); if (bytes == null) { ++serverPresentedRejected; return; }
            try
            {
                if (projector.ConfirmPresented(BroadcastPresentationAck.Parser.ParseFrom(bytes), DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())) ++serverPresentedAccepted;
                else ++serverPresentedRejected;
            }
            catch (InvalidProtocolBufferException) { ++serverPresentedRejected; }
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
            if (!HasDeliveryCapacity) return;
            foreach (var binding in match.ObserverBindings)
            {
                if (!manager.ConnectedClients.ContainsKey(binding.Client)) continue;
                if (!transports.TryGetValue(binding.Client, out var transport)) transports[binding.Client] = transport = new Transport();
                if (!transport.Window.CanSend) continue;
                if (!projectors.TryGetValue(binding.Client, out var projector))
                    projectors[binding.Client] = projector = new BroadcastViewProjector(binding.Scope, (x, y, tx, ty) => SocialVisibilitySystem.LineOfSight(new Vector2(x, y), new Vector2(tx, ty)), now);
                long buildStarted = System.Diagnostics.Stopwatch.GetTimestamp();
                var view = projector.Build(state, emergency, meeting, actors, bodies, summary, now);
                buildMs.Add((System.Diagnostics.Stopwatch.GetTimestamp() - buildStarted) * 1000.0 / System.Diagnostics.Stopwatch.Frequency); ++viewsBuilt;
                long serializeStarted = System.Diagnostics.Stopwatch.GetTimestamp();
                if (view.CalculateSize() > MaxBytes) { ++oversize; continue; }
                byte[] bytes = view.ToByteArray();
                serializeMs.Add((System.Diagnostics.Stopwatch.GetTimestamp() - serializeStarted) * 1000.0 / System.Diagnostics.Stopwatch.Frequency);
                if (bytes.Length < 1 || bytes.Length > MaxBytes) { ++oversize; continue; }
                using var writer = new FastBufferWriter(bytes.Length + 4, Allocator.Temp, MaxBytes + 4);
                writer.WriteValueSafe(bytes.Length); writer.WriteBytesSafe(bytes);
                double queuedAt = Time.realtimeSinceStartupAsDouble;
                if (!transport.Window.RecordSent(view.Sequence, queuedAt)) continue;
                windowPeak = Math.Max(windowPeak, transport.Window.PeakCount);
                try { manager.CustomMessagingManager.SendNamedMessage(ViewMessage, binding.Client, writer, NetworkDelivery.ReliableFragmentedSequenced); }
                catch { ++sendFailures; throw; } // Keep the reserved slot: delivery after a thrown send is unknown.
                if (transport.LastSentAt >= 0) sendIntervalsMs.Add((queuedAt - transport.LastSentAt) * 1000);
                transport.LastSentAt = queuedAt; ++viewsSent; payloadBytes += bytes.Length;
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
            if (lastPresentedAt >= 0) presentedIntervalsMs.Add((presentedAt - lastPresentedAt) * 1000);
            if (firstPresentedAt < 0) firstPresentedAt = presentedAt;
            lastPresentedAt = presentedAt; ++clientPresented;
            receiveToPresentedMs.Add((presentedAt - receivedAt) * 1000);
            Presented = new BroadcastPresentedViewObservation { View = cognitive, Presentation = ack };
            Send(PresentedMessage, NetworkManager.ServerClientId, ack); FramePresented?.Invoke(Presented.Clone()); return true;
        }
        private void Disconnected(ulong client)
        { projectors.Remove(client); transports.Remove(client); requestAt.Remove(client); if (manager.IsClient && client == manager.LocalClientId) { Received = null; Presented = null; ExpectedScope = null; } }
        private void Unregister()
        {
            if (!registered) return; registered = false;
            if (manager != null) { manager.OnClientDisconnectCallback -= Disconnected; var messages = manager.CustomMessagingManager;
                if (messages != null) foreach (var name in new[] { ViewMessage, ReceiptMessage, PresentedMessage, SelectMessage, ResultMessage }) messages.UnregisterNamedMessageHandler(name); }
            projectors.Clear(); transports.Clear(); requestAt.Clear(); Received = null; Presented = null;
        }
        private void OnDestroy() => Unregister();
    }
}
