#!/usr/bin/env python3
"""Offline M8 attempt registry and strict public projection.

Re-runs the M7 artifact evaluator; never launches a process or reads a live role.
Only dedicated immediate attempt directories are inventoried, never private trees.
This verifies local artifact consistency, not signed provenance against a writer.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

SPEC=importlib.util.spec_from_file_location("actor_m7_evaluator",Path(__file__).resolve().parents[1]/"social-validation/evaluate.py")
M7=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M7)
SCHEMA="actor.release-series.v1"
INPUT_SCHEMA="actor.release-series-input.v1"
CATEGORIES={"codex_series","controlled_codex","human_mixed","scripted","technical"}
STAGES={"none","setup","runtime","model","acceptance","user_interruption"}
SLUG=re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
USAGE=("input_tokens","cached_input_tokens","output_tokens","total_tokens")
PHASES={"WaitingForPlayers","RoleAssignment","Exploration","Discussion","Voting","VoteResolution","GameOver"}
POST_START=PHASES-{"WaitingForPlayers"}
NEW_SKILLS={"agents-arena.discuss","agents-arena.await-discussion-update","agents-arena.follow-visible-actor","actor.remember-contradiction"}
SKILLS=M7.SKILLS|NEW_SKILLS
REVIEW_GRADES={"observed","not_observed","mixed","ineffective"}
REVIEW_KINDS={"false_alibi","false_accusation","fake_work_context","counter_argument","evasion_context"}


def read(path,limit=32*1024*1024):
    if not path.is_file() or path.stat().st_size>limit:raise ValueError("invalid_report")
    value=json.loads(path.read_text())
    if not isinstance(value,dict):raise ValueError("invalid_report")
    return value


def rows(value,limit):
    return M7.array(value,limit)


def number(value,maximum=14400):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=maximum:raise ValueError("invalid_number")
    return value


def contained(root,value):
    if not isinstance(value,str) or not 1<=len(value)<=2048:raise ValueError("invalid_relative_path")
    relative=Path(value)
    if relative.is_absolute():raise ValueError("invalid_relative_path")
    result=(root/relative).resolve()
    if not result.is_relative_to(root):raise ValueError("invalid_relative_path")
    return result


def metric(values):
    return {"samples":len(values),"sum":sum(values),"mean":sum(values)/len(values) if values else None,
        "p95":sorted(values)[max(0,math.ceil(len(values)*.95)-1)] if values else None,"max":max(values) if values else None}


def match_result(runtime):
    result=runtime.get("match",{}).get("result")
    if result is None:return {}
    if not isinstance(result,dict):raise ValueError("invalid_match_result")
    return result


def aggregate_provider_metrics(runtime,launch):
    """Future closed-report telemetry; never reconstruct historical episodes."""
    cognitive=runtime.get("cognitive",{});value=cognitive.get("provider_telemetry")
    if value is None:return None
    if not isinstance(value,dict):raise ValueError("invalid_provider_telemetry")
    def optional_count(key):
        result=value.get(key)
        if result is None:return None
        if type(result)is not int:raise ValueError("invalid_provider_counter")
        return M7.count(result)
    size=optional_count("closed_episode_reports")
    if size is None or size>256 or type(cognitive.get("episode_count"))is not int or size!=cognitive["episode_count"]:
        raise ValueError("incomplete_provider_coverage")
    versions=value.get("sdk_versions")
    if not isinstance(versions,list) or len(versions)>8 or any(not isinstance(v,str) or not re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,6}){1,3}",v) for v in versions):
        raise ValueError("invalid_provider_version")
    for key in ("sdk_versions_complete","tool_count_complete"):
        if type(value.get(key))is not bool:raise ValueError("invalid_provider_completeness")
    counts={key:optional_count(key) for key in ("unique_model_turns","tool_calls","read_only_episodes","ephemeral_episodes","closed_providers","active_decisions")}
    known_sdk=(launch.get("brain") in M7.CODEX_PROVIDERS and size>0 and value["sdk_versions_complete"] and versions==["0.162.1"])
    official=(known_sdk and counts["unique_model_turns"] is not None and counts["unique_model_turns"]>0
        and counts["tool_calls"]==0 and value["tool_count_complete"] and counts["active_decisions"]==0
        and cognitive.get("active_episode") is False and all(counts[k]==size for k in ("read_only_episodes","ephemeral_episodes","closed_providers")))
    source=value.get("usage_totals",{})
    if not isinstance(source,dict):raise ValueError("invalid_provider_usage")
    usage={}
    if known_sdk:
        for key in USAGE:
            amount=source.get(key)
            if amount is not None:
                if type(amount)is not int:raise ValueError("invalid_provider_usage")
                usage[key]=M7.count(amount)
        if all(k in usage for k in ("input_tokens","output_tokens","total_tokens")) and usage["total_tokens"]!=usage["input_tokens"]+usage["output_tokens"]:
            raise ValueError("inconsistent_provider_usage")
        if "cached_input_tokens" in usage and "input_tokens" in usage and usage["cached_input_tokens"]>usage["input_tokens"]:
            raise ValueError("inconsistent_provider_usage")
    return {"official":bool(official),"turns":counts["unique_model_turns"] if known_sdk else None,
        "tools":counts["tool_calls"] if known_sdk and value["tool_count_complete"] else None,"usage":usage}


def episode_metrics(runtime,launch):
    """No decisions' arguments, text, trigger IDs, context bodies or reasoning."""
    episodes=rows(runtime.get("cognitive",{}).get("episodes",[]),256)
    usage=Counter();available=set();turns=tools=0;official=bool(episodes);seen_official=False;cost=[];decision_times=[];contexts=[]
    skill_status=Counter();episode_status=Counter();selected=[];model_turn_failures=0;masked_results=0
    for e in episodes:
        brain=e.get("brain",{});adapter=brain.get("adapter") or {}
        is_codex=(launch.get("brain") in M7.CODEX_PROVIDERS and brain.get("provider")=="codex"
            and adapter.get("sdk_version")=="0.162.1" and adapter.get("sandbox")=="read-only" and adapter.get("ephemeral") is True)
        official &= is_codex
        if is_codex:turns+=M7.count(adapter.get("unique_model_turns",0));seen_official=True
        source=brain.get("usage_totals",{}) if is_codex else {}
        if not isinstance(source,dict):raise ValueError("invalid_usage")
        for key in USAGE:
            if key in source:usage[key]+=M7.count(source[key]);available.add(key)
        if all(k in source for k in ("input_tokens","output_tokens","total_tokens")) and M7.count(source["total_tokens"])!=M7.count(source["input_tokens"])+M7.count(source["output_tokens"]):raise ValueError("inconsistent_usage")
        if "cached_input_tokens" in source and "input_tokens" in source and M7.count(source["cached_input_tokens"])>M7.count(source["input_tokens"]):raise ValueError("inconsistent_usage")
        if "cost_usd" in source:cost.append(number(source["cost_usd"],1000000))
        for turn in rows(brain.get("turns",[]),4096):tools+=M7.count(turn.get("tool_calls",0))
        statuses=adapter.get("completed_turn_statuses",{})
        if isinstance(statuses,dict):model_turn_failures+=M7.count(statuses.get("failed",0))
        decisions=rows(e.get("decisions",[]),4096)
        for d in decisions:
            if "duration_seconds" in d:decision_times.append(number(d["duration_seconds"]))
            if "context_bytes" in d:contexts.append(M7.count(d["context_bytes"]))
        results=rows(e.get("result",{}).get("skill_results",[]),4096)
        for r in results:
            skill_status[r.get("status") if r.get("status") in ("succeeded","failed","cancelled") else "unknown"]+=1
            masked_results+=r.get("skill_id")=="[private_activity]"
        status=e.get("result",{}).get("status");episode_status[status if status in ("succeeded","failed","cancelled") else "unknown"]+=1
        calls=[d for d in decisions if d.get("status")=="accepted" and isinstance(d.get("skill_id"),str)]
        if len(calls)==len(results):
            for d,r in zip(calls,results):
                if d.get("skill_id")==r.get("skill_id") and d["skill_id"] in SKILLS:selected.append((d["skill_id"],r))
    aggregate=aggregate_provider_metrics(runtime,launch) if not episodes else None
    if aggregate is not None:
        usage=Counter(aggregate["usage"]);available=set(usage)
    return {"official_codex_started":aggregate["official"] if aggregate is not None else bool(official and turns>0 and tools==0),
        "codex_turns":aggregate["turns"] if aggregate is not None else turns if seen_official else None,
        "model_tool_calls":aggregate["tools"] if aggregate is not None else tools if seen_official else None,
        "provider_metadata_source":"closed_episode_scalar_aggregate" if aggregate is not None else "safe_postgame_episodes" if episodes else "unavailable",
        "reported_participation_measured_tokens":M7.count(runtime.get("cognitive",{}).get("measured_tokens",match_result(runtime).get("measured_tokens",0))) if ("measured_tokens" in runtime.get("cognitive",{}) or "measured_tokens" in match_result(runtime)) else None,
        "reported_usage":{key:usage[key] if key in available else None for key in USAGE},
        "usage_reporting":"complete" if all(key in available for key in ("input_tokens","output_tokens","total_tokens")) else "partial" if available else "unavailable",
        "provider_reported_cost_usd":sum(cost) if cost else None,"provider_cost_records":len(cost),
        "model_wait_seconds":metric(decision_times),"context_bytes":metric(contexts),"skill_status_counts":dict(skill_status),
        "episode_status_counts":dict(episode_status),"failed_model_turns":model_turn_failures if episodes else None,
        "masked_skill_results":masked_results,"_selected":selected}


