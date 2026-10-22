"""Compact commentary external state; no player catalog or private memory."""
from dataclasses import asdict
import hashlib
import json
import math

from .contracts import CommentaryCandidate, CommentaryRequest
from .editorial import editorial_frame


def topic_key(mode, facts):
    # Version/time-independent lineage blocks refreshes, while a substantive
    # correction under the same opaque evidence ID remains new material.
    def material(record):
        value={"kind":record["kind"],"origin":record["content"]["editorial"].get("origin","game"),
            "value":record["content"]["value"]}
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    lineage=sorted({(record["content"]["editorial"]["evidence_id"],
        tuple(record["content"]["editorial"]["subjects"]),record["content"]["editorial"]["predicate"],material(record)) for _,record in facts})
    return hashlib.sha256(json.dumps(lineage,sort_keys=True).encode()).hexdigest()


def speech_presentation(candidate, config):
    """Trusted local sizing, not instructions supplied by game/chat content."""
    mode=candidate.mode
    if mode in ("intro","outro"):
        words=(8,16); clauses=(1,1); maximum=1
        purpose="ground_current_game_after_local_greeting" if mode=="intro" else "ground_result_before_local_farewell"
    elif candidate.priority==0:
        words=(5,16);clauses=(1,1);maximum=1;purpose="urgent_observation"
    else:
        idle=mode in ("idle_recap","memory_callback")
        words=config.idle.words_target if idle else config.spoken_words_target
        clauses=(3,3) if idle else (2,3);maximum=4
        purpose="historical_connection" if mode=="memory_callback" else "grounded_conversation" if idle else "grounded_reaction"
    return {"purpose":purpose,"words_target":list(words),"clauses_preferred":list(clauses),
        "max_segments":maximum,"words_per_segment_target":16,"padding_allowed":False,
        "courtesy_handled_locally":mode in ("intro","outro"),
        "fourth_segment_only_if_brief":maximum==4}


def build_context(candidate, view, evidence, spoken, config, *, utterance_id, epoch):
    values=[]
    for ref in candidate.evidence:
        record=evidence.resolve(ref)
        if record is None: raise ValueError("Commentary evidence expired before context construction")
        value=record.model_dump(mode="json")
        # Metadata remains external data. Historical records retain exact time
        # and kind; a model summary cannot refresh them or turn claims into truth.
        metadata=value["content"]["editorial"]
        values.append({"ref":asdict(ref),"kind":"viewer_claim" if metadata.get("origin")=="viewer_chat" else value["kind"],
            "origin":metadata.get("origin","game"),"source_event_id":value["source_event_id"],
            "observed_at_unix_ms":value["observed_at_unix_ms"],"expires_at_unix_ms":value["expires_at_unix_ms"],
            "age_at_presented_view_seconds":((view.observed_at_unix_ms-value["observed_at_unix_ms"])/1000
                if view.observed_at_unix_ms>=value["observed_at_unix_ms"] else None),
            "value":value["content"]["value"],"subjects":value["content"]["editorial"]["subjects"],
            "predicate":value["content"]["editorial"]["predicate"]})
    external={"mode":candidate.mode,"phase":view.phase,"current_view":view.current,
        "presented_view_observed_at_unix_ms":view.observed_at_unix_ms,
        "evidence":values,"actually_spoken":spoken.recent(6),"topic_key":candidate.topic_key,
        "speech_presentation":speech_presentation(candidate,config),
        "historical_callback":candidate.mode=="memory_callback",
        "public_notes":evidence.selected_notes(set(candidate.evidence)),
        "editorial_frame":editorial_frame(view,values)}
    if (view.phase in ("Discussion","Voting","VoteResolution") and candidate.priority>1
            and external["editorial_frame"]["public_arguments"]):
        external["speech_presentation"].update(purpose="meeting_arguments",words_target=[40,65],clauses_preferred=[3,4])
    maximum=config.client_context_hard_tokens*4
    target=(config.idle.context_target_tokens if candidate.mode in ("idle_recap","memory_callback") else config.context_target_tokens)*4
    encoded=lambda:json.dumps(external,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()
    # Selected exact evidence is mandatory. Trim optional current detail and
    # playback examples instead of silently losing a supporting citation.
    if len(encoded())>target: external["current_view"]={}
    while len(encoded())>target and external["actually_spoken"]: external["actually_spoken"].pop()
    if len(encoded())>maximum: raise ValueError("Selected evidence exceeds commentary context budget")
    return CommentaryRequest(candidate,external,
        config.idle.max_output_tokens if candidate.mode in ("idle_recap","memory_callback") else 768 if candidate.mode=="analysis" else 512,
        utterance_id,epoch,"medium" if candidate.mode=="analysis" and candidate.priority>=2 else "default")


def estimated_tokens(request):
    return math.ceil(len(json.dumps(request.external_state,ensure_ascii=False).encode())/4)


def reservation_input_tokens(provider,request):
    minimum=estimated_tokens(request)+512
    estimate=getattr(provider,"input_token_reservation",None)
    if not callable(estimate):return minimum
    value=estimate(request)
    if type(value)is not int or not 1<=value<=1000000:
        raise ValueError("Invalid provider input reservation")
    return max(minimum,value)
