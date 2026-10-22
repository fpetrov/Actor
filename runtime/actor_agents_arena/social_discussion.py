"""Bounded discussion procedures over the existing communication inbox.

Messages stay attributed claims. These procedures do not call a model, infer a
culprit, choose a ballot or turn arbitrary player prose into a strategic event.
"""
import time
from functools import cache

from pydantic import Field, field_validator
from extensions.agents_arena.v1 import social_pb2 as pb
from actor_runtime.skills import ActorSkill, CapabilityRequirement as Req, SkillDefinition, SkillError, SkillTimeout, SkillModel
from actor_standard_communication import (PEERS, SEND_MESSAGE, MESSAGE_RECEIVED, MESSAGE_DELIVERY_RESULT,
    CommunicationPeersObservation, SendMessageCommand, MessageReceivedEvent, MessageDeliveryResultEvent)
from actor_standard_communication.skills import SendMessage, SendMessageInput
from . import SOCIAL_MEETING
from .social_skills import BASE, states, permitted


class AwaitDiscussionInput(SkillModel):
    after_server_sequence: int = Field(default=0, ge=0,
        description="Only unread discussion messages after this server sequence. Zero includes retained early arrivals; timestamps still identify older claims.")
    timeout_seconds: float = Field(default=12.0, gt=0, le=30,
        description="Bounded model-free wait. Returns before the discussion deadline, reserving time for another decision.")
    settle_seconds: float = Field(default=1.5, ge=0, le=5,
        description="After the first reply, collect a short burst into one result instead of calling the Brain on each message.")
    max_messages: int = Field(default=6, ge=1, le=12)
    decision_reserve_seconds: float = Field(default=8.0, ge=0, le=30)


class DiscussInput(AwaitDiscussionInput):
    text: str = Field(min_length=1, max_length=512,
        description="Your chosen concise argument, question, defence or misleading game claim; never a verified fact merely because it was sent.")

    @field_validator("text")
    @classmethod
    def utf8(cls, value):
        if len(value.encode("utf-8")) > 512:
            raise ValueError("Discussion message exceeds 512 UTF-8 bytes")
        return value


class DiscussionClaim(SkillModel):
    message_id: str
    sender_actor_id: str
    text: str
    server_sequence: int
    server_unix_ms: int
    expires_at_unix_ms: int
    epistemic_status: str = "unverified_player_claim"


class DiscussionOutput(SkillModel):
    match_id: str
    meeting_id: str
    phase_revision: int
    claims: list[DiscussionClaim]
    latest_server_sequence: int
    received_count: int
    timed_out: bool
    deadline_near: bool
    accepted_by_server: bool = False
    receipt_stage: str = "not_sent"


