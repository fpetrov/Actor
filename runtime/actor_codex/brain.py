"""Codex implements the same Brain interface used by deterministic providers."""
from __future__ import annotations

import json
import asyncio
import logging
import time
from pydantic import ValidationError

from actor_runtime.brains import DECISION_ADAPTER, InvalidBrainDecision
from actor_runtime.errors import ActorError
from .adapter import OfficialCodexAdapter


class CodexBrain:
    # Trusted diagnostic identifiers only; no provider message/details or game
    # state is admitted to this optional generic Runtime logging contract.
    public_error_codes = frozenset({
        "codex_not_installed", "codex_version_mismatch", "codex_episode_busy",
        "codex_authentication_required", "codex_episode_unavailable", "codex_turn_busy",
        "codex_turn_incomplete", "codex_provider_error", "codex_interrupt_timeout",
        "codex_turn_limit", "codex_usage_limit", "codex_preemption_unsupported",
        "codex_stream_limit",
    })

    def __init__(self, *, model=None, adapter=None, max_turns=24,
                 usage_warning_tokens=100000, usage_limit_tokens=200000):
        if (type(max_turns) is not int or max_turns < 1 or
                type(usage_warning_tokens) is not int or usage_warning_tokens < 1 or
                type(usage_limit_tokens) is not int or usage_limit_tokens < usage_warning_tokens):
            raise ValueError("Codex episode limits must be positive and ordered")
        self.adapter = adapter or OfficialCodexAdapter(model=model)
        self.turns = []
        self.max_turns, self.usage_warning_tokens, self.usage_limit_tokens = max_turns, usage_warning_tokens, usage_limit_tokens
        self.usage_totals = {}
        self.usage_warning = False
        self._active = False
        self._epoch = 0

    async def start(self, objective, catalog):
        self.turns.clear()
        self.usage_totals.clear()
        self.usage_warning = False
        self._epoch = 0
        await self.adapter.start_episode(objective.text, catalog)

    async def decide(self, context):
        if self._active:
            raise ActorError("codex_turn_busy", "A Codex decision is already active")
        self._refresh_usage()
        if len(self.turns) >= self.max_turns:
            raise ActorError("codex_turn_limit", "Codex Objective turn limit reached")
        if self.usage_totals.get("total_tokens", 0) >= self.usage_limit_tokens:
            raise ActorError("codex_usage_limit", "Codex Objective measured token budget reached")
        self._active = True
        epoch, started = self._epoch, time.monotonic()
        record = {"decision_index": context.decision_index, "cognitive_revision": getattr(context, "cognitive_revision", 0),
                  "status": "running", "duration_seconds": 0.0}
        self.turns.append(record)
        try:
            reply = await self.adapter.decide(context.external_json())
            record.update(turn_id=reply.turn_id, duration_seconds=reply.duration_seconds, status=reply.status,
                          usage=reply.usage)
            if type(reply.tool_calls) is int and reply.tool_calls >= 0:
                record.update(tool_calls=reply.tool_calls, tool_count_scope=reply.tool_count_scope)
            self._refresh_usage(reply.usage)
            if epoch != self._epoch:
                record["status"] = "stale"
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            record["status"] = "interrupted"
            raise
        except Exception:
            record["status"] = "failed"
            raise
        finally:
            record["duration_seconds"] = time.monotonic() - started
            telemetry = getattr(self.adapter, "last_decision_tool_telemetry", None)
            if callable(telemetry):
                value = telemetry()
                if (value.get("tool_count_complete") is True and value.get("tool_count_scope") == "sdk_item_lifecycle"
                        and type(value.get("tool_calls")) is int and value["tool_calls"] >= 0):
                    record.update(tool_calls=value["tool_calls"], tool_count_scope="sdk_item_lifecycle")
            self._refresh_usage()
            self._active = False
        try:
            if len(reply.output.encode("utf-8")) > 65536:
                raise ValueError("Structured decision is too large")
            payload = json.loads(reply.output)
            if not isinstance(payload, dict) or set(payload) != {"decision"}:
                raise ValueError("Output must contain exactly the structured decision")
            return DECISION_ADAPTER.validate_python(payload["decision"])
        except (ValueError, TypeError, ValidationError) as error:
            raise InvalidBrainDecision("Codex output failed structured BrainDecision validation",
                                       {"provider_code": "codex_invalid_output"}) from error

    async def interrupt(self):
        await self.adapter.interrupt()

    async def interrupt_decision(self):
        self._epoch += 1
        interrupt = getattr(self.adapter, "interrupt_decision", None)
        if not callable(interrupt):
            raise ActorError("codex_preemption_unsupported", "Adapter cannot interrupt a turn nonterminally")
        await interrupt()

    def _refresh_usage(self, usage=None):
        values = usage.get("total", {}) if isinstance(usage, dict) else {}
        adapter_values = self.adapter.report().get("usage_totals", {}) if hasattr(self.adapter, "report") else {}
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"):
            for value in (values.get(key), adapter_values.get(key)):
                if type(value) is int and value >= 0:
                    self.usage_totals[key] = max(value, self.usage_totals.get(key, 0))
        if not self.usage_warning and self.usage_totals.get("total_tokens", 0) >= self.usage_warning_tokens:
            self.usage_warning = True
            logging.getLogger("actor_runtime").warning("Codex Objective token warning threshold reached")

    async def close(self):
        await self.adapter.close()

    def report(self):
        return {"provider": "codex", "decision_count": len(self.turns), "turns": list(self.turns),
                "usage_totals": dict(self.usage_totals), "usage_warning": self.usage_warning,
                "limits": {"max_turns": self.max_turns, "usage_warning_tokens": self.usage_warning_tokens,
                           "usage_limit_tokens": self.usage_limit_tokens},
                "adapter": self.adapter.report() if hasattr(self.adapter, "report") else None}
