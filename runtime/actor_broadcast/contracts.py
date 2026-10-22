"""Canonical bounded Python boundaries between presentation, text and audio.

These are local contracts, not a second game transport. Only an installed
observer projector may construct PresentedView from server-permitted data.
Audio acknowledgements certify the local audio graph, never a Twitch viewer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import AsyncIterator, Literal, Protocol

Mode = Literal["reactive", "idle_recap", "memory_callback", "chat_response", "intro", "outro", "analysis", "repair"]


def bounded_text(value, maximum, *, empty=False):
    if not isinstance(value, str) or (not value and not empty) or len(value.encode("utf-8")) > maximum or "\x00" in value:
        raise ValueError("A bounded UTF-8 string is required")


def integer(value, minimum=0, maximum=2**63-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("An integer within the declared bound is required")


def finite(value, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError("A finite nonnegative clock value is required")


def json_copy(value, maximum=32768):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError("JSON boundary exceeded")
    return json.loads(encoded)


@dataclass(frozen=True)
class Scope:
    broadcast_session_id: str
    match_id: str
    connection_generation: int
    view_epoch: int

    def __post_init__(self):
        bounded_text(self.broadcast_session_id, 128); bounded_text(self.match_id, 128)
        integer(self.connection_generation, 1); integer(self.view_epoch, 1)


@dataclass(frozen=True)
class EvidenceRef:
    id: str
    version: str

    def __post_init__(self):
        bounded_text(self.id, 256); bounded_text(self.version, 128)


@dataclass(frozen=True)
class PresentationAck:
    scope: Scope
    rendered_sequence: int
    frame_counter: int
    received_at_monotonic: float
    presented_evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self):
        integer(self.rendered_sequence, 1); integer(self.frame_counter, 1)
        finite(self.received_at_monotonic)
        if (not isinstance(self.presented_evidence,tuple) or len(self.presented_evidence)>96 or
                not all(isinstance(ref,EvidenceRef) for ref in self.presented_evidence) or
                len({ref.id for ref in self.presented_evidence})!=len(self.presented_evidence)):
            raise ValueError("Presented evidence must contain unique bounded exact versions")


@dataclass(frozen=True)
class EditorialFact:
    id: str
    version: str
    kind: Literal["historical_observation", "player_claim"]
    source_event_id: str
    subject_ids: tuple[str, ...]
    predicate: str
    observed_at_unix_ms: int
    expires_at_unix_ms: int
    content: dict
    priority: int = 2
    topic_key: str = ""
    unresolved: bool = False
    origin: Literal["game", "viewer_chat"] = "game"

    def __post_init__(self):
        EvidenceRef(self.id, self.version)
        if self.kind not in ("historical_observation", "player_claim"):
            raise ValueError("Only observed facts and attributed claims enter evidence")
        if self.origin not in ("game","viewer_chat") or self.origin=="viewer_chat" and (self.kind!="player_claim" or self.priority!=3):
            raise ValueError("Viewer input is a noncritical attributed claim")
        bounded_text(self.source_event_id, 512); bounded_text(self.predicate, 128)
        bounded_text(self.topic_key, 512, empty=True)
        if not isinstance(self.subject_ids, tuple) or len(self.subject_ids) > 8:
            raise ValueError("Bounded canonical subjects are required")
        for subject in self.subject_ids: bounded_text(subject, 128)
        if not isinstance(self.content,dict) or type(self.unresolved)is not bool:raise ValueError("Typed editorial content required")
        integer(self.priority, 0, 3); integer(self.observed_at_unix_ms, 1)
        integer(self.expires_at_unix_ms, self.observed_at_unix_ms + 1)
        object.__setattr__(self, "content", json_copy(self.content, 4096))

    @property
    def ref(self): return EvidenceRef(self.id, self.version)


@dataclass(frozen=True)
class PresentedView:
    scope: Scope
    sequence: int
    semantic_revision: int
    observed_at_unix_ms: int
    expires_at_unix_ms: int
    phase: str
    current: dict
    facts: tuple[EditorialFact, ...]
    presentation: PresentationAck
    terminal: bool = False
    revoked_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.scope,Scope) or not isinstance(self.presentation,PresentationAck) or not isinstance(self.current,dict) or type(self.terminal)is not bool:
            raise ValueError("Typed presented scope and state are required")
        integer(self.sequence, 1); integer(self.semantic_revision)
        integer(self.observed_at_unix_ms, 1); integer(self.expires_at_unix_ms, self.observed_at_unix_ms + 1)
        bounded_text(self.phase, 64)
        if self.presentation.scope != self.scope or self.presentation.rendered_sequence != self.sequence:
            raise ValueError("A coherent exact-sequence presentation ACK is required")
        if not isinstance(self.facts, tuple) or len(self.facts) > 96 or not all(isinstance(x, EditorialFact) for x in self.facts):
            raise ValueError("Presented facts must be bounded typed records")
        if (len({fact.id for fact in self.facts})!=len(self.facts) or
                {fact.ref for fact in self.facts}!=set(self.presentation.presented_evidence)):
            raise ValueError("Every fact must have its exact native presentation acknowledgement")
        if len(self.revoked_evidence_ids) > 96: raise ValueError("Revocation bound exceeded")
        for identity in self.revoked_evidence_ids: bounded_text(identity, 256)
        object.__setattr__(self, "current", json_copy(self.current))


@dataclass(frozen=True)
class CommentaryCandidate:
    scope: Scope
    presentation_sequence: int
    candidate_id: str
    mode: Mode
    priority: int
    topic_key: str
    evidence: tuple[EvidenceRef, ...]
    expires_at_unix_ms: int

    def __post_init__(self):
        integer(self.presentation_sequence,1);integer(self.priority,0,3);integer(self.expires_at_unix_ms,1)
        bounded_text(self.candidate_id,128);bounded_text(self.topic_key,512)
        if self.mode not in ("reactive","idle_recap","memory_callback","chat_response","intro","outro","analysis","repair"):
            raise ValueError("Unknown commentary mode")
        if not isinstance(self.evidence,tuple) or not 1<=len(self.evidence)<=8 or not all(isinstance(ref,EvidenceRef) for ref in self.evidence):
            raise ValueError("Candidate evidence must be exact bounded versions")


@dataclass(frozen=True)
class CommentaryRequest:
    candidate: CommentaryCandidate
    external_state: dict
    max_output_tokens: int
    utterance_id: str
    utterance_epoch: int

    def __post_init__(self):
        integer(self.max_output_tokens, 32, 4096); bounded_text(self.utterance_id, 128)
        integer(self.utterance_epoch, 1)
        object.__setattr__(self, "external_state", json_copy(self.external_state, 16384))


@dataclass(frozen=True)
class ProviderSegment:
    index: int
    text: str
    evidence_versions: tuple[EvidenceRef, ...]

    def __post_init__(self):
        integer(self.index, 0, 15); bounded_text(self.text, 4096)
        if not 1 <= len(self.evidence_versions) <= 8: raise ValueError("Speech requires bounded evidence")


@dataclass(frozen=True)
class ProviderNote:
    kind: Literal["hypothesis", "contradiction"]
    statement: str
    confidence: float
    evidence_versions: tuple[EvidenceRef, ...]

    def __post_init__(self):
        if self.kind not in ("hypothesis","contradiction"):raise ValueError("Unknown note kind")
        bounded_text(self.statement,512);finite(self.confidence)
        if self.confidence>1:raise ValueError("Confidence exceeds one")
        if not 1<=len(self.evidence_versions)<=8:raise ValueError("A note requires retained evidence")


@dataclass(frozen=True)
class ProviderTerminal:
    status: Literal["completed", "interrupted", "failed"]
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    provider_calls: int = 1
    tool_calls: int | None = None
    tool_count_scope: str = "unavailable"

    def __post_init__(self):
        if self.status not in ("completed", "interrupted", "failed"): raise ValueError("Invalid terminal")
        for value in (self.input_tokens, self.output_tokens, self.cached_input_tokens, self.reasoning_tokens, self.tool_calls):
            if value is not None: integer(value)
        integer(self.provider_calls, 1, 8)


@dataclass(frozen=True)
class SegmentKey:
    utterance_id: str
    utterance_epoch: int
    segment_index: int

    def __post_init__(self):
        bounded_text(self.utterance_id, 128); integer(self.utterance_epoch, 1); integer(self.segment_index, 0, 15)


@dataclass(frozen=True)
class SpeechSegment:
    scope: Scope
    key: SegmentKey
    priority: int
    text: str
    evidence_versions: tuple[EvidenceRef, ...]
    semantic_revision: int
    expires_at_monotonic_ns: int
    max_tokens: int = 512
    max_audio_frames: int = 480000

    def __post_init__(self):
        bounded_text(self.text, 4096); integer(self.priority, 0, 3)
        integer(self.semantic_revision); integer(self.expires_at_monotonic_ns, 1)
        integer(self.max_tokens, 1, 4096); integer(self.max_audio_frames, 1, 24000 * 60)
        if len(self.evidence_versions) > 8: raise ValueError("Evidence reference bound exceeded")


Synthesize = SpeechSegment


@dataclass(frozen=True)
class PcmChunk:
    scope: Scope
    key: SegmentKey
    chunk_seq: int
    source_frame_offset: int
    pcm: bytes = field(repr=False)
    rate: int = 24000
    channels: int = 1
    format: str = "f32le"

    def __post_init__(self):
        integer(self.chunk_seq); integer(self.source_frame_offset)
        if self.rate != 24000 or self.channels != 1 or self.format != "f32le": raise ValueError("Expected mono 24kHz F32LE")
        if not isinstance(self.pcm, bytes) or not 0 < len(self.pcm) <= 131072 or len(self.pcm) % 4:
            raise ValueError("Bounded whole PCM frames required")


@dataclass(frozen=True)
class SynthesisDone:
    scope: Scope
    key: SegmentKey
    total_source_frames: int
    token_count: int
    may_be_truncated: bool
    terminal_reason: str

    def __post_init__(self):
        integer(self.total_source_frames); integer(self.token_count); bounded_text(self.terminal_reason, 64)
        if type(self.may_be_truncated)is not bool:raise ValueError("Truncation flag must be explicit")


@dataclass(frozen=True)
class Cancel:
    scope: Scope
    utterance_id: str
    utterance_epoch: int

    def __post_init__(self):
        bounded_text(self.utterance_id,128);integer(self.utterance_epoch,1)


@dataclass(frozen=True)
class CancelAck:
    scope: Scope
    utterance_id: str
    utterance_epoch: int
    last_chunk_seq: int
    emission_stopped: bool

    def __post_init__(self):
        bounded_text(self.utterance_id,128);integer(self.utterance_epoch,1);integer(self.last_chunk_seq,-1)
        if type(self.emission_stopped)is not bool:raise ValueError("Cancellation ACK must be explicit")


@dataclass(frozen=True)
class FlushAck:
    scope: Scope
    utterance_id: str
    utterance_epoch: int
    flushed: bool

    def __post_init__(self):
        bounded_text(self.utterance_id,128);integer(self.utterance_epoch,1)
        if type(self.flushed)is not bool:raise ValueError("Render flush ACK must be explicit")


@dataclass(frozen=True)
class PlaybackAck:
    scope: Scope
    key: SegmentKey
    status: Literal["started", "progress", "completed", "interrupted"]
    consumed_source_frames: int
    total_source_frames: int
    received_at_monotonic: float
    clock_domain: str = "browser_audio_render"

    def __post_init__(self):
        if self.status not in ("started", "progress", "completed", "interrupted"): raise ValueError("Invalid playback ACK")
        integer(self.total_source_frames, 1); integer(self.consumed_source_frames, 0, self.total_source_frames)
        finite(self.received_at_monotonic)
        if self.status == "completed" and self.consumed_source_frames != self.total_source_frames:
            raise ValueError("Completed requires actual complete frame consumption")
        if self.clock_domain != "browser_audio_render": raise ValueError("Unknown playback clock")


class CommentaryProvider(Protocol):
    def generate(self, request: CommentaryRequest) -> AsyncIterator[ProviderSegment | ProviderNote | ProviderTerminal]: ...
    async def interrupt(self) -> None: ...
    async def close(self) -> None: ...


class SpeechOutput(Protocol):
    async def submit(self, segment: SpeechSegment) -> None: ...
    async def cancel(self, request: Cancel, *, fade_seconds: float) -> FlushAck: ...
