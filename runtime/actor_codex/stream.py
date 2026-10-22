"""One public SDK stream consumer; keep tool lifecycle counters, never payload logs."""
from dataclasses import dataclass, field

from actor_runtime.errors import ActorError

TOOL_ITEMS = frozenset({"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
    "collabAgentToolCall", "webSearch", "imageView", "imageGeneration", "sleep"})
PASSIVE_ITEMS = frozenset({"userMessage", "agentMessage", "reasoning", "plan", "contextCompaction",
    "functionCallOutput"})  # ExternalMessage output receipt, not a model tool invocation.
MAX_ITEMS = 4096


@dataclass
class ToolObservation:
    started: set[str] = field(default_factory=set)
    completed: set[str] = field(default_factory=set)
    valid: bool = True
    primary_terminal: bool = False

    def observe(self, item, *, started):
        root = getattr(item, "root", item)
        kind = getattr(root, "type", None)
        if kind in PASSIVE_ITEMS:
            return
        identity = getattr(root, "id", None)
        if (kind not in TOOL_ITEMS or not isinstance(identity, str) or not 1 <= len(identity) <= 256
                or len(self.started | self.completed | {identity}) > MAX_ITEMS):
            self.valid = False
            return
        (self.started if started else self.completed).add(identity)

    @property
    def count(self):
        return len(self.started | self.completed)

    @property
    def complete(self):
        return self.valid and self.primary_terminal


async def collect_turn(sdk, handle, observation, *, primary):
    """Preserve public run() result semantics while also observing item starts.

    The initiating handle's SDK subscription buffers from its turn request.
    Joined subscriptions may start later; only the initiating stream can certify
    complete coverage. Shared item IDs deduplicate both lifecycle notifications
    and duplicate handles. Raw completed items live only in this SDK result.
    """
    stream = handle.stream()
    items = []; usage = None; terminal = None; messages = 0
    try:
        async for event in stream:
            messages += 1
            if messages > 100000:
                observation.valid = False
                raise ActorError("codex_stream_limit", "SDK notification bound exceeded")
            payload = event.payload
            if event.method in ("item/started", "item/completed"):
                if getattr(payload, "turn_id", None) != handle.id:
                    observation.valid = False
                    continue
                item = payload.item
                observation.observe(item, started=event.method == "item/started")
                if event.method == "item/completed":
                    if len(items) >= MAX_ITEMS:
                        observation.valid = False
                        raise ActorError("codex_stream_limit", "SDK item bound exceeded")
                    items.append(item)
            elif event.method == "thread/tokenUsage/updated" and getattr(payload, "turn_id", None) == handle.id:
                usage = payload.token_usage
            elif event.method == "turn/completed":
                if getattr(getattr(payload, "turn", None), "id", None) != handle.id:
                    observation.valid = False
                    continue
                terminal = payload.turn
        if terminal is None:
            raise RuntimeError("SDK turn completed event not received")
        status = getattr(terminal.status, "value", terminal.status)
        if status == "failed":
            # Match the SDK's run() failure boundary; never publish error prose.
            raise RuntimeError(getattr(getattr(terminal, "error", None), "message", None) or "SDK turn failed")
        final = legacy = None
        for item in reversed(items):
            root = getattr(item, "root", item)
            if getattr(root, "type", None) != "agentMessage":
                continue
            phase = getattr(getattr(root, "phase", None), "value", getattr(root, "phase", None))
            if phase == "final_answer":
                final = root.text
                break
            if phase is None and legacy is None:
                legacy = root.text
        result = sdk.TurnResult(id=terminal.id, status=terminal.status, error=terminal.error,
            started_at=terminal.started_at, completed_at=terminal.completed_at, duration_ms=terminal.duration_ms,
            final_response=final if final is not None else legacy, items=items, usage=usage)
        if primary:
            observation.primary_terminal = status in ("completed", "interrupted")
        return result
    except BaseException:
        if primary:
            observation.valid = False
        raise
    finally:
        await stream.aclose()
