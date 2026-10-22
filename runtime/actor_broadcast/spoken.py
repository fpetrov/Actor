"""Bounded playback history. Generated or synthesized text is never coverage."""
from collections import OrderedDict, deque
from dataclasses import dataclass
import hashlib

from .contracts import EvidenceRef, PlaybackAck, SpeechSegment


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
        # Only actual completed idle audio enters this broadcast-scoped ring.
        # Reactive coverage must not forbid one later historical callback.
        self.idle_topics = OrderedDict()
        self._idle_declarations = OrderedDict()
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
        self._promote_idle((ack.scope,ack.key.utterance_id,ack.key.utterance_epoch))
        return True

    def declare_idle_complete(self, scope, utterance_id, epoch, expected_segments, topic, evidence_refs):
        """A completed provider declares its exact segment count, not playback.

        Pending declarations never count as coverage. Every expected registered
        clause must independently receive an actual completed playback ACK.
        """
        if type(expected_segments)is not int or not 1<=expected_segments<=4:
            raise ValueError("A bounded idle segment declaration is required")
        references=frozenset(evidence_refs)
        if not 1<=len(references)<=8 or not all(isinstance(ref,EvidenceRef) for ref in references):
            raise ValueError("Exact bounded idle evidence is required")
        identity=(scope,utterance_id,epoch)
        self._idle_declarations[identity]=(expected_segments,topic,references)
        self._idle_declarations.move_to_end(identity)
        while len(self._idle_declarations)>128:self._idle_declarations.popitem(last=False)
        self._promote_idle(identity)

    def _idle_rows(self,identity):
        return [row for (scope,key),row in self.records.items()
            if (scope,key.utterance_id,key.utterance_epoch)==identity]

    def _promote_idle(self,identity):
        declaration=self._idle_declarations.get(identity)
        if declaration is None:return
        count,topic,references=declaration;rows=self._idle_rows(identity)
        if (len(rows)!=count or {row.segment.key.segment_index for row in rows}!=set(range(count))
                or any(row.mode not in ("idle_recap","memory_callback") or row.topic_key!=topic or row.status=="interrupted" for row in rows)):
            self._idle_declarations.pop(identity,None);return
        if not all(row.status=="completed" for row in rows):return
        self._idle_declarations.pop(identity,None)
        if {ref for row in rows for ref in row.segment.evidence_versions}!=references:return
        self.idle_topics[topic]=None;self.idle_topics.move_to_end(topic)
        while len(self.idle_topics)>128:self.idle_topics.popitem(last=False)

    def interrupted(self, scope, utterance_id, epoch, now):
        self._idle_declarations.pop((scope,utterance_id,epoch),None)
        for (record_scope, key), row in self.records.items():
            if record_scope == scope and key.utterance_id == utterance_id and key.utterance_epoch == epoch and row.status not in ("completed", "interrupted"):
                row.status = "interrupted"; row.completed_at = now

    def covered(self, ref, now, repeat_seconds=120):
        return ref in self.coverage and now - self.coverage[ref] < repeat_seconds

    def topic_recent(self, topic, now, repeat_seconds=120):
        return topic in self.topics and now - self.topics[topic] < repeat_seconds

    def idle_topic_completed(self, topic):
        return topic in self.idle_topics

    def prune(self, evidence):
        for key,row in tuple(self.records.items()):
            if row.segment.evidence_versions and not evidence.valid(row.segment.evidence_versions):
                self.records.pop(key)
        for ref in tuple(self.coverage):
            if evidence.resolve(ref) is None:self.coverage.pop(ref)
        for identity,(count,_,_) in tuple(self._idle_declarations.items()):
            if len(self._idle_rows(identity))!=count:self._idle_declarations.pop(identity,None)

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