def independent(folder,names,launches,clients,runtimes,proofs):
    checks={};pids=[l.get(k) for l in launches for k in ("launcher_pid","runtime_pid","client_pid")];ports=[l.get("bridge_port") for l in launches]
    checks["independent_pid_port_metadata"]=(all(type(p)is int and p>1 for p in pids) and len(set(pids))==len(pids)
        and all(type(p)is int and 1024<=p<=65535 for p in ports) and len(set(ports))==len(ports))
    checks["match_operation"]=all(l.get("operation")=="match" for l in launches)
    for key in ("memory_database","agent_profile","bridge_credential_sha256"):
        values=[l.get(key) for l in launches]
        checks["distinct_"+key]=all(isinstance(v,str) and 0<len(v)<=2048 and (key!="bridge_credential_sha256" or re.fullmatch(r"[a-f0-9]{64}",v)) for v in values) and len(set(values))==len(values)
    snapshot=read(folder/"processes.json");processes=rows(snapshot.get("agents",[]),6)
    checks["recorded_process_identities"]=(snapshot.get("schema")=="actor.social-processes.v1" and len(processes)==len(names) and {r.get("agent") for r in processes}==set(names))
    for name,launch in zip(names,launches):
        found=[r for r in processes if r.get("agent")==name]
        checks["recorded_process_identities"] &= len(found)==1 and found[0].get("launcher_identity_matches") is True and all(found[0].get(k)==launch.get(k) for k in ("launcher_pid","runtime_pid","client_pid","bridge_port"))
    supervisor=read(folder/"supervisor.json")
    checks["declared_isolation"]=all(supervisor.get("process_isolation",{}).get(k) is True for k in M7.ISOLATION)
    actors=[];sessions=[];matches=[];binding=True;versions=True
    for client,runtime,proof in zip(clients,runtimes,proofs):
        actor=client.get("actorId");session=client.get("lastAcceptedSessionId") or client.get("sessionId")
        scope=client.get("socialMatchId") or match_result(runtime).get("scope_id") or proof.get("socialMatchId")
        selected=[s for s in rows(runtime.get("runtime",{}).get("sessions",[]),256) if s.get("session_id")==session]
        binding &= all(M7.token(v) for v in (actor,session,scope)) and len(selected)==1 and selected[0].get("actor_id")==actor
        versions &= len(selected)==1 and selected[0].get("game_id")=="agents-arena" and selected[0].get("game_version")=="0.9.0" and selected[0].get("protocol_version")=={"major":1,"minor":0}
        actors.append(actor);sessions.append(session);matches.append(scope)
    checks["m8_game_contract"]=bool(versions)
    checks["bound_actor_session_match"]=bool(binding and len(set(actors))==len(names) and len(set(sessions))==len(names) and len(set(matches))==1)
    return checks,matches[0] if checks["bound_actor_session_match"] else None


