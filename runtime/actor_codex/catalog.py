"""Deduplicate trusted installed schemas without changing decision validation."""
import hashlib
import json


def compact_catalog(catalog):
    schemas={}
    skills=[]
    for specification in sorted(catalog,key=lambda value:value["id"]):
        skill={key:specification[key] for key in ("id","version","description","timeout_seconds") if key in specification}
        for field in ("input_schema","result_schema"):
            value=specification.get(field)
            if value is None:continue
            encoded=json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False)
            identity="schema_"+hashlib.sha256(encoded.encode()).hexdigest()[:16]
            if identity in schemas and schemas[identity]!=value:
                raise ValueError("Installed catalog schema hash collision")
            schemas[identity]=value
            skill[field+"_ref"]=identity
        skills.append(skill)
    return {"skills":skills,"schemas":schemas,
        "schema_references":"Each *_schema_ref names the complete JSON Schema in schemas. Runtime independently enforces capability ownership and availability."}
