"""Isolated true-bidi Unity compatibility fixture; this is not ACTOR Core."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
from pathlib import Path
import signal
import time

import grpc
from google.protobuf.wrappers_pb2 import BytesValue

METHOD = "/actor.spike.Echo/Connect"


class EchoService:
    def __init__(self, token: str, tick_seconds: float, endpoint: str, status: Path):
        self.token = token
        self.tick_seconds = tick_seconds
        self.endpoint = endpoint
        self.status = status
        self.connected = 0
        self.active = 0
        self.received = 0
        self.echoed = 0
        self.ticks = 0
        self.state = "starting"

    def log(self, event: str, **fields):
        print(json.dumps({"event": event, **fields}), flush=True)

    def write_status(self):
        payload = {
            "pid": os.getpid(), "endpoint": self.endpoint, "method": METHOD,
            "state": self.state, "sessions_connected": self.connected,
            "sessions_active": self.active, "received": self.received,
            "echoed": self.echoed, "ticks": self.ticks,
            "updated_at_unix": time.time(),
        }
        self.status.parent.mkdir(parents=True, exist_ok=True)
        temp = self.status.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n")
        temp.replace(self.status)

    async def connect(self, requests, context):
        supplied = dict(context.invocation_metadata()).get("x-actor-spike-token", "")
        if not hmac.compare_digest(supplied.encode(), self.token.encode()):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid spike session token")
        self.connected += 1
        session = self.connected
        self.active += 1
        self.log("connected", session=session, peer=context.peer())
        self.write_status()
        echoes: asyncio.Queue[BytesValue] = asyncio.Queue(maxsize=32)
        finished = asyncio.Event()

        async def reader():
            try:
                async for message in requests:
                    self.received += 1
                    await echoes.put(BytesValue(value=b"echo:" + message.value))
            finally:
                finished.set()

        read_task = asyncio.create_task(reader(), name=f"spike-reader-{session}")
        tick = 0
        next_tick = asyncio.get_running_loop().time() + self.tick_seconds
        try:
            while not finished.is_set() or not echoes.empty():
                delay = max(0, next_tick - asyncio.get_running_loop().time())
                try:
                    reply = await asyncio.wait_for(echoes.get(), timeout=delay)
                    self.echoed += 1
                    yield reply
                except TimeoutError:
                    tick += 1
                    self.ticks += 1
                    yield BytesValue(value=f"tick:{tick}".encode("ascii"))
                    next_tick = asyncio.get_running_loop().time() + self.tick_seconds
                self.write_status()
            await read_task
        finally:
            read_task.cancel()
            await asyncio.gather(read_task, return_exceptions=True)
            self.active -= 1
            self.log("disconnected", session=session)
            self.write_status()


async def serve(args):
    token = os.environ.get("ACTOR_SPIKE_TOKEN", "")
    if len(token) < 32:
        raise ValueError("ACTOR_SPIKE_TOKEN must contain at least 32 characters")
    if not 0 <= args.port <= 65535:
        raise ValueError("Invalid localhost port")
    if args.tick_seconds <= 0:
        raise ValueError("Tick interval must be positive")
    server = grpc.aio.server(options=[
        ("grpc.max_receive_message_length", 65536),
        ("grpc.max_send_message_length", 65536),
    ])
    service = EchoService(token, args.tick_seconds, f"127.0.0.1:{args.port}", args.status)
    handler = grpc.stream_stream_rpc_method_handler(
        service.connect,
        request_deserializer=BytesValue.FromString,
        response_serializer=BytesValue.SerializeToString,
    )
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(
        "actor.spike.Echo", {"Connect": handler},
    ),))
    bound = server.add_insecure_port(service.endpoint)
    if not bound:
        raise OSError("Cannot bind loopback gRPC endpoint")
    service.endpoint = f"127.0.0.1:{bound}"
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopping.set)
    await server.start()
    service.state = "listening"
    service.write_status()
    service.log("listening", endpoint=service.endpoint, method=METHOD)
    try:
        await stopping.wait()
    finally:
        service.state = "stopping"
        service.write_status()
        await server.stop(grace=1.0)
        await server.wait_for_termination()
        service.state = "stopped"
        service.write_status()
        service.log("stopped", sessions=service.connected, received=service.received,
                    echoed=service.echoed, ticks=service.ticks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=50061)
    parser.add_argument("--tick-seconds", type=float, default=0.1)
    parser.add_argument("--status", type=Path,
                        default=Path("build/grpc-spike/python-status.json"))
    asyncio.run(serve(parser.parse_args()))


if __name__ == "__main__":
    main()
