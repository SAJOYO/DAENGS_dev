"""Rule-derived relations retain object identity, query scope and missing evidence."""

from copy import deepcopy

import pytest

from daengs_walk.diary.relational.relations.registry import collect_spatial_comparisons
from tests.walk.diary.test_scene_comparison_contracts import request_data


def pair():
    data = request_data()
    for scene in (data["earlier"], data["current"]):
        scene["facts"][0]["value"]["registered_point"] = {"lat": 37.42, "lng": 127.01}
    return data["earlier"], data["current"]


def compare(a, b, family="proximity"):
    return collect_spatial_comparisons(b, a)[family]


def test_same_object_distance_ignores_fetch_time_and_names():
    a, b = pair()
    b["facts"][0]["value"]["name"] = "변경된 공원명"
    b["facts"][0]["retrieved_at"] = "2026-09-15T02:00:00Z"
    slot = compare(a, b)
    assert slot["status"] == "confirmed"
    assert slot["items"][0]["distance_delta_m"] == -50
    assert "중간" in slot["items"][0]["scope"]
    assert compare(a, b) == slot


def test_same_name_different_ids_never_match():
    a, b = pair()
    b["facts"][0]["subject_key"] = "parks:other"
    items = compare(a, b)["items"]
    assert len(items) == 2
    assert {r["result"] for r in items} == {"only_one_snapshot_has_evidence"}
    assert all(r["distance_delta_m"] is None for r in items)


def test_changed_registration_does_not_become_walk_distance_change():
    a, b = pair()
    b["facts"][0]["value"]["registered_point"]["lat"] += 0.1
    slot = compare(a, b)
    assert slot["status"] == "insufficient_evidence"
    assert slot["items"][0]["result"] == "incomparable"
    assert slot["items"][0]["distance_delta_m"] is None


def test_missing_lookup_is_not_departure():
    a, b = pair()
    b.update(facts=[], collection={"surrounding_object": "failed"})
    item = compare(a, b)["items"][0]
    assert item["result"] == "only_one_snapshot_has_evidence"
    assert item["current_evidence_ids"] == []


def area_pair():
    a, b = pair()
    for i, scene in enumerate((a, b)):
        fact = scene["facts"][0]
        fact.update(
            family="area_context",
            subject_key=f"commerce:area:{i}",
            reference_date="202606",
            scope={"kind": "query_area", "description": "조회 원"},
            value={
                "composition": {"업종구성": "여러 업종 혼합"},
                "query": {"kind": "query_circle", "point": scene["point"], "radius_m": 1000},
                "classification_policy": {"version": "v1"},
                "registered_count": 10 + i,
            },
        )
        scene["collection"] = {"area_context": "complete"}
    return a, b


def test_equal_composition_can_have_different_area_and_count():
    a, b = area_pair()
    slot = compare(a, b, "area_context")
    item = slot["items"][0]
    assert item["result"] == "different_query_areas"
    assert item["composition_values_equal"] is True
    assert item["comparison_basis"]["changed_fields"] == ["registered_count"]
    assert slot["status"] == "confirmed"


@pytest.mark.parametrize("changed", ["month", "radius", "policy"])
def test_incomparable_statistics_keep_area_relation(changed):
    a, b = area_pair()
    fact = b["facts"][0]
    if changed == "month":
        fact["reference_date"] = "202607"
    elif changed == "radius":
        fact["value"]["query"]["radius_m"] = 500
    else:
        fact["value"]["classification_policy"] = {"version": "v2"}
    slot = compare(a, b, "area_context")
    assert slot["status"] == "insufficient_evidence"
    assert slot["items"][0]["result"] == "different_query_areas"
    assert slot["items"][0]["composition_values_equal"] is None
    assert slot["items"][0]["comparison_basis"]["changed_fields"] == []


def test_area_metadata_refresh_is_not_new_content():
    a, b = area_pair()
    b["facts"][0]["value"] = deepcopy(a["facts"][0]["value"])
    b["facts"][0]["value"]["source_hash"] = "refreshed"
    assert compare(a, b, "area_context")["status"] == "no_change"


def test_point_cover_compares_classification_not_whole_object():
    a, b = pair()
    for scene in (a, b):
        scene["collection"] = {"land_cover": "complete"}
        scene["facts"][0].update(
            family="land_cover",
            value={"classification": {"피복": "도로"}, "layer": "2025"},
            scope={"kind": "record_point", "description": "기록점 분류"},
        )
    assert compare(a, b, "background")["status"] == "no_change"
    b["facts"][0]["value"]["classification"]["피복"] = "활엽수림"
    item = compare(a, b, "background")["items"][0]
    assert (item["axis"], item["result"]) == ("record_location", "different_values")
    a["facts"][0]["reference_date"] = "2025-04-01"
    b["facts"][0]["reference_date"] = "2025-04-02"
    item = compare(a, b, "background")["items"][0]
    assert (item["axis"], item["result"]) == ("record_location", "different_values")
    assert item["comparison_basis"]["source_dates_differ"] is True
    assert item["comparison_basis"]["source_dates"] == ["2025-04-01", "2025-04-02"]
    b["facts"][0]["value"]["layer"] = "different-classification-layer"
    assert compare(a, b, "background")["items"][0]["result"] == "incomparable"


def test_initial_and_invalid_chronology():
    a, b = pair()
    assert {s["status"] for s in collect_spatial_comparisons(a).values()} == {"not_applicable"}
    with pytest.raises(ValueError, match="chronological"):
        compare(b, a)
    b["walk_id"] = "another-walk"
    with pytest.raises(ValueError, match="same walk"):
        compare(a, b)


def test_ambiguous_multiple_areas_do_not_create_cartesian_comparisons():
    a, b = area_pair()
    extra = deepcopy(a["facts"][0])
    extra["id"] = "s1:second-area"
    a["facts"].append(extra)
    slot = compare(a, b, "area_context")
    assert len(slot["items"]) == 1
    assert slot["items"][0]["result"] == "incomparable"
