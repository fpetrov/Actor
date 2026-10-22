"""Local selection from acknowledged public evidence, with no model calls.

Camera control lives in director.py. This module only frames already permitted
material for speech; a hypothesis never grants another observation or fact.
"""
import hashlib
import json

MEETING_PHASES=frozenset({"Discussion","Voting","VoteResolution"})
REPEATABLE_PREDICATES=frozenset({"discussion-claim","actor-seen","movement-seen"})


def material_key(record):
    metadata=record["content"]["editorial"]
    value=dict(record["content"]["value"])
    if isinstance(value.get("text"),str):value["text"]=" ".join(value["text"].casefold().split())
    return hashlib.sha256(json.dumps([record["kind"],metadata.get("origin","game"),
        metadata["predicate"],metadata["subjects"],value],sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def heard_materials(evidence,spoken,now):
    return {material_key(record) for ref,record in evidence.facts()
        if record["content"]["editorial"]["predicate"] in REPEATABLE_PREDICATES and spoken.covered(ref,now,120)}


def discussion_refs(view,evidence,spoken,now,wall,attempts,*,maximum=6):
    if view.phase not in MEETING_PHASES:return ()
    rows=[];heard=heard_materials(evidence,spoken,now);selected_material=set()
    for ref,record in evidence.facts():
        metadata=record["content"]["editorial"]
        if (record["kind"]!="player_claim" or metadata.get("origin","game")!="game"
                or metadata["predicate"]!="discussion-claim"
                or not 0<=wall-record["observed_at_unix_ms"]<=120000
                or spoken.covered(ref,now,3600) or material_key(record) in heard
                or now-attempts.get(ref,-float("inf"))<30):continue
        rows.append((record["observed_at_unix_ms"],ref.id,ref,tuple(metadata["subjects"]),material_key(record)))
    rows.sort(key=lambda row:row[:2])
    unique=[]
    for row in rows:
        if row[4] not in selected_material:unique.append(row);selected_material.add(row[4])
    rows=unique
    # First pass gives each heard public speaker a place before taking another
    # line from a prolific speaker. It does not invent a complete player roster.
    result=[];speakers=set()
    for row in rows:
        speaker=row[3][0] if row[3] else row[1]
        if speaker not in speakers:
            result.append(row[2]);speakers.add(speaker)
            if len(result)==maximum:return tuple(result)
    for row in rows:
        if row[2] not in result:result.append(row[2])
        if len(result)==maximum:break
    return tuple(result)


def editorial_frame(view,values):
    focus=view.current.get("camera",{}).get("focus_actor_id","")
    actor=next((row for row in view.current.get("actors",()) if row.get("actor_id")==focus),None)
    claims=[row["ref"] for row in values if row["kind"]=="player_claim" and row["origin"]=="game"]
    mode="meeting_arguments" if view.phase in MEETING_PHASES else "player_watch" if actor else "scene"
    return {"purpose":mode,"watched_player":({"actor_id":focus,"display_name":actor["display_name"]} if actor else None),
        "focus_evidence":[row["ref"] for row in values if focus and focus in row["subjects"]],
        "public_arguments":claims,"opinion_is_hypothesis":True,"intentions_are_unconfirmed":True,
        "movement_direction_values":{"1":"north","2":"north-east","3":"east","4":"south-east",
            "5":"south","6":"south-west","7":"west","8":"north-west","9":"stopped"}}
