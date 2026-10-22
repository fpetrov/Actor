"""Bounded playback history. Generated or synthesized text is never coverage."""
from collections import OrderedDict, deque
from dataclasses import dataclass
import hashlib

from .contracts import PlaybackAck, SpeechSegment


@dataclass
class SpokenRecord:
    segment: SpeechSegment
    topic_key: str
    mode: str
    text_hash: str
    status: str = "pending"
    consumed_source_frames: int = 0
    total_source_frames: int = 0
    completed_at: float | None = None


class SpokenLedger:
    def __init__(self, *, capacity=64, max_bytes=32768):
        self.capacity, self.max_bytes = capacity, max_bytes
        self.records = OrderedDict()
        self.coverage = OrderedDict()
        self.topics = OrderedDict()
        self.audible_seconds = 0.0

    def register(self, segment, *, topic_key, mode):
        key = (segment.scope, segment.key)
        if key in self.records: return False
        record = SpokenRecord(segment, topic_key, mode, hashlib.sha256(segment.text.encode()).hexdigest())
        self.records[key] = record
        while (len(self.records) > self.capacity or
               sum(len(row.segment.text.encode()) for row in self.records.values()) > self.max_bytes):
            self.records.popitem(last=False)
        return key in self.records

    def acknowledge(self, ack: PlaybackAck):
        record = self.records.get((ack.scope, ack.key))
        if record is None or record.status in ("completed", "interrupted"):
            return False
        if ack.consumed_source_frames < record.consumed_source_frames:
            return False
        # Streaming audio does not know its final duration at the first ACK.
        # The sink advertises frames sent so far; that bound may only grow.
        if ack.total_source_frames < record.total_source_frames:
            return False
        record.total_source_frames = ack.total_source_frames
        self.audible_seconds += (ack.consumed_source_frames - record.consumed_source_frames) / 24000
        record.consumed_source_frames = ack.consumed_source_frames
        record.status = ack.status
        if ack.status in ("completed", "interrupted"):
            record.completed_at = ack.received_at_monotonic
        if ack.status == "completed":
            for ref in record.segment.evidence_versions:
                self.coverage[ref] = ack.received_at_monotonic
                self.coverage.move_to_end(ref)
            self.topics[record.topic_key] = ack.received_at_monotonic
            self.topics.move_to_end(record.topic_key)
            while len(self.coverage) > 256: self.coverage.popitem(last=False)
            while len(self.topics) > 128: self.topics.popitem(last=False)
        return True

    def interrupted(self, scope, utterance_id, epoch, now):
        for (record_scope, key), row in self.records.items():
            if record_scope == scope and key.utterance_id == utterance_id and key.utterance_epoch == epoch and row.status not in ("completed", "interrupted"):
                row.status = "interrupted"; row.completed_at = now

    def covered(self, ref, now, repeat_seconds=120):
        return ref in self.coverage and now - self.coverage[ref] < repeat_seconds

    def topic_recent(self, topic, now, repeat_seconds=120):
        return topic in self.topics and now - self.topics[topic] < repeat_seconds

    def prune(self, evidence):
        for key,row in tuple(self.records.items()):
            if row.segment.evidence_versions and not evidence.valid(row.segment.evidence_versions):
                self.records.pop(key)
        for ref in tuple(self.coverage):
            if evidence.resolve(ref) is None:self.coverage.pop(ref)

    def recent(self, limit=2):
        # A partial sentence has no word alignment. It is not represented as
        # an exact heard text prefix in a subsequent model context.
        return [{"text": row.segment.text, "mode": row.mode, "topic": row.topic_key,
                 "completed_at_monotonic": row.completed_at}
                for row in reversed(self.records.values()) if row.status == "completed"][:limit]

    def report(self):
        latest=self.recent(1)
        return {"segments": len(self.records), "completed": sum(row.status == "completed" for row in self.records.values()),
            "interrupted": sum(row.status == "interrupted" for row in self.records.values()),
                "covered_versions": len(self.coverage), "audible_seconds": self.audible_seconds,
                "last_completed":latest[0] if latest else None}
