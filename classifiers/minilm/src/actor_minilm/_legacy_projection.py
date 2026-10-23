"""A bounded projection of already permitted Runtime inputs, never postgame truth.

0.2 adds an honest server_private provenance value. Missing queued plans remain
unknown. Router hints, rule outputs, identities and future outcomes are excluded.
"""
import hashlib
from pathlib import Path
from .contract import ClassifierInput

STATIC_GUIDANCE = (Path(__file__).resolve().parent / "adapter_resources/arena-policy-0.7.0.txt").read_text()
if hashlib.sha256(STATIC_GUIDANCE.encode()).hexdigest() != "2de5d07493c05fe40c2a892f5cc5c0ccada19bbd491478af88a69e6e2b4e63f9":
    raise ValueError("Unexpected installed Arena guidance resource")


def project_objective(text):
    # Replace only this exact versioned static rule catalogue, never arbitrary
    # user instructions or a similar-looking suffix. The raw log retains it.
    suffix = "\n\n" + STATIC_GUIDANCE
    return text[:-len(suffix)] if text.endswith(suffix) else text

SKILLS = {
    "agents-arena.kill-visible-actor": "attack",
    "agents-arena.follow-visible-actor": "approach_target",
    "agents-arena.navigate-social-room": "navigate",
    "agents-arena.perform-task": "perform_task",
    "agents-arena.fake-task": "fake_task",
    "agents-arena.report-body": "report_body",
    "agents-arena.discuss": "discuss",
    "agents-arena.await-discussion-update": "await_discussion",
    "agents-arena.cast-vote": "vote",
    "agents-arena.ready-for-match": "ready",
    "agents-arena.repair-emergency": "repair_emergency",
}


def enum(value, prefix):
    return str(value or "").removeprefix(prefix).lower() or "unknown"


def states(row):
    entries = row.get("supplementary_self_observations") or row["input"]["context"]["relevant_state"]
    result = {}
    for item in entries:
        kind = item["type_url"].rsplit(".", 1)[-1]
        if kind in {"SocialOwnStateObservation", "SocialMatchStateObservation", "SocialVisibleWorldObservation", "SocialMeetingObservation"}:
            result[kind] = item
    event = row["input"]["event"]
    value = event["entry"]["payload"]
    replacements = {"SocialMatchChangedEvent": "SocialMatchStateObservation",
                    "SocialOwnStateChangedEvent": "SocialOwnStateObservation",
                    "SocialMeetingChangedEvent": "SocialMeetingObservation"}
    kind = event["type_url"].rsplit(".", 1)[-1]
    if kind in replacements and isinstance(value.get("state"), dict):
        result[replacements[kind]] = {"payload": value["state"], "age_ms": event["entry"]["age_ms"]}
    return result


