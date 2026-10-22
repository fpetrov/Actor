"""Whitelisted Arena observer DTO projection; no player context or policy."""
import math
import time

from extensions.agents_arena.v1 import broadcast_pb2 as pb
from extensions.agents_arena.v1 import social_pb2 as social
from .contracts import EditorialFact, EvidenceRef, PresentedView, PresentationAck, Scope, bounded_text

PHASES = {social.SOCIAL_PHASE_WAITING_FOR_PLAYERS:"Waiting",social.SOCIAL_PHASE_ROLE_ASSIGNMENT:"RoleAssignment",
    social.SOCIAL_PHASE_EXPLORATION:"Exploration",social.SOCIAL_PHASE_DISCUSSION:"Discussion",
    social.SOCIAL_PHASE_VOTING:"Voting",social.SOCIAL_PHASE_VOTE_RESOLUTION:"VoteResolution",
    social.SOCIAL_PHASE_GAME_OVER:"GameOver"}


class ArenaBroadcastProjector:
    def __init__(self, *, observer_id, broadcast_session_id, match_id, clock=time.monotonic):
        for identity in (observer_id,broadcast_session_id,match_id):bounded_text(identity,128)
        self.observer_id,self.session_id,self.match_id=observer_id,broadcast_session_id,match_id
        self.clock=clock

    def project(self, source):
        payload=getattr(source,"payload",source)
        if getattr(getattr(payload,"DESCRIPTOR",None),"full_name",None)!=pb.BroadcastPresentedViewObservation.DESCRIPTOR.full_name:
            raise ValueError("Expected the typed presented broadcast observation")
        if getattr(source,"entity_id",self.observer_id)!=self.observer_id:
            raise ValueError("Broadcast observation belongs to another recipient")
        typed=pb.BroadcastPresentedViewObservation()
        typed.ParseFromString(payload.SerializeToString())
        view,ack=typed.view,typed.presentation
        if (view.scope.observer_id!=self.observer_id or view.scope.broadcast_session_id!=self.session_id or
                view.scope.match_id!=self.match_id or view.scope!=ack.scope):
            raise ValueError("Observer admission scope mismatch")
        if ack.width<1 or ack.height<1 or not math.isfinite(ack.presented_at_monotonic_ms) or ack.presented_at_monotonic_ms<0:
            raise ValueError("A native rendered frame is required")
        if view.phase not in PHASES or bool(view.terminal)!=(view.phase==social.SOCIAL_PHASE_GAME_OVER):
            raise ValueError("Incoherent authoritative phase/terminal boundary")
        if not view.terminal and (view.winning_side or view.win_reason or view.final_players):
            raise ValueError("Premature terminal disclosure")
        if len(view.actors)>6 or len(view.bodies)>6 or len(view.facts)>96 or len(view.final_players)>6 or len(view.revoked_evidence_ids)>96:
            raise ValueError("Observer projection cardinality exceeded")
        if len(ack.presented_evidence)>96:raise ValueError("Native evidence acknowledgement bound exceeded")
        refs=tuple(EvidenceRef(row.evidence_id,str(row.version)) for row in ack.presented_evidence)
        if any(row.version<1 for row in ack.presented_evidence) or len({ref.id for ref in refs})!=len(refs):
            raise ValueError("Native evidence acknowledgements must be unique exact versions")
        scope=Scope(view.scope.broadcast_session_id,view.scope.match_id,view.scope.connection_generation,view.scope.view_epoch)
        current={"camera":{"anchor_id":view.camera.anchor_id,"focus_actor_id":view.camera.focus_actor_id,
            "x":view.camera.x,"y":view.camera.y,"visibility_radius":view.camera.visibility_radius},
            "phase_revision":view.phase_revision,"phase_ends_at_unix_ms":view.phase_ends_at_unix_ms,
            "crew_tasks_completed":view.crew_tasks_completed,"task_threshold":view.task_threshold,
            "visible_actor_ids":[actor.actor_id for actor in view.actors],
            "actors":[{"actor_id":actor.actor_id,"display_name":actor.display_name,"x":actor.x,"y":actor.y} for actor in view.actors],
            "bodies":[{"body_id":body.body_id,"victim_actor_id":body.victim_actor_id,"victim_display_name":body.victim_display_name,
                       "x":body.x,"y":body.y} for body in view.bodies],
            "emergency":{"active":view.emergency_active,"ends_at_unix_ms":view.emergency_ends_at_unix_ms,"room_id":view.emergency_room_id}}
        meeting=view.meeting
        if len(meeting.participants)>6 or len(meeting.result)>6:raise ValueError("Public meeting bound exceeded")
        current["meeting"]={"meeting_id":meeting.meeting_id,"resolution":meeting.resolution,"ejected_actor_id":meeting.ejected_actor_id,
            "participants":[{"actor_id":row.actor_id,"display_name":row.display_name,"life":row.life} for row in meeting.participants],
            "result":[{"candidate_actor_id":row.candidate_actor_id,"votes":row.votes} for row in meeting.result]}
        if view.terminal:
            current["terminal"]={"winning_side":view.winning_side,"win_reason":view.win_reason,
                "players":[{"actor_id":row.actor_id,"display_name":row.display_name,"alignment":row.alignment,"life":row.life} for row in view.final_players]}
        facts=[]
        for fact in view.facts:
            if fact.kind not in (pb.BROADCAST_FACT_KIND_OBSERVATION,pb.BROADCAST_FACT_KIND_PLAYER_CLAIM) or fact.version<1:
                raise ValueError("Unknown editorial fact kind/version")
            if len(fact.subject_ids)>2:raise ValueError("Editorial subjects exceeded")
            bounded_text(fact.text,512,empty=True)
            content={"text":fact.text,"room_id":fact.room_id,"actor_id":fact.actor_id,
                "target_actor_id":fact.target_actor_id,"value":fact.value,"label":fact.label}
            facts.append(EditorialFact(fact.evidence_id,str(fact.version),
                "player_claim" if fact.kind==pb.BROADCAST_FACT_KIND_PLAYER_CLAIM else "historical_observation",
                fact.source_event_id,tuple(fact.subject_ids),fact.predicate,fact.observed_at_unix_ms,
                fact.retention_expires_at_unix_ms,content,fact.priority,fact.topic_key,fact.unresolved))
        return PresentedView(scope,view.sequence,view.semantic_revision,view.observed_at_unix_ms,view.expires_at_unix_ms,
            PHASES[view.phase],current,tuple(facts),PresentationAck(scope,ack.rendered_sequence,ack.frame_counter,self.clock(),refs),
            view.terminal,tuple(view.revoked_evidence_ids))
