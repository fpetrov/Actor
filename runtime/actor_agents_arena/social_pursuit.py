"""Bounded local steering from permitted current actors and honest last sight.

The public floor layout constrains candidate steps; real collision and fresh own
motion remain authoritative. This module never queries another actor's server
pose, selects a victim, changes Alignment or issues a kill.
"""
from functools import cache
import hashlib
from importlib import resources
import json
import math

from pydantic import Field
from extensions.agents_arena.v1 import social_pb2 as pb
from actor_runtime.skills import SkillDefinition, CapabilityRequirement as Req, SkillModel, SkillError
from . import SOCIAL_VISIBLE_WORLD
from .social_skills import SocialActionSkill, TargetInput, BASE, MOVEMENT, states, permitted
from .skills import stopped


@cache
def public_floors():
    return tuple(tuple((point["x"], point["y"]) for point in floor["physical_polygon"])
        for floor in json.loads(resources.files(__package__).joinpath("social_layout.json").read_text())["floors"])


def inside(point, polygon):
    x, y = point
    result = False
    for index, (ax, ay) in enumerate(polygon):
        bx, by = polygon[index-1]
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            result = not result
    return result


def floor_step(x, y, dx, dy, distance, floors):
    # Physical capsule envelope (vertical .81, horizontal .28), not sprite size.
    for fraction in (.35, .7, 1):
        px, py = x+dx*distance*fraction, y+dy*distance*fraction
        for ox, oy in ((0,0),(-.14,-.265),(-.14,.265),(.14,-.265),(.14,.265),(0,-.405),(0,.405)):
            if not any(inside((px+ox, py+oy), polygon) for polygon in floors):
                return False
    return True


def local_direction(x, y, target_x, target_y, actors, *, actor_id, floors=None, step=.3):
    """Choose a finite short direction, avoiding only currently delivered peers."""
    dx, dy = target_x-x, target_y-y
    distance = math.hypot(dx, dy)
    if not math.isfinite(distance) or distance < .001:
        return 0.0, 0.0
    dx, dy = dx/distance, dy/distance
    sign = 1 if hashlib.sha256(actor_id.encode()).digest()[0] % 2 else -1
    choices = []
    for index, angle in enumerate((0,.38*sign,-.38*sign,.76*sign,-.76*sign,1.15*sign,-1.15*sign)):
        vx, vy = dx*math.cos(angle)-dy*math.sin(angle), dx*math.sin(angle)+dy*math.cos(angle)
        if floors is not None and not floor_step(x,y,vx,vy,min(step,distance),floors):
            continue
        penalty = 0.0
        for actor in actors:
            before = math.hypot((x-actor.x)/.34, (y-actor.y)/.87)
            after = math.hypot((x+vx*step-actor.x)/.34, (y+vy*step-actor.y)/.87)
            if after < 1.05 and after < before:
                penalty += 10*(1.05-after)
            penalty += .2/max(.2,after)
        score = math.cos(angle)-penalty-index*.001
        choices.append((score,vx,vy))
    if not choices:
        return 0.0, 0.0
    _, vx, vy = max(choices)
    return vx, vy


def capsule_contact_distance(dx, dy):
    """Centre separation along a ray for the authored .28×.81 capsules."""
    distance = math.hypot(dx, dy)
    if distance <= .001:
        return .28
    ux, uy = abs(dx)/distance, abs(dy)/distance
    # Minkowski sum: radius .28 with a vertical centre segment ±.53.
    if ux > .001 and .28*uy/ux <= .53:
        return .28/ux
    return .53*uy + math.sqrt(max(0, .28**2-(.53*ux)**2))


class FollowInput(TargetInput):
    timeout_seconds: float = Field(default=20.0, ge=1, le=45)
    lost_visibility_grace_seconds: float = Field(default=1.2, ge=0, le=3,
        description="After losing sight, move only toward the actual last delivered location for this short duration; never extrapolate a hidden actor.")
    stop_distance: float | None = Field(default=None, ge=.3, le=2,
        description="Default stays .04m inside the public kill distance; this procedure never kills or identifies a side.")


class FollowOutput(SkillModel):
    match_id: str
    stop_reason: str
    reached_current_target: bool
    target_currently_visible: bool
    last_seen_age_seconds: float
    local_adjustments: int


