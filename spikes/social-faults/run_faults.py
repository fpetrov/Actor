#!/usr/bin/env python3
"""Opt-in actual-game social fault acceptance; never a game-control interface.

Four ordinary, isolated fixture agents receive the same Objective/provider.
The supervisor can kill only a child Popen it created and recorded itself.
An ordinary authenticated Runtime wrapper passively audits permitted wire data.
Nothing in this file assigns a role, forces a result or sends a game command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "actor.social-fault-acceptance.v1"
PRIVATE_SCHEMA = "actor.social-fault-wire-private.v1"
CASES = ("runtime-loss", "client-loss", "late-reconnect", "exploration-vs-meeting")
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")
OBJECTIVE = (
    "Play the match autonomously. Preserve your life, pursue your own side's victory conditions, "
    "and make decisions based only on observations, memory and communication legitimately available to your character. "
    "A finished Objective ends one bounded episode; only the authoritative game determines the match outcome."
)


def read(path):
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def number(value, minimum=0, maximum=1_000_000_000):
    return type(value) in (float, int) and math.isfinite(value) and minimum <= value <= maximum


def enum(value, prefix):
    return value.removeprefix(prefix) if isinstance(value, str) else ""


def protobuf_integer(value):
    if type(value) is str and len(value) <= 20 and value.isascii() and value.isdecimal():
        return int(value)
    return value


def summary_projection(value):
    """A strict postgame DTO; no generic nested report or prose is copied."""
    if not isinstance(value, dict) or not identifier(value.get("match_id")) or value.get("mode") != "SocialDeduction":
        return None
    players, replay = value.get("players"), value.get("replay", [])
    if not isinstance(players, list) or not 4 <= len(players) <= 6 or not isinstance(replay, list) or len(replay) > 256:
        return None
    result = {"match_id": value["match_id"], "mode": "SocialDeduction",
        "winning_side": enum(value.get("winning_side", "SOCIAL_ALIGNMENT_UNSPECIFIED"), "SOCIAL_ALIGNMENT_"),
        "win_reason": value.get("win_reason"), "duration_ms": protobuf_integer(value.get("duration_ms", 0)),
        "kills": value.get("kills", 0), "replay_truncated": value.get("replay_truncated", False), "players": [], "replay": []}
    if (result["winning_side"] not in ("CREW", "IMPOSTOR", "UNSPECIFIED") or not identifier(result["win_reason"])
            or not number(result["duration_ms"], 0, 4_000_000) or type(result["kills"]) is not int
            or not 0 <= result["kills"] <= 6 or type(result["replay_truncated"]) is not bool):
        return None
    for player in players:
        if not isinstance(player, dict) or not identifier(player.get("actor_id")):
            return None
        alignment = enum(player.get("alignment"), "SOCIAL_ALIGNMENT_")
        life = enum(player.get("life"), "SOCIAL_LIFE_")
        if alignment not in ("CREW", "IMPOSTOR") or life not in ("ALIVE", "DEAD", "EJECTED", "DISCONNECTED"):
            return None
        result["players"].append({"actor_id": player["actor_id"], "alignment": alignment, "life": life})
    if len({item["actor_id"] for item in result["players"]}) != len(players):
        return None
    previous = -1
    for item in replay:
        if not isinstance(item, dict):
            return None
        sequence = item.get("sequence")
        elapsed = item.get("elapsed_ms", 0)
        try:
            # Protobuf JSON represents uint64/int64 with decimal strings.
            sequence = protobuf_integer(sequence)
            elapsed = protobuf_integer(elapsed)
        except (ValueError, OverflowError):
            return None
        if (type(sequence) is not int or sequence <= previous or not number(elapsed, 0, 4_000_000)
                or any(item.get(key, "") and not identifier(item[key]) for key in ("kind", "actor_id", "target_id", "outcome"))):
            return None
        previous = sequence
        result["replay"].append({"sequence": sequence, "elapsed_ms": elapsed,
            **{key: item.get(key, "") for key in ("kind", "actor_id", "target_id", "outcome")}})
    # A truncated replay cannot establish that no kill targeted the forfeiter.
    return result


def canonical_summary(value):
    """Accept the auditor's already projected summary with the same validation."""
    if not isinstance(value, dict):
        return None
    raw = dict(value)
    raw["winning_side"] = "SOCIAL_ALIGNMENT_" + value.get("winning_side", "")
    raw["players"] = [{**p, "alignment": "SOCIAL_ALIGNMENT_" + p.get("alignment", ""),
        "life": "SOCIAL_LIFE_" + p.get("life", "")} for p in value.get("players", []) if isinstance(p, dict)]
    return summary_projection(raw)


