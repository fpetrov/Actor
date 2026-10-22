import json

import pytest

from actor_broadcast.contracts import EvidenceRef
from actor_broadcast.segmentation import SpeechObjects


REF=EvidenceRef("source","1")


def document(text="Это фраза: «25,5» и имя Ёж. Всё видно."):
    return json.dumps({"segments":[{"text":text,"evidence_versions":[{"id":"source","version":"1"}]}]},ensure_ascii=False)


def test_incremental_unicode_nested_escapes_only_complete_objects_and_no_final_replay():
    parser=SpeechObjects([REF]);encoded=document('Ёж сказал: "я видел {это}". 25,5 — число.')
    emitted=[]
    for character in encoded:emitted.extend(parser.feed(character))
    assert len(emitted)==1 and emitted[0].text=='Ёж сказал: "я видел {это}". 25,5 — число.'
    assert parser.finish()==tuple(emitted)
    assert parser.feed("")==[]


@pytest.mark.parametrize("change",[
    lambda doc:doc["segments"][0].update(text="<speak>Привет</speak>"),
    lambda doc:doc["segments"][0].update(text="\x01управление"),
    lambda doc:doc["segments"][0].update(text="слово "*56),
    lambda doc:doc["segments"][0].update(evidence_versions=[{"id":"foreign","version":"1"}]),
    lambda doc:doc["segments"][0].update(evidence_versions=[{"id":"source","version":"1"}]*2),
    lambda doc:doc["segments"][0].update(reasoning="private"),
])
def test_unsafe_or_unsupported_speech_object_is_never_emitted(change):
    doc=json.loads(document());change(doc);parser=SpeechObjects([REF])
    with pytest.raises(ValueError):parser.feed(json.dumps(doc))
    assert parser.emitted==[]


def test_unclosed_json_is_not_speech_and_output_is_bounded():
    parser=SpeechObjects([REF])
    assert parser.feed('{"segments":[{"text":"Незавершённая фраза')==[]
    with pytest.raises(ValueError):parser.finish()
    with pytest.raises(ValueError):SpeechObjects([REF],max_bytes=64).feed(document())
