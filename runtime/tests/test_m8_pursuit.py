"""Visible/local pursuit under actual capsule-shaped contact constraints."""
import asyncio
import math
from types import SimpleNamespace

import pytest

from extensions.agents_arena.v1 import social_pb2 as pb
from actor_agents_arena import SOCIAL_VISIBLE_WORLD, SOCIAL_MATCH
from actor_agents_arena.social_pursuit import FollowVisible, FollowInput, local_direction, floor_step, capsule_contact_distance
from actor_standard import LOCOMOTION
from test_social_navigation import PhysicalContext
from test_social_runtime import observe, engine


class PursuitContext(PhysicalContext):
    def __init__(self,*,speed=0.0,lose_at=None,hidden_x=1000,cached=False,kill_distance=.9,vertical=False):
        super().__init__(cached=cached)
        self.target_speed,self.lose_at,self.hidden_x=speed,lose_at,hidden_x
        self.kill_distance=kill_distance
        self.vertical=vertical

    def observation(self,cap,cls):
        value=super().observation(cap,cls)
        if cap==SOCIAL_MATCH:value.kill_distance=self.kill_distance
        return value

    def observation_entry(self,cap,cls,**kwargs):
        if cap==SOCIAL_VISIBLE_WORLD:
            actors=[]
            if self.lose_at is None or self.at<self.lose_at:
                actors=[pb.SocialVisibleActor(actor_id="target",x=0 if self.vertical else 2+self.target_speed*self.at,
                    y=self.target_speed*self.at if self.vertical else -.94,
                    velocity_x=0 if self.vertical else self.target_speed,
                    velocity_y=self.target_speed if self.vertical else 0,observed_revision=self.sequence)]
            return SimpleNamespace(payload=pb.SocialVisibleWorldObservation(match_id="fixture",recipient_actor_id=self.actor_id,
                actors=actors),sequence=self.sequence,received_at=self.at)
        return super().observation_entry(cap,cls)

    def clear(self,x,y):
        # Only the fixture's physics knows this body's hidden position after
        # loss. No policy or observation API exposes hidden_x.
        tx=0 if self.vertical else 2+self.target_speed*self.at
        ty=self.target_speed*self.at-.46 if self.vertical else -1.4
        if self.lose_at is not None and self.at>=self.lose_at:tx=self.hidden_x
        return super().clear(x,y) and (x-tx)**2+max(0,abs(y-ty)-.53)**2>=.28**2


@pytest.mark.parametrize("speed,kill_distance",[(0.0,.9),(.25,.9),(0.0,.6)])
async def test_moving_visible_actor_reached_within_public_range_and_four_distinct_stops(speed,kill_distance):
    context=PursuitContext(speed=speed,kill_distance=kill_distance)
    result=await FollowVisible().execute(context,FollowInput(actor_id="target",timeout_seconds=8.0))
    assert result.reached_current_target and result.target_currently_visible and result.stop_reason=="opportunity"
    assert math.hypot(2+speed*context.at-context.x,-.94-(context.y+.46))<=kill_distance
    assert context.commands[-1]==(0,0) and context.sequence>=5


async def test_vertical_moving_capsule_contact_can_reach_a_real_kill_range_opportunity():
    context=PursuitContext(speed=.15,vertical=True)
    result=await FollowVisible().execute(context,FollowInput(actor_id="target",timeout_seconds=6.0))
    assert result.reached_current_target and result.stop_reason=="opportunity"
    distance=math.hypot(context.x,.15*context.at-(context.y+.46))
    assert .81<=distance<=.86 and context.commands[-1]==(0,0)
    # PhysicalContext.command rejects every capability except locomotion.
    assert context.at<6


async def test_lost_target_uses_only_fixed_last_delivered_position_then_stops():
    paths=[]
    for hidden in (-1000,1000):
        context=PursuitContext(lose_at=.2,hidden_x=hidden)
        result=await FollowVisible().execute(context,FollowInput(actor_id="target",lost_visibility_grace_seconds=.3))
        assert result.stop_reason=="target_lost" and not result.target_currently_visible and not result.reached_current_target
        assert context.at<.6 and result.last_seen_age_seconds>=.3 and context.commands[-1]==(0,0)
        paths.append(context.trajectory)
    assert paths[0]==paths[1]


async def test_cached_stop_cannot_be_counted_as_four_new_authoritative_samples():
    context=PursuitContext(cached=True)
    context.x=1.2
    result=await FollowVisible().execute(context,FollowInput(actor_id="target",timeout_seconds=1.0))
    assert not result.reached_current_target and result.stop_reason=="deadline" and context.commands[-1]==(0,0)


def test_visible_peer_changes_local_direction_and_public_floor_constrains_it():
    assert math.isclose(capsule_contact_distance(0,1),.81)
    assert math.isclose(capsule_contact_distance(1,0),.28)
    peer=pb.SocialVisibleActor(actor_id="other",x=.3,y=0)
    vx,vy=local_direction(0,0,3,0,[peer],actor_id="own")
    assert abs(vy)>.1 and math.isclose(math.hypot(vx,vy),1)
    tiny=((( -.15,-.41),(.15,-.41),(.15,.41),(-.15,.41)),)
    assert local_direction(0,0,3,0,[],actor_id="own",floors=tiny)==(0,0)
    broad=(((-2,-2),(2,-2),(2,2),(-2,2)),)
    assert floor_step(0,0,vx,vy,.3,broad)


async def test_foreground_follow_cancellation_zeroes_intent_and_stops_command_count(session):
    await observe(session,visible=[pb.SocialVisibleActor(actor_id="target",x=2,y=-.94,observed_revision=1)])
    scheduler=engine(session)
    pending=asyncio.create_task(scheduler.run("agents-arena.follow-visible-actor",{"actor_id":"target"}))
    await asyncio.sleep(.02)
    await scheduler.cancel()
    result=await pending
    assert result.status=="cancelled" and not scheduler.report().get("active_skill")
    count=session.next_command_id
    value=next(item for item in session.mailbox.commands.values() if item.command.capability_id==LOCOMOTION)
    from actor_standard import Locomotion2DCommand
    move=Locomotion2DCommand();value.command.payload.Unpack(move)
    assert move.desired_move_x==move.desired_move_y==0
    await asyncio.sleep(.07)
    assert session.next_command_id==count
