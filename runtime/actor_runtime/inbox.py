"""Bounded typed-event retention within one WorldState, not another transport."""
from collections import OrderedDict
from dataclasses import dataclass
import math
import time

from google.protobuf.message import Message
from .extensions import type_url


@dataclass(frozen=True)
class EventInboxPolicy:
    identity_field: str | None = None
    ordering_field: str | None = None
    expiry_field: str | None = None
    max_items: int = 64
    max_age_seconds: float = 120
    max_dedup_keys: int = 1024
    max_payload_bytes: int = 16384

    def __post_init__(self):
        if (type(self.max_items) is not int or not 1 <= self.max_items <= 512 or
                type(self.max_dedup_keys) is not int or not self.max_items <= self.max_dedup_keys <= 4096 or
                type(self.max_payload_bytes) is not int or not 1 <= self.max_payload_bytes <= 65536 or
                not math.isfinite(self.max_age_seconds) or not 0 < self.max_age_seconds <= 3600):
            raise ValueError("Event inbox bounds must be finite and limited")


class EventInbox:
    def __init__(self, policy=None):
        self.policy = policy or EventInboxPolicy()
        self.items, self.seen = OrderedDict(), OrderedDict()
        self.revision = self.received = self.duplicates = self.expired = self.dropped = 0
        self.last_order = 0

    def prune(self, now=None):
        now = time.monotonic() if now is None else now
        for identity, entry in tuple(self.items.items()):
            expiry = getattr(entry.payload, self.policy.expiry_field, 0) if self.policy.expiry_field else 0
            if now - entry.received_at > self.policy.max_age_seconds or expiry and expiry <= int(time.time() * 1000):
                self.items.pop(identity)
                self.expired += 1
                self.revision += 1

    def ingest(self, entry):
        if not isinstance(entry.payload, Message):
            return False
        if entry.payload.ByteSize() > self.policy.max_payload_bytes:
            self.dropped += 1
            self.revision += 1
            return False
        self.prune()
        identity = (getattr(entry.payload, self.policy.identity_field, None)
                    if self.policy.identity_field else entry.sequence)
        identity = identity or entry.sequence
        if identity in self.seen:
            self.duplicates += 1
            return False
        order = getattr(entry.payload, self.policy.ordering_field, 0) if self.policy.ordering_field else entry.sequence
        if order and order <= self.last_order:
            self.duplicates += 1
            return False
        self.last_order = max(self.last_order, order)
        self.seen[identity] = None
        while len(self.seen) > self.policy.max_dedup_keys:
            self.seen.popitem(last=False)
        self.items[identity] = entry
        self.received += 1
        self.revision += 1
        while len(self.items) > self.policy.max_items:
            self.items.popitem(last=False)
            self.dropped += 1
        return True

    def find(self, entity_id, message_type, predicate=None, *, consume=True, after_sequence=0):
        self.prune()
        for identity, entry in tuple(self.items.items()):
            if (entry.entity_id != entity_id or entry.sequence <= after_sequence or
                    not isinstance(entry.payload, Message) or type_url(type(entry.payload)) != type_url(message_type)):
                continue
            value = message_type(); value.ParseFromString(entry.payload.SerializeToString())
            if predicate is not None and not predicate(value):
                continue
            if consume:
                self.items.pop(identity)
                self.revision += 1
            from dataclasses import replace
            return replace(entry, payload=value)
        return None

    def report(self):
        return {"pending": len(self.items), "received": self.received, "duplicates": self.duplicates,
                "expired": self.expired, "dropped": self.dropped, "dedup_keys": len(self.seen)}
