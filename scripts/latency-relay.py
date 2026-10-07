#!/usr/bin/env python3
"""One-client loopback UDP relay for real Unity/VPS latency tests (no ticket access)."""
import argparse
import heapq
import random
import select
import socket
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("host")
parser.add_argument("port", type=int)
parser.add_argument("--listen-port", type=int, default=17777)
parser.add_argument("--delay-ms", type=float, default=100)
parser.add_argument("--jitter-ms", type=float, default=10)
parser.add_argument("--loss-percent", type=float, default=3)
args = parser.parse_args()
if not (0 <= args.delay_ms <= 1000 and 0 <= args.jitter_ms <= args.delay_ms and 0 <= args.loss_percent <= 50):
    parser.error("Invalid delay/jitter/loss")
server = (socket.gethostbyname(args.host), args.port)
incoming = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
incoming.bind(("127.0.0.1", args.listen_port))
outgoing = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
outgoing.connect(server)
for sock in (incoming, outgoing):
    sock.setblocking(False)
client = None
queue = []
counter = 0
rng = random.Random(42)
print(f"Loopback UDP {args.listen_port} → {server[0]}:{server[1]}, each direction {args.delay_ms}±{args.jitter_ms} ms, loss {args.loss_percent}%", flush=True)
try:
    while True:
        now = time.monotonic()
        while queue and queue[0][0] <= now:
            _, _, sock, data, destination = heapq.heappop(queue)
            if destination is None:
                sock.send(data)
            else:
                sock.sendto(data, destination)
        ready, _, _ = select.select([incoming, outgoing], [], [], max(0, min(0.02, queue[0][0] - now)) if queue else 0.02)
        for sock in ready:
            try:
                data, source = sock.recvfrom(65535)
            except (BlockingIOError, ConnectionRefusedError):
                continue
            if sock is incoming:
                if client is None:
                    client = source
                if source != client:
                    continue
                target, destination = outgoing, None
            else:
                if client is None:
                    continue
                target, destination = incoming, client
            if rng.random() * 100 < args.loss_percent:
                continue
            delay = (args.delay_ms + rng.uniform(-args.jitter_ms, args.jitter_ms)) / 1000
            counter += 1
            heapq.heappush(queue, (time.monotonic() + delay, counter, target, data, destination))
except KeyboardInterrupt:
    pass
finally:
    incoming.close()
    outgoing.close()