def project(row):
    raw = row["input"]
    event = raw["event"]
    name = event["type_url"].rsplit(".", 1)[-1]
    value = event["entry"]["payload"]
    if not isinstance(value, dict):raise ValueError("Opaque event is unsupported")
    known = states(row)
    own = known.get("SocialOwnStateObservation", {}).get("payload", {})
    match = known.get("SocialMatchStateObservation", {}).get("payload", {})
    role = enum(own.get("alignment"), "SOCIAL_ALIGNMENT_")
    phase = enum(match.get("phase"), "SOCIAL_PHASE_")
    skill = raw["context"].get("current_skill") or {}
    action = SKILLS.get(skill.get("skill_id"), skill.get("skill_id") or "unknown")
    arguments = skill.get("arguments") or {}
    target = arguments.get("target_actor_id") if action in ("attack", "approach_target") else None
    source, kind, payload = "server_private", name, {}
    if name == "SocialActionResultEvent":
        task = enum(value.get("action"), "SOCIAL_ACTION_")
        source = "own_action_result"
        kind = "attack_result" if task == "kill" else "action_result"
        payload = {"action": task, "accepted": value.get("accepted", False),
                   "completed": value.get("completed", False), "reason": value.get("reason_code", "unknown")}
        if task == "kill":
            payload["stage"] = "completed" if value.get("completed") else "accepted" if value.get("accepted") else "rejected"
            if phase == "exploration":payload["match_continues"] = True
            if target:payload["target"] = target
    elif name == "SocialMatchChangedEvent":
        kind = {"discussion":"meeting_started", "voting":"voting_started", "exploration":"exploration_started", "game_over":"game_over"}.get(phase, "phase_changed")
        source, payload = "server_public", {"phase": phase}
    elif name == "SocialVisibleAttackEvent":
        kind, source = "witnessed_attack", "direct_vision"
        payload = {"actor": value.get("attacker_actor_id"), "victim": value.get("victim_actor_id")}
    elif name == "SocialVisibleActivityEvent":
        kind, source = "visible_activity", "direct_vision"
        payload = {"actor": value.get("actor_id"), "activity": value.get("activity"), "terminal": value.get("terminal_entity_id")}
    elif name == "RoomChangedEvent":
        kind, payload = "room_changed", {"previous_room":value.get("previous_room_id"), "current_room":value.get("current_room_id")}
    elif name == "SocialOwnStateChangedEvent":
        kind, payload = "own_state_changed", {"life":enum(own.get("life"), "SOCIAL_LIFE_"), "assigned":own.get("assigned", False)}
    elif name == "SocialMeetingChangedEvent":
        meeting = value.get("state", {})
        kind, payload = "meeting_state_changed", {"own_ballot_submitted":meeting.get("own_ballot_submitted", False),
            "resolution":meeting.get("resolution", ""), "phase":phase}
    elif name == "SocialEmergencyChangedEvent":
        state = value.get("state", {})
        kind = "sabotage_started" if state.get("active") and not state.get("paused_for_meeting") else "sabotage_state_changed"
        payload = {"active":state.get("active", False), "paused":state.get("paused_for_meeting", False), "room":state.get("room_id")}
    elif name == "SocialKillPresentationEvent":
        kind, payload = "kill_presentation", {"victim":value.get("victim_actor_id")}
    elif name == "MessageReceivedEvent":
        kind, payload = "message_received", {"actor":value.get("sender_actor_id"), "channel":value.get("channel_id"), "text":value.get("text", "")}
    elif name == "MessageDeliveryResultEvent":
        kind, source = "message_delivery", "own_action_result"
        payload = {"accepted":value.get("accepted_by_server",False), "stage":value.get("stage"), "reason":value.get("reason_code")}
    elif name == "SocialMatchEndedEvent":
        # Never consume summary.players, winner or replay as preterminal features.
        kind, source, payload = "game_over", "server_public", {"phase":"game_over"}
        phase = "game_over"
    else:raise ValueError("Unsupported event type: " + name)
    observations = []
    if own:
        observations.append({"kind":"self_life", "source":"own_state", "age_ms":known["SocialOwnStateObservation"]["age_ms"],
                             "value":enum(own.get("life"), "SOCIAL_LIFE_")})
    visible = known.get("SocialVisibleWorldObservation")
    if visible and phase == "exploration":
        contents = visible["payload"]
        observations.append({"kind":"visible_players", "source":"direct_vision", "age_ms":visible["age_ms"],
                             "value":[x["actor_id"] for x in contents.get("actors", []) if x.get("actor_id")][:6]})
        for body in contents.get("bodies", [])[:2]:
            observations.append({"kind":"visible_body", "source":"direct_vision", "age_ms":visible["age_ms"], "value":body["body_id"]})
    objective = raw["context"].get("objective") or {}
    if isinstance(objective, dict):objective = objective.get("text", "")
    objective = project_objective(objective)
    return ClassifierInput.model_validate({
        "schema_version":"0.2", "game_id":"agents-arena", "ruleset":"social-deduction-0.9.0",
        "own_role":role if role in ("crew", "impostor") else None,
        "phase":"tasks" if phase == "exploration" else "meeting" if phase in ("discussion", "voting", "vote_resolution") else None,
        "objective":objective,
        "plan":{"action":action, "target":target, "conditions":[], "next_action":None, "fallback":None},
        "event":{"kind":kind, "source":source, "payload":payload}, "observations":observations,
    })