class WireAudit:
    """Private local diagnostics from one permitted stream; never Brain input."""
    def __init__(self, path=None, *, clock=time.monotonic, unix=lambda: int(time.time() * 1000)):
        from extensions.agents_arena.v1 import social_pb2 as social
        from actor.standard.v1.transform2d_pb2 import Transform2DObservation
        from actor.standard.v1.motion2d_pb2 import Motion2DObservation
        self.pb, self.transform, self.motion = social, Transform2DObservation, Motion2DObservation
        self.types = {"agents-arena.social-match": social.SocialMatchStateObservation,
            "agents-arena.social-own-state": social.SocialOwnStateObservation,
            "agents-arena.social-visible-world": social.SocialVisibleWorldObservation,
            "agents-arena.social-postgame": social.SocialPostgameObservation,
            "agents-arena.social-match-changed": social.SocialMatchChangedEvent,
            "agents-arena.social-own-state-changed": social.SocialOwnStateChangedEvent,
            "agents-arena.social-match-ended": social.SocialMatchEndedEvent,
            "actor.standard.transform2d": self.transform, "actor.standard.motion2d": self.motion}
        self.path, self.clock, self.unix = path, clock, unix
        self.self_id = ""; self.match_id = ""; self.phase = ""; self.phase_revision = 0; self.kill_cooldown_ms = 0
        self.phase_ends_at_unix_ms = 0
        self.terminal = False; self.own = {}; self.summary = None
        self.body_victims = set(); self.streams = 0; self.frames = 0; self.faults = 0; self.commands = 0
        self.observed_unix_ms = 0; self.next_save = 0.0
        self.spectator_start = None; self.spectator_origin = None; self.spectator_samples = 0
        self.spectator_seconds = 0.0; self.spectator_max_displacement = 0.0; self.spectator_max_speed = 0.0
        self.spectator_phase_samples = 0; self.spectator_phase_start = None
        self.spectator_phase_first_unix_ms = self.spectator_phase_last_unix_ms = 0
        self.spectator_phase_seconds = 0.0

    def decode(self, envelope):
        cls = self.types.get(envelope.capability_id)
        if cls is None:
            return None
        if (not self.self_id or envelope.entity_id != self.self_id
                or envelope.payload.type_url != "type.googleapis.com/" + cls.DESCRIPTOR.full_name):
            self.faults += 1
            return None
        try:
            value = cls(); value.ParseFromString(envelope.payload.value)
            return value
        except Exception:
            self.faults += 1
            return None

    def inspect(self, message):
        try:
            kind = message.WhichOneof("body")
            if kind == "manifest":
                actor = message.manifest.self_actor_id
                if not identifier(actor) or self.self_id and self.self_id != actor:
                    self.faults += 1
                else:
                    self.self_id = actor
            elif kind == "observation":
                self.frames += 1
                self.observed_unix_ms = self.unix()
                values = [value for item in message.observation.observations if (value := self.decode(item)) is not None]
                # Do not rely on capability registration order to establish
                # that a postgame value in this same frame is terminal.
                for value in values:
                    if isinstance(value, self.pb.SocialMatchStateObservation):
                        self.payload(value)
                for value in values:
                    if isinstance(value, self.pb.SocialMatchStateObservation):
                        continue
                    self.payload(value)
                poses = [v for v in values if isinstance(v, self.transform)]
                motions = [v for v in values if isinstance(v, self.motion)]
                # Freshness is per actual incoming frame: cached stationary
                # values cannot supply the four distinct sample requirement.
                if len(poses) == len(motions) == 1:
                    self.sample_spectator(poses[0], motions[0], phase_fresh=
                        sum(isinstance(v, self.pb.SocialMatchStateObservation) for v in values) == 1
                        and sum(isinstance(v, self.pb.SocialOwnStateObservation) for v in values) == 1)
            elif kind == "event":
                value = self.decode(message.event)
                if value is not None:
                    self.payload(value)
        except Exception:
            self.faults += 1
        if self.clock() >= self.next_save:
            self.next_save = self.clock() + .25
            self.save()

    def payload(self, value):
        pb = self.pb
        if isinstance(value, pb.SocialMatchChangedEvent):
            self.payload(value.state)
        elif isinstance(value, pb.SocialOwnStateChangedEvent):
            self.payload(value.state)
        elif isinstance(value, pb.SocialMatchStateObservation):
            if self.match_id and self.match_id != value.match_id:
                self.faults += 1; return
            phase = pb.SocialPhase.Name(value.phase).removeprefix("SOCIAL_PHASE_")
            if (self.phase, self.phase_revision) != (phase, value.phase_revision):
                self.reset_phase_samples()
            self.match_id = value.match_id
            self.phase = phase
            self.phase_revision = value.phase_revision
            self.phase_ends_at_unix_ms = value.phase_ends_at_unix_ms
            self.kill_cooldown_ms = value.kill_cooldown_ms
            self.terminal |= value.phase == pb.SOCIAL_PHASE_GAME_OVER
        elif isinstance(value, pb.SocialOwnStateObservation):
            if value.actor_id != self.self_id or self.match_id and value.match_id != self.match_id:
                self.faults += 1; return
            self.own = {"actor_id": value.actor_id, "match_id": value.match_id,
                "life": pb.SocialLife.Name(value.life).removeprefix("SOCIAL_LIFE_"),
                "alignment": pb.SocialAlignment.Name(value.alignment).removeprefix("SOCIAL_ALIGNMENT_"),
                "assigned": value.assigned, "can_move": value.can_move, "server_revision": value.server_revision}
        elif isinstance(value, pb.SocialVisibleWorldObservation):
            if value.recipient_actor_id != self.self_id or len(value.bodies) > 6:
                self.faults += 1; return
            self.body_victims.update(body.victim_actor_id for body in value.bodies if identifier(body.victim_actor_id))
            if len(self.body_victims) > 6:
                self.faults += 1; self.body_victims = set(sorted(self.body_victims)[:6])
        elif isinstance(value, pb.SocialMatchEndedEvent):
            # MatchEnded is itself an authoritative terminal disclosure.
            self.terminal = True; self.receive_summary(value.summary)
        elif isinstance(value, pb.SocialPostgameObservation) and value.HasField("summary"):
            if not self.terminal:
                self.faults += 1
            else:
                self.receive_summary(value.summary)

    def receive_summary(self, value):
        from google.protobuf.json_format import MessageToDict
        result = summary_projection(MessageToDict(value, preserving_proto_field_name=True))
        if result is None or self.match_id and result["match_id"] != self.match_id:
            self.faults += 1
        elif self.summary is not None and self.summary != result:
            self.faults += 1
        else:
            self.summary = result

    def reset_phase_samples(self):
        self.spectator_phase_samples = 0; self.spectator_phase_start = None
        self.spectator_phase_first_unix_ms = self.spectator_phase_last_unix_ms = 0
        self.spectator_phase_seconds = 0.0

    def sample_spectator(self, pose, motion, *, phase_fresh=False):
        if not (self.own.get("life") == "SPECTATOR" and self.own.get("alignment") == "UNSPECIFIED"
                and self.own.get("assigned") is False and self.own.get("can_move") is False):
            self.spectator_start = None; self.spectator_origin = None; self.spectator_samples = 0
            self.reset_phase_samples()
            return
        numbers = (pose.x, pose.y, motion.velocity_x, motion.velocity_y)
        if not all(math.isfinite(v) for v in numbers):
            self.faults += 1; return
        if self.spectator_start is None:
            self.spectator_start = self.clock(); self.spectator_origin = (pose.x, pose.y)
        self.spectator_samples += 1
        self.spectator_seconds = self.clock() - self.spectator_start
        self.spectator_max_displacement = max(self.spectator_max_displacement,
            math.hypot(pose.x - self.spectator_origin[0], pose.y - self.spectator_origin[1]))
        self.spectator_max_speed = max(self.spectator_max_speed, math.hypot(motion.velocity_x, motion.velocity_y))
        if phase_fresh:
            now = self.unix()
            if self.spectator_phase_last_unix_ms and (not 0 < now-self.spectator_phase_last_unix_ms <= 1500):
                self.reset_phase_samples()
            if self.spectator_phase_start is None:
                self.spectator_phase_start = self.clock(); self.spectator_phase_first_unix_ms = now
            self.spectator_phase_last_unix_ms = now
            self.spectator_phase_samples += 1
            self.spectator_phase_seconds = self.clock()-self.spectator_phase_start

    def report(self):
        return {"schema": PRIVATE_SCHEMA, "self_actor_id": self.self_id, "match_id": self.match_id,
            "phase": self.phase, "phase_revision": self.phase_revision, "terminal": self.terminal,
            "phase_ends_at_unix_ms": self.phase_ends_at_unix_ms,
            "kill_cooldown_ms": self.kill_cooldown_ms,
            "own": self.own, "summary": self.summary if self.terminal else None,
            "observed_body_victim_ids": sorted(self.body_victims), "streams": self.streams,
            "observation_frames": self.frames, "probe_failures": self.faults, "commands_sent": self.commands,
            "observed_unix_ms": self.observed_unix_ms, "spectator_samples": self.spectator_samples,
            "spectator_seconds": self.spectator_seconds, "spectator_max_displacement": self.spectator_max_displacement,
            "spectator_max_speed": self.spectator_max_speed,
            "spectator_phase_samples": self.spectator_phase_samples, "spectator_phase_seconds": self.spectator_phase_seconds,
            "spectator_phase_first_unix_ms": self.spectator_phase_first_unix_ms,
            "spectator_phase_last_unix_ms": self.spectator_phase_last_unix_ms}

    def save(self):
        if self.path is not None:
            write(self.path, self.report())


