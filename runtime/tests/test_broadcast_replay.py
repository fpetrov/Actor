"""Ten-minute deterministic local replay; not a real provider/audio/game test."""
import json

from actor_broadcast.contracts import Scope
from test_broadcast_commentary import Clock,Provider,fact,view,system,pump


async def replay():
    clock=Clock();scheduler,provider,speech=system(clock)
    old_claim=fact(clock,"old-public-claim",age=120,kind="player_claim",unresolved=True,lifetime=900)
    first=fact(clock,"arrival",priority=1,lifetime=900)
    scheduler.observe(view(clock,facts=[old_claim,first]));scheduler.ready()
    acknowledgements=set();due={};critical_in_recap=False;events=2;transforms=0;sequence=1
    disconnected=False;generation=1
    for tick in range(1,2401):
        clock.advance(.25)
        if 300<=clock.now<310:
            disconnected=True
            await pump(scheduler)
            continue
        if disconnected:
            generation=2;disconnected=False
        phase="Discussion" if 180<=clock.now<240 else "Exploration"
        terminal=clock.now>=590
        if terminal:phase="GameOver"
        additions=[]
        if clock.now in (50,110,150,260,340,400,480):
            additions.append(fact(clock,"routine-"+str(tick),priority=3))
        if clock.now==195:
            additions.extend([fact(clock,"claim-a",priority=3,kind="player_claim",unresolved=True),
                fact(clock,"claim-b",priority=1,kind="player_claim",unresolved=True)])
        if clock.now==590:additions.append(fact(clock,"public-result",priority=0))
        request=scheduler._active_request
        if (not critical_in_recap and request and request.candidate.mode in ("idle_recap","memory_callback") and scheduler._speech_pending):
            additions.append(fact(clock,"permitted-critical",priority=0));critical_in_recap=True
        # Ten coherent transform-only frames per quarter-second; no fabricated
        # semantic event/revision or player hidden state accompanies them.
        for _ in range(10):
            sequence+=1;transforms+=1
            scheduler.observe(view(clock,sequence=sequence,facts=additions,
                phase=phase,terminal=terminal,scope=Scope("broadcast","match",generation,1),
                current={"visible_actor_ids":["visible"],"room_id":"hall","permitted_x":tick/1000}))
            additions=[]
        events=scheduler.report()["counts"].get("new_facts",events)
        await pump(scheduler)
        for index,segment in enumerate(speech.segments):
            key=(segment.scope,segment.key)
            if key not in due:due[key]=clock.now+2
            if key not in acknowledgements and clock.now>=due[key]:
                speech.acknowledge(scheduler,clock,index=index)
                acknowledgements.add(key)
    report=scheduler.report();budget=await scheduler.budget.report()
    result={"schema":"actor.broadcast.synthetic-replay.v1","duration_seconds":600,
        "scope":"fake_clock_provider_and_audio_sink_not_live_acceptance",
        "events":events,"transform_frames":transforms,"semantic_clusters":report["counts"].get("new_facts",0),
        "calls_by_mode":report["calls_by_mode"],"calls_total":len(provider.requests),
        "suppressed":report["suppressed"],"usage":{"source":"synthetic_provider_counters",**budget},
        "speech":report["spoken"],"preemptions":report["counts"].get("preemptions",0),
        "critical_during_recap":critical_in_recap,"maximum_provider_concurrency":provider.max_active,
        "history":report["evidence"],"context_bytes_max":report["context_bytes_max"],
        "estimated_client_tokens_max":(report["context_bytes_max"]+3)//4,
        "limits":{"hourly_calls":scheduler.config.hard_calls_per_hour,"match_calls":scheduler.config.hard_calls_per_match}}
    await scheduler.stop()
    result["cleanup"]={"provider_closed":provider.closed,"pending_segments":scheduler.report()["pending_segments"]}
    return result


async def test_ten_minute_event_recaps_callbacks_priority_reconnect_terminal_replay(tmp_path):
    report=await replay()
    assert report["duration_seconds"]==600 and report["transform_frames"]>20000
    assert 0<report["calls_total"]<=60 and report["calls_total"]<report["transform_frames"]//1000
    assert report["calls_by_mode"].get("memory_callback",0)>0
    assert report["calls_by_mode"].get("idle_recap",0)>0
    assert report["calls_by_mode"].get("outro",0)==1
    assert report["critical_during_recap"] and report["maximum_provider_concurrency"]==1
    assert report["speech"]["interrupted"]>0 and report["speech"]["completed"]>0
    assert report["cleanup"]=={"provider_closed":True,"pending_segments":0}
    (tmp_path/"replay.json").write_text(json.dumps(report,indent=2))
