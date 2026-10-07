#!/usr/bin/env python3
"""Create real VPS matches and verify the configured five-server capacity. No join tickets are fetched."""
import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("base_url")
parser.add_argument("--output", default="build/verification/capacity.json")
args = parser.parse_args()
base = args.base_url.rstrip("/")

def request(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)

_, version = request("/api/v1/game/version")
_, before = request("/api/v1/lobbies")
limit = version["maxConcurrentGameServers"]
created = []
for index in range(limit - len(before["lobbies"])):
    status, lobby = request("/api/v1/lobbies", {"displayName": f"Capacity validation {index + 1}",
        "maxPlayers": 8, "gameVersion": version["gameVersion"], "protocolVersion": version["protocolVersion"]})
    assert status == 201, (status, lobby)
    created.append(lobby)
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    _, active = request("/api/v1/lobbies")
    if len(active["lobbies"]) == limit and all(l["status"] in ("Ready", "Running") for l in active["lobbies"]):
        break
    time.sleep(0.5)
else:
    raise RuntimeError("Five real match servers did not become ready.")
sixth_status, sixth = request("/api/v1/lobbies", {"displayName": "Sixth validation room", "maxPlayers": 8,
    "gameVersion": version["gameVersion"], "protocolVersion": version["protocolVersion"]})
health_status, health = request("/health")
ports = [l["gamePort"] for l in active["lobbies"]]
report = {"baseUrl": base, "limit": limit, "created": created, "active": active["lobbies"],
    "ports": ports, "sixthHttpStatus": sixth_status, "sixthErrorCode": sixth.get("error", {}).get("code"),
    "healthHttpStatus": health_status, "health": health,
    "passed": len(ports) == limit and len(set(ports)) == limit and sixth_status == 409
        and sixth.get("error", {}).get("code") == "server_capacity_reached" and health_status == 200}
path = Path(args.output)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({k: report[k] for k in ("ports", "sixthHttpStatus", "sixthErrorCode", "passed")}))
assert report["passed"]
