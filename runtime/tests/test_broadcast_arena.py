from dataclasses import replace

import pytest

from extensions.agents_arena.v1 import broadcast_pb2 as pb
from extensions.agents_arena.v1 import social_pb2 as social
from actor_broadcast.arena import ArenaBroadcastProjector
from actor_broadcast.contracts import PcmChunk,SegmentKey,Scope


def presented():
    result=pb.BroadcastPresentedViewObservation()
    result.view.scope.CopyFrom(pb.BroadcastScope(broadcast_session_id="session",match_id="match",observer_id="observer",connection_generation=1,view_epoch=1))
    result.view.sequence=1;result.view.observed_at_unix_ms=1000;result.view.expires_at_unix_ms=2000
    result.view.phase=social.SOCIAL_PHASE_EXPLORATION
    result.view.camera.visibility_radius=4
    result.presentation.scope.CopyFrom(result.view.scope)
    result.presentation.rendered_sequence=1;result.presentation.frame_counter=1
    result.presentation.width=800;result.presentation.height=600
    return result


def projector():return ArenaBroadcastProjector(observer_id="observer",broadcast_session_id="session",match_id="match",clock=lambda:5.0)


def test_only_presented_permitted_typed_projection_no_player_fields_or_cross_clock_comparison():
    source=presented();source.presentation.presented_at_monotonic_ms=99999999
    result=projector().project(source)
    assert result.presentation.received_at_monotonic==5.0 and result.phase=="Exploration"
    assert "own_alignment" not in str(result.current) and "replay" not in str(result.current)


def test_public_camera_roster_requires_its_own_actual_render_ack_and_exposes_only_identity():
    source=presented();source.view.camera_targets.add(actor_id="offscreen",display_name="Мира")
    with pytest.raises(ValueError):projector().project(source)
    source.presentation.camera_targets_presented=True
    result=projector().project(source)
    assert result.current["camera_targets"]==[{"actor_id":"offscreen","display_name":"Мира"}]
    assert result.current["actors"]==[] and result.current["visible_actor_ids"]==[] and result.facts==()
    source.view.ClearField("camera_targets");source.presentation.camera_targets_presented=False
    assert projector().project(source).current["camera_targets"]==[]


@pytest.mark.parametrize("mutation",["duplicate","overbound","empty_id","empty_name"])
def test_camera_roster_rejects_ambiguous_or_unbounded_identity(mutation):
    source=presented();source.presentation.camera_targets_presented=True
    source.view.camera_targets.add(actor_id="one",display_name="Мира")
    if mutation=="duplicate":source.view.camera_targets.add(actor_id="one",display_name="Иное имя")
    if mutation=="overbound":
        for i in range(6):source.view.camera_targets.add(actor_id=str(i),display_name="Имя")
    if mutation=="empty_id":source.view.camera_targets[0].actor_id=""
    if mutation=="empty_name":source.view.camera_targets[0].display_name=""
    with pytest.raises(ValueError):projector().project(source)


@pytest.mark.parametrize("mutation",[
    lambda value:setattr(value.view.scope,"observer_id","foreign"),
    lambda value:setattr(value.view.scope,"match_id","old-match"),
    lambda value:setattr(value.presentation,"rendered_sequence",2),
    lambda value:setattr(value.presentation,"width",0),
    lambda value:setattr(value.view,"winning_side",social.SOCIAL_ALIGNMENT_IMPOSTOR),
    lambda value:setattr(value.view.camera,"x",float("nan")),
])
def test_incoherent_or_unrendered_or_premature_terminal_data_fails_closed(mutation):
    source=presented();mutation(source)
    with pytest.raises(ValueError):projector().project(source)


def test_claim_identity_and_retention_are_exact_not_current_position_or_verified_truth():
    source=presented()
    source.view.facts.add(evidence_id="claim",version=2,kind=pb.BROADCAST_FACT_KIND_PLAYER_CLAIM,
        source_event_id="public-message",subject_ids=["visible"],predicate="public-claim",
        observed_at_unix_ms=100,retention_expires_at_unix_ms=3000,priority=3,text="Я был в холле.")
    source.presentation.presented_evidence.add(evidence_id="claim",version=2)
    result=projector().project(source)
    assert result.facts[0].kind=="player_claim" and result.facts[0].expires_at_unix_ms==3000
    assert result.current["actors"]==[]


@pytest.mark.parametrize("mutation",[
    lambda source:source.presentation.ClearField("presented_evidence"),
    lambda source:setattr(source.presentation.presented_evidence[0],"evidence_id","never-shown"),
    lambda source:setattr(source.presentation.presented_evidence[0],"version",1),
    lambda source:setattr(source.presentation.presented_evidence[0],"version",0),
    lambda source:source.presentation.presented_evidence.add(evidence_id="shown",version=2),
    lambda source:source.view.facts.add().CopyFrom(source.view.facts[0]),
])
def test_same_frame_ack_does_not_certify_unshown_unknown_or_duplicate_facts(mutation):
    source=presented()
    source.view.facts.add(evidence_id="shown",version=2,kind=pb.BROADCAST_FACT_KIND_OBSERVATION,
        source_event_id="activity",predicate="activity",observed_at_unix_ms=100,retention_expires_at_unix_ms=3000)
    source.presentation.presented_evidence.add(evidence_id="shown",version=2)
    assert projector().project(source).facts[0].id=="shown"
    mutation(source)
    with pytest.raises(ValueError):projector().project(source)


def test_filtered_native_fact_subset_requires_only_the_actual_presented_versions():
    source=presented()
    # Native may select fewer than the server history. No omitted fact can
    # enter Python merely because the same view sequence was rendered.
    source.view.facts.add(evidence_id="third-visible-claim",version=4,kind=pb.BROADCAST_FACT_KIND_PLAYER_CLAIM,
        source_event_id="message",predicate="claim",observed_at_unix_ms=100,retention_expires_at_unix_ms=3000)
    source.presentation.presented_evidence.add(evidence_id="third-visible-claim",version=4)
    result=projector().project(source)
    assert [fact.id for fact in result.facts]==["third-visible-claim"]
    assert result.presentation.presented_evidence==(result.facts[0].ref,)


def test_pcm_shape_requires_bounded_whole_float_frames_and_exact_rate():
    arguments=(Scope("s","m",1,1),SegmentKey("u",1,0),0,0)
    assert PcmChunk(*arguments,b"\0"*4).rate==24000
    with pytest.raises(ValueError):PcmChunk(*arguments,b"\0"*3)
    with pytest.raises(ValueError):PcmChunk(*arguments,b"\0"*4,rate=48000)
