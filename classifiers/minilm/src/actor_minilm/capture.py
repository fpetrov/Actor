"""Opt-in, bounded private recording of this actor's inputs and decision trace."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import secrets
import stat
import time

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message
from actor_runtime.classifiers import RuleBasedEventClassifier

STATE_TYPES = frozenset({"SocialOwnStateObservation", "SocialMatchStateObservation",
                         "SocialVisibleWorldObservation", "SocialMeetingObservation"})
MAX_ROW_BYTES = 256 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024


def snapshot_entry(entry, now):
    typed = isinstance(entry.payload, Message)
    return {"entity_id": entry.entity_id, "capability_id": entry.capability_id,
            "sequence": entry.sequence, "revision": entry.revision,
            "age_ms": max(0, round((now - entry.received_at) * 1000)),
            "data_policy": entry.data_policy, "expires_at_unix_ms": entry.expires_at_unix_ms,
            "type_url": "type.googleapis.com/" + entry.payload.DESCRIPTOR.full_name if typed else entry.payload.type_url,
            "payload": MessageToDict(entry.payload, preserving_proto_field_name=True) if typed else None,
            "opaque": not typed}


def snapshot_input(context, event):
    now = time.monotonic()
    objective = context.objective
    if hasattr(objective, "model_dump"):
        objective = objective.model_dump(mode="json")
    if objective is not None and not isinstance(objective, (str, dict)):
        raise TypeError("Unsupported objective representation")
    skill = context.current_skill
    if hasattr(skill, "model_dump"):
        skill = skill.model_dump(mode="json")
    return {"context": {"objective": objective, "current_skill": skill,
                        "current_plan": getattr(context, "current_plan", None),
                        "relevant_state": [snapshot_entry(x, now) for x in context.relevant_state],
                        "policy_tags": list(context.policy_tags)},
            "event": {"id": event.id, "session_id": event.session_id,
                      "extension_id": event.extension_id, "extension_version": event.extension_version,
                      "type_url": event.type_url, "entry": snapshot_entry(event.entry, now),
                      "hints": asdict(event.hints), "aggregate_count": event.aggregate_count}}


class Capture:
    def __init__(self, rules, capture_root=None):
        self.delegate = RuleBasedEventClassifier(rules)
        self.counts = Counter()
        self.instance = f"{os.getpid()}-{secrets.token_hex(4)}"
        self.fd = None
        self.path = None
        self.bytes_written = 0
        self.router = None
        self._previous_trace_sink = None
        self._trace_sink = self.trace
        if capture_root:
            root = Path(capture_root).expanduser()
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            info = root.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("Capture root must be an owned private directory (mode 0700)")
            self.path = root / f"classifier-{self.instance}.jsonl"
            self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    def bind_router(self, router):
        if self.router is router:
            return
        if self.router is not None:
            raise RuntimeError("A classifier instance belongs to exactly one session")
        self.router = router
        if self.fd is not None:
            self._previous_trace_sink = router.session.trace_sink
            router.session.trace_sink = self._trace_sink

    def trace(self, phase, fields):
        session = self.router.session
        self.record({"schema": "actor.classifier-trace.v1", "phase": phase,
            "session_id": session.session_id, "actor_id": session.world.self_actor_id,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(), "monotonic": time.monotonic(),
            "details": fields})
        if self._previous_trace_sink is not None:
            self._previous_trace_sink(phase, fields)

    def record(self, row):
        if self.fd is None:
            return False
        try:
            raw = (json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()
            if len(raw) > MAX_ROW_BYTES or self.bytes_written + len(raw) > MAX_FILE_BYTES:
                self.counts["dropped_size_limit"] += 1
                return False
            view = memoryview(raw)
            while view:
                count = os.write(self.fd, view)
                if count <= 0:
                    raise OSError("Capture write made no progress")
                # Also account for a short write before a later write failure.
                self.bytes_written += count
                view = view[count:]
            return True
        except (OSError, TypeError, ValueError):
            self.counts["capture_errors"] += 1
            return False

    def before(self, context, event, route):
        self.counts["assessment_calls"] += 1
        identity = f"{self.instance}:{self.counts['assessment_calls']}"
        owner = self.router.session.world.self_actor_id if self.router else event.entry.entity_id
        permitted = tuple(x for x in context.relevant_state
            if x.entity_id == owner and isinstance(x.payload, Message) and x.payload.DESCRIPTOR.name in STATE_TYPES)
        context = replace(context, relevant_state=permitted) if hasattr(context, "__dataclass_fields__") else context
        # Snapshot once: the projection and retained input use the same data.
        data = snapshot_input(context, event)
        if not hasattr(context, "__dataclass_fields__"):
            data["context"]["relevant_state"] = [snapshot_entry(x, time.monotonic()) for x in permitted]
        match = next((x for x in permitted if x.payload.DESCRIPTOR.name == "SocialMatchStateObservation"), None)
        row = {"schema": "actor.local-classifier-input.v3", "collector_version": "0.2.0",
               "call_id": identity, "event_id": event.id, "session_id": event.session_id, "actor_id": owner,
               "match_id": getattr(match.payload, "match_id", None) if match else None,
               "captured_at_utc": datetime.now(timezone.utc).isoformat(),
               "runtime_version": version("actor-runtime"), "assessment_route": route,
               "input": data, "label": None}
        self.inputs[identity] = row
        self.counts["inputs_recorded"] += int(self.record(row))
        return identity

    def after(self, identity, assessment, **detail):
        row = self.inputs.pop(identity)
        self.counts["outputs_recorded"] += int(self.record({
            "schema": "actor.local-classifier-output.v4", "call_id": identity,
            "event_id": row["event_id"], "session_id": row["session_id"], "actor_id": row["actor_id"],
            "assessment_before_router_policy": assessment.model_dump(mode="json") if assessment is not None else None,
            "assessment_source": "trusted_game_rule", "model_sha256": self.model.model_sha,
            "is_ground_truth": False, **detail}))

    def observe_rule_assessment(self, context, event, assessment):
        if self.fd is None:
            return
        identity = self.before(context, event, "critical_rule")
        self.after(identity, assessment)

    async def close(self):
        try:
            if self.router is not None and self.router.session.trace_sink is self._trace_sink:
                try:
                    self.trace("collector_closed", {"counts": dict(self.counts), "trace_errors": self.router.session.trace_errors})
                except Exception:
                    self.counts["trace_callback_errors"] += 1
                finally:
                    self.router.session.trace_sink = self._previous_trace_sink
            await self.delegate.close()
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
