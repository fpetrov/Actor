"""Exercise independent bidi IO, binary echo, cancellation and reconnect."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import statistics
import time

import grpc
from google.protobuf.wrappers_pb2 import BytesValue

from server import METHOD

ROOT = Path(__file__).resolve().parents[3]


async def run(endpoint: str):
    token = (ROOT / "build/grpc-spike/token").read_text().strip()
    metadata = (("x-actor-spike-token", token),)
    checks = []
    latencies = []
    async with grpc.aio.insecure_channel(endpoint) as channel:
        rpc = channel.stream_stream(METHOD, request_serializer=BytesValue.SerializeToString,
                                    response_deserializer=BytesValue.FromString)
        call = rpc(metadata=metadata)
        first = await asyncio.wait_for(call.read(), timeout=3)
        assert first.value.startswith(b"tick:")
        checks.append("server writes before any client payload")
        for payload in (b"hello", b"\x00\x01binary\xff", b"shutdown-cancel"):
            await call.write(BytesValue(value=payload))
            while True:
                reply = await asyncio.wait_for(call.read(), timeout=3)
                if reply.value.startswith(b"echo:"):
                    assert reply.value == b"echo:" + payload
                    break
        checks.append("client binary stream and tagged replies")
        assert call.cancel()
        try:
            await call.read()
            raise AssertionError("Cancellation did not stop reading")
        except asyncio.CancelledError:
            checks.append("client cancellation")

        reconnected = rpc(metadata=metadata)
        for i in range(30):
            start = time.monotonic()
            payload = f"reconnect-{i}".encode()
            await reconnected.write(BytesValue(value=payload))
            while True:
                reply = await asyncio.wait_for(reconnected.read(), timeout=3)
                if reply.value.startswith(b"echo:"):
                    assert reply.value == b"echo:" + payload
                    latencies.append((time.monotonic() - start) * 1000)
                    break
        checks.append("reconnect and 30 streamed messages")
        await reconnected.done_writing()
        while await asyncio.wait_for(reconnected.read(), timeout=3) is not grpc.aio.EOF:
            pass
        assert await reconnected.code() == grpc.StatusCode.OK
        checks.append("half-close drains and completes gracefully")

        rejected = rpc(metadata=(("x-actor-spike-token", "wrong"),))
        try:
            await rejected.read()
            raise AssertionError("Invalid authentication was accepted")
        except grpc.aio.AioRpcError as error:
            assert error.code() == grpc.StatusCode.UNAUTHENTICATED
            checks.append("invalid token rejected")

    ordered = sorted(latencies)
    report = {
        "passed": True, "endpoint": endpoint, "method": METHOD, "checks": checks,
        "rtt_ms": {"samples": len(ordered), "median": statistics.median(ordered),
                   "p95": ordered[int((len(ordered) - 1) * 0.95)], "max": max(ordered)},
    }
    path = ROOT / "build/grpc-spike/python-selftest.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="127.0.0.1:50061")
    args = parser.parse_args()
    asyncio.run(run(args.endpoint))


if __name__ == "__main__":
    main()
