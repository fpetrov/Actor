"""Bounded local episodic evidence, independent of WorldState and Brain providers.

SQLite belongs to one worker thread. Callers await a bounded queue; event
ingestion must enqueue routing work, rather than await disk I/O itself.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Literal

from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .errors import ActorError
from .extensions import OpaquePayload, type_url

_FORMAT_VERSION = 1
_APPLICATION_ID = 0x41435452  # ACTR, never adopt another application's database.
_SENSITIVE_KEYS = frozenset(key.replace("_", "") for key in {
                             "authorization", "api_key", "apikey", "access_token", "refresh_token",
                             "token", "bridge_token", "actor_bridge_token", "client_secret", "secret_key",
                             "password", "secret", "credentials", "hidden_reasoning", "reasoning"})


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _clean(value):
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()
                if str(key).lower().replace("-", "").replace("_", "") not in _SENSITIVE_KEYS}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _words(value):
    return set(re.findall(r"\w{3,}", str(value).casefold()))


class MemoryEntry(BaseModel):
    """Past evidence, never a claim about the current authoritative world."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    id: str
    agent_profile_id: str
    game_id: str
    session_id: str
    actor_id: str
    evidence_kind: Literal["observed_event", "skill_result", "objective_result", "observed_fact"]
    source_event_id: str
    capability_id: str = ""
    type_url: str = ""
    extension_id: str = ""
    extension_version: str = ""
    summary: str
    content: JsonValue
    content_truncated: bool = False
    importance: float = Field(ge=0, le=1)
    tags: list[str] = Field(default_factory=list)
    created_at: float
    expires_at: float


