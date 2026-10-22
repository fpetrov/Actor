"""Compact commentary external state; no player catalog or private memory."""
from dataclasses import asdict
import hashlib
import json
import math

from .contracts import CommentaryCandidate, CommentaryRequest


def topic_key(mode, facts):
    # Version-independent lineage prevents synonyms or a refreshed latest slot
    # from disguising the same semantic topic as new material.
    lineage=sorted((record["content"]["editorial"]["evidence_id"],
        tuple(record["content"]["editorial"]["subjects"]),record["content"]["editorial"]["predicate"]) for _,record in facts)
    return hashlib.sha256(json.dumps(lineage,sort_keys=True).encode()).hexdigest()


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
            "value":value["content"]["value"],"subjects":value["content"]["editorial"]["subjects"],
            "predicate":value["content"]["editorial"]["predicate"]})
    external={"mode":candidate.mode,"phase":view.phase,"current_view":view.current,
        "evidence":values,"actually_spoken":spoken.recent(2),"topic_key":candidate.topic_key,
        "historical_callback":candidate.mode=="memory_callback",
        "public_notes":evidence.selected_notes(set(candidate.evidence))}
    maximum=config.client_context_hard_tokens*4
    target=(config.idle.context_target_tokens if candidate.mode in ("idle_recap","memory_callback") else config.context_target_tokens)*4
    encoded=lambda:json.dumps(external,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()
    # Selected exact evidence is mandatory. Trim optional current detail and
    # playback examples instead of silently losing a supporting citation.
    if len(encoded())>target: external["current_view"]={}
    if len(encoded())>target: external["actually_spoken"]=external["actually_spoken"][:1]
    if len(encoded())>maximum: raise ValueError("Selected evidence exceeds commentary context budget")
    return CommentaryRequest(candidate,external,
        config.idle.max_output_tokens if candidate.mode in ("idle_recap","memory_callback") else 512,
        utterance_id,epoch)


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
