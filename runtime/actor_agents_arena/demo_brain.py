"""Deterministic reference-game objective demo; no room concepts enter Core."""

from actor_runtime.brains import FinishObjective, SequenceBrain, SkillCall


def scripted_brain(**_options):
    return SequenceBrain([
        SkillCall(skill_id="agents-arena.navigate-to-room", arguments={"target_room_id": "engineering"}),
        SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 2.0}),
        SkillCall(skill_id="agents-arena.navigate-to-room", arguments={"target_room_id": "main-hall"}),
        FinishObjective(status="success"),
    ])
