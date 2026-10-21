"""Bounded private working knowledge across episodes, never durable game truth.

Installed policies supply projections of legitimately received payloads. This
store does not infer hidden state, summarize conversations or refresh history.
"""
from collections import OrderedDict, Counter
import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .errors import ActorError


class KnowledgeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=256)
    scope_id: str = Field(min_length=1, max_length=128)
    kind: Literal["historical_observation", "player_claim", "hypothesis"]
    source_event_id: str = Field(default="", max_length=512)
    capability_id: str = Field(default="", max_length=256)
    source_actor_id: str = Field(default="", max_length=128)
    sequence: int = Field(default=0, ge=0)
    revision: int = Field(default=0, ge=0)
    observed_at_unix_ms: int = Field(gt=0)
    expires_at_unix_ms: int = Field(gt=0)
    content: dict[str, JsonValue]
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)
    confidence: float | None = Field(default=None, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    data_policy: Literal["session_only"] = "session_only"


class WorkingKnowledge:
    def __init__(self, scope_id, *, max_entries=64, max_record_bytes=2048, max_total_bytes=32768, clock=time.time):
        if (not isinstance(scope_id, str) or not 1 <= len(scope_id) <= 128 or
                type(max_entries) is not int or not 1 <= max_entries <= 512 or
                type(max_record_bytes) is not int or not 256 <= max_record_bytes <= 8192 or
                type(max_total_bytes) is not int or not max_record_bytes <= max_total_bytes <= 262144):
            raise ValueError("Working knowledge requires bounded scope and storage")
        self.scope_id, self.max_entries = scope_id, max_entries
        self.max_record_bytes, self.max_total_bytes, self.clock = max_record_bytes, max_total_bytes, clock
        self.records = OrderedDict()
        self.dropped = self.expired = 0

    @staticmethod
    def _size(record):
        return len(record.model_dump_json().encode("utf-8"))

    def _prune(self):
        now = int(self.clock() * 1000)
        for key, value in tuple(self.records.items()):
            if value.expires_at_unix_ms <= now:
                self.records.pop(key)
                self.expired += 1

    def remember(self, record):
        record = KnowledgeRecord.model_validate(record, strict=True)
        if record.scope_id != self.scope_id:
            raise ActorError("knowledge_scope_mismatch", "Working evidence belongs to another participation")
        self._prune()
        if record.expires_at_unix_ms <= int(self.clock() * 1000):
            return False
        if self._size(record) > self.max_record_bytes:
            self.dropped += 1
            return False
        previous = self.records.get(record.id)
        if previous is not None and (record.sequence, record.revision) <= (previous.sequence, previous.revision):
            return False
        self.records[record.id] = record.model_copy(deep=True)
        self.records.move_to_end(record.id)
        while len(self.records) > self.max_entries or sum(map(self._size, self.records.values())) > self.max_total_bytes:
            victim = min(self.records, key=lambda key: (self.records[key].importance,
                                                       self.records[key].observed_at_unix_ms))
            self.records.pop(victim)
            self.dropped += 1
        return record.id in self.records

    def hypothesize(self, statement, confidence, evidence_refs):
        self._prune()
        refs = list(dict.fromkeys(evidence_refs))
        if not refs or any(ref not in self.records for ref in refs):
            raise ActorError("hypothesis_evidence_missing", "A hypothesis must cite retained evidence available to this participant")
        now = int(self.clock() * 1000)
        record = KnowledgeRecord(id=uuid4().hex, scope_id=self.scope_id, kind="hypothesis",
            observed_at_unix_ms=now, expires_at_unix_ms=min(self.records[ref].expires_at_unix_ms for ref in refs),
            content={"statement": statement, "epistemic_status": "unverified_hypothesis"},
            confidence=confidence, evidence_refs=refs, importance=.65)
        if not self.remember(record):
            raise ActorError("knowledge_capacity", "Hypothesis exceeds bounded working storage")
        return record.id

    def context(self):
        self._prune()
        return [record.model_dump(mode="json") for record in sorted(self.records.values(),
            key=lambda record: (record.importance, record.observed_at_unix_ms, record.id), reverse=True)]

    def report(self):
        self._prune()
        return {"entries": len(self.records), "kinds": dict(Counter(record.kind for record in self.records.values())),
                "bytes": sum(map(self._size, self.records.values())), "dropped": self.dropped, "expired": self.expired}