class FollowVisible(SocialActionSkill):
    owns_movement = True

    async def execute(self, context, arguments):
        deadline = context.now()+arguments.timeout_seconds
        match, _ = states(context)
        stop_distance = max(.3,(match.kill_distance or .9)-.04)
        if arguments.stop_distance is not None:
            stop_distance = min(stop_distance, arguments.stop_distance)
        last_seen = last_sequence = None
        target_x = target_y = 0.0
        distinct = adjustments = 0
        settling = False
        last_pair = None
        progress_point, progress_at = None, context.now()
        reason, visible = "deadline", False
        while context.now() < deadline:
            transform, motion, _ = self.navigator.state(context)
            frame = context.observation_entry(SOCIAL_VISIBLE_WORLD, pb.SocialVisibleWorldObservation, max_age_seconds=1)
            if frame.payload.match_id != match.match_id or frame.payload.recipient_actor_id != context.actor_id:
                raise SkillError("target_unavailable", "Current permitted view belongs to another scope")
            target = next((value for value in frame.payload.actors if value.actor_id == arguments.actor_id),None)
            visible = target is not None
            if visible:
                if frame.sequence != last_sequence:
                    last_seen, last_sequence = frame.received_at, frame.sequence
                # A tiny current-sight lead helps moving targets. Never use it
                # once the full replacement bundle no longer includes them.
                vx, vy = target.velocity_x*.12, target.velocity_y*.12
                magnitude = max(1.0,math.hypot(vx,vy)/.3)
                target_x, target_y = target.x+vx/magnitude, target.y+vy/magnitude
                last_position = target.x, target.y
            else:
                if last_seen is None:
                    raise SkillError("target_unavailable", "Follow requires a currently permitted target at its start")
                target_x, target_y = last_position
                if context.now()-last_seen >= arguments.lost_visibility_grace_seconds:
                    reason="target_lost"; break
            x, y = transform.payload.x, transform.payload.y+.46
            distance = math.hypot(target_x-x,target_y-y)
            pair = transform.sequence, motion.sequence
            # Enter a little inside the limit before collecting stopped
            # samples. Otherwise a slowly departing target can move outside
            # the threshold before four observations and cause endless taps.
            target_speed = math.hypot(target.velocity_x,target.velocity_y) if visible else 0
            enter_distance = max(.29,stop_distance-min(.16,.015+target_speed*.25))
            if visible:
                # Vertical capsule contact is .81m, not the horizontal .28m.
                # Hysteresis must never demand passing through a visible
                # target's collider before accepting a valid opportunity.
                # Leave room for one small discrete controller adjustment;
                # demanding exact contact can oscillate around the collider.
                contact = capsule_contact_distance(target.x-x,target.y-y)+.025
                if contact < stop_distance:
                    enter_distance = max(enter_distance,contact)
            actual_distance = math.hypot(target.x-x,target.y-y) if visible else math.inf
            if visible and (actual_distance <= enter_distance or settling and actual_distance <= stop_distance):
                settling = True
                self.navigator.move(context)
                if stopped(motion.payload) and pair != last_pair:
                    distinct += 1
                elif not stopped(motion.payload):
                    distinct = 0
                if distinct >= 4:
                    reason="opportunity"; break
            elif not visible and distance <= .15:
                self.navigator.move(context)
            else:
                settling = False
                distinct = 0
                obstacles = tuple(actor for actor in frame.payload.actors if actor.actor_id != arguments.actor_id)
                vx, vy = local_direction(x,y,target_x,target_y,obstacles,actor_id=context.actor_id,floors=public_floors())
                if abs(vx*(target_y-y)-vy*(target_x-x)) > .05:
                    adjustments += 1
                strength=min(1.0,max(.18+target_speed/2.6,distance-max(0,enter_distance-.08)))
                self.navigator.move(context,vx*strength,vy*strength)
            if pair != last_pair:
                if progress_point is None or math.hypot(x-progress_point[0],y-progress_point[1]) >= .04:
                    progress_point, progress_at = (x,y), context.now()
                elif context.now()-progress_at >= 2 and distinct == 0:
                    reason="blocked"; break
            last_pair=pair
            await context.sleep(.05)
        self.navigator.move(context)
        return FollowOutput(match_id=match.match_id,stop_reason=reason,reached_current_target=reason=="opportunity",
            target_currently_visible=visible,last_seen_age_seconds=max(0,context.now()-last_seen) if last_seen is not None else 0,
            local_adjustments=adjustments)


@cache
def skill_package():
    return (SkillDefinition("agents-arena.follow-visible-actor","1.0.0",
        "Follow a player you currently see, with bounded local avoidance of other currently visible peers. "
        "If sight is lost, approach only the fixed last delivered location for a short grace, then stop. "
        "No hidden tracking, role inference or automatic attack. A reached opportunity still needs your own next decision.",
        FollowInput,FollowOutput,FollowVisible,BASE+MOVEMENT+
            (Req(SOCIAL_VISIBLE_WORLD,pb.SocialVisibleWorldObservation,"observation",1),),
        ("actor.standard.locomotion2d",),timeout_seconds=50,result_policy="session_only",
        availability=permitted((pb.SOCIAL_PHASE_EXPLORATION,))),)
