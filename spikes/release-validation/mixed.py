#!/usr/bin/env python3
"""Offline Human + three official Codex artifact-consistency check.

Human input is an explicit operator declaration with hashed native screenshots,
not inferred from a control-mode field and not a signed input attestation. The
four-Runtime evaluator is deliberately unchanged. No game/model/process launch.
Private input artifacts may contain identities; the output never copies them.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import zlib

SPEC=importlib.util.spec_from_file_location("mixed_m7_evaluator",Path(__file__).resolve().parents[1]/"social-validation/evaluate.py")
M7=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M7)
SCHEMA="actor.human-mixed-evaluation.v1"
HUMAN_SCHEMA="actor.human-client-completion.v1"
OPERATOR_SCHEMA="actor.native-operator-proof.v1"
SUPERVISOR_SCHEMA="actor.m8-human-mixed-supervisor.v1"
USAGE=("input_tokens","cached_input_tokens","output_tokens","total_tokens")
ACTIONS={"move","ready","chat","report_body","call_meeting","cast_vote","work_task",
    "task_step","repair_emergency","kill","sabotage"}
STATUSES={"authoritative_match_completed","interrupted","failed","waiting_timeout","running","starting","incomplete"}


def optional_read(path):
    return M7.read(path) if path.is_file() else {}


def positive(value):
    return type(value) is int and 0<value<=2**53-1


def png_proof(folder,record):
    """Validate contained file, digest and bounded PNG chunk integrity only.

    Pixel meaning is declared by the operator; this cannot recognize a native
    window or prove that a screenshot depicts the claimed match.
    """
    filename=record.get("file")
    if not isinstance(filename,str) or not 1<=len(filename)<=2048:return False
    relative=Path(filename)
    path=(folder/relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(folder) or not path.is_file():return False
    if not 64<=path.stat().st_size<=16*1024*1024:return False
    data=path.read_bytes()
    if record.get("sha256")!=hashlib.sha256(data).hexdigest() or not data.startswith(b"\x89PNG\r\n\x1a\n"):return False
    position=8;header=False;pixels=False;ended=False;compressed=[]
    while position<len(data):
        if len(data)-position<12:return False
        size=struct.unpack_from(">I",data,position)[0]
        if size>16*1024*1024 or position+12+size>len(data):return False
        kind=data[position+4:position+8];payload=data[position+8:position+8+size]
        crc=struct.unpack_from(">I",data,position+8+size)[0]
        if zlib.crc32(kind+payload)&0xffffffff!=crc:return False
        if not header:
            if kind!=b"IHDR" or size!=13:return False
            width,height,depth,color,compression,filtering,interlace=struct.unpack(">IIBBBBB",payload)
            depths={0:{1,2,4,8,16},2:{8,16},3:{1,2,4,8},4:{8,16},6:{8,16}}
            if not(320<=width<=8192 and 200<=height<=8192 and depth in depths.get(color,set())
                    and compression==filtering==interlace==0):return False
            stride=(width*{0:1,2:3,3:1,4:2,6:4}[color]*depth+7)//8
            expected=height*(stride+1)
            if expected>128*1024*1024:return False
            header=True
        elif kind==b"IHDR":return False
        if kind==b"IDAT":pixels|=bool(size);compressed.append(payload)
        position+=12+size
        if kind==b"IEND":
            ended=size==0 and position==len(data)
            break
    if not(header and pixels and ended):return False
    try:
        decoder=zlib.decompressobj();raw=decoder.decompress(b"".join(compressed),expected+1)
        return (len(raw)==expected and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
            and all(raw[offset] in range(5) for offset in range(0,len(raw),stride+1)))
    except zlib.error:
        return False


def model_proof(runtime,client,launch,proof,summary):
    cognition=runtime.get("cognitive",{})
    episodes=M7.array(cognition.get("episodes",[]),256)
    valid=bool(episodes) and launch.get("brain")=="codex" and cognition.get("active_episode",False)is False
    turns=tools=0;usage={key:0 for key in USAGE};known={key:bool(episodes) for key in USAGE}
    for episode in episodes:
        brain=episode.get("brain",{});adapter=brain.get("adapter") or {}
        valid&=(brain.get("provider")=="codex" and adapter.get("sdk_version")=="0.162.1"
            and adapter.get("sandbox")=="read-only" and adapter.get("ephemeral") is True
            and adapter.get("closed") is True and adapter.get("decision_active") is False
            and episode.get("cleanup_errors")==[] and type(episode.get("pending_cleanup_tasks",0)) is int
            and episode.get("pending_cleanup_tasks",0)==0)
        amount=M7.count(adapter.get("unique_model_turns",0));turns+=amount
        records=M7.array(brain.get("turns",[]),4096)
        valid&=len(records)>=amount
        for record in records:
            # No missing-tool-count -> zero inference, even on interrupted turns.
            if "tool_calls" not in record:valid=False
            tools+=M7.count(record.get("tool_calls",0))
        values=brain.get("usage_totals",{})
        if not isinstance(values,dict):raise ValueError("invalid_provider_usage")
        if all(key in values for key in ("input_tokens","output_tokens","total_tokens")):
            valid&=M7.count(values["total_tokens"])==M7.count(values["input_tokens"])+M7.count(values["output_tokens"])
        if "cached_input_tokens"in values and "input_tokens"in values:
            valid&=M7.count(values["cached_input_tokens"])<=M7.count(values["input_tokens"])
        for key in USAGE:
            if key not in values:known[key]=False
            else:usage[key]+=M7.count(values[key])
    accepted=proof.get("acceptedSessionId")
    sessions=[row for row in M7.array(runtime.get("runtime",{}).get("sessions",[]),256)
        if row.get("session_id")==accepted]
    binding=len(sessions)==1 and sessions[0].get("game_id")=="agents-arena" and sessions[0].get("game_version")=="0.9.0"
    major=sessions[0].get("protocol_version",{}).get("major") if len(sessions)==1 else None
    binding&=type(major)is int and major==1
    checks={"terminal_identity":M7.terminal_identity(runtime,client,proof,summary),
        "ordinary_game_binding":bool(binding),"official_closed_model":bool(valid and turns>0 and tools==0),
        "cleanup_errors_absent":cognition.get("cleanup_errors")==[]}
    return checks,{"codex_turns":turns if episodes else None,"model_tool_calls":tools if valid else None,
        "reported_usage":{key:usage[key] if known[key] else None for key in USAGE}}


def replay_lives(players,rows):
    """Join public postgame lives to the actual irreversible replay events."""
    roles={row.get("actor_id"):row.get("alignment") for row in players}
    lives={actor:"SOCIAL_LIFE_ALIVE" for actor in roles}
    valid=True;meeting=False;ballots={};ejected=False
    for row in rows:
        kind,actor,target=row.get("kind"),row.get("actor_id"),row.get("target_id")
        if kind in {"kill","meeting","vote","sabotage","repair","task","disconnect","ejection"}:
            valid&=actor in lives and lives.get(actor)=="SOCIAL_LIFE_ALIVE"
        if kind=="kill":
            valid&=(roles.get(actor)=="SOCIAL_ALIGNMENT_IMPOSTOR" and target in lives and target!=actor
                and lives.get(target)=="SOCIAL_LIFE_ALIVE" and row.get("outcome")=="dead")
            if target in lives:lives[target]="SOCIAL_LIFE_DEAD"
        elif kind=="ejection":
            valid&=row.get("outcome")=="ejected"
            tally=Counter(ballots.values());skips=tally.pop("",0)
            targets={target:amount for target,amount in tally.items() if lives.get(target)=="SOCIAL_LIFE_ALIVE"}
            valid&=(meeting and not ejected and targets.get(actor,0)>skips
                and all(target==actor or amount<targets.get(actor,0) for target,amount in targets.items()))
            ejected=True
            if actor in lives:lives[actor]="SOCIAL_LIFE_EJECTED"
        elif kind=="disconnect":
            if actor in lives:lives[actor]="SOCIAL_LIFE_DISCONNECTED"
        elif kind=="vote":
            valid&=(row.get("outcome")=="skip" and not target or row.get("outcome")=="cast"
                and target in lives and lives.get(target)=="SOCIAL_LIFE_ALIVE")
            valid&=meeting and actor not in ballots
            ballots[actor]=target or ""
        elif kind=="meeting":
            meeting=True;ballots={};ejected=False
        elif kind=="sabotage":valid&=roles.get(actor)=="SOCIAL_ALIGNMENT_IMPOSTOR"
        elif kind=="task":valid&=roles.get(actor)=="SOCIAL_ALIGNMENT_CREW"
    valid&=all(lives.get(row.get("actor_id"))==row.get("life") for row in players)
    return bool(valid),Counter(roles[actor] for actor,life in lives.items() if life=="SOCIAL_LIFE_ALIVE")


def evaluate(folder,*,require_human_report=False,require_human_vote=False):
    try:
        return _evaluate(Path(folder).resolve(),require_human_report,require_human_vote)
    except (OSError,ValueError,TypeError,KeyError,AttributeError,struct.error):
        return {"schema":SCHEMA,"category":"human_mixed","passed":False,"status":"failed",
            "checks":{"input_reports_valid":False},"error_code":"invalid_or_missing_evidence"}


def _evaluate(folder,require_report,require_vote):
    supervisor=optional_read(folder/"mixed-supervisor.json")
    recorded=supervisor.get("status");recorded=recorded if recorded in STATUSES else "unknown"
    checks={"supervisor_completed":supervisor.get("schema")==SUPERVISOR_SCHEMA and recorded=="authoritative_match_completed"
        and supervisor.get("codex_participants")==3 and type(supervisor.get("codex_participants"))is int
        and supervisor.get("completed_codex_reports")==3 and type(supervisor.get("completed_codex_reports"))is int,
        "owned_model_cleanup_declared":supervisor.get("owned_codex_cleanup") is True,
        "isolation_declared":all(supervisor.get("process_isolation",{}).get(key)is True for key in M7.ISOLATION)}
    names=[f"agent-{index:02}" for index in range(1,4)]
    checks["exactly_three_model_participants"]={p.name for p in folder.glob("agent-*") if p.is_dir()}==set(names)
    artifacts=[tuple(optional_read(folder/name/file) for file in
        ("runtime.json","client.json","launch.json","client-social-completed.json")) for name in names]
    checks["all_model_artifacts_present"]=all(all(row) for row in artifacts)
    summaries=[];actors=[];sessions=[];metrics=[];launches=[];revisions=[]
    for index,(runtime,client,launch,proof) in enumerate(artifacts,1):
        summary=(runtime.get("match",{}).get("result") or {}).get("outcome",{}).get("authoritative_summary",{})
        summaries.append(summary);actors.append(proof.get("actorId"));sessions.append(proof.get("acceptedSessionId"));launches.append(launch)
        revisions.append(proof.get("socialPhaseRevision"))
        model_checks,metric=model_proof(runtime,client,launch,proof,summary)
        checks.update({f"model_{index}_"+key:value for key,value in model_checks.items()})
        metrics.append(metric)
    pids=[launch.get(key) for launch in launches for key in ("launcher_pid","runtime_pid","client_pid")]
    ports=[launch.get("bridge_port") for launch in launches]
    checks["independent_launches"]=(all(positive(value) for value in pids) and len(set(pids))==9
        and all(type(value)is int and 1024<=value<=65535 for value in ports) and len(set(ports))==3
        and all(launch.get("operation")=="match" and type(launch.get("headless_client"))is bool for launch in launches))
    for key in ("memory_database","agent_profile","bridge_credential_sha256"):
        values=[launch.get(key) for launch in launches]
        valid=all(isinstance(value,str) and 1<=len(value)<=2048 for value in values)
        if key=="bridge_credential_sha256":valid&=all(isinstance(value,str) and len(value)==64 and all(c in "0123456789abcdef" for c in value) for value in values)
        checks["independent_"+key]=valid and len(set(values))==3
    checks["recorded_process_identities"]=M7.process_proof(folder,names,launches)
    after=optional_read(folder/"processes-after-cleanup.json");processes=M7.array(after.get("agents",[]),6)
    if "remaining_owned_process_groups"in after:
        group_zero=(type(after["remaining_owned_process_groups"])is int and after["remaining_owned_process_groups"]==0
            and all(row.get("process_group_alive_after_cleanup",False)is False for row in processes))
    else:
        group_zero=len(processes)==3 and all(row.get("process_group_alive_after_cleanup")is False for row in processes)
    checks["three_model_process_groups_cleaned"]=bool(group_zero and len(processes)==3 and all(
        type(row.get("launcher_exit_code"))is int for row in processes))
    summary=summaries[0];players=M7.array(summary.get("players",[]),6)
    checks["identical_authoritative_summaries"]=bool(summary) and all(value==summary for value in summaries)
    human=optional_read(folder/"human-proof.json");operator=optional_read(folder/"native-operator.json")
    human_actor=human.get("actorId");match=summary.get("match_id")
    checks["distinct_three_runtime_sessions"]=all(M7.token(v) for v in actors+sessions) and len(set(actors))==len(set(sessions))==3
    player_ids=[row.get("actor_id") for row in players]
    checks["exact_four_player_composition"]=(summary.get("mode")=="SocialDeduction" and M7.token(match)
        and len(players)==4 and all(M7.token(v) for v in player_ids) and len(set(player_ids))==4
        and all(row.get("alignment")in M7.SIDES-{"SOCIAL_ALIGNMENT_UNSPECIFIED"} and row.get("life")in M7.LIVES for row in players)
        and sum(row.get("alignment")=="SOCIAL_ALIGNMENT_IMPOSTOR" for row in players)==1
        and M7.token(human_actor) and human_actor not in actors and set(player_ids)==set(actors+[human_actor]))
    winner=M7.side(summary.get("winning_side"));reason=summary.get("win_reason")
    checks["known_gameplay_terminal_outcome"]=reason in M7.WINNERS and reason!="waiting_timeout" and winner==M7.WINNERS.get(reason)
    checks["frozen_human_terminal_identity"]=(human.get("schema")==HUMAN_SCHEMA and human.get("mode")=="Human"
        and human.get("headless")is False and human.get("gameMode")=="SocialDeduction" and human.get("socialMatchId")==match
        and human.get("socialPhase")=="GameOver" and human.get("socialSummaryPresent")is True
        and positive(human.get("socialPhaseRevision")) and all(v==human["socialPhaseRevision"] for v in revisions)
        and M7.side(human.get("socialWinningSide"))==winner and human.get("socialWinReason")==reason
        and human.get("captured_before_leave")is True and positive(human.get("captured_at_unix_ms"))
        and positive(human.get("client_pid")) and human["client_pid"]not in pids)
    rows=M7.array(summary.get("replay",[]),256);seq=[M7.count(row.get("sequence",0)) for row in rows]
    ended=[index for index,row in enumerate(rows) if row.get("kind")=="match_ended"]
    checks["complete_ordered_terminal_replay"]=(bool(rows) and summary.get("replay_truncated",False)is False
        and all(row.get("kind")in M7.REPLAY_KINDS for row in rows)
        and all(a<b for a,b in zip([0]+seq,seq)) and len(ended)==1
        and rows[ended[0]].get("outcome")==reason
        and all(row.get("kind")=="phase" and row.get("outcome")=="GameOver" for row in rows[ended[0]+1:])
        and rows[0].get("kind")=="match_started" and sum(row.get("kind")=="match_started" for row in rows)==1)
    counts=Counter(row.get("kind") for row in rows)
    counters={key:M7.count(summary.get(key,0)) for key in ("kills","body_reports","meetings","votes","crew_tasks_completed","sabotages")}
    checks["summary_gameplay_counters_match_replay"]=(all(counters[key]==counts[kind] for key,kind in
        (("kills","kill"),("meetings","meeting"),("votes","vote"),("crew_tasks_completed","task"),("sabotages","sabotage")))
        and counters["body_reports"]==sum(row.get("kind")=="meeting" and row.get("outcome")=="body_report" for row in rows))
    checks["postgame_lives_supported_by_replay"],alive=replay_lives(players,rows)
    real_gameplay=True
    if reason=="parity":real_gameplay=alive["SOCIAL_ALIGNMENT_IMPOSTOR"]==1 and alive["SOCIAL_ALIGNMENT_CREW"]<=1
    elif reason=="impostors_eliminated":real_gameplay=alive["SOCIAL_ALIGNMENT_IMPOSTOR"]==0 and alive["SOCIAL_ALIGNMENT_CREW"]>0
    elif reason=="tasks_completed":real_gameplay=counts["task"]>0
    elif reason=="sabotage_expired":real_gameplay=counts["sabotage"]>0
    elif reason=="all_players_lost":real_gameplay=sum(alive.values())==0
    checks["terminal_outcome_supported_by_gameplay"]=bool(real_gameplay)
    actions=M7.array(operator.get("actions",[]),32);screens=M7.array(operator.get("screenshots",[]),8)
    checks["native_operator_identity"]=(operator.get("schema")==OPERATOR_SCHEMA and operator.get("actor_id")==human_actor
        and operator.get("match_id")==match and operator.get("client_pid")==human.get("client_pid")
        and operator.get("observed_native_window")is True and operator.get("human_input_performed")is True
        and operator.get("terminal_visible")is True)
    checks["native_input_explicitly_declared"]=bool(actions) and all(row.get("action")in ACTIONS and row.get("performed")is True for row in actions)
    checks["native_screenshots_bound_and_intact"]=(2<=len(screens)<=8 and {row.get("stage") for row in screens}=={"native_gameplay","terminal"}
        and len({row.get("sha256") for row in screens})==len(screens)
        and all(positive(row.get("captured_at_unix_ms")) and positive(human.get("captured_at_unix_ms"))
            and row["captured_at_unix_ms"]<=human["captured_at_unix_ms"] and png_proof(folder,row) for row in screens)
        and max(row["captured_at_unix_ms"] for row in screens if row.get("stage")=="native_gameplay")
            <=min(row["captured_at_unix_ms"] for row in screens if row.get("stage")=="terminal"))
    own_reports=sum(row.get("kind")=="meeting" and row.get("outcome")=="body_report" and row.get("actor_id")==human_actor for row in rows)
    own_votes=sum(row.get("kind")=="vote" and row.get("outcome")in ("cast","skip") and row.get("actor_id")==human_actor for row in rows)
    declared={row.get("action") for row in actions}
    checks["claimed_human_report_has_server_replay"]="report_body"not in declared or own_reports>0
    checks["claimed_human_vote_has_server_replay"]="cast_vote"not in declared or own_votes>0
    if require_report:checks["required_native_human_report"]="report_body"in declared and own_reports>0
    if require_vote:checks["required_native_human_vote"]="cast_vote"in declared and own_votes>0
    passed=all(checks.values())
    return {"schema":SCHEMA,"category":"human_mixed","passed":passed,
        "status":"accepted" if passed else "incomplete" if recorded in {"running","starting","incomplete"} else "failed",
        "recorded_supervisor_status":recorded,"checks":checks,
        "winner":winner if reason in M7.WINNERS else None,"reason":reason if reason in M7.WINNERS else "unknown",
        "duration_ms":M7.count(summary.get("duration_ms",0)),"gameplay_counts":counters,
        "model_participants":metrics,"human_server_actions":{"body_reports":own_reports,"votes":own_votes},
        "native_screenshot_count":len(screens),"human_exit_evaluated":False,
        "scope":"Three isolated official Codex terminal reports, four-player authoritative summary and frozen Human client/native operator consistency; screenshots and input declarations are not signed attestation",
        "privacy":"Whitelisted aggregate counts and booleans only; no identities, paths, PIDs, chat, arguments or model context"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,required=True);parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--require-human-report",action="store_true");parser.add_argument("--require-human-vote",action="store_true")
    args=parser.parse_args();value=evaluate(args.input,require_human_report=args.require_human_report,require_human_vote=args.require_human_vote)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"passed":value["passed"],"status":value["status"],"checks":value["checks"]}))
    return 0 if value["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
