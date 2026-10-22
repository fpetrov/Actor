#!/usr/bin/env python3
"""Version-bound M8 entry point for existing isolated acceptance helpers.

Plan-only by default. --execute performs a bounded, read-only public staging
preflight, then execs the original helper unchanged; that helper owns all child
cleanup. No server mutation, credentials, model context or debug game API.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import os
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
API = "http://127.0.0.1:5081"
HELPERS = {
    "privacy": "spikes/social-privacy/run_probe.py",
    "protocol": "spikes/social-protocol/controller.py",
    "fault": "spikes/social-faults/run_faults.py",
    "lifecycle": "spikes/social-faults/lifecycle.py",
    "sandbox": "spikes/sandbox-regression/run_case.py",
}
COMMON = {"--timeout": 1, "--base-port": 1, "--config": 1}
OPTIONS = {
    "privacy": {**COMMON, "--server-summary": 1, "--probe-out-of-sight-kill": 0},
    "protocol": COMMON,
    "fault": {**COMMON, "--case": 1, "--phase": 1, "--component": 1, "--participant": 1},
    "lifecycle": {"--case": 1, "--port": 1, "--timeout": 1, "--ready-social": 0,
        "--skill": 1, "--target-room": 1},
    "sandbox": {"--case": 1, "--base-port": 1, "--timeout": 1},
}


class Rejected(ValueError):
    """Fixed public diagnostic, never embeds response text or input paths."""


def require(condition, code):
    if not condition:
        raise Rejected(code)


def extra_options(suite, values):
    values = list(values)
    if values[:1] == ["--"]:
        values.pop(0)
    seen = set(); index = 0
    while index < len(values):
        key = values[index]
        require(key in OPTIONS[suite] and key not in seen, "unsupported_helper_option")
        seen.add(key); arity = OPTIONS[suite][key]
        require(index + arity < len(values), "missing_helper_option_value")
        if arity:
            require(not values[index + 1].startswith("--"), "missing_helper_option_value")
        index += arity + 1
    require(suite not in ("fault", "lifecycle", "sandbox") or "--case" in seen, "helper_case_required")
    return values


def mode_for(suite, extras):
    return "Sandbox" if suite == "sandbox" or suite == "lifecycle" and "--ready-social" not in extras else "SocialDeduction"


def validate_public_target(version, lobby, args, *, now=None):
    require(isinstance(version, dict) and isinstance(lobby, dict), "public_object_required")
    require(version.get("gameVersion") == args.game_version
        and version.get("protocolVersion") == args.protocol_version, "public_version_mismatch")
    try:
        match = uuid.UUID(lobby.get("lobbyId", "")).hex
        created = datetime.fromisoformat(lobby["createdAt"].replace("Z", "+00:00"))
        require(created.tzinfo is not None, "fresh_room_required")
        age = (time.time() if now is None else now) - created.timestamp()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise Rejected("fresh_room_required") from None
    require(match == args.match_id and lobby.get("roomCode") == args.room, "exact_room_match_required")
    require(lobby.get("gameVersion") == args.game_version
        and lobby.get("protocolVersion") == args.protocol_version, "room_version_mismatch")
    require(lobby.get("gameMode") == mode_for(args.suite, args.helper_options), "room_mode_mismatch")
    require(lobby.get("status") == "Ready" and type(lobby.get("currentPlayers")) is int
        and lobby["currentPlayers"] == 0 and type(lobby.get("maxPlayers")) is int
        and 4 <= lobby["maxPlayers"] <= 6 and type(lobby.get("gamePort")) is int
        and lobby["gamePort"] == 7781 and 0 <= age <= 600, "fresh_empty_staging_room_required")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        raise Rejected("public_redirect_rejected")


def fetch_json(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with opener.open(request, timeout=5) as response:
        require(response.status == 200 and response.geturl() == url, "exact_public_endpoint_required")
        data = response.read(65537)
    require(len(data) <= 65536, "public_response_too_large")
    value = json.loads(data)
    require(isinstance(value, dict), "public_object_required")
    return value


def command(args):
    values = [str(ROOT / HELPERS[args.suite]), "--room", args.room, "--output", str(args.output),
        "--client", str(args.client)]
    if args.suite != "sandbox":
        values += ["--api-url", args.api_url]
    else:
        values += ["--execute"]
    if args.suite == "lifecycle":
        values += ["--match-id", args.match_id]
    return [sys.executable, *values, *args.helper_options]


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--suite", choices=HELPERS, required=True)
    parser.add_argument("--room", required=True)
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--client", type=Path, default=ROOT / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena")
    parser.add_argument("--api-url", default=API, help="Dedicated loopback staging endpoint only")
    parser.add_argument("--game-version", default="0.9.0")
    parser.add_argument("--protocol-version", default="4")
    parser.add_argument("--execute", action="store_true", help="Requires an exclusive staging lease; otherwise no network or process launch")
    parser.add_argument("helper_options", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    require(args.api_url == API and re.fullmatch(r"[A-Z0-9]{6}", args.room) is not None, "invalid_staging_scope")
    require(re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", args.game_version) is not None
        and re.fullmatch(r"[1-9][0-9]{0,2}", args.protocol_version) is not None, "invalid_expected_version")
    try:
        args.match_id = uuid.UUID(args.match_id).hex
    except (ValueError, AttributeError):
        raise Rejected("invalid_match_id") from None
    args.output = args.output.resolve(); args.client = args.client.resolve()
    require(args.output != ROOT / "build" and args.output.is_relative_to(ROOT / "build"), "output_inside_build_required")
    require(not args.output.exists() or args.output.is_dir() and not any(args.output.iterdir()), "fresh_output_required")
    args.helper_options = extra_options(args.suite, args.helper_options)
    return args


def main(argv=None):
    try:
        args = arguments(argv)
        if args.execute:
            require(args.client.is_file(), "ordinary_client_required")
            validate_public_target(fetch_json(args.api_url + "/api/v1/game/version"),
                fetch_json(args.api_url + "/api/v1/lobbies/" + args.room), args)
        print(json.dumps({"schema": "actor.version-bound-acceptance-launch.v1", "suite": args.suite,
            "execute": args.execute, "public_preflight_passed": args.execute,
            "expected_game_version": args.game_version, "expected_protocol_version": args.protocol_version,
            "scope": "Existing acceptance helper; no implicit gameplay, privacy or fault PASS."}), flush=True)
        if args.execute:
            os.execv(sys.executable, command(args))
        return 0
    except Rejected as error:
        code = str(error)
    except (OSError, ValueError):
        code = "bounded_public_preflight_failed"
    print(json.dumps({"schema": "actor.version-bound-acceptance-launch.v1", "execute": False,
        "public_preflight_passed": False, "error_code": code}), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
