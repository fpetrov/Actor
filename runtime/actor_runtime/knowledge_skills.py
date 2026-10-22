"""Optional private working-note procedure, independent of any game."""
from pydantic import Field
from typing import Annotated
from .skills import ActorSkill, SkillDefinition, SkillModel


class HypothesisInput(SkillModel):
    statement: str = Field(min_length=1, max_length=512,
        description="A concise, unverified game belief, not hidden reasoning or a claim of authoritative truth.")
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(min_length=1, max_length=8,
        description="IDs of personally available working evidence; a player's statement remains a claim.")


class HypothesisOutput(SkillModel):
    note_id: str
    epistemic_status: str = "unverified_hypothesis"


class RememberHypothesis(ActorSkill):
    async def execute(self, context, arguments):
        return HypothesisOutput(note_id=context.remember_hypothesis(
            arguments.statement, arguments.confidence, arguments.evidence_refs))


class ContradictionInput(HypothesisInput):
    evidence_refs: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(min_length=2, max_length=8,
        description="At least two distinct retained sources that appear incompatible. This does not establish which source is correct.")


class RememberContradiction(ActorSkill):
    async def execute(self, context, arguments):
        return HypothesisOutput(note_id=context.remember_contradiction(
            arguments.statement, arguments.confidence, arguments.evidence_refs),
            epistemic_status="unresolved_incompatibility")


def skill_package():
    return (SkillDefinition(skill_id="actor.remember-hypothesis", version="1.0.0",
        description="Optionally retain a concise private unverified belief across bounded episodes, citing available evidence. "
            "This changes no game state, verifies no claim and never creates an authoritative observation.",
        input_model=HypothesisInput, result_model=HypothesisOutput, factory=RememberHypothesis,
        result_policy="session_only", availability=lambda context: context.has_working_knowledge),
        SkillDefinition(skill_id="actor.remember-contradiction", version="1.0.0",
            description="Optionally retain a concise incompatibility between at least two available sources. "
                "This never determines a liar, proves a role or rewrites an observation; source provenance and uncertainty remain intact.",
            input_model=ContradictionInput, result_model=HypothesisOutput, factory=RememberContradiction,
            result_policy="session_only", availability=lambda context: context.has_working_knowledge))
