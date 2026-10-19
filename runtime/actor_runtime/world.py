"""A deterministic session-scoped cache; no game ontology is prescribed here."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import time
from typing import Callable, TypeVar

from google.protobuf.message import Message

from .errors import ActorError
from .extensions import OpaquePayload, type_url
from .inbox import EventInbox, EventInboxPolicy

T = TypeVar("T", bound=Message)


@dataclass(frozen=True)
class CachedPayload:
    entity_id: str
    capability_id: str
    sequence: int
    revision: int
    payload: Message | OpaquePayload
    received_at: float = field(default_factory=time.monotonic)
    data_policy: str = "public"
    expires_at_unix_ms: int = 0


class WorldState:
    def __init__(self, self_actor_id: str, *, event_capacity: int = 64, observation_capacity: int = 512):
        self.self_actor_id = self_actor_id
        self.last_sequence = 0
        self.revision = 0
        self.observations: dict[tuple[str, str], CachedPayload] = {}
        self.observation_capacity = observation_capacity
        self.events: deque[CachedPayload] = deque(maxlen=event_capacity)
        self.last_frame_received_at = 0.0
        self.changed = asyncio.Condition()
        self.inboxes: dict[str, EventInbox] = {}

    def apply_observations(self, items: list[CachedPayload], sequence: int, revision: int):
        if sequence <= self.last_sequence or revision < self.revision:
            return False
        for item in items:
            self.observations[(item.entity_id, item.capability_id)] = item
            while len(self.observations) > self.observation_capacity:
                del self.observations[next(iter(self.observations))]
        self.last_sequence, self.revision = sequence, revision
        self.last_frame_received_at = time.monotonic()
        return True

    def retain_event(self, event: CachedPayload):
        if isinstance(event.payload, Message):
            if not self.inboxes.setdefault(event.capability_id, EventInbox()).ingest(event):
                return False
            # A global bound supplements each inbox's limit. Oldest unread data
            # is evicted visibly; no reliable network queue grows here.
            while sum(len(box.items) for box in self.inboxes.values()) > 256:
                candidates = [(entry.received_at, box, identity) for box in self.inboxes.values()
                              for identity, entry in box.items.items()]
                _, box, identity = min(candidates, key=lambda item: item[0])
                box.items.pop(identity); box.dropped += 1; box.revision += 1
        return True

    def apply_event(self, event: CachedPayload, *, retained=False):
        if not retained and not self.retain_event(event):
            return False
        self.events.append(event)
        return True

    def configure_inbox(self, capability_id, policy: EventInboxPolicy):
        if capability_id not in self.inboxes:
            self.inboxes[capability_id] = EventInbox(policy)
        elif not self.inboxes[capability_id].received:
            self.inboxes[capability_id].policy = policy

    async def notify(self):
        async with self.changed:
            self.changed.notify_all()

    def latest(self, message_type: type[T], entity_id: str | None = None,
               *, capability_id: str | None = None) -> T | None:
        entry = self.latest_entry(message_type, entity_id, capability_id=capability_id)
        return entry.payload if entry is not None else None

    def latest_entry(self, message_type: type[T], entity_id: str | None = None,
                     *, capability_id: str | None = None) -> CachedPayload | None:
        wanted = type_url(message_type)
        entity = entity_id or self.self_actor_id
        matched = []
        for (item_entity, item_capability), item in self.observations.items():
            if (item_entity == entity and (capability_id is None or capability_id == item_capability)
                    and isinstance(item.payload, Message)):
                if type_url(type(item.payload)) == wanted:
                    matched.append(item)
        if not matched:
            return None
        if len(matched) > 1:
            raise ActorError("ObservationAmbiguous", "Specify capability_id when observations share one type",
                             {"type_url": wanted})
        result = message_type()
        result.ParseFromString(matched[0].payload.SerializeToString())
        source = matched[0]
        from dataclasses import replace
        return replace(source, payload=result)

    async def wait_until(self, predicate: Callable[[WorldState], bool], timeout: float):
        async with asyncio.timeout(timeout):
            async with self.changed:
                await self.changed.wait_for(lambda: predicate(self))
        return self
