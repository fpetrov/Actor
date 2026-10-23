"""Project permitted Runtime data and the actual accepted executable plan."""
from .contract import ClassifierInput
from ._legacy_projection import SKILLS, enum, project_objective, states
from ._v5_projection import project as project_v5

VISIBLE_WORLD = "agents-arena.social-visible-world"


def call_text(call):
    if not call:
        return None
    skill, args = call["skill_id"], call["arguments"]
    if skill == "agents-arena.navigate-social-room":
        return "Go to " + str(args.get("target_room_id", "unspecified")) + "."
    if skill in ("agents-arena.perform-task", "agents-arena.fake-task"):
        return "Perform " + str(args.get("task_id", "unspecified")) + "."
    if skill == "agents-arena.report-visible-body":
        return "Report a currently visible body."
    return skill + " " + str(args)


def _project_plan(row):
    raw = row["input"]
    event = raw["event"]
    name = event["type_url"].rsplit(".", 1)[-1]
    if name == "SocialVisibleWorldObservation":
        contents = event["entry"]["payload"]
        owner = event["entry"]["entity_id"]
        if not owner or contents.get("recipient_actor_id") != owner or not contents.get("bodies"):
            raise ValueError("A body condition requires a permitted nonempty own view")
        known = states(row)
        own_entry = known.get("SocialOwnStateObservation", {})
        own = own_entry.get("payload", {})
        match = known.get("SocialMatchStateObservation", {}).get("payload", {})
        role = enum(own.get("alignment"), "SOCIAL_ALIGNMENT_")
        phase = enum(match.get("phase"), "SOCIAL_PHASE_")
        objective = raw["context"].get("objective") or {}
        objective = objective.get("text", "") if isinstance(objective, dict) else objective
        observations = []
        if own:
            observations.append({"kind": "self_life", "source": "own_state", "age_ms": own_entry["age_ms"],
                                 "value": enum(own.get("life"), "SOCIAL_LIFE_")})
        for body in contents["bodies"][:2]:
            observations.append({"kind": "visible_body", "source": "direct_vision", "age_ms": event["entry"]["age_ms"], "value": body["body_id"]})
        value = ClassifierInput.model_validate({"schema_version": "0.2", "game_id": "agents-arena", "ruleset": "social-deduction-0.9.0",
            "own_role": role if role in ("crew", "impostor") else None,
            "phase": "tasks" if phase == "exploration" else "meeting" if phase in ("discussion", "voting") else None,
            "objective": project_objective(objective),
            "plan": {"action": "unknown", "target": None, "conditions": [], "next_action": None, "fallback": None},
            "event": {"kind": "body_seen", "source": "direct_vision", "payload": {"body": contents["bodies"][0]["body_id"]}},
            "observations": observations})
    else:
        value = project_v5(row)
    accepted = raw["context"].get("current_plan")
    if accepted is None:
        return value
    if accepted.get("schema_version") != "actor.executable-plan.v1":
        raise ValueError("Unsupported executable plan snapshot")
    current = accepted["current"]
    args = current["arguments"]
    action = SKILLS.get(current["skill_id"], current["skill_id"])
    if current["skill_id"] == "agents-arena.report-visible-body":
        action = "report_body"
    target = args.get("actor_id") if action in ("attack", "approach_target") else args.get("target_room_id") or args.get("task_id") or args.get("body_id")
    conditions = []
    for condition in accepted["conditions"]:
        if (condition["capability_id"] == VISIBLE_WORLD and condition["field"] == "bodies" and condition["when"] == "nonempty"
                and condition["response"]["skill_id"] == "agents-arena.report-visible-body"):
            conditions.append("Report any visible body immediately.")
        else:
            conditions.append(f"When {condition['capability_id']}.{condition['field']} is nonempty, execute {call_text(condition['response'])}")
    next_step = accepted["remaining_steps"][0] if accepted["remaining_steps"] else None
    data = value.model_dump()
    data["plan"] = {"action": action, "target": target, "conditions": conditions,
                    "next_action": call_text(next_step), "fallback": call_text(accepted["fallback"])}
    return ClassifierInput.model_validate(data)


def project(row):
    """Use the exact release projection frozen before the v7 holdout matches."""
    from copy import deepcopy
    from pathlib import Path

    row = deepcopy(row)
    guidance = (Path(__file__).parent / "adapter_resources/arena-policy-0.10.0.txt").read_text()
    objective = row["input"]["context"].get("objective")
    if isinstance(objective, dict):
        suffix = "\n\n" + guidance
        if objective.get("text", "").endswith(suffix):
            objective["text"] = objective["text"][:-len(suffix)]
    value = _project_plan(row)
    return value.model_copy(update={"ruleset": "social-deduction-0.10.0"})
