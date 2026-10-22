"""Bounded incremental structured speech; JSON fragments are never spoken."""
import json
import re

from .contracts import EvidenceRef, ProviderSegment, ProviderNote


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError("Duplicate structured output key")
        result[key]=value
    return result


class SpeechObjects:
    """Recognize only complete objects inside the root `segments` array.

    The final whole document is validated separately. Closed earlier objects
    can be used before completion; strings/escapes/nested braces are respected.
    No regex extracts quoted speech from incomplete JSON.
    """
    def __init__(self, allowed_refs, *, max_bytes=16384):
        self.allowed = {(ref.id, ref.version) for ref in allowed_refs}
        self.max_bytes = max_bytes
        self.buffer = ""
        self.count = 0
        self.emitted = []

    def feed(self, delta):
        if not isinstance(delta, str): raise ValueError("Text delta required")
        self.buffer += delta
        if len(self.buffer.encode()) > self.max_bytes: raise ValueError("Speech output bound exceeded")
        # Require a single known root key before considering any object.
        match = re.match(r'\s*\{', self.buffer)
        if not match:return []
        decoder = json.JSONDecoder(object_pairs_hook=unique_object)
        position=match.end();keys=set()
        while True:
            while position<len(self.buffer) and self.buffer[position].isspace():position+=1
            try:key,end=decoder.raw_decode(self.buffer,position)
            except json.JSONDecodeError:return []
            if key not in ("segments","notes") or key in keys:raise ValueError("Unknown root speech field")
            keys.add(key);position=end
            while position<len(self.buffer) and self.buffer[position].isspace():position+=1
            if position==len(self.buffer):return []
            if self.buffer[position]!=":":raise ValueError("Invalid root speech field")
            position+=1
            while position<len(self.buffer) and self.buffer[position].isspace():position+=1
            if position==len(self.buffer):return []
            if key=="segments":
                if self.buffer[position]!="[":raise ValueError("Speech array required")
                position+=1;break
            try:_,position=decoder.raw_decode(self.buffer,position)
            except json.JSONDecodeError:return []
            while position<len(self.buffer) and self.buffer[position].isspace():position+=1
            if position==len(self.buffer):return []
            if self.buffer[position]!=",":raise ValueError("Speech segments missing")
            position+=1
        parsed = []
        while True:
            while position < len(self.buffer) and self.buffer[position].isspace(): position += 1
            if position == len(self.buffer) or self.buffer[position] == "]": break
            if self.buffer[position] != "{": raise ValueError("Expected a closed speech object")
            try: value, end = decoder.raw_decode(self.buffer, position)
            except json.JSONDecodeError: break
            parsed.append(self._validate(value, len(parsed)))
            position = end
            while position < len(self.buffer) and self.buffer[position].isspace(): position += 1
            if position == len(self.buffer) or self.buffer[position] == "]": break
            if self.buffer[position] != ",": raise ValueError("Invalid speech array")
            position += 1
        if len(parsed) > 4: raise ValueError("At most four short speech objects are allowed")
        new = parsed[self.count:]
        self.count = len(parsed)
        self.emitted.extend(new)
        return new

    def _validate(self, value, index):
        if not isinstance(value, dict) or set(value) != {"text", "evidence_versions"}:
            raise ValueError("Invalid speech object")
        refs = value["evidence_versions"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 8:
            raise ValueError("Speech must cite retained evidence")
        if any(not isinstance(ref, dict) or set(ref) != {"id", "version"} for ref in refs):
            raise ValueError("Invalid evidence reference")
        typed = tuple(EvidenceRef(**ref) for ref in refs)
        if len(set(typed)) != len(typed):raise ValueError("Repeated speech evidence")
        if any((ref.id, ref.version) not in self.allowed for ref in typed):raise ValueError("Unknown speech evidence")
        text = value["text"]
        if not isinstance(text, str) or not text.strip() or re.search(r"[<>\x00-\x08\x0b\x0c\x0e-\x1f]", text):
            raise ValueError("Speech contains unsupported markup/control characters")
        if len(text.split()) > 55: raise ValueError("Speech object is too long")
        return ProviderSegment(index, text, typed)

    def finish(self):
        value = json.loads(self.buffer,object_pairs_hook=unique_object)
        if not isinstance(value, dict) or set(value) not in ({"segments"},{"segments","notes"}) or not isinstance(value["segments"], list):
            raise ValueError("Invalid final speech document")
        if not 1 <= len(value["segments"]) <= 4: raise ValueError("Empty or overlong speech document")
        parsed = [self._validate(row, index) for index, row in enumerate(value["segments"])]
        if parsed != self.emitted: raise ValueError("Final speech differs from streamed objects")
        return tuple(parsed)

    def notes(self):
        value=json.loads(self.buffer,object_pairs_hook=unique_object).get("notes",[])
        if not isinstance(value,list) or len(value)>2:raise ValueError("At most two concise public notes are allowed")
        result=[]
        for note in value:
            if not isinstance(note,dict) or set(note)!={"kind","statement","confidence","evidence_versions"}:raise ValueError("Invalid public note")
            refs=tuple(EvidenceRef(**ref) for ref in note["evidence_versions"])
            if any((ref.id,ref.version) not in self.allowed for ref in refs):raise ValueError("Foreign note evidence")
            result.append(ProviderNote(note["kind"],note["statement"],note["confidence"],refs))
        return tuple(result)
