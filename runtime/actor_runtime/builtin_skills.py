"""Reusable Runtime skills independent of any game's capabilities."""
from pydantic import Field

from .skills import ActorSkill, SkillContext, SkillDefinition, SkillModel


class WaitInput(SkillModel):
    duration_seconds: float = Field(ge=0, le=300, description="Monotonic wait duration, from zero to 300 seconds.")


class WaitOutput(SkillModel):
    waited_seconds: float = Field(ge=0)


class Wait(ActorSkill):
    async def execute(self, context: SkillContext, arguments: WaitInput) -> WaitOutput:
        started = context.now()
        deadline = started + arguments.duration_seconds
        while context.now() < deadline:
            await context.sleep(min(0.1, deadline - context.now()))
        return WaitOutput(waited_seconds=max(0, context.now() - started))


def skill_package():
    return (SkillDefinition(skill_id="actor.wait", version="1.0.0",
        description="Wait for a specified monotonic duration without changing game state. Supports cancellation.",
        input_model=WaitInput, result_model=WaitOutput, factory=Wait, timeout_seconds=301),)