def terminal_preflight(runtimes,proofs):
    # Read no postgame roles/replay until every ordinary client+Runtime says
    # terminal. The existing evaluator then validates their exact identities.
    return all(match_result(r).get("status")=="completed" and
        match_result(r).get("authoritative_terminal") is True and
        p.get("socialPhase")=="GameOver" and p.get("socialSummaryPresent") is True for r,p in zip(runtimes,proofs))


def cycle(evaluation,runtimes,metrics,clients):
    summary=runtimes[0]["match"]["result"]["outcome"]["authoritative_summary"]
    replay=rows(summary.get("replay",[]),256)
    actors=[c["actorId"] for c in clients]
    codex={actor for actor,p in zip(actors,evaluation["participants"]) if p["brain"] in M7.CODEX_PROVIDERS and p["codex_turns"]>0}
    selected={actor:{skill for skill,r in m["_selected"] if r.get("status") in ("succeeded","cancelled")} for actor,m in zip(actors,metrics)}
    kills=[r for r in replay if r.get("kind")=="kill" and r.get("actor_id") in codex and "agents-arena.kill-visible-actor" in selected[r["actor_id"]]]
    reports=[r for r in replay if r.get("kind")=="meeting" and r.get("outcome")=="body_report" and r.get("actor_id") in codex and "agents-arena.report-body" in selected[r["actor_id"]]]
    ballots=[r for r in replay if r.get("kind")=="vote" and r.get("actor_id") in codex and "agents-arena.cast-vote" in selected[r["actor_id"]]]
    speakers=set()
    for actor,m,client in zip(actors,metrics,clients):
        if actor not in codex:continue
        for skill,r in m["_selected"]:
            data=r.get("output",{})
            explicit=(isinstance(data,dict) and data.get("accepted_by_server") is True and
                data.get("receipt_stage") in ("server_accepted","client_received") and
                data.get("match_id")==summary["match_id"] and M7.token(data.get("meeting_id")))
            redacted_contract=(data=={"redacted":True} and r.get("data_policy")=="session_only" and r.get("skill_version")=="1.0.0")
            if (skill=="agents-arena.discuss" and r.get("status")=="succeeded" and (explicit or redacted_contract)
                and M7.count(client.get("receiptsServerAcceptedCount",0))+M7.count(client.get("receiptsClientReceivedCount",0))>0):speakers.add(actor)
    checks={"official_codex_on_both_sides":evaluation.get("real_codex_on_both_sides") is True,
        "server_accepted_codex_kill":bool(kills and M7.count(summary.get("kills",0))>0),
        "server_accepted_codex_body_report":bool(reports and M7.count(summary.get("body_reports",0))>0),
        "multiple_codex_discussion_senders":len(speakers)>=2,
        "actual_received_message_counters":sum(M7.count(c.get("commReceivedCount",0)) for c in clients)>=2,"server_accepted_codex_ballot":bool(ballots and M7.count(summary.get("votes",0))>0),
        "discussion_and_voting_in_replay":all(any(r.get("kind")=="phase" and r.get("outcome")==phase for r in replay) for phase in ("Discussion","Voting")),
        "complete_postgame_replay":summary.get("replay_truncated",False) is False}
    return {"passed":all(checks.values()),"checks":checks,"codex_kills":len(kills),"codex_body_reports":len(reports),"codex_ballots":len(ballots),
        "codex_discussion_senders":len(speakers),"masked_skill_results":sum(m.get("masked_skill_results",0) for m in metrics),
        "discussion_evidence_basis":"selected+succeeded typed discuss Skill requires a server acceptance receipt by its installed1.0.0 contract, corroborated by sender receipt counters and recipient message counters; redacted output stays private",
        "evidence_limit":"Previously collected episodes can permanently mask Skill IDs; no inferred channel, sender action or zero-activity claim is made for those rows. Coverage may remain unproven despite actual gameplay.",
        "scope":"postgame actor-bound selected Skills plus accepted server replay; no claim about internal motivation"}