class AwaitDiscussionUpdate(ActorSkill):
    async def collect(self, context, arguments, *, receipt=None):
        match, own = states(context)
        meeting = context.observation(SOCIAL_MEETING, pb.SocialMeetingObservation)
        if (match.phase != pb.SOCIAL_PHASE_DISCUSSION or own.life != pb.SOCIAL_LIFE_ALIVE or
                meeting.match_id != match.match_id or meeting.phase_revision != match.phase_revision):
            raise SkillError("discussion_unavailable", "A coherent live Discussion is required")
        deadline = context.now() + arguments.timeout_seconds
        phase_end = meeting.discussion_ends_at_unix_ms or match.phase_ends_at_unix_ms
        if phase_end:
            deadline = min(deadline, context.now() + max(0, (phase_end - int(time.time()*1000))/1000 - arguments.decision_reserve_seconds))
        sequence, claims = arguments.after_server_sequence, []
        deadline_near = deadline <= context.now()
        while context.now() < deadline and len(claims) < arguments.max_messages:
            current, current_own = states(context)
            if (current.match_id, current.phase_revision, current.phase, current_own.life) != (
                    match.match_id, match.phase_revision, pb.SOCIAL_PHASE_DISCUSSION, pb.SOCIAL_LIFE_ALIVE):
                raise SkillError("phase_changed", "Discussion changed while collecting replies")
            try:
                entry = await context.wait_event(MESSAGE_RECEIVED, MessageReceivedEvent,
                    predicate=lambda value: value.match_id == match.match_id and value.channel_id == "discussion"
                        and value.recipient_actor_id == context.actor_id and value.sender_actor_id != context.actor_id
                        and value.server_sequence > sequence,
                    timeout_seconds=min(.2, max(.001, deadline-context.now())))
            except SkillTimeout:
                continue
            value = entry.payload
            context.expire_result_at(value.expires_at_unix_ms)
            claims.append(DiscussionClaim(message_id=value.message_id, sender_actor_id=value.sender_actor_id,
                text=value.text, server_sequence=value.server_sequence, server_unix_ms=value.server_unix_ms,
                expires_at_unix_ms=value.expires_at_unix_ms))
            sequence = value.server_sequence
            if len(claims) == 1:
                deadline = min(deadline, context.now() + arguments.settle_seconds)
        return DiscussionOutput(match_id=match.match_id, meeting_id=meeting.meeting_id,
            phase_revision=match.phase_revision, claims=claims, latest_server_sequence=sequence,
            received_count=len(claims), timed_out=not claims and not deadline_near, deadline_near=deadline_near,
            accepted_by_server=receipt.accepted_by_server if receipt else False,
            receipt_stage=receipt.stage if receipt else "not_sent")

    async def execute(self, context, arguments):
        return await self.collect(context, arguments)


class Discuss(AwaitDiscussionUpdate):
    async def execute(self, context, arguments):
        match, _ = states(context)
        if match.phase != pb.SOCIAL_PHASE_DISCUSSION:
            raise SkillError("discussion_unavailable", "Discussion is not active")
        remaining = (match.phase_ends_at_unix_ms - int(time.time()*1000))/1000 if match.phase_ends_at_unix_ms else 10
        if remaining <= 0:
            raise SkillError("phase_expired", "Discussion deadline has elapsed")
        receipt = await SendMessage().execute(context, SendMessageInput(channel_id="discussion", text=arguments.text,
            ttl_ms=300000, timeout_seconds=max(.01, min(10.0, remaining))))
        return await self.collect(context, arguments, receipt=receipt)

    async def cleanup(self, context):
        # One accepted message has no persistent actuator intent.
        pass


@cache
def skill_package():
    requirements = BASE + (Req(SOCIAL_MEETING, pb.SocialMeetingObservation, "observation", 3),
        Req(PEERS, CommunicationPeersObservation, "observation", 3),
        Req(MESSAGE_RECEIVED, MessageReceivedEvent, "event"))
    availability = permitted((pb.SOCIAL_PHASE_DISCUSSION,))
    return (
        SkillDefinition("agents-arena.await-discussion-update", "1.0.0",
            "Read a bounded burst of attributed untrusted discussion replies without model polling. Early messages are retained, "
            "ordinary chat never forces a replan, and this wait reserves time before Voting. Decide afterwards what to believe or ask.",
            AwaitDiscussionInput, DiscussionOutput, AwaitDiscussionUpdate, requirements,
            timeout_seconds=35, result_policy="session_only", availability=availability),
        SkillDefinition("agents-arena.discuss", "1.0.0",
            "Send your own chosen argument, question, defence or strategic misleading game claim, await real server acceptance, "
            "then gather a short burst of replies in one bounded procedure. No model call occurs inside; acceptance is not a read receipt. "
            "Returned messages remain attributed claims, never verified Alignment or direct observations.",
            DiscussInput, DiscussionOutput, Discuss, requirements + (Req(SEND_MESSAGE, SendMessageCommand, "command"),
                Req(MESSAGE_DELIVERY_RESULT, MessageDeliveryResultEvent, "event")), (SEND_MESSAGE,),
            timeout_seconds=45, result_policy="session_only", availability=availability),
    )
