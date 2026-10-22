using System;

namespace Actor.Networking
{
    // Transport receipts only. A receipt must never commit presented facts.
    // One instance belongs to one authenticated client transport, not a camera
    // epoch. Dispose that instance on disconnect; never reset it on view change.
    // Called on the Unity main thread; fixed arrays allocate nothing per frame.
    internal sealed class BroadcastDeliveryWindow
    {
        internal const int Capacity = 8;
        private readonly ulong[] sequences = new ulong[Capacity];
        private readonly double[] sentAt = new double[Capacity];
        private int count, peak;
        private ulong lastSent;
        private double lastEventAt = -1;

        internal int Count => count;
        internal int PeakCount => peak;
        internal bool CanSend => count < Capacity;
        internal ulong LastSentSequence => lastSent;

        // The caller checks size/serialization before reserving this slot, then
        // queues the synchronous send. An unknown send failure retains ownership
        // until a real receipt or transport cleanup, never a guessed timeout.
        internal bool RecordSent(ulong sequence, double monotonicAt)
        {
            if (!CanSend || sequence == 0 || sequence <= lastSent || !ValidTime(monotonicAt)) return false;
            sequences[count] = sequence; sentAt[count] = monotonicAt;
            count++; peak = Math.Max(peak, count); lastSent = sequence; lastEventAt = monotonicAt;
            return true;
        }

        internal bool TryAcknowledge(ulong sequence, double monotonicAt, out int retired, out double roundTripSeconds)
        {
            retired = 0; roundTripSeconds = double.NaN;
            if (sequence == 0 || !ValidTime(monotonicAt)) return false;
            int index = -1;
            for (int i = 0; i < count; i++)
                if (sequences[i] == sequence) { index = i; break; }
            // A future, skipped, duplicate or already cumulatively retired
            // sequence is not a receipt for any outstanding wire frame.
            if (index < 0) return false;
            roundTripSeconds = monotonicAt - sentAt[index];
            retired = index + 1;
            int remaining = count - retired;
            Array.Copy(sequences, retired, sequences, 0, remaining);
            Array.Copy(sentAt, retired, sentAt, 0, remaining);
            Array.Clear(sequences, remaining, retired); Array.Clear(sentAt, remaining, retired);
            count = remaining; lastEventAt = monotonicAt;
            return true;
        }

        internal double? OldestPendingAge(double monotonicAt)
            => count > 0 && ValidTime(monotonicAt) ? monotonicAt - sentAt[0] : (double?)null;

        private bool ValidTime(double value) => double.IsFinite(value) && value >= 0 && value >= lastEventAt;
        // Deliberately no TTL eviction: an old unacknowledged frame still owns
        // reliable transport capacity even after its presentation cache expires.
    }

    internal readonly struct BroadcastTicks
    {
        internal readonly bool Authority, Observer;
        internal BroadcastTicks(bool authority, bool observer) { Authority = authority; Observer = observer; }
    }

    // Both schedules are evaluated together, so an authority timer which is not
    // due cannot accidentally prevent a due observer-only sample. Event handlers
    // update state; the owner calls Take once per Update to coalesce that state.
    internal sealed class BroadcastDeliveryCadence
    {
        internal const double AuthorityInterval = .1;
        internal const double ObserverInterval = 1.0 / 30;
        private bool initialized;
        private double lastAt = -1, nextAuthority, nextObserver;
        internal double NextAuthorityAt => nextAuthority;
        internal double NextObserverAt => nextObserver;

        internal BroadcastTicks Take(double monotonicAt)
        {
            if (!double.IsFinite(monotonicAt) || monotonicAt < 0 || monotonicAt < lastAt
                || !(monotonicAt + ObserverInterval > monotonicAt)
                || !double.IsFinite(monotonicAt + AuthorityInterval)) return default;
            lastAt = monotonicAt;
            if (!initialized)
            {
                initialized = true;
                nextAuthority = monotonicAt + AuthorityInterval; nextObserver = monotonicAt + ObserverInterval;
                return new BroadcastTicks(true, true);
            }
            bool authority = TakeDue(monotonicAt, AuthorityInterval, ref nextAuthority);
            bool observer = TakeDue(monotonicAt, ObserverInterval, ref nextObserver);
            return new BroadcastTicks(authority, observer);
        }

        private static bool TakeDue(double now, double interval, ref double deadline)
        {
            if (now < deadline) return false;
            // Advance past all missed slots, but emit just one current tick.
            // Retaining phase avoids adding a render-frame delay every period.
            double next = deadline + (Math.Floor((now - deadline) / interval) + 1) * interval;
            deadline = double.IsFinite(next) && next > now ? next : now + interval;
            return true;
        }
    }
}
