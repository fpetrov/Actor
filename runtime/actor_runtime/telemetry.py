"""Bounded scalar provider diagnostics, independent of game/provider imports."""
import re


def provider_telemetry(episode_reports):
    reports=episode_reports[:256]
    versions=set()
    turns=tools=readonly=ephemeral=closed=active=0
    turns_known=tools_known=versions_known=active_known=bool(reports)
    usage={key:0 for key in ("input_tokens","cached_input_tokens","output_tokens","total_tokens")}
    usage_known={key:bool(reports) for key in usage}
    def count(value):
        return type(value) is int and 0<=value<=2**53-1
    for report in reports:
        if not isinstance(report,dict):report={}
        brain=report.get("brain",{})
        if not isinstance(brain,dict):brain={}
        adapter=brain.get("adapter",{})
        if not isinstance(adapter,dict):adapter={}
        version=adapter.get("sdk_version")
        if (isinstance(version,str) and re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,6}){1,3}",version)
                and (version in versions or len(versions)<8)):
            versions.add(version)
        else:versions_known=False
        amount=adapter.get("unique_model_turns")
        if count(amount):turns+=amount
        else:turns_known=False
        readonly+=adapter.get("sandbox") in ("read-only","read_only")
        ephemeral+=adapter.get("ephemeral") is True
        closed+=adapter.get("closed") is True
        if type(adapter.get("decision_active")) is bool:active+=adapter["decision_active"]
        else:active_known=False
        records=brain.get("turns")
        if not isinstance(records,list) or len(records)>4096:
            tools_known=False
        else:
            if not count(amount) or len(records)<amount:tools_known=False
            for record in records:
                value=record.get("tool_calls") if isinstance(record,dict) else None
                if count(value):tools+=value
                else:tools_known=False
        values=brain.get("usage_totals",{})
        if not isinstance(values,dict):values={}
        for key in usage:
            if count(values.get(key)):usage[key]+=values[key]
            else:usage_known[key]=False
    turns_known=turns_known and count(turns)
    tools_known=tools_known and count(tools)
    usage_known={key:known and count(usage[key]) for key,known in usage_known.items()}
    return {"closed_episode_reports":len(reports),"sdk_versions":sorted(versions),
        "sdk_versions_complete":versions_known,
        "unique_model_turns":turns if turns_known else None,
        "tool_calls":tools if tools_known else None,"tool_count_complete":tools_known,
        "read_only_episodes":readonly,"ephemeral_episodes":ephemeral,"closed_providers":closed,
        "active_decisions":active if active_known else None,
        "usage_totals":{key:value if usage_known[key] else None for key,value in usage.items()},
        "scope":"Whitelisted aggregate self-reported provider metadata from closed episodes; no private episode content"}
