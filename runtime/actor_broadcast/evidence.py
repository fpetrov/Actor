"""A broadcast-scoped adapter over WorkingKnowledge, never player memory."""
from dataclasses import asdict
import hashlib
import time

from actor_runtime.knowledge import KnowledgeRecord, WorkingKnowledge, select_knowledge
from .contracts import EditorialFact, EvidenceRef


def storage_id(ref):
    return "broadcast-evidence:" + hashlib.sha256((ref.id + "\0" + ref.version).encode()).hexdigest()


class EvidenceLedger:
    def __init__(self, scope_id, *, clock=time.time, max_entries=96, max_bytes=32768):
        self.clock = clock
        self.knowledge = WorkingKnowledge(scope_id, max_entries=max_entries,
            max_record_bytes=8192, max_total_bytes=max_bytes, clock=clock)
        self.revocations = 0
        self.revoked=set()
        self.revocation_overflow=False
        self._pruned_at_ms=None

    def _prune(self):
        now=int(self.clock()*1000)
        if now!=self._pruned_at_ms:
            self.knowledge.context()
            self._pruned_at_ms=now

    def remember(self, fact: EditorialFact, view):
        """Every source version is immutable, including its original expiry."""
        ref = fact.ref
        if fact.id in self.revoked or self.revocation_overflow:return False
        identity = storage_id(ref)
        if self.resolve(ref) is not None:
            return False
        metadata = {"evidence_id": fact.id, "version": fact.version, "subjects": list(fact.subject_ids),
            "predicate": fact.predicate, "priority": fact.priority, "topic_key": fact.topic_key,
            "unresolved": fact.unresolved, "connection_generation": view.scope.connection_generation,
            "origin":fact.origin,
            "view_epoch": view.scope.view_epoch, "presentation_sequence": view.presentation.rendered_sequence,
            "received_at_monotonic": view.presentation.received_at_monotonic}
        record = KnowledgeRecord(id=identity, scope_id=self.knowledge.scope_id, kind=fact.kind,
            source_event_id=fact.source_event_id, source_actor_id=fact.subject_ids[0] if fact.subject_ids else "",
            sequence=view.sequence, revision=view.semantic_revision,
            observed_at_unix_ms=fact.observed_at_unix_ms, expires_at_unix_ms=fact.expires_at_unix_ms,
            content={"value": fact.content, "editorial": metadata}, importance=(1, .8, .5, .25)[fact.priority])
        return self.knowledge.remember(record)

    def revoke(self, identities):
        identities = set(identities)
        self.revoked.update(identities)
        if len(self.revoked)>512:
            self.revocation_overflow=True
            self.revoked=set(list(self.revoked)[:512])
            self.knowledge.records.clear()
        for key, record in tuple(self.knowledge.records.items()):
            if record.content.get("editorial", {}).get("evidence_id") in identities:
                self.knowledge.records.pop(key); self.revocations += 1
        self.knowledge.context()  # Existing dependency cascade removes orphaned notes.

    def resolve(self, ref):
        self._prune()
        value = self.knowledge.records.get(storage_id(ref))
        if value is None:
            value=next((record for record in self.knowledge.records.values()
                if record.content.get("editorial",{}).get("evidence_id")==ref.id and
                   record.content.get("editorial",{}).get("version")==ref.version),None)
        return value.model_copy(deep=True) if value else None

    def valid(self, refs):
        return bool(refs) and all(self.resolve(ref) is not None for ref in refs)

    def facts(self):
        values = self.knowledge.context()
        result = [];seen=set()
        for value in values:
            metadata = value["content"].get("editorial")
            if metadata is None:
                continue
            ref=EvidenceRef(metadata["evidence_id"], metadata["version"])
            if ref not in seen:result.append((ref,value));seen.add(ref)
        return result

    def selected_notes(self, refs=None):
        self.knowledge.context();result=[]
        for record in self.knowledge.records.values():
            if record.kind not in ("hypothesis","contradiction"):continue
            references=[]
            for identity in record.evidence_refs:
                source=self.knowledge.records.get(identity)
                metadata=source.content.get("editorial",{}) if source else {}
                if metadata.get("evidence_id") and metadata.get("version"):
                    references.append(EvidenceRef(metadata["evidence_id"],metadata["version"]))
            if len(references)!=len(record.evidence_refs):continue
            if refs is not None and not set(references).issubset(refs):continue
            result.append({"kind":record.kind,"statement":record.content["statement"],
                "confidence":record.confidence,"evidence_versions":[asdict(ref) for ref in references],
                "observed_at_unix_ms":record.observed_at_unix_ms,"expires_at_unix_ms":record.expires_at_unix_ms})
        return result[-2:]

    def noted_refs(self):
        return {EvidenceRef(**ref) for row in self.selected_notes() for ref in row["evidence_versions"]}

    def note(self, kind, statement, confidence, refs):
        if kind not in ("hypothesis","contradiction"):
            raise ValueError("Unknown epistemic note kind")
        sources=[self.resolve(ref) for ref in refs]
        if any(value is None for value in sources):
            raise ValueError("A note cannot cite missing evidence")
        if kind=="contradiction" and len({(value.source_event_id,value.source_actor_id,
                value.content["editorial"]["predicate"]) for value in sources})<2:
            raise ValueError("Versions of one claim are not independent sources")
        identities = [value.id for value in sources]
        function = self.knowledge.contradict if kind == "contradiction" else self.knowledge.hypothesize
        return function(statement, confidence, identities)

    def selected(self, refs, *, max_entries=12, max_bytes=8192):
        requested = {storage_id(ref) for ref in refs}
        values = self.knowledge.context()
        # A selected model note may carry dependencies; never manufacture an
        # independent witness from its paraphrase of an original source.
        return select_knowledge([value for value in values if value["id"] in requested],
            max_entries=max_entries, max_bytes=max_bytes)

    def report(self):
        return {**self.knowledge.report(), "revocations": self.revocations,"revocation_overflow":self.revocation_overflow}
