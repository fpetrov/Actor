"""One local broadcast lifecycle; publication is a separate explicit action.

The injected factory owns private integration details. A prepared run must
prove presentation, model, voice, OBS capture and actual audio readiness before
it is returned. This controller never receives native game-agent state.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from uuid import uuid4

from .config import CommentaryConfig
from .obs import PublicStartPermit


class CoordinatorError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


def reason_code(error, default="broadcast_failed"):
    code = getattr(error, "code", default)
    return code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,64}", code) else default


class BroadcastCoordinator:
    def __init__(self, factory, *, channel="fedorr_codex", config=None, chat_enabled=True, outro_timeout=45, mux_drain_seconds=2):
        if channel != "fedorr_codex": raise CoordinatorError("broadcast_channel_not_configured")
        if not 5 <= outro_timeout <= 90 or not 0 <= mux_drain_seconds <= 5:
            raise CoordinatorError("broadcast_drain_invalid")
        self.factory, self.channel, self.config = factory, channel, config or CommentaryConfig()
        if type(chat_enabled) is not bool: raise CoordinatorError("broadcast_chat_policy_invalid")
        self.chat_enabled = chat_enabled
        self.outro_timeout, self.mux_drain_seconds = outro_timeout, mux_drain_seconds
        self.state, self.reason = "Idle", "not_started"
        self.room_code = self.broadcast_session_id = None
        self._run = None; self._generation = 0; self._closed = False
        self._prepare_task = self._start_task = self._stop_task = self._ending_task = None
        self._tasks = set(); self._lock = asyncio.Lock()
        self._ready = dict.fromkeys(("observer", "model", "tts", "obs", "audio", "chat"), False)
        self._required = frozenset(self._ready) if chat_enabled else frozenset(self._ready) - {"chat"}
        self.metrics = {"preview_starts": 0, "public_starts": 0, "unsafe_stops": 0,
                        "terminal_drains": 0, "outro_timeouts": 0, "cleanup_failures": 0}

    def _background(self, coroutine, name):
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task); task.add_done_callback(self._tasks.discard)
        return task

    def preparing_status(self, generation, flags):
        if generation != self._generation or self.state != "Preparing": return
        for key, value in flags.items():
            if key in self._ready and type(value) is bool: self._ready[key] = value

    async def execute(self, action, payload):
        if not isinstance(payload, dict): raise CoordinatorError("operator_payload_invalid")
        async with self._lock:
            if self._closed: raise CoordinatorError("broadcast_controller_closed")
            if action == "preview":
                if set(payload) != {"room_code"} or not isinstance(payload["room_code"], str) or not re.fullmatch(r"[A-Z0-9]{6}", payload["room_code"]):
                    raise CoordinatorError("broadcast_room_invalid")
                if self.state not in ("Idle", "Stopped", "Failed") or self._run is not None:
                    raise CoordinatorError("broadcast_already_owned")
                if self._prepare_task is not None and not self._prepare_task.done():
                    raise CoordinatorError("broadcast_cleanup_pending")
                if self._stop_task is not None and not self._stop_task.done(): raise CoordinatorError("broadcast_cleanup_pending")
                self._generation += 1; generation = self._generation
                self.broadcast_session_id, self.room_code = uuid4().hex, payload["room_code"]
                self.state, self.reason = "Preparing", "preparing_dependencies"
                self._ready = dict.fromkeys(self._ready, False)
                self._stop_task = self._start_task = self._ending_task = None
                self.metrics["preview_starts"] += 1
                self._prepare_task = self._background(self._prepare(generation), "actor-broadcast-prepare")
            elif action == "public_start":
                if (set(payload) != {"channel", "broadcast_session_id", "confirmation"}
                        or payload["confirmation"] != "START PUBLIC STREAM" or payload["channel"] != self.channel
                        or payload["broadcast_session_id"] != self.broadcast_session_id):
                    raise CoordinatorError("broadcast_public_confirmation_required")
                if self.state == "Live": return {"accepted": True, "state": self.state}
                if self.state != "PreviewReady" or not all(self._ready[key] for key in self._required) or self._run is None:
                    raise CoordinatorError("broadcast_preview_not_ready")
                if self._start_task is None or self._start_task.done():
                    self.reason = "confirming_public_output"
                    self._start_task = self._background(self._public_start(self._generation), "actor-broadcast-public-start")
            elif action in ("stop", "emergency_stop"):
                if payload: raise CoordinatorError("operator_payload_invalid")
                self._request_stop(emergency=action == "emergency_stop")
            elif action == "configure":
                if not set(payload) <= {"idle_enabled", "idle_after_seconds", "allow_memory_callbacks"} or not payload:
                    raise CoordinatorError("broadcast_config_invalid")
                updates = {}
                if "idle_enabled" in payload:
                    if type(payload["idle_enabled"]) is not bool: raise CoordinatorError("broadcast_config_invalid")
                    updates["enabled"] = payload["idle_enabled"]
                if "idle_after_seconds" in payload:
                    value = payload["idle_after_seconds"]
                    if type(value) not in (int, float) or not math.isfinite(value) or not 5 <= value <= 120: raise CoordinatorError("broadcast_config_invalid")
                    updates["after_seconds"] = float(value)
                if "allow_memory_callbacks" in payload:
                    if type(payload["allow_memory_callbacks"]) is not bool: raise CoordinatorError("broadcast_config_invalid")
                    updates["allow_memory_callbacks"] = payload["allow_memory_callbacks"]
                # Validate instead of unchecked Pydantic model_copy mutation.
                data = self.config.model_dump(); data["idle"].update(updates)
                self.config = CommentaryConfig.model_validate(data)
                if self._run is not None: self._run.scheduler.configure(self.config)
            else: raise CoordinatorError("operator_action_unknown")
            return {"accepted": True, "state": self.state}

    async def _prepare(self, generation):
        run = None
        try:
            run = await self.factory.prepare(self.room_code, self.broadcast_session_id, self.config,
                on_ready=lambda flags: self.preparing_status(generation, flags),
                on_unsafe=lambda code: self.unsafe(code, generation=generation),
                on_terminal=lambda: self.terminal(generation=generation))
            async with self._lock:
                if generation != self._generation or self.state != "Preparing": return
                self._run = run; run = None
                flags = self._run.readiness()
                if (set(flags) != set(self._ready) or not all(type(value) is bool for value in flags.values())
                        or not all(flags[key] for key in self._required)):
                    raise CoordinatorError("broadcast_dependency_not_ready")
                self._ready = flags
                # configure may have changed the idle policy while preparation
                # awaited its dependencies. Apply the current validated policy
                # before the returned scheduler is allowed to run.
                self._run.scheduler.configure(self.config)
                if not self._run.scheduler.ready(): raise CoordinatorError("broadcast_presented_view_missing")
                await self._run.activate()
                self.state, self.reason = "PreviewReady", "local_preview_ready"
        except asyncio.CancelledError: raise
        except Exception as error:
            if generation == self._generation:
                self.reason = reason_code(error)
                self._request_stop(emergency=True, final_state="Failed", preserve_reason=True)
        finally:
            if run is not None:
                try: await asyncio.wait_for(run.close(emergency=True), 25)
                except BaseException as error:
                    self.metrics["cleanup_failures"] += 1
                    # A late prepare still owns resources even though its
                    # generation was stopped. Preserve it for stop/retry and
                    # keep further previews fenced until actual cleanup.
                    self._run = run
                    if isinstance(error, asyncio.CancelledError): raise

    async def _public_start(self, generation):
        try:
            run = self._run
            if run is None: raise CoordinatorError("broadcast_preview_not_ready")
            await run.obs.start_stream(PublicStartPermit(self.channel, time.monotonic() + 30, True))
            async with self._lock:
                if generation != self._generation or self.state != "PreviewReady":
                    await run.obs.emergency_stop(); return
                self.state, self.reason = "Live", "public_output_confirmed"
                self.metrics["public_starts"] += 1
                run.scheduler.public_started()
        except asyncio.CancelledError: raise
        except Exception as error:
            self.unsafe(reason_code(error), generation=generation)

    def unsafe(self, code, *, generation=None):
        if generation is not None and generation != self._generation: return
        if self.state not in ("PreviewReady", "Live", "Ending"): return
        self.reason = code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,64}", code) else "broadcast_unsafe"
        self.metrics["unsafe_stops"] += 1
        self._request_stop(emergency=True, final_state="Failed", preserve_reason=True)

    def terminal(self, *, generation=None):
        if generation is not None and generation != self._generation: return
        if self.state not in ("PreviewReady", "Live"): return
        self.state, self.reason = "Ending", "awaiting_terminal_outro"
        self._ending_task = self._background(self._ending(self._generation), "actor-broadcast-ending")

    async def _ending(self, generation):
        deadline = time.monotonic() + self.outro_timeout
        try:
            if self._run is not None: await self._run.obs.show("ACTOR Match")
            while generation == self._generation and self.state == "Ending" and self._run is not None:
                report = self._run.scheduler.report()
                if report.get("terminal_outro_finished") is True:
                    await asyncio.sleep(self.mux_drain_seconds)
                    self.metrics["terminal_drains"] += 1
                    break
                if time.monotonic() >= deadline:
                    self.metrics["outro_timeouts"] += 1; self.reason = "terminal_outro_timeout"; break
                await asyncio.sleep(.1)
            if generation == self._generation and self.state == "Ending": self._request_stop(emergency=False)
        except asyncio.CancelledError: raise
        except Exception as error: self.unsafe(reason_code(error), generation=generation)

    def _request_stop(self, *, emergency, final_state="Stopped", preserve_reason=False):
        if self._stop_task is not None and not self._stop_task.done(): return
        if self.state in ("Idle", "Stopped") and self._run is None:
            self.state, self.reason = "Stopped", "already_stopped"; return
        self._generation += 1
        self.state = "Stopping"
        if not preserve_reason: self.reason = "emergency_stop" if emergency else "stop_requested"
        self._stop_task = self._background(self._stop(emergency, final_state, asyncio.current_task()), "actor-broadcast-stop")

    async def _stop(self, emergency, final_state, initiator):
        clean = True
        try:
            for task in (self._prepare_task, self._start_task, self._ending_task):
                if task is not None and task is not initiator and not task.done():
                    task.cancel()
                    try: await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 30)
                    except Exception: clean = False
            run = self._run
            if run is not None:
                try:
                    await asyncio.wait_for(run.close(emergency=emergency), 35)
                    self._run = None
                except Exception: clean = False  # Preserve the handle for a subsequent stop retry.
            self._ready = dict.fromkeys(self._ready, False)
            if not clean: raise CoordinatorError("broadcast_cleanup_failed")
            self.state = final_state
        except Exception:
            self.metrics["cleanup_failures"] += 1
            self.state, self.reason = "Failed", "broadcast_cleanup_failed"

    async def status(self):
        report = self._run.scheduler.report() if self._run is not None else {}
        details = await self._run.status() if self._run is not None else {}
        pending=report.get('pending_segments')
        pending=pending if type(pending)is int and 0<=pending<=2 else None
        calls=report.get('calls_by_mode');modes={}
        for name in ('reactive','idle_recap','memory_callback'):
            count=calls.get(name,0) if isinstance(calls,dict) else None
            modes[name]=count if type(count)is int and 0<=count<=1000000 else None
        director={};raw=details.get('director')
        for name in ('intents','confirmed_presented_switches','rejected_selections','selection_timeouts',
                'empty_frame_seeks','commands_dispatched','selection_results_accepted','selection_results_rejected'):
            count=raw.get(name) if isinstance(raw,dict) else None
            director[name]=count if type(count)is int and 0<=count<=1000000 else None
        for name in ('pending','terminal'):
            value=raw.get(name) if isinstance(raw,dict) else None
            director[name]=value if type(value)is bool else None
        return {"state": self.state, "reason": self.reason, "channel": self.channel,
            "room_code": self.room_code, "broadcast_session_id": self.broadcast_session_id,
            "rooms": await self.factory.rooms(), "ready": dict(self._ready), "live": self.state == "Live",
            "chat_enabled": self.chat_enabled,
            "camera_director":director,
            "last_spoken": details.get("last_spoken"), "usage": details.get("usage", {}),
            "commentary": {"idle_enabled": self.config.idle.enabled, "idle_after_seconds": self.config.idle.after_seconds,
                "allow_memory_callbacks":self.config.idle.allow_memory_callbacks,
                "pending_speech_segments":pending,"calls_by_mode":modes,
                "idle_reason": report.get("silence_reason"), "idle_deadline_seconds": details.get("idle_deadline_seconds")},
            "metrics": dict(self.metrics)}

    async def aclose(self):
        async with self._lock:
            self._closed = True; self._request_stop(emergency=True)
        if self._stop_task is not None: await asyncio.shield(self._stop_task)
        if self._run is not None or self._prepare_task is not None and not self._prepare_task.done():
            raise CoordinatorError("broadcast_cleanup_failed")