class MemoryStore:
    def __init__(self, *, game_id: str, session_id: str, actor_id: str = "",
                 path: str | None = None, profile: str | None = None, max_entries=1000,
                 max_payload_bytes=4096, ttl_seconds=604800, max_retrieved=8,
                 enabled=True, clock=time.time, administration=False):
        if (not isinstance(game_id, str) or not game_id or len(game_id) > 160 or
                not isinstance(session_id, str) or not session_id or len(session_id) > 128 or
                not isinstance(actor_id, str) or len(actor_id) > 128 or
                (profile is not None and (not isinstance(profile, str) or not profile or len(profile) > 128))):
            raise ValueError("Memory requires bounded game, session and profile identities")
        if path is not None and profile is None:
            raise ValueError("Persistent memory requires an explicit agent profile")
        if administration and path is None:
            raise ValueError("Memory administration requires an existing database path")
        if (type(max_entries) is not int or not 1 <= max_entries <= 100000 or
                type(max_payload_bytes) is not int or not 128 <= max_payload_bytes <= 65536 or
                type(max_retrieved) is not int or not 1 <= max_retrieved <= 128 or
                not math.isfinite(ttl_seconds) or ttl_seconds <= 0):
            raise ValueError("Memory limits must be finite and bounded")
        self.game_id, self.session_id, self.actor_id = game_id, session_id, actor_id
        self.profile = profile if profile is not None else "session:" + session_id
        self.persistent = path is not None
        self.path = Path(path).expanduser().absolute() if path is not None else None
        self.max_entries, self.max_payload_bytes = max_entries, max_payload_bytes
        self.ttl_seconds, self.max_retrieved = float(ttl_seconds), max_retrieved
        self.enabled, self._clock = enabled, clock
        self.administration = administration
        self.state = "ready" if enabled else "disabled"
        self.error_code = None
        self._connection = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="actor-memory")
        self._pending = 0
        self._closed = False
        self._close_task = None
        self._retrieval_count = 0
        self._retrieved_entries = 0
        # Bound the entire file, including free pages; one global row limit also
        # applies across all profiles/games sharing this explicitly chosen file.
        self.max_database_bytes = max(262144, (max_entries * (max_payload_bytes + 8192) * 2 + 65536))

    @classmethod
    def from_config(cls, config, session):
        values = config.model_dump() if hasattr(config, "model_dump") else dict(config)
        return cls(game_id=session.manifest.game_id, session_id=session.session_id,
                   actor_id=session.world.self_actor_id, **values)

    async def _call(self, operation, *args):
        if self._closed:
            raise ActorError("memory_closed", "MemoryStore has been closed")
        if self.administration and operation in (self._remember, self._retrieve):
            raise ActorError("memory_administration", "Administration supports only statistics and scoped clear")
        if not self.enabled:
            return None
        if self.state == "degraded":
            raise ActorError("memory_unavailable", "MemoryStore is degraded", {"reason": self.error_code})
        # Do not let disk contention create an unbounded executor work queue.
        # This check/increment has no await and is atomic on the owning loop.
        if self._pending >= 32:
            raise ActorError("memory_busy", "MemoryStore's bounded operation queue is full")
        self._pending += 1
        future = asyncio.get_running_loop().run_in_executor(self._executor, self._operate, operation, args)
        # Count actual worker lifetime, even when the awaiting caller is cancelled.
        future.add_done_callback(lambda _: setattr(self, "_pending", self._pending - 1))
        return await asyncio.shield(future)

    def _operate(self, operation, args):
        try:
            self._open()
            return operation(*args)
        except (sqlite3.Error, OSError, ValueError, TypeError) as error:
            self.state, self.error_code = "degraded", "database_" + type(error).__name__.lower()
            raise ActorError("memory_unavailable", "MemoryStore could not safely use its database",
                             {"reason": self.error_code}) from None

    def _open(self):
        if self._connection is not None:
            return
        fresh = self.path is None
        if self.path is not None:
            if self.administration:
                if not self.path.exists():
                    self.state = "missing"
                    return
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    pass
                else:
                    os.close(descriptor)
                    fresh = True
        connection = (sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=0.25)
                      if self.administration else
                      sqlite3.connect(str(self.path) if self.path else ":memory:", timeout=0.25))
        try:
            application_id = connection.execute("PRAGMA application_id").fetchone()[0]
            if fresh:
                connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
                connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={_FORMAT_VERSION}")
                connection.execute("""CREATE TABLE memories (
                    id TEXT PRIMARY KEY, profile TEXT NOT NULL, game TEXT NOT NULL,
                    session TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL,
                    source TEXT NOT NULL, capability TEXT NOT NULL, type_url TEXT NOT NULL,
                    extension_id TEXT NOT NULL, extension_version TEXT NOT NULL,
                    summary TEXT NOT NULL, content TEXT NOT NULL, truncated INTEGER NOT NULL,
                    importance REAL NOT NULL, tags TEXT NOT NULL,
                    created REAL NOT NULL, expires REAL NOT NULL)""")
                connection.execute("CREATE INDEX memory_scope ON memories(profile, game)")
                connection.execute("CREATE INDEX memory_expiry ON memories(expires)")
                connection.commit()
            elif (application_id != _APPLICATION_ID or
                  connection.execute("PRAGMA user_version").fetchone()[0] != _FORMAT_VERSION or
                  connection.execute("PRAGMA auto_vacuum").fetchone()[0] != 2 or
                  connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok"):
                raise ValueError("Not a supported healthy ACTOR memory database")
            if not self.administration:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA secure_delete=ON")
                page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                pages = max(64, math.ceil(self.max_database_bytes / page_size))
                self.max_database_bytes = pages * page_size
                connection.execute(f"PRAGMA max_page_count={pages}")
                current_pages = connection.execute("PRAGMA page_count").fetchone()[0]
                if current_pages > pages:
                    raise ValueError("Existing memory database exceeds configured file bound")
            connection.row_factory = sqlite3.Row
            self._connection = connection
            self.state = "ready"
        except BaseException:
            connection.close()
            raise

    def _prune(self):
        db = self._connection
        db.execute("DELETE FROM memories WHERE expires <= ?", (self._clock(),))
        db.execute("""DELETE FROM memories WHERE id IN
            (SELECT id FROM memories ORDER BY importance DESC, created DESC, id DESC LIMIT -1 OFFSET ?)""",
                   (self.max_entries,))
        db.commit()
        db.execute("PRAGMA incremental_vacuum(128)")

    def _entry(self, row):
        return MemoryEntry(id=row["id"], agent_profile_id=row["profile"], game_id=row["game"],
                           session_id=row["session"], actor_id=row["actor"], evidence_kind=row["kind"],
                           source_event_id=row["source"], capability_id=row["capability"],
                           type_url=row["type_url"], extension_id=row["extension_id"],
                           extension_version=row["extension_version"], summary=row["summary"],
                           content=json.loads(row["content"]), content_truncated=bool(row["truncated"]),
                           importance=float(row["importance"]), tags=json.loads(row["tags"]),
                           created_at=float(row["created"]), expires_at=float(row["expires"]))

    async def remember(self, *, evidence_kind, source_event_id: str, content,
                       summary: str, importance=0.5, tags=(), capability_id="", type_url="",
                       extension_id="", extension_version="", session_id=None,
                       content_truncated=False, replace_existing=False) -> MemoryEntry | None:
        """Record explicit observed evidence; callers must not invent hidden facts."""
        if evidence_kind not in ("observed_event", "skill_result", "objective_result", "observed_fact"):
            raise ValueError("Unknown memory evidence kind")
        if replace_existing and evidence_kind != "objective_result":
            raise ValueError("Only an Objective's final result can be reconciled")
        if not isinstance(source_event_id, str) or not source_event_id or len(source_event_id) > 512:
            raise ValueError("Memory evidence requires a bounded idempotency key")
        if not math.isfinite(importance) or not 0 <= importance <= 1:
            raise ValueError("Memory importance must be between zero and one")
        content = _clean(content)
        encoded = _json(content)
        size = len(encoded.encode("utf-8"))
        oversized = size > self.max_payload_bytes
        truncated = bool(content_truncated) or oversized
        if oversized:
            content = {"payload_omitted": True, "original_bytes": size}
        identity = _json([self.profile, self.game_id, evidence_kind, source_event_id])
        now = float(self._clock())
        entry = MemoryEntry(id=hashlib.sha256(identity.encode()).hexdigest(),
                            agent_profile_id=self.profile, game_id=self.game_id,
                            session_id=(session_id or self.session_id)[:128], actor_id=self.actor_id,
                            evidence_kind=evidence_kind, source_event_id=source_event_id,
                            capability_id=capability_id[:256], type_url=type_url[:512],
                            extension_id=extension_id[:160], extension_version=extension_version[:80],
                            summary=str(summary)[:256], content=content, content_truncated=truncated,
                            importance=float(importance), tags=sorted({str(tag)[:64] for tag in tags})[:16],
                            created_at=now, expires_at=now + self.ttl_seconds)
        return await self._call(self._remember, entry, replace_existing)

    def _remember(self, entry, replace_existing=False):
        self._prune()
        db = self._connection
        existing = db.execute("SELECT * FROM memories WHERE id=?", (entry.id,)).fetchone()
        if existing is not None:
            if replace_existing:
                # Late terminal cancellation can win while a provisional result
                # is already queued on SQLite. Reconcile that outcome, preserving
                # original evidence identity and TTL rather than inserting twice.
                db.execute("""UPDATE memories SET summary=?,content=?,truncated=?,importance=?,tags=?
                    WHERE id=?""", (entry.summary, _json(entry.content), int(entry.content_truncated),
                                     entry.importance, _json(entry.tags), entry.id))
                db.commit()
                existing = db.execute("SELECT * FROM memories WHERE id=?", (entry.id,)).fetchone()
            return self._entry(existing)
        # Always retain the newly acknowledged evidence. Evict the least salient
        # oldest existing entry first, across every scope using this file.
        db.execute("""DELETE FROM memories WHERE id IN
            (SELECT id FROM memories ORDER BY importance DESC, created DESC, id DESC LIMIT -1 OFFSET ?)""",
                   (self.max_entries - 1,))
        db.execute("""INSERT OR IGNORE INTO memories VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (entry.id, entry.agent_profile_id, entry.game_id, entry.session_id, entry.actor_id,
                    entry.evidence_kind, entry.source_event_id, entry.capability_id, entry.type_url,
                    entry.extension_id, entry.extension_version, entry.summary, _json(entry.content),
                    int(entry.content_truncated), entry.importance, _json(entry.tags),
                    entry.created_at, entry.expires_at))
        db.commit()
        found = db.execute("SELECT * FROM memories WHERE id=?", (entry.id,)).fetchone()
        self._prune()
        return self._entry(found)

    async def remember_event(self, event, *, summary=None, importance=None, tags=()):
        entry = getattr(event, "entry", event)
        if getattr(entry, "data_policy", "public") != "public":
            return None
        opaque = isinstance(entry.payload, OpaquePayload)
        url = entry.payload.type_url if opaque else type_url(type(entry.payload))
        content = {"opaque": True} if opaque else (
            {"payload_omitted": True, "original_bytes": entry.payload.ByteSize()}
            if entry.payload.ByteSize() > self.max_payload_bytes else
            MessageToDict(entry.payload, preserving_proto_field_name=False,
                          always_print_fields_with_no_presence=True))
        source_session = getattr(event, "session_id", self.session_id)
        wire_id = getattr(event, "id", None)
        key = wire_id or hashlib.sha256(_json([
            source_session, entry.sequence, entry.revision, entry.entity_id, entry.capability_id,
        ]).encode()).hexdigest()
        semantic_id = getattr(getattr(event, "hints", None), "semantic_id", "")[:256]
        if semantic_id:
            # Only an installed game rule can assert a logical event identity.
            # It survives a new bridge session/sequence during replay.
            key = "logical:" + hashlib.sha256(_json([entry.capability_id, semantic_id]).encode()).hexdigest()
        assessment = getattr(event, "assessment", None)
        if importance is None:
            importance = max({"unrelated": 0.1, "uncertain": 0.5, "relevant": 0.7}.get(
                getattr(assessment, "relevance", "uncertain"), 0.5),
                {"routine": 0.1, "important": 0.75, "critical": 1.0}.get(
                    getattr(assessment, "urgency", "routine"), 0.1))
        content = {"event": content, "entity_id": entry.entity_id,
                   "sequence": entry.sequence, "revision": entry.revision, "opaque": opaque}
        if wire_id:
            content["wire_event_id"] = wire_id[:512]
        if semantic_id:
            content["semantic_id"] = semantic_id
        if assessment is not None and hasattr(assessment, "model_dump"):
            content["assessment"] = assessment.model_dump(mode="json")
        return await self.remember(evidence_kind="observed_event", source_event_id=key,
                                   content=content, summary=summary or f"Observed {entry.capability_id}",
                                   importance=min(1.0, max(0.0, importance)), tags=tags,
                                   capability_id=entry.capability_id, type_url=url,
                                   extension_id=getattr(event, "extension_id", ""),
                                   extension_version=getattr(event, "extension_version", ""),
                                   session_id=source_session,
                                   content_truncated=not opaque and entry.payload.ByteSize() > self.max_payload_bytes)

    async def remember_skill_result(self, result, *, objective_id=None):
        from .privacy import public_projection
        value = public_projection(result)
        source = _json([self.session_id, objective_id, value])
        key = hashlib.sha256(source.encode()).hexdigest()
        return await self.remember(evidence_kind="skill_result", source_event_id=key,
                                   content=value, summary=f"Skill {value.get('skill_id', '')}: {value.get('status', '')}",
                                   importance=0.5 if value.get("status") == "succeeded" else 0.7,
                                   tags=("skill_result", str(value.get("skill_id", ""))))

    async def remember_objective_result(self, result):
        from .privacy import public_projection
        value = public_projection(result)
        key = _json([self.session_id, value.get("objective_id")])
        return await self.remember(evidence_kind="objective_result", source_event_id=key,
                                   content=value, summary=f"Objective: {value.get('status', '')}",
                                   importance=0.75, tags=("objective_result",), replace_existing=True)

    async def retrieve(self, *, objective="", current_skill=None, capability_ids=(), type_urls=(),
                       tags=(), limit=None) -> list[MemoryEntry]:
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("Memory retrieval limit must be nonnegative")
        limit = min(self.max_retrieved, self.max_retrieved if limit is None else limit)
        query = objective.text if hasattr(objective, "text") else str(objective)
        skill = current_skill.model_dump(mode="json") if hasattr(current_skill, "model_dump") else current_skill
        result = await self._call(self._retrieve, query, skill, set(capability_ids), set(type_urls),
                                  set(tags), limit)
        return result or []

    def _retrieve(self, objective, current_skill, capabilities, urls, tags, limit):
        self._prune()
        rows = self._connection.execute("SELECT * FROM memories WHERE profile=? AND game=?",
                                        (self.profile, self.game_id))
        now, keywords = self._clock(), _words(objective) | _words(_json(current_skill))
        def score(row):
            overlap = len(keywords & _words(row["summary"] + " " + row["content"] + " " + row["tags"]))
            filter_match = (not capabilities or row["capability"] in capabilities) and (
                not urls or row["type_url"] in urls) and (not tags or tags.intersection(json.loads(row["tags"])))
            recency = 1 / (1 + max(0, now - row["created"]) / 86400)
            return filter_match, overlap * 2 + row["importance"] * 2 + recency, row["created"], row["id"]
        # A large explicitly configured history must not be copied wholesale
        # into Python memory just to return its best few pieces of evidence.
        ranked = heapq.nlargest(limit, (row for row in rows if score(row)[0]), key=score)
        self._retrieval_count += 1
        self._retrieved_entries += len(ranked)
        return [self._entry(row) for row in ranked]

    async def stats(self) -> dict:
        if not self.enabled:
            return {"state": "disabled", "entries": 0, "persistent": self.persistent}
        if self._closed or self.state == "degraded":
            return {"state": "closed" if self._closed else "degraded", "error_code": self.error_code,
                    "persistent": self.persistent, "pending_operations": self._pending}
        return await self._call(self._stats)

    def _stats(self):
        if self._connection is None and self.administration:
            return {"state": "missing", "entries": 0, "global_entries": 0, "database_bytes": 0,
                    "persistent": True, "profile": self.profile, "game_id": self.game_id,
                    "administration": True}
        if not self.administration:
            self._prune()
        db = self._connection
        return {"state": self.state, "persistent": self.persistent, "profile": self.profile,
                "administration": self.administration, "limits_enforced": not self.administration,
                "game_id": self.game_id,
                "entries": db.execute("SELECT count(*) FROM memories WHERE profile=? AND game=?",
                                      (self.profile, self.game_id)).fetchone()[0],
                "global_entries": db.execute("SELECT count(*) FROM memories").fetchone()[0],
                "max_entries": self.max_entries, "max_payload_bytes": self.max_payload_bytes,
                "database_bytes": db.execute("PRAGMA page_count").fetchone()[0] * db.execute(
                    "PRAGMA page_size").fetchone()[0], "max_database_bytes": self.max_database_bytes,
                "retrieval_count": self._retrieval_count, "retrieved_entries": self._retrieved_entries,
                "pending_operations": self._pending}

    async def clear(self) -> int:
        return await self._call(self._clear) or 0

    def _clear(self):
        if self._connection is None and self.administration:
            return 0
        if self.administration:
            self._connection.execute("PRAGMA secure_delete=ON")
        result = self._connection.execute("DELETE FROM memories WHERE profile=? AND game=?",
                                          (self.profile, self.game_id))
        if self.administration:
            self._connection.commit()
        else:
            self._prune()
        return result.rowcount

    async def close(self):
        if self._close_task is None:
            self._closed = True
            async def finish():
                def close_connection():
                    if self._connection is not None:
                        self._connection.close()
                        self._connection = None
                try:
                    await asyncio.get_running_loop().run_in_executor(self._executor, close_connection)
                finally:
                    self._executor.shutdown(wait=False)
            self._close_task = asyncio.create_task(finish())
        await asyncio.shield(self._close_task)

    async def __aenter__(self):
        if self.enabled:
            await self._call(lambda: None)
        return self

    async def __aexit__(self, *_):
        await self.close()