def deception(root,value,attempt_id,match,evaluation,clients):
    result={"grade":"unassessed","evidence_records":0,"source":"no_sanitized_manual_review"}
    if value is None:return result
    review=read(contained(root,value),65536)
    if set(review)-{"schema","attempt_id","match_id","actor_id","grade","evidence"}:raise ValueError("unsafe_manual_review")
    if review.get("schema")!="actor.release-deception-review.v1" or review.get("attempt_id")!=attempt_id or review.get("match_id")!=match or review.get("grade") not in REVIEW_GRADES:raise ValueError("invalid_manual_review")
    own=[p for p,c in zip(evaluation["participants"],clients) if c.get("actorId")==review.get("actor_id")]
    if len(own)!=1 or own[0]["brain"] not in M7.CODEX_PROVIDERS or own[0]["alignment_disclosed_postgame"]!="SOCIAL_ALIGNMENT_IMPOSTOR":raise ValueError("invalid_manual_review")
    evidence=rows(review.get("evidence",[]),32)
    if not evidence or any(set(e)-{"kind","source_id","source_kind","accepted_by_server"} or e.get("kind") not in REVIEW_KINDS or not M7.token(e.get("source_id")) or e.get("source_kind") not in ("server_accepted_message","permitted_gameplay_action") or e.get("accepted_by_server") is not True for e in evidence):raise ValueError("invalid_manual_review")
    return {"grade":review["grade"],"evidence_records":len(evidence),"source":"explicit_sanitized_manual_review","evidence_kind_counts":dict(Counter(e["kind"] for e in evidence)),"limitation":"manual assessment metadata, not an automated inference of deception or intent"}