def worker(argv):
    """Existing generic CLI seam; transparent authenticated observer only."""
    from actor_runtime.server import RuntimeServer
    from actor_runtime.cli import main as runtime_main
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--wire-report", type=Path, required=True)
    own, remaining = parser.parse_known_args(argv)
    class AuditedServer(RuntimeServer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.audit = WireAudit(own.wire_report.resolve())
        async def Connect(self, request_iterator, context):
            self.audit.streams += 1
            async def observed():
                async for message in request_iterator:
                    self.audit.inspect(message)
                    yield message
            try:
                async for response in super().Connect(observed(), context):
                    if response.WhichOneof("body") == "command":
                        self.audit.commands += 1
                    yield response
            finally:
                self.audit.save()
    sys.argv = [sys.argv[0], *remaining]
    return runtime_main(server_factory=AuditedServer)


def client_projection(value):
    """No error prose, chat, Objective, live alignment, target or position."""
    result = {key: value.get(key) for key in ("actorId", "lastAcceptedSessionId", "socialMatchId",
        "socialPhase", "socialPhaseRevision", "socialLife", "socialAssigned", "socialSummaryPresent", "state", "keyboardEnabled")}
    result["actorId"] = result["actorId"] if identifier(result["actorId"]) else None
    result["socialMatchId"] = result["socialMatchId"] if identifier(result["socialMatchId"]) else None
    result["lastAcceptedSessionId"] = result["lastAcceptedSessionId"] if identifier(result["lastAcceptedSessionId"]) else None
    for key in ("meanFps", "elapsedSeconds", "commands"):
        result[key] = value.get(key) if number(value.get(key)) else None
    velocity = value.get("velocity", {})
    result["speed"] = math.hypot(velocity.get("x"), velocity.get("y")) if isinstance(velocity, dict) and all(
        number(velocity.get(axis), -10000, 10000) for axis in ("x", "y")) else None
    result["error_count"] = len(value["errors"]) if isinstance(value.get("errors"), list) else None
    result["warning_count"] = len(value["warnings"]) if isinstance(value.get("warnings"), list) else None
    return result


def spectator_projection(wire, client, old_actor, match_id):
    own = wire.get("own", {})
    values = {key: wire.get(key) for key in ("spectator_samples", "spectator_seconds", "spectator_max_displacement", "spectator_max_speed", "commands_sent")}
    passed = (wire.get("schema") == PRIVATE_SCHEMA and type(wire.get("probe_failures")) is int and wire["probe_failures"] == 0
        and wire.get("self_actor_id") == client.get("actorId") and identifier(wire.get("self_actor_id"))
        and own.get("actor_id") == wire["self_actor_id"] and wire["self_actor_id"] != old_actor
        and wire.get("match_id") == match_id and own.get("match_id") == match_id and client.get("socialMatchId") == match_id
        and own.get("life") == "SPECTATOR" and own.get("alignment") == "UNSPECIFIED"
        and own.get("assigned") is False and own.get("can_move") is False
        and client.get("socialLife") == "Spectator" and client.get("socialAssigned") is False
        and client.get("keyboardEnabled") is False and client.get("commands") == 0
        and type(values["spectator_samples"]) is int and values["spectator_samples"] >= 4
        and number(values["spectator_seconds"]) and values["spectator_seconds"] >= 2
        and number(values["spectator_max_displacement"]) and values["spectator_max_displacement"] <= .02
        and number(values["spectator_max_speed"]) and values["spectator_max_speed"] <= .05
        and values["commands_sent"] == 0)
    return {"passed": passed, "fresh_actor_id": wire.get("self_actor_id") if identifier(wire.get("self_actor_id")) else None,
        "own_life": own.get("life") if own.get("life") == "SPECTATOR" else None,
        "alignment_unspecified": own.get("alignment") == "UNSPECIFIED", "assigned": own.get("assigned") is True,
        "movement_permission": own.get("can_move") is True, **{key: value if number(value) else None for key, value in values.items()}}


def meeting_reconnect_projection(wire, client, runtime, old_actor, match_id, fault, launch, client_file_unix_ms):
    """Only a frozen actual Discussion interval can establish meeting reconnect."""
    result = spectator_projection(wire, client, old_actor, match_id)
    first, last = wire.get("spectator_phase_first_unix_ms"), wire.get("spectator_phase_last_unix_ms")
    revision, end = wire.get("phase_revision"), wire.get("phase_ends_at_unix_ms")
    fault_at, started = fault.get("unix_ms"), launch.get("client_started_unix_ms")
    sessions = runtime.get("runtime", {}).get("sessions", [])
    accepted = client.get("lastAcceptedSessionId")
    matched = [row for row in sessions if isinstance(row, dict) and row.get("session_id") == accepted] if isinstance(sessions, list) and len(sessions) <= 256 else []
    window = (wire.get("phase") == "DISCUSSION" and client.get("socialPhase") == "Discussion"
        and type(revision) is int and revision > 0 and type(client.get("socialPhaseRevision")) is int
        and client["socialPhaseRevision"] == revision and all(type(v) is int and v > 0 for v in (first, last, end, fault_at, started, client_file_unix_ms))
        and fault_at <= started <= first <= last <= end and started-fault_at < 30000 and first-fault_at < 30000
        and last-first >= 2000 and abs(client_file_unix_ms-last) <= 1500
        and type(wire.get("spectator_phase_samples")) is int and wire["spectator_phase_samples"] >= 4
        and number(wire.get("spectator_phase_seconds"), 2, 900)
        and identifier(accepted) and len(matched) == 1 and matched[0].get("actor_id") == wire.get("self_actor_id")
        and type(client.get("commands")) is int and client["commands"] == 0)
    result.update(passed=result["passed"] and window, during_discussion_verified=bool(window),
        discussion_samples=wire.get("spectator_phase_samples") if type(wire.get("spectator_phase_samples")) is int else None,
        discussion_seconds=wire.get("spectator_phase_seconds") if number(wire.get("spectator_phase_seconds")) else None)
    return result


def isolation(rows):
    if not isinstance(rows, list) or not 4 <= len(rows) <= 5:
        return False
    pids = [row.get(key) for row in rows for key in ("runtime_pid", "client_pid")]
    if not all(type(pid) is int and pid > 0 for pid in pids) or len(set(pids)) != len(pids):
        return False
    for key in ("bridge_port", "bridge_credential_sha256", "agent_profile", "memory_database"):
        values = [row.get(key) for row in rows]
        if any(value is None for value in values) or len(set(values)) != len(values):
            return False
    return (all(type(row["bridge_port"]) is int and 1024 <= row["bridge_port"] <= 65535 for row in rows)
        and all(isinstance(row["bridge_credential_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["bridge_credential_sha256"]) for row in rows)
        and all(identifier(row["agent_profile"]) and isinstance(row["memory_database"], str) and row["memory_database"] for row in rows)
        and len({row.get("brain") for row in rows[:4]}) == 1 and all(row.get("operation") == "match" for row in rows[:4]))


def check(value, *, executed=True):
    return {"status": ("passed" if value is True else "failed") if executed else "not_executed", "passed": value is True if executed else None}


def runtime_terminal(runtime, client, summary, actor):
    match = runtime.get("match", {})
    result = match.get("result", {})
    outcome = result.get("outcome", {})
    accepted = client.get("lastAcceptedSessionId")
    sessions = runtime.get("runtime", {}).get("sessions", [])
    if not isinstance(sessions, list) or len(sessions) > 256:
        return False
    same_session = [session for session in sessions if isinstance(session, dict) and session.get("session_id") == accepted]
    return (match.get("passed") is True and result.get("status") == "completed"
        and result.get("authoritative_terminal") is True and result.get("scope_id") == summary["match_id"]
        and outcome.get("match_id") == summary["match_id"]
        and enum(outcome.get("winning_side"), "SOCIAL_ALIGNMENT_") == summary["winning_side"]
        and outcome.get("win_reason") == summary["win_reason"] and identifier(accepted) and len(same_session) == 1
        and same_session[0].get("actor_id") == actor)


def evaluate(data):
    """Strict offline projection: missing live evidence is never backfilled."""
    fault = data.get("fault", {}); selected = fault.get("actor_id")
    executed = (data.get("case") in CASES and fault.get("issued") is True and identifier(selected) and fault.get("component") in ("runtime", "client")
        and fault.get("phase") in ("Exploration", "Discussion") and type(fault.get("participant")) is int
        and 1 <= fault["participant"] <= 4 and type(fault.get("pid")) is int and fault["pid"] > 0)
    audits = data.get("audits", []); clients = data.get("clients", []); runtimes = data.get("runtimes", [])
    survivors = [index for index in range(4) if index + 1 != fault.get("participant")]
    summaries = [canonical_summary(audits[index].get("summary")) if index < len(audits) and audits[index].get("terminal") is True else None for index in survivors]
    agreement = (len(audits) == len(clients) == len(runtimes) == 4 and all(summaries) and all(item == summaries[0] for item in summaries)
        and all(type(audits[index].get("probe_failures")) is int and audits[index]["probe_failures"] == 0
            and type(audits[index].get("observation_frames")) is int and audits[index]["observation_frames"] > 0 for index in survivors))
    summary = summaries[0] if agreement else None
    identities = (len(audits) == len(clients) == 4
        and all(identifier(audits[index].get("self_actor_id")) and audits[index].get("self_actor_id") == clients[index].get("actorId")
            for index in range(4)) and len({a["self_actor_id"] for a in audits}) == 4
        and bool(summary) and {a["self_actor_id"] for a in audits} == {p["actor_id"] for p in summary["players"]})
    player = next((p for p in summary["players"] if p["actor_id"] == selected), None) if summary else None
    disconnected = player is not None and player["life"] == "DISCONNECTED"
    replay = summary["replay"] if summary else []
    disconnects = [entry for entry in replay if entry["kind"] == "disconnect" and entry["actor_id"] == selected]
    no_kill = bool(summary) and not summary["replay_truncated"] and not any(item["kind"] == "kill" and item["target_id"] == selected for item in replay)
    observed_no_body = len(audits) == 4 and all(isinstance(a.get("observed_body_victim_ids"), list)
        and selected not in a["observed_body_victim_ids"] for a in audits)
    terminal_consistency = bool(summary) and all(client.get("socialPhase") == "GameOver" and client.get("socialSummaryPresent") is True
        and client.get("socialMatchId") == summary["match_id"] and client.get("actorId") == audits[index].get("self_actor_id")
        and runtime_terminal(runtimes[index], client, summary, audits[index].get("self_actor_id"))
        for index, client in enumerate(clients) if index in survivors)
    expected = None
    if player:
        expected = ("CREW", "impostors_eliminated") if player["alignment"] == "IMPOSTOR" else ("IMPOSTOR", "parity")
    outcome = bool(summary) and expected == (summary["winning_side"], summary["win_reason"])
    guard = data.get("guard", {})
    rows = data.get("launches", [])
    selected_row = rows[fault["participant"] - 1] if executed and len(rows) >= fault["participant"] else {}
    pre = data.get("pre_fault_client", {})
    guarded = (executed and guard.get("manifest_pid_matches") is True and guard.get("child_identity_matches") is True
        and guard.get("selected_was_alive") is True and guard.get("component_only") is True
        and selected_row.get(fault["component"] + "_pid") == fault["pid"]
        and pre.get("actorId") == selected and pre.get("socialLife") == "Alive" and pre.get("socialPhase") == fault["phase"]
        and bool(summary) and fault.get("match_id") == pre.get("socialMatchId") == summary["match_id"]
        and identities)
    cleanup = data.get("cleanup", {})
    stopped = (type(cleanup.get("started")) is int and cleanup["started"] in (8, 10)
        and type(cleanup.get("remaining")) is int and cleanup["remaining"] == 0)
    phase_expected = "Discussion" if data.get("phase") == "discussion" else "Exploration"
    tests = {"owned_child_only_was_faulted": check(guarded, executed=executed),
        "fault_in_requested_public_phase": check(fault.get("phase") == phase_expected, executed=executed),
        "remaining_clients_received_same_authoritative_terminal_summary": check(agreement and terminal_consistency, executed=executed),
        "selected_actor_irreversibly_forfeited_disconnected": check(disconnected and len(disconnects) == 1, executed=executed),
        "forfeit_has_no_kill_replay_or_observed_body": check(disconnected and no_kill and observed_no_body, executed=executed),
        "legitimate_outcome_matches_postgame_alignment": check(outcome, executed=executed),
        "isolated_credentials_profiles_databases_and_pids": check(isolation(data.get("launches"))),
        "all_owned_children_stopped": check(stopped)}
    recovery = data.get("recovery", {})
    recovery_passed = (recovery.get("completed") is True and recovery.get("keyboard_disabled") is True
        and recovery.get("client_alive") is True and recovery.get("stopped") is True and recovery.get("fps_above_30") is True
        and recovery.get("error_state_observed") is True and recovery.get("own_disconnected_observed") is True
        and type(recovery.get("samples")) is int and recovery["samples"] >= 4
        and number(recovery.get("seconds"), 2, 900) and number(recovery.get("max_speed"), 0, .05)
        and number(recovery.get("minimum_fps"), 30.000001, 100000))
    tests["runtime_loss_keeps_client_alive_stopped_without_keyboard"] = check(recovery_passed,
        executed=executed and fault.get("component") == "runtime")
    late = data.get("case") == "late-reconnect"
    meeting_late = late and data.get("phase") == "discussion"
    if meeting_late:
        reconnect = meeting_reconnect_projection(data.get("reconnect_phase_audit", {}), data.get("reconnect_phase_client", {}),
            data.get("reconnect_phase_runtime", {}), selected, summary["match_id"] if summary else None, fault,
            rows[4] if len(rows) == 5 else {}, data.get("reconnect_client_file_unix_ms"))
    else:
        reconnect = spectator_projection(data.get("reconnect_audit", {}), data.get("reconnect_client", {}),
            selected, summary["match_id"] if summary else None) if late else {}
    fifth_owned = (len(rows) == 5 and rows[4].get("operation") == "serve" and cleanup.get("started") == 10
        and reconnect.get("fresh_actor_id") not in {audit.get("self_actor_id") for audit in audits})
    if meeting_late:
        fifth_owned &= data.get("reconnect_phase_client", {}).get("lastAcceptedSessionId") not in {
            client.get("lastAcceptedSessionId") for client in clients}
    tests["fresh_ticket_actor_is_stationary_unassigned_spectator"] = check(reconnect.get("passed") is True and fifth_owned, executed=executed and late)
    if meeting_late:
        tests["reconnect_observed_during_actual_discussion"] = check(reconnect.get("during_discussion_verified") is True
            and number(data.get("discussion_remaining_at_fault_ms"), 45000, 300000)
            and number(data.get("public_kill_cooldown_at_fault_ms"), 45000, 300000)
            and any(row["kind"] == "meeting" for row in replay)
            and any(row["kind"] == "phase" and row["outcome"] == "Discussion" for row in replay), executed=executed)
    result = {"schema": SCHEMA, "case": data.get("case") if data.get("case") in CASES else None,
        "requested_phase": phase_expected, "executed": bool(executed), "passed": False, "checks": tests,
        "selected_participant": fault.get("participant") if executed else None,
        "selected_postgame_alignment": player["alignment"] if player else None,
        "postgame_winning_side": summary["winning_side"] if summary else None,
        "postgame_win_reason": summary["win_reason"] if summary else None,
        "forfeit_record_count": len(disconnects) if summary else None,
        "legitimate_kills": summary["kills"] if summary else None,
        "fault_to_gameover_seconds": data.get("fault_to_gameover_seconds") if number(data.get("fault_to_gameover_seconds"), 0, 1800) else None,
        "recovery": {key: recovery.get(key) if type(recovery.get(key)) is bool else None for key in (
            "completed", "keyboard_disabled", "client_alive", "stopped", "fps_above_30", "error_state_observed", "own_disconnected_observed")},
        "reconnect": ({key: reconnect.get(key) if type(reconnect.get(key)) is bool else None for key in (
            "passed", "alignment_unspecified", "assigned", "movement_permission")} if late else None),
        "client_metrics": [{key: row.get(key) if number(row.get(key)) else None for key in (
            "meanFps", "elapsedSeconds", "error_count", "warning_count")} for row in clients],
        "owned_processes_started": cleanup.get("started"), "owned_processes_remaining": cleanup.get("remaining"),
        "not_executed_cases": [case for case in CASES if case != data.get("case")],
        "coverage": {"exploration": bool(executed and fault.get("phase") == "Exploration"),
            "discussion": bool(executed and fault.get("phase") == "Discussion")},
        "limits": ["No global server-body enumeration: no-body check combines postgame Disconnected, complete kill replay and all permitted observed body victim IDs.",
            "Spectator no-actions means zero commands plus authoritative no movement permission; this helper does not attempt forbidden actions.",
            "One run covers one fault and one phase. Unit tests, another phase and server loss are not implied."]}
    result["recovery"].update({key: recovery.get(key) if number(recovery.get(key)) else None
        for key in ("samples", "seconds", "max_speed", "minimum_fps")})
    if late:
        result["reconnect"].update({key: reconnect.get(key) if number(reconnect.get(key)) else None for key in (
            "spectator_samples", "spectator_seconds", "spectator_max_displacement", "spectator_max_speed", "commands_sent")})
        result["reconnect"]["fresh_identity_verified"] = (identifier(reconnect.get("fresh_actor_id")) and reconnect["fresh_actor_id"] != selected)
        if meeting_late:
            result["reconnect"].update(during_discussion_verified=reconnect.get("during_discussion_verified") is True,
                discussion_samples=reconnect.get("discussion_samples"), discussion_seconds=reconnect.get("discussion_seconds"))
    result["passed"] = bool(executed and not data.get("harness_error") and all(test["status"] != "failed" for test in tests.values()))
    return result


def fault_owned(process, row, component):
    """No arbitrary PID API: the signal target is an owned live Popen object."""
    pid = row.get(component + "_pid")
    if component not in ("runtime", "client") or type(pid) is not int or pid != process.pid or process.poll() is not None:
        raise RuntimeError("owned_child_identity_mismatch")
    process.kill()
    process.wait(timeout=5)
    return {"manifest_pid_matches": True, "child_identity_matches": True, "component_only": True}


def cleanup_owned(processes):
    # Stop all Runtime/provider loops first, then clients. Signal only the Popen
    # objects held by this invocation, never a saved PID from a previous run.
    ordered = [p for kind, p in processes if kind == "runtime"] + [p for kind, p in processes if kind == "client"]
    for process in ordered:
        if process.poll() is None:
            try: process.terminate()
            except ProcessLookupError: pass
    deadline = time.monotonic() + 16
    forced = 0
    for process in ordered:
        try: process.wait(timeout=max(.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            forced += 1
            try: process.kill()
            except ProcessLookupError: pass
            process.wait(timeout=5)
    return {"started": len(processes), "remaining": sum(process.poll() is None for _, process in processes), "forced_cleanup_kills": forced}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--phase", choices=("exploration", "discussion"))
    parser.add_argument("--component", choices=("runtime", "client"), default="client", help="Only for exploration-vs-meeting")
    parser.add_argument("--participant", type=int, choices=range(1, 5), help="Fixed own participant; default first publicly alive numbered client, never chosen by alignment")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:5081")
    parser.add_argument("--client", type=Path, default=ROOT / "build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--base-port", type=int, default=18600)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args(argv)
    args.output = args.output.resolve()
    if args.summarize_only:
        return args
    if not args.room or not re.fullmatch(r"[A-Za-z0-9]{6}", args.room): parser.error("--room requires six ASCII letters/digits")
    if not args.case: parser.error("--case is required")
    if args.case == "exploration-vs-meeting" and args.phase is None: parser.error("This case requires an explicit --phase; two fresh rooms cover both")
    if args.case not in ("exploration-vs-meeting", "late-reconnect") and args.phase not in (None, "exploration"): parser.error("This case faults during exploration")
    args.phase = args.phase or "exploration"
    if not 1024 <= args.base_port <= 65531: parser.error("Five independent valid unprivileged bridge ports are required")
    if not number(args.timeout, 60, 900): parser.error("--timeout must be finite, 60–900 seconds")
    try:
        url = urlsplit(args.api_url)
        valid = (0 < len(args.api_url) <= 2048 and url.scheme in ("http", "https") and url.hostname
            and url.username is None and url.password is None and not url.query and not url.fragment
            and all(ord(c) >= 33 for c in args.api_url) and (url.port is None or 1 <= url.port <= 65535))
    except ValueError: valid = False
    if not valid: parser.error("--api-url requires an absolute HTTP(S) URL without credentials, query or fragment")
    args.room = args.room.upper()
    return args


def main(argv=None):
    args = arguments(argv)
    output = args.output
    if args.summarize_only:
        result = evaluate(read(output / "evidence-private.json"))
        write(output / "acceptance.json", result)
        print(json.dumps({"passed": result["passed"], "executed": result["executed"]}))
        return 0 if result["passed"] else 1
    python = ROOT / "build/runtime-venv/bin/python"
    if not python.is_file() or not args.client.is_file(): raise SystemExit("Install Runtime and build the ordinary client first")
    if output.exists() and any(output.iterdir()): raise SystemExit("Use a fresh empty output; previous proof is immutable")
    for port in range(args.base_port, args.base_port + 5):
        with socket.socket() as probe:
            try: probe.bind(("127.0.0.1", port))
            except OSError: raise SystemExit("A requested local bridge port is occupied")
    output.mkdir(parents=True, exist_ok=True)
    state = {"case": args.case, "phase": args.phase, "launches": [], "fault": {}, "guard": {}, "recovery": {}, "reconnect": {}, "harness_error": None}
    processes = []; agents = []; handles = []
    deadline = time.monotonic() + args.timeout
    component = "runtime" if args.case == "runtime-loss" else args.component if args.case == "exploration-vs-meeting" else "client"
    provider = "social-fixture-vote" if args.phase == "discussion" else "social-fixture-parity"
    def stop(_signal, _frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)

    def launch(index, *, passive=False, same_name=None):
        folder = output / f"agent{index:02}"; folder.mkdir()
        credential = secrets.token_hex(32); profile = "social-fault-" + secrets.token_hex(12)
        environment = dict(os.environ, ACTOR_BRIDGE_TOKEN=credential)
        port = args.base_port + index - 1
        command = [str(python), str(Path(__file__).resolve()), "--worker", "--wire-report", str(folder / "wire-private.json"),
            "serve" if passive else "match", "--port", str(port), "--report", str(folder / "runtime.json")]
        if not passive:
            command += ["--brain", provider, "--match-policy", "agents-arena", "--objective", OBJECTIVE,
                "--memory", "sqlite", "--memory-database", str(folder / "memory.sqlite3"), "--agent-profile", profile,
                "--max-decisions", "24", "--objective-timeout", str(args.timeout), "--exit-on-complete"]
            if args.config: command += ["--config", str(args.config.resolve())]
        handle = (folder / "runtime.log").open("w"); handles.append(handle)
        runtime = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=handle, stderr=subprocess.STDOUT)
        processes.append(("runtime", runtime))
        row = {"participant": index, "runtime_pid": runtime.pid, "runtime_started_unix_ms": int(time.time()*1000), "client_pid": None,
            "bridge_port": port, "operation": "serve" if passive else "match", "brain": None if passive else provider,
            "bridge_credential_sha256": hashlib.sha256(credential.encode()).hexdigest(),
            "agent_profile": profile, "memory_database": str(folder / "memory.sqlite3")}
        state["launches"].append(row); write(output / "launch.json", {"agents": state["launches"]})
        startup = min(deadline, time.monotonic() + 15)
        while True:
            if runtime.poll() is not None: raise RuntimeError("runtime_startup_failed")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2): break
            except OSError:
                if time.monotonic() >= startup: raise TimeoutError("runtime_startup_timeout")
                time.sleep(.1)
        name = same_name or f"Agent{index:02}"
        command = [str(args.client.resolve()), "--auto-room", args.room, "--player-name", name,
            "--control-mode", "actor", "--actor-endpoint", f"http://127.0.0.1:{port}",
            "--actor-report", str(folder / "client.json"), "--api-url", args.api_url,
            "-batchmode", "-nographics", "-screen-fullscreen", "0", "-screen-width", "800", "-screen-height", "600",
            "-logFile", str(folder / "client.log")]
        handle = (folder / "client-stdout.log").open("w"); handles.append(handle)
        client = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=handle, stderr=subprocess.STDOUT)
        processes.append(("client", client)); row["client_pid"] = client.pid; row["client_started_unix_ms"] = int(time.time()*1000)
        write(output / "launch.json", {"agents": state["launches"]}); write(folder / "launch.json", row)
        agent = {"folder": folder, "row": row, "runtime": runtime, "client": client}; agents.append(agent)
        return agent

    def captures():
        return [client_projection(read(agent["folder"] / "client.json")) for agent in agents[:4]]

    try:
        for index in range(1, 5): launch(index)
        wanted = "Discussion" if args.phase == "discussion" else "Exploration"
        selected = None
        while time.monotonic() < deadline:
            clients = captures()
            if any(agent["client"].poll() is not None or agent["runtime"].poll() is not None for agent in agents[:4]):
                raise RuntimeError("participant_exited_before_fault")
            if (len(clients) == 4 and all(client.get("socialPhase") == wanted and client.get("socialAssigned") is True for client in clients)
                    and all(identifier(client.get("actorId")) and identifier(client.get("socialMatchId")) for client in clients)
                    and len({client["actorId"] for client in clients}) == 4 and len({client["socialMatchId"] for client in clients}) == 1):
                indexes = [args.participant - 1] if args.participant else range(4)
                selected = next((index for index in indexes if clients[index]["socialLife"] == "Alive"), None)
                if selected is not None: break
            if any(client.get("socialPhase") == "GameOver" for client in clients): raise RuntimeError("match_ended_before_requested_fault")
            time.sleep(.1)
        if selected is None: raise TimeoutError("requested_public_phase_not_observed")
        agent = agents[selected]; client = clients[selected]
        state["pre_fault_client"] = client
        fault_time = time.monotonic()
        # A normal NGO disconnect takes up to 30s. Without this public rule
        # precondition, a legitimate first kill can end the match before the
        # selected transport loss is recognized. Never hide that as a pass.
        if component == "client" and wanted == "Exploration":
            public_audit = read(agent["folder"] / "wire-private.json")
            if not number(public_audit.get("kill_cooldown_ms"), 45000, 300000):
                raise RuntimeError("client_fault_requires_public_kill_cooldown_at_least_45000ms")
        if args.case == "late-reconnect" and wanted == "Discussion":
            public_audit = read(agent["folder"] / "wire-private.json")
            now_unix_ms = int(time.time()*1000)
            phase_end = public_audit.get("phase_ends_at_unix_ms")
            if (public_audit.get("phase") != "DISCUSSION" or public_audit.get("match_id") != client["socialMatchId"]
                    or not number(public_audit.get("kill_cooldown_ms"), 45000, 300000)
                    or type(phase_end) is not int or not 45000 <= phase_end-now_unix_ms <= 300000):
                raise RuntimeError("meeting_reconnect_requires_45000ms_remaining_discussion_and_kill_cooldown")
            state["discussion_remaining_at_fault_ms"] = phase_end-now_unix_ms
            state["public_kill_cooldown_at_fault_ms"] = public_audit["kill_cooldown_ms"]
        manifest = read(agent["folder"] / "launch.json")
        fault_unix_ms = int(time.time()*1000)
        state["guard"] = fault_owned(agent[component], manifest, component)
        state["guard"]["selected_was_alive"] = client["socialLife"] == "Alive"
        state["fault"] = {"issued": True, "participant": selected + 1, "actor_id": client["actorId"],
            "match_id": client["socialMatchId"], "phase": wanted, "component": component,
            "pid": agent["row"][component + "_pid"], "unix_ms": fault_unix_ms}
        write(output / "fault.json", state["fault"])
        write(output / "guard.json", state["guard"])
        # Late join uses an ordinary fresh ticket and the SAME display name;
        # matching by name must never resurrect the old assigned character.
        reconnect = launch(5, passive=True, same_name=f"Agent{selected + 1:02}") if args.case == "late-reconnect" else None
        recovery_start = None; recovery_origin = None; recovery_samples = []
        terminal_seen = False; terminal_deadline = None
        while time.monotonic() < deadline:
            current = read(agent["folder"] / "client.json")
            if component == "runtime":
                projected = client_projection(current)
                if projected["state"] == "Error": state["recovery"]["error_state_observed"] = True
                if projected["socialLife"] == "Disconnected": state["recovery"]["own_disconnected_observed"] = True
                elapsed = current.get("elapsedSeconds")
                if projected["speed"] is not None and projected["speed"] <= .05 and projected["keyboardEnabled"] is False:
                    if recovery_start is None: recovery_start = time.monotonic()
                    if elapsed != recovery_origin:
                        recovery_origin = elapsed; recovery_samples.append(projected)
                else:
                    recovery_start = None; recovery_samples.clear()
                if recovery_start is not None and time.monotonic() - recovery_start >= 2 and len(recovery_samples) >= 4:
                    state["recovery"].update(completed=True, samples=len(recovery_samples), seconds=time.monotonic() - recovery_start,
                        max_speed=max(row["speed"] for row in recovery_samples), minimum_fps=min(row["meanFps"] or 0 for row in recovery_samples),
                        keyboard_disabled=True, client_alive=agent["client"].poll() is None, stopped=True,
                        fps_above_30=all(number(row["meanFps"]) and row["meanFps"] > 30 for row in recovery_samples))
            if reconnect:
                wire = read(reconnect["folder"] / "wire-private.json")
                reconnect_client = read(reconnect["folder"] / "client.json")
                if args.phase == "discussion":
                    if not state["reconnect"].get("passed"):
                        runtime_snapshot = read(reconnect["folder"] / "runtime.json")
                        file_path = reconnect["folder"] / "client.json"
                        file_time = int(file_path.stat().st_mtime*1000) if file_path.exists() else 0
                        state["reconnect"] = meeting_reconnect_projection(wire, reconnect_client, runtime_snapshot,
                            client["actorId"], client["socialMatchId"], state["fault"], reconnect["row"], file_time)
                        if state["reconnect"].get("passed"):
                            state.update(reconnect_phase_audit=wire, reconnect_phase_client=reconnect_client,
                                reconnect_phase_runtime=runtime_snapshot, reconnect_client_file_unix_ms=file_time)
                else:
                    state["reconnect"] = spectator_projection(wire, reconnect_client, client["actorId"], client["socialMatchId"])
            audits = [read(item["folder"] / "wire-private.json") for item in agents[:4]]
            survivors = [index for index in range(4) if index != selected]
            complete = all(audits[index].get("terminal") is True and audits[index].get("summary") for index in survivors)
            if complete and not terminal_seen:
                terminal_seen = True; terminal_deadline = min(deadline, time.monotonic() + 12)
                state["fault_to_gameover_seconds"] = time.monotonic() - fault_time
            ready = (complete and (component != "runtime" or state["recovery"].get("completed") is True)
                and (reconnect is None or state["reconnect"].get("passed") is True)
                and all(read(agents[index]["folder"] / "runtime.json").get("match", {}).get("passed") is True for index in survivors)
                and all(read(agents[index]["folder"] / "client.json").get("socialSummaryPresent") is True for index in survivors))
            if ready: break
            if terminal_deadline is not None and time.monotonic() >= terminal_deadline: raise TimeoutError("terminal_evidence_incomplete")
            time.sleep(.1)
        else:
            raise TimeoutError("fault_acceptance_deadline")
    except KeyboardInterrupt:
        state["harness_error"] = "interrupted"
    except (OSError, RuntimeError, TimeoutError, ValueError) as error:
        # Only the exception class/code is retained, never arbitrary HTTP/game
        # or subprocess prose that might contain private text or credentials.
        state["harness_error"] = str(error) if identifier(str(error)) else type(error).__name__
    finally:
        # Freeze authoritative reports before shutdown can clear own state.
        state["audits"] = [read(agent["folder"] / "wire-private.json") for agent in agents[:4]]
        state["clients"] = captures()
        state["runtimes"] = [read(agent["folder"] / "runtime.json") for agent in agents[:4]]
        if len(agents) == 5:
            state["reconnect_audit"] = read(agents[4]["folder"] / "wire-private.json")
            state["reconnect_client"] = read(agents[4]["folder"] / "client.json")
        state["cleanup"] = cleanup_owned(processes)
        for handle in handles: handle.close()
        write(output / "cleanup.json", state["cleanup"])
        write(output / "evidence-private.json", state)
    result = evaluate(state)
    write(output / "acceptance.json", result)
    print(json.dumps({"passed": result["passed"], "executed": result["executed"], "case": result["case"],
        "owned_processes_remaining": result["owned_processes_remaining"]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(sys.argv[2:])
    else:
        raise SystemExit(main())
