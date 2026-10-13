"""Codex implements the same Brain interface used by deterministic providers."""
from __future__ import annotations

import json
from pydantic import ValidationError

from actor_runtime.brains import DECISION_ADAPTER, InvalidBrainDecision
from .adapter import OfficialCodexAdapter


class CodexBrain:
    def __init__(self, *, model=None, adapter=None):
        self.adapter = adapter or OfficialCodexAdapter(model=model)
        self.turns = []

    async def start(self, objective, catalog):
        self.turns.clear()
        await self.adapter.start_episode(objective.text, catalog)

    async def decide(self, context):
        reply = await self.adapter.decide(context.external_json())
        self.turns.append({"decision_index": context.decision_index, "turn_id": reply.turn_id,
                           "duration_seconds": reply.duration_seconds, "status": reply.status,
                           "usage": reply.usage, "tool_calls": reply.tool_calls})
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

    async def close(self):
        await self.adapter.close()

    def report(self):
        return {"provider": "codex", "decision_count": len(self.turns), "turns": list(self.turns),
                "adapter": self.adapter.report() if hasattr(self.adapter, "report") else None}