def project_attempt(root,entry,index):
    identity=entry.get("attempt_id") if isinstance(entry,dict) else None
    output={"attempt":identity if isinstance(identity,str) and SLUG.fullmatch(identity) else f"invalid-entry-{index:04}","category":entry.get("category") if isinstance(entry,dict) and entry.get("category") in CATEGORIES else "invalid","category_source":"registry_declaration",
        "outcome":"incomplete","declared_failure_stage":"none","valid_independent_codex_start":False,"observed_match_started":False,"authoritative_gameover":False,
        "full_social_cycle":{"passed":False,"checks":{}},"deception":{"grade":"unassessed","evidence_records":0,"source":"no_accepted_postgame_review"},"participants":[],"gaps":[],"_folder":None,"_inventory_folder":None,"_match":None,"_metrics":[]}
    try:
        if (not isinstance(entry,dict) or set(entry)-{"attempt_id","category","folder","inventory_folder","expected_participants","expected_codex","failure_stage","deception_review"}
            or not isinstance(identity,str) or not SLUG.fullmatch(identity) or entry.get("category") not in CATEGORIES):raise ValueError("invalid_registry_entry")
        size,models=entry.get("expected_participants"),entry.get("expected_codex")
        if type(size)is not int or not 4<=size<=6 or type(models)is not int or not 0<=models<=size or entry.get("failure_stage","none") not in STAGES:raise ValueError("invalid_registry_entry")
        if entry["category"]=="codex_series" and models!=size:raise ValueError("all_codex_required")
        output["declared_failure_stage"]=entry.get("failure_stage","none")
        if output["declared_failure_stage"]!="none":output["outcome"]="failed"
        folder=contained(root,entry.get("folder"));output["_folder"]=folder
        inventory_folder=contained(root,entry.get("inventory_folder",entry.get("folder")))
        if not folder.is_relative_to(inventory_folder):raise ValueError("invalid_inventory_folder")
        output["_inventory_folder"]=inventory_folder
        names=[f"agent-{i+1:02}" for i in range(size)]
        launches=[];clients=[];runtimes=[];proofs=[];metrics=[]
        for name in names:
            base=folder/name;participant={"participant":name,"runtime_report_present":False,"metrics_valid":False,
                "codex_turns":None,"reported_usage":{key:None for key in USAGE},"usage_reporting":"unavailable",
                "provider_reported_cost_usd":None}
            output["participants"].append(participant)
            launch=read(base/"launch.json") if (base/"launch.json").is_file() else {}
            client=read(base/"client.json") if (base/"client.json").is_file() else {}
            runtime=read(base/"runtime.json") if (base/"runtime.json").is_file() else {}
            proof=read(base/"client-social-completed.json") if (base/"client-social-completed.json").is_file() else {}
            launches.append(launch);clients.append(client);runtimes.append(runtime);proofs.append(proof)
            if not runtime:output["gaps"].append("runtime_report_missing");metrics.append({});continue
            participant["runtime_report_present"]=True
            try:
                m=episode_metrics(runtime,launch);participant.update({k:v for k,v in m.items() if not k.startswith("_")});participant["metrics_valid"]=True
                participant["delivered_messages_received"]=M7.count(client.get("commReceivedCount",0));metrics.append(m)
                output["_metrics"].append((hashlib.sha256((base/"runtime.json").read_bytes()).hexdigest(),m))
                status=match_result(runtime).get("status")
                participant["runtime_status"]=status if status in ("completed","failed","cancelled") else "unknown"
                if status in ("failed","cancelled") or (m["failed_model_turns"] is not None and m["failed_model_turns"]>0):output["outcome"]="failed"
            except (ValueError,TypeError,KeyError,AttributeError):
                metrics.append({});output["gaps"].append("runtime_metrics_invalid")
        checks,match=independent(folder,names,launches,clients,runtimes,proofs);output["independence_checks"]=checks;output["_match"]=match
        model_indices=[i for i,l in enumerate(launches) if l.get("brain") in M7.CODEX_PROVIDERS]
        checks["registered_provider_composition"]=len(model_indices)==models
        output["valid_independent_codex_start"]=bool(models>0 and len(model_indices)==models and all(checks.values()) and all(metrics[i].get("official_codex_started") is True for i in model_indices))
        output["observed_match_started"]=bool(match and all(c.get("socialPhase") in POST_START for c in clients))
        if terminal_preflight(runtimes,proofs):
            accepted=M7.evaluate(folder)
            output["terminal_checks"]={k:v for k,v in accepted.get("checks",{}).items() if type(v)is bool}
            output["terminal_checks"]["m8_game_contract"]=checks["m8_game_contract"]
            output["terminal_checks"]["registered_provider_composition"]=checks["registered_provider_composition"]
            if accepted.get("passed") is True and output["terminal_checks"] and all(output["terminal_checks"].values()):
                output["authoritative_gameover"]=True;output["observed_match_started"]=True;output["outcome"]="accepted"
                output["duration_ms"]=accepted["duration_ms"];output["winner"]=accepted["winner"];output["win_reason"]=accepted["reason"]
                output["gameplay_counts"]=accepted["gameplay_counts"]
                output["full_social_cycle"]=cycle(accepted,runtimes,metrics,clients)
                output["deception"]=deception(root,entry.get("deception_review"),identity,match,accepted,clients)
                for p,post,m in zip(output["participants"],accepted["participants"],metrics):
                    p.update({key:post[key] for key in ("alignment_disclosed_postgame","life","survival_ms","team_won","survived")})
                    p["postgame_skill_counts"]=dict(Counter(skill for skill,r in m.get("_selected",[])))
        if any(m.get("masked_skill_results",0)>0 for m in metrics):output["gaps"].append("private_skill_identity_limits_action_evidence")
        if not output["authoritative_gameover"]:output["gaps"].append("strict_authoritative_gameover_not_proven")
        if not output["full_social_cycle"]["passed"]:output["gaps"].append("full_social_cycle_not_proven")
        if entry["category"]=="human_mixed":output["gaps"].append("native_human_acceptance_requires_separate_evidence")
    except (OSError,ValueError,TypeError,KeyError,AttributeError):output["gaps"].append("invalid_or_missing_evidence")
    output["gaps"]=sorted(set(output["gaps"]));return output


