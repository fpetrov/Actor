"""Bounded private working knowledge across episodes, never durable game truth.

Installed policies supply projections of legitimately received payloads. This
store does not infer hidden state, summarize conversations or refresh history.
"""
from collections import OrderedDict, Counter
import hashlib
import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .errors import ActorError


class KnowledgeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=256)
    scope_id: str = Field(min_length=1, max_length=128)
    kind: Literal["historical_observation", "player_claim", "hypothesis", "contradiction"]
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
        self._prune_orphans()

    def _prune_orphans(self):
        # A model note never outlives the actual evidence it cites. Cascades
        # also handle a hypothesis citing another bounded note.
        while True:
            missing = [key for key, value in self.records.items()
                       if value.evidence_refs and any(ref not in self.records for ref in value.evidence_refs)]
            if not missing:
                return
            for key in missing:
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
        if previous is not None:
            # Stable latest-value IDs may advance, but direct dependants must
            # never silently acquire different evidence under the same name.
            self.records.pop(record.id)
            self._prune_orphans()
        self.records[record.id] = record.model_copy(deep=True)
        self.records.move_to_end(record.id)
        while len(self.records) > self.max_entries or sum(map(self._size, self.records.values())) > self.max_total_bytes:
            referenced = {ref for value in self.records.values() for ref in value.evidence_refs}
            # Once its note is evicted, a frozen copy must not monopolise
            # storage merely because it had protected citation priority.
            victim = min(self.records, key=lambda key: (0 if key != record.id and
                key.startswith("evidence-snapshot:") and key not in referenced else self.records[key].importance,
                                                       self.records[key].observed_at_unix_ms))
            self.records.pop(victim)
            self.dropped += 1
            self._prune_orphans()
        return record.id in self.records

    def hypothesize(self, statement, confidence, evidence_refs):
        return self._note("hypothesis", statement, confidence, evidence_refs, minimum=1)

    def contradict(self, statement, confidence, evidence_refs):
        """Record a model-noticed incompatibility, never decide which source lies."""
        return self._note("contradiction", statement, confidence, evidence_refs, minimum=2)

    def _note(self, kind, statement, confidence, evidence_refs, *, minimum):
        self._prune()
        refs = list(dict.fromkeys(evidence_refs))
        if len(refs) < minimum or len(refs) > 8 or any(ref not in self.records for ref in refs):
            raise ActorError("hypothesis_evidence_missing", "A hypothesis must cite retained evidence available to this participant")
        if not isinstance(statement, str) or not 1 <= len(statement.strip()) <= 512:
            raise ActorError("knowledge_note_invalid", "A working note must be a concise bounded statement")
        frozen_refs, snapshots = [], {}
        for ref in refs:
            source = self.records[ref]
            if source.kind in ("historical_observation", "player_claim") and not ref.startswith("evidence-snapshot:"):
                # Copy the exact personally available version being cited.
                # Its original provenance/time/expiry remain unchanged while
                # a live latest-value slot can continue receiving new facts.
                identity = "evidence-snapshot:" + hashlib.sha256(source.model_dump_json().encode("utf-8")).hexdigest()
                if identity not in self.records:
                    snapshots[identity] = source.model_copy(deep=True, update={"id": identity,
                        "importance": max(.66, source.importance)})
                ref = identity
            frozen_refs.append(ref)
        refs = list(dict.fromkeys(frozen_refs))
        if len(refs) < minimum:
            raise ActorError("hypothesis_evidence_missing", "A note requires distinct retained evidence versions")
        sources = {**self.records, **snapshots}
        now = int(self.clock() * 1000)
        record = KnowledgeRecord(id=uuid4().hex, scope_id=self.scope_id, kind=kind,
            observed_at_unix_ms=now, expires_at_unix_ms=min(sources[ref].expires_at_unix_ms for ref in refs),
            content={"statement": statement, "epistemic_status": "unresolved_incompatibility" if kind == "contradiction" else "unverified_hypothesis",
                     "source_verification": "not_performed"},
            confidence=confidence, evidence_refs=refs, importance=.65)
        self._install_note(record, snapshots)
        return record.id

    def _install_note(self, note, snapshots):
        """Install one note and its exact evidence as one bounded transaction."""
        additions = [*snapshots.values(), note]
        if any(self._size(record) > self.max_record_bytes for record in additions):
            self.dropped += 1
            raise ActorError("knowledge_capacity", "Cited evidence exceeds bounded working storage")
        candidate = OrderedDict(self.records)
        candidate.update((record.id, record.model_copy(deep=True)) for record in additions)
        protected, pending = set(), [note.id]
        while pending:
            identity = pending.pop()
            if identity in protected:
                continue
            if identity not in candidate:
                raise ActorError("hypothesis_evidence_missing", "Cited evidence is no longer retained")
            protected.add(identity)
            pending.extend(candidate[identity].evidence_refs)
        if (len(protected) > self.max_entries or
                sum(self._size(candidate[key]) for key in protected) > self.max_total_bytes):
            self.dropped += 1
            raise ActorError("knowledge_capacity", "Note and cited evidence exceed bounded working storage")
        dropped = invalidated = 0
        while (len(candidate) > self.max_entries or
                sum(map(self._size, candidate.values())) > self.max_total_bytes):
            referenced = {ref for value in candidate.values() for ref in value.evidence_refs}
            victim = min((key for key in candidate if key not in protected),
                key=lambda key: (0 if key.startswith("evidence-snapshot:") and key not in referenced
                    else candidate[key].importance, candidate[key].observed_at_unix_ms))
            candidate.pop(victim); dropped += 1
            while True:
                missing = [key for key, value in candidate.items() if key not in protected and
                    any(ref not in candidate for ref in value.evidence_refs)]
                if not missing:
                    break
                for key in missing:
                    candidate.pop(key); invalidated += 1
        self.records = candidate
        self.dropped += dropped
        self.expired += invalidated

    def context(self):
        self._prune()
        return [record.model_dump(mode="json") for record in sorted(self.records.values(),
            key=lambda record: (record.importance, record.observed_at_unix_ms, record.id), reverse=True)]

    def report(self):
        self._prune()
        return {"entries": len(self.records), "kinds": dict(Counter(record.kind for record in self.records.values())),
                "bytes": sum(map(self._size, self.records.values())), "dropped": self.dropped, "expired": self.expired}


def select_knowledge(records, *, max_entries=24, max_bytes=8192):
    """Budget private records as evidence groups: never send an orphaned note.

    No text is interpreted, no claim is promoted, and timestamps are unchanged.
    Callers may pass already priority-ordered records from their installed policy.
    """
    import json
    values = list(records)
    by_id = {value.get("id"): value for value in values}
    selected = {}
    def dependencies(value, seen):
        identity = value.get("id")
        if identity in seen:
            return None
        seen = {*seen, identity}
        result = {}
        for ref in value.get("evidence_refs", ()):
            if ref not in by_id:
                return None
            branch = dependencies(by_id[ref], seen)
            if branch is None:
                return None
            result.update(branch)
        result[identity] = value
        return result
    for value in values:
        branch = dependencies(value, set())
        if branch is None:
            continue
        candidate = {**selected, **branch}
        encoded = json.dumps(list(candidate.values()), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(candidate) <= max_entries and len(encoded) <= max_bytes:
            selected = candidate
    # Retain the caller's ordering, including dependencies selected by a note.
    return [value for value in values if value.get("id") in selected]
