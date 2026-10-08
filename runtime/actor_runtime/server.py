"""Authenticated IPv4-loopback grpc.aio server and session lifecycle."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import hmac
import logging
import time
import tracemalloc

import grpc
from actor.core.v1 import actor_pb2 as core
from actor.core.v1 import bridge_pb2 as wire
from actor.core.v1 import bridge_pb2_grpc

from .errors import ActorError
from .extensions import ExtensionRegistry
from .session import RuntimeSession

LOG = logging.getLogger("actor_runtime")


class RuntimeServer(bridge_pb2_grpc.ActorBridgeServicer):
    def __init__(self, registry: ExtensionRegistry, token: str, *, port=50051,
                 command_timeout_ms=250, peer_timeout_seconds=5):
        if len(token) < 32 or not token.isascii():
            raise ActorError("ConfigurationInvalid", "ACTOR_BRIDGE_TOKEN needs at least 32 ASCII characters")
        if not 0 <= port <= 65535:
            raise ActorError("ConfigurationInvalid", "Invalid loopback port")
        if not 1 <= command_timeout_ms <= 5000 or peer_timeout_seconds <= 0:
            raise ActorError("ConfigurationInvalid", "Invalid command or peer timeout")
        self.registry = registry
        self.token = token
        self.port = port
        self.command_timeout_ms = command_timeout_ms
        self.peer_timeout_seconds = peer_timeout_seconds
        self.sessions: set[RuntimeSession] = set()
        self.completed = []
        self.available: OrderedDict[RuntimeSession, None] = OrderedDict()
        self.availability_changed = asyncio.Event()
        self.stopping = False
        self.server = grpc.aio.server(options=[
            ("grpc.so_reuseport", 0),
            ("grpc.max_receive_message_length", 1024 * 1024),
            ("grpc.max_send_message_length", 1024 * 1024),
        ], maximum_concurrent_rpcs=8)
        bridge_pb2_grpc.add_ActorBridgeServicer_to_server(self, self.server)
        self.started_at = time.monotonic()
        self.cpu_started = time.process_time()

    async def start(self):
        try:
            bound = self.server.add_insecure_port(f"127.0.0.1:{self.port}")
        except RuntimeError as error:
            raise ActorError("BindFailed", "Could not bind loopback gRPC endpoint") from error
        if not bound:
            raise ActorError("BindFailed", "Could not bind loopback gRPC endpoint")
        self.port = bound
        await self.server.start()
        LOG.info("Bridge listening 127.0.0.1:%s", self.port)

    async def Connect(self, request_iterator, context):
        supplied = dict(context.invocation_metadata()).get("x-actor-bridge-token", "")
        if not hmac.compare_digest(supplied.encode(), self.token.encode()):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid bridge session token")
        session = RuntimeSession(self.registry, command_timeout_ms=self.command_timeout_ms)
        self.sessions.add(session)
        published = False

        async def consume():
            nonlocal published
            try:
                async for message in request_iterator:
                    await session.accept(message)
                    if session.ready.is_set() and not published:
                        self._publish_session(session)
                        published = True
            except ActorError as error:
                session.fail(error)
            finally:
                session.mailbox.finish()

        async def heartbeat():
            next_log = time.monotonic() + 10
            while not session.closed.is_set():
                await asyncio.sleep(1)
                session.expire_results()
                if time.monotonic() - session.received_at > self.peer_timeout_seconds:
                    session.fail(ActorError("PeerTimeout", "Game stream heartbeat deadline elapsed"))
                    return
                if session.ready.is_set() and not session.mailbox.closed:
                    try:
                        session.mailbox.put_control(wire.RuntimeToGame(
                            heartbeat=core.Heartbeat(last_received_sequence=session.last_received_sequence)))
                    except ActorError:
                        # A stalled peer must not leave an orphaned heartbeat task.
                        session.mailbox.close()
                        return
                    if time.monotonic() >= next_log:
                        LOG.info("Session actor=%s observations=%s commands_sent=%s events=%s",
                                 session.world.self_actor_id, session.observation_frames,
                                 session.commands_sent, session.events_received)
                        next_log = time.monotonic() + 10

        reader = asyncio.create_task(consume(), name="actor-game-reader")
        ticker = asyncio.create_task(heartbeat(), name="actor-runtime-heartbeat")
        try:
            async for outgoing in session.outgoing():
                yield outgoing
        finally:
            session.close()
            for task in (reader, ticker):
                task.cancel()
            await asyncio.gather(reader, ticker, return_exceptions=True)
            self.sessions.discard(session)
            self.available.pop(session, None)
            self.completed.append(session.summary())
            self.completed = self.completed[-16:]
            LOG.info("Unity disconnected session=%s commands_sent=%s", session.session_id,
                     session.commands_sent)

    async def next_session(self, timeout=60) -> RuntimeSession:
        async with asyncio.timeout(timeout):
            while True:
                if self.stopping:
                    raise ActorError("RuntimeUnavailable", "Runtime is shutting down")
                while self.available:
                    session, _ = self.available.popitem(last=False)
                    if not session.closed.is_set() and not session.mailbox.closed:
                        return session
                self.availability_changed.clear()
                await self.availability_changed.wait()

    def _publish_session(self, session: RuntimeSession):
        for previous in tuple(self.available):
            if previous.closed.is_set() or previous.mailbox.closed:
                self.available.pop(previous)
        if len(self.available) >= 8:
            raise ActorError("RuntimeBusy", "Too many active waiting game sessions")
        if not session.closed.is_set() and not session.mailbox.closed:
            self.available[session] = None
            self.availability_changed.set()

    def report(self):
        wall = time.monotonic() - self.started_at
        cpu = time.process_time() - self.cpu_started
        report = {"endpoint": f"127.0.0.1:{self.port}", "elapsed_seconds": wall,
                "cpu_seconds": cpu, "cpu_percent_one_core": cpu / max(wall, 0.001) * 100,
                "sessions": self.completed + [session.summary() for session in self.sessions]}
        if tracemalloc.is_tracing():
            current, peak = tracemalloc.get_traced_memory()
            report["python_traced_memory_bytes"] = {"current": current, "peak": peak}
        return report

    async def stop(self):
        self.stopping = True
        self.availability_changed.set()
        try:
            for session in tuple(self.sessions):
                session.disconnect("Runtime shutdown")
        finally:
            try:
                await self.server.stop(grace=1)
            finally:
                try:
                    await self.server.wait_for_termination()
                finally:
                    for session in tuple(self.sessions):
                        session.close()
                    self.sessions.clear()
                    self.available.clear()