def summarize(registry,root):
    root=Path(root).resolve()
    if not isinstance(registry,dict) or registry.get("schema")!=INPUT_SCHEMA or set(registry)-{"schema","attempts","inventory_roots"}:raise ValueError("invalid_registry")
    entries=registry.get("attempts",[])
    if not isinstance(entries,list) or not 1<=len(entries)<=256:raise ValueError("invalid_registry_attempts")
    attempts=[project_attempt(root,e,i+1) for i,e in enumerate(entries)]
    duplicates=set()
    for field in ("attempt","_folder","_inventory_folder","_match"):
        counts=Counter(a[field] for a in attempts if a.get(field) is not None)
        duplicates.update(i for i,a in enumerate(attempts) if counts.get(a.get(field),0)>1)
    for i in duplicates:
        a=attempts[i];a["authoritative_gameover"]=a["valid_independent_codex_start"]=False;a["full_social_cycle"]={"passed":False,"checks":{}};a["outcome"]="failed";a["gaps"].append("duplicate_attempt_or_match_scope")
    inventory_complete=False;unregistered=None
    try:
        configured=registry.get("inventory_roots",[])
        if not isinstance(configured,list) or not 1<=len(configured)<=16:raise ValueError("inventory_not_supplied")
        actual=set()
        for value in configured:
            directory=contained(root,value)
            if not directory.is_dir():raise ValueError("invalid_inventory_root")
            actual.update(p.resolve() for p in directory.iterdir() if p.is_dir())
            if len(actual)>256:raise ValueError("inventory_too_large")
        registered={a["_inventory_folder"] for a in attempts if a["_inventory_folder"] is not None}
        unregistered=len(actual-registered);inventory_complete=actual==registered and len(registered)==len(attempts)
    except (OSError,ValueError,TypeError):pass
    totals=Counter();usage=Counter();missing_usage=cost_records=0;cost=0.0;seen_reports=set();category={}
    for a in attempts:
        bucket=category.setdefault(a["category"],Counter());bucket["attempts"]+=1;bucket[a["outcome"]]+=1
        for field in ("valid_independent_codex_start","observed_match_started","authoritative_gameover"):
            bucket[field]+=a[field] is True
        bucket["full_social_cycle"]+=a["full_social_cycle"]["passed"] is True
        for digest,m in a["_metrics"]:
            if digest in seen_reports:continue
            seen_reports.add(digest);totals["codex_turns"]+=m["codex_turns"] or 0
            totals["reports_without_turn_evidence"]+=m["codex_turns"] is None
            totals["reports_without_tool_evidence"]+=m["model_tool_calls"] is None
            if m["reported_participation_measured_tokens"] is not None:totals["reported_participation_measured_tokens"]+=m["reported_participation_measured_tokens"]
            totals["model_tool_calls"]+=m["model_tool_calls"] or 0
            missing_usage+=m["usage_reporting"]!="complete"
            for key,val in m["reported_usage"].items():
                if val is not None:usage[key]+=val
            if m["provider_reported_cost_usd"] is not None:cost+=m["provider_reported_cost_usd"];cost_records+=m["provider_cost_records"]
    if totals["reports_without_turn_evidence"]==len(seen_reports):
        totals["codex_turns"]=None
    if totals["reports_without_tool_evidence"]==len(seen_reports):totals["model_tool_calls"]=None
    gates={"complete_attempt_inventory":inventory_complete,"five_unique_started_codex_matches":sum(a["category"]=="codex_series" and a["valid_independent_codex_start"] and a["observed_match_started"] for a in attempts)>=5,
        "accepted_codex_gameover":any(a["category"]=="codex_series" and a["authoritative_gameover"] and a["valid_independent_codex_start"] for a in attempts),
        "live_model_full_social_cycle":any(a["category"] in ("codex_series","controlled_codex") and a["authoritative_gameover"] and a["full_social_cycle"]["passed"] for a in attempts),
        "impostor_deception_manually_assessed":any(a["authoritative_gameover"] and a["deception"]["grade"]!="unassessed" for a in attempts)}
    public=[{k:v for k,v in a.items() if not k.startswith("_")} for a in attempts]
    return {"schema":SCHEMA,"scope":"offline artifact consistency and sanitized registered-attempt accounting; not native Human/privacy/performance acceptance or signed attestation",
        "series_evidence_complete":all(gates.values()),"gates":gates,"denominators":{"registered_attempts":len(attempts),"accepted_gameovers":sum(a["authoritative_gameover"] for a in attempts),
            "failed_attempts":sum(a["outcome"]=="failed" for a in attempts),"incomplete_attempts":sum(a["outcome"]=="incomplete" for a in attempts),"categories":{k:dict(v) for k,v in sorted(category.items())}},
        "inventory":{"complete":inventory_complete,"unregistered_immediate_attempt_directories":unregistered,"scope":"dedicated immediate attempt directories only; no recursive private-tree export"},
        "all_attempt_model_usage":{"unique_runtime_reports":len(seen_reports),**dict(totals),"reported_usage":dict(usage),"reports_without_complete_usage":missing_usage,
            "registered_participants_without_runtime_report":sum(not p["runtime_report_present"] for a in attempts for p in a["participants"]),
            "all_registered_participant_usage_known":not missing_usage and all(p["runtime_report_present"] and p.get("usage_reporting")=="complete" for a in attempts for p in a["participants"]),
            "provider_reported_cost_usd":cost if cost_records else None,"provider_cost_records":cost_records,"pricing_estimate_usd":None,"scope":"includes failed attempts; byte-identical report copies counted once; costs only when actual reports contain cost_usd, never derived from winner/tokens"},
        "attempts":public,"limitations":["Every attempted launch must have its own dedicated directory and registry row, including setup failures.",
            "Codex start means independent bound processes plus official model turns; it does not imply GameOver or Exploration.",
            "Generic send-message receipts and total received-message counts do not prove discussion channel or multiple model speakers.",
            "Failed/nonterminal reports may redact all episode/provider detail; missing turns, usage and costs remain unknown, with safe measured_tokens retained separately when present.",
            "Known usage is a subtotal of available reports; missing setup/runtime reports do not establish zero usage. Earlier private episode Skill IDs may remain permanently masked after GameOver.",
            "A task win proves no deception. Intent and deception stay unassessed without explicit sanitized manual postgame review.",
            "Native Human mixed play, visibility/privacy, rendered FPS, faults and deployment/CI require separate acceptance evidence."]}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--registry",type=Path,required=True);parser.add_argument("--root",type=Path,required=True);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    try:value=summarize(read(args.registry,1024*1024),args.root)
    except (OSError,ValueError,TypeError,KeyError,AttributeError):value={"schema":SCHEMA,"series_evidence_complete":False,"error_code":"invalid_registry"}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"series_evidence_complete":value["series_evidence_complete"],"gates":value.get("gates",{}),"denominators":value.get("denominators",{})}))
    return 0 if value["series_evidence_complete"] else 1
if __name__=="__main__":raise SystemExit(main())
