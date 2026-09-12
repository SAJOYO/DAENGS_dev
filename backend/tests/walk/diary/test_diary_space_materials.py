"""Meaning and source-scope regressions for the spatial candidate boundary."""

import json
import math
from collections import Counter
from copy import deepcopy

import pytest
from pydantic import ValidationError

from daengs_walk.diary_space_cases import CASES
from daengs_walk.diary_space_materials import SpaceMaterial, normalize_spaces
from daengs_walk.diary_space_normalize import load_input

POINT = {"lat": 37.487, "lng": 127.052}


def moved(east=0, north=0, point=POINT):
    return {
        "lat": point["lat"] + math.degrees(north / 6371000),
        "lng": point["lng"] + math.degrees(east / (6371000 * math.cos(math.radians(point["lat"])))),
    }


def page(rows, kind="commerce", number=1, total=None):
    return {
        "header": {"resultCode": "00"},
        "body": {
            "pageNo": number,
            "totalCount": len(rows) if total is None else total,
            "items": {"item": rows} if kind == "park" else rows,
        },
    }


def area(rows, kind="commerce", point=POINT, radius=500):
    return {"query_point": point, "radius_m": radius, "pages": [page(rows, kind)]}


def shop(i, major="I2", middle="I201", *, east=0, north=0, point=POINT):
    at = moved(east, north, point)
    return {
        "bizesId": str(i),
        "lat": at["lat"],
        "lon": at["lng"],
        "indsLclsCd": major,
        "indsMclsCd": middle,
        "bizesNm": "상호는 작성 재료가 아님",
    }


def park(i, *, east=0, kind="근린공원"):
    at = moved(east)
    return {
        "manageNo": str(i),
        "parkNm": f"공원 {i}",
        "parkSe": kind,
        "latitude": str(at["lat"]),
        "longitude": str(at["lng"]),
    }


def ring(east0, north0, east1, north1):
    return [
        [p["lng"], p["lat"]]
        for p in (
            moved(east0, north0),
            moved(east1, north0),
            moved(east1, north1),
            moved(east0, north1),
            moved(east0, north0),
        )
    ]


def land(code="131", rings=None):
    return {
        "query_point": POINT,
        "layer": "EGIS:lv3_2025y",
        "response": {
            "type": "FeatureCollection",
            "crs": {"properties": {"name": "OGC:CRS84"}},
            "features": [
                {
                    "id": "lv3_2025y.test",
                    "type": "Feature",
                    "properties": {"l3_code": code, "l2_code": code[:2] + "0"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": rings or [ring(-50, -50, 50, 50)],
                    },
                }
            ],
        },
    }


def cases(result):
    return {c for m in result.materials for c in m.case_ids}


def test_same_frozen_dictionary_distinguishes_food_and_education_at_different_coordinates():
    food = [shop(i, east=10 * i) for i in range(12)]
    b = {"lat": 37.54, "lng": 127.03}
    education = [shop(i, "P1", "P105", east=10 * i, point=b) for i in range(12)]
    a = normalize_spaces({"point": POINT, "commerce": area(food)})
    result_b = normalize_spaces({"point": b, "commerce": area(education, point=b)})
    assert "commerce:food" in cases(a)
    assert "commerce:education" in cases(result_b)
    assert a.policy == result_b.policy
    assert "commerce:clustered" in cases(a) & cases(result_b)
    assert "상호는 작성 재료가 아님" not in a.model_dump_json()


def test_dispersed_registered_sites_and_one_building_have_different_patterns():
    dispersed = [shop(i, east=300 * math.cos(i), north=300 * math.sin(i)) for i in range(20)]
    one_building = [shop(i, east=200) for i in range(20)]
    spread = normalize_spaces({"point": POINT, "commerce": area(dispersed)})
    cluster = normalize_spaces({"point": POINT, "commerce": area(one_building)})
    assert "commerce:scattered" in cases(spread)
    assert "commerce:clustered" in cases(cluster)
    support = cluster.materials[0].support
    assert support["registered_count"] == 20 and support["registered_sites"] == 1
    assert support["centroid_distance_m"] == pytest.approx(200)


def test_sparse_is_not_forced_into_a_dominant_composition_and_empty_is_not_sparse():
    sparse = normalize_spaces({"point": POINT, "commerce": area([shop(1)])})
    empty = normalize_spaces({"point": POINT, "commerce": area([])})
    assert cases(sparse) == {"commerce:sparse"}
    assert not empty.materials
    assert any(a["reason"] == "no_registered_shops_in_footprint" for a in empty.audit)


def test_evenly_mixed_groups_do_not_choose_winner_by_input_order():
    rows = [shop(i) if i < 10 else shop(i, "P1", "P105") for i in range(20)]
    first = normalize_spaces({"point": POINT, "commerce": area(rows)})
    reversed_ = normalize_spaces({"point": POINT, "commerce": area(list(reversed(rows)))})
    assert "commerce:mixed" in cases(first)
    assert first == reversed_


def test_beverages_remain_in_food_total_and_only_dominate_when_their_own_share_qualifies():
    rows = [shop(i, middle="I212" if i < 4 else "I201") for i in range(8)]
    rows += [shop(i, "P1", "P105") for i in range(8, 12)]
    food = normalize_spaces({"point": POINT, "commerce": area(rows)})
    assert "commerce:food" in cases(food)
    assert food.materials[0].support["groups"]["food"] == 8
    for row in rows[:8]:
        row["indsMclsCd"] = "I212"
    beverages = normalize_spaces({"point": POINT, "commerce": area(rows)})
    assert "commerce:beverage" in cases(beverages)


@pytest.mark.parametrize("change", ["invented_case", "invented_meaning", "added_fact", "overwrite"])
def test_only_predefined_meanings_can_cross_the_material_boundary(change):
    result = normalize_spaces({"point": POINT, "commerce": area([shop(1)])})
    item = result.materials[0].model_dump()
    if change == "invented_case":
        item["case_ids"] = ["commerce:lively"]
    elif change == "invented_meaning":
        item["material"]["분포"] = "활기찬 분위기"
    elif change == "added_fact":
        item["material"]["행동"] = "주변을 둘러봄"
    else:
        item["case_ids"] += ("commerce:clustered",)
    with pytest.raises(ValidationError):
        SpaceMaterial.model_validate(item)


def test_duplicates_are_one_business_and_conflicts_do_not_make_a_composition():
    original = shop(1)
    result = normalize_spaces({"point": POINT, "commerce": area([original, deepcopy(original)])})
    assert result.materials[0].support["registered_count"] == 1
    conflict = {**original, "indsLclsCd": "P1", "indsMclsCd": "P105"}
    bad = normalize_spaces({"point": POINT, "commerce": area([original, conflict])})
    assert not bad.materials
    assert any(a.get("conflicting_ids") == ["1"] for a in bad.audit)


@pytest.mark.parametrize(
    "change", ["missing_page", "different_total", "duplicate_page", "unknown_code"]
)
def test_incomplete_or_inconsistent_commerce_does_not_become_a_region_case(change):
    rows = [shop(i) for i in range(12)]
    source = area(rows)
    if change == "missing_page":
        source["pages"][0]["body"]["totalCount"] = 24
    elif change == "different_total":
        source["pages"].append(page([shop(13)], number=2, total=13))
    elif change == "duplicate_page":
        source["pages"].append(deepcopy(source["pages"][0]))
    else:
        rows[0]["indsLclsCd"] = "ZZ"
    result = normalize_spaces({"point": POINT, "commerce": source})
    assert not result.materials
    assert any(a["source"] == "commerce" for a in result.audit)


def test_three_sources_coexist_without_capacity_or_an_action_anchor():
    result = normalize_spaces(
        {
            "point": POINT,
            "commerce": area([shop(i) for i in range(12)]),
            "park": area([park(i, east=10 * i) for i in range(20)], "park"),
            "land_cover": land(),
        }
    )
    assert count_sources(result) == {"commerce": 1, "land_cover": 1, "park": 20}
    for item in result.materials:
        assert all(c in CASES for c in item.case_ids)
        assert not any(k in item.material for k in ("行動", "행동", "무엇을", "혼잡도"))
    near = next(m for m in result.materials if m.source == "park" and m.support["source_id"] == "1")
    assert near.scope["distance_m"] == pytest.approx(10)
    assert near.material == {"공원명": "공원 1", "공원종류": "근린공원"}
    assert "공원 내부" not in result.model_dump_json()


def count_sources(result):
    return Counter(m.source for m in result.materials)


def test_partial_park_catalog_can_supply_a_known_park_without_claiming_complete_coverage():
    source = area([park(1)], "park")
    source["pages"][0]["body"]["totalCount"] = 200
    result = normalize_spaces({"point": POINT, "park": source})
    assert len(result.materials) == 1
    assert result.materials[0].support["source_complete"] is False


def test_source_dates_and_park_area_survive_for_later_admission_policy():
    commerce = area([shop(1)])
    commerce["pages"][0]["header"]["stdrYm"] = "202606"
    parks = area([{**park(1), "parkAr": "4457", "referenceDate": "2025-12-15"}], "park")
    result = normalize_spaces({"point": POINT, "commerce": commerce, "park": parks})
    assert (
        next(m for m in result.materials if m.source == "commerce").support["reference_month"]
        == "202606"
    )
    support = next(m for m in result.materials if m.source == "park").support
    assert support["area_m2"] == 4457
    assert support["reference_dates"] == ["2025-12-15"]


def test_park_reference_date_change_alone_is_not_a_location_conflict():
    rows = [{**park(1), "referenceDate": date} for date in ("2025-12-15", "2026-06-04")]
    result = normalize_spaces({"point": POINT, "park": area(rows, "park")})
    assert len(result.materials) == 1
    assert result.materials[0].support["reference_dates"] == ["2025-12-15", "2026-06-04"]
    assert result.materials[0].support["source_complete"] is True


def test_park_conflict_is_local_and_unknown_type_falls_back_to_park():
    rows = [park(1), park(1, east=20), park(2, kind="새로생긴유형")]
    result = normalize_spaces({"point": POINT, "park": area(rows, "park")})
    assert len(result.materials) == 1
    assert result.materials[0].case_ids == ("park:unspecified",)
    assert "새로생긴유형" not in result.materials[0].material.values()


def test_query_radius_is_preserved_and_outside_points_are_not_candidates():
    result = normalize_spaces({"point": POINT, "park": area([park(1, east=60)], "park", radius=50)})
    assert not result.materials
    result = normalize_spaces({"point": POINT, "commerce": area([shop(1)], radius=1000)})
    assert result.materials[0].scope["radius_m"] == 1000


@pytest.mark.parametrize(
    "rings,expected",
    [
        ([ring(-50, -50, 50, 50)], True),
        ([ring(-50, -50, 50, 50), ring(-10, -10, 10, 10)], False),
        ([ring(0, 0, 50, 50)], True),
        ([ring(50, 50, 100, 100)], False),
    ],
)
def test_land_scope_obeys_polygon_holes_boundaries_and_query_point(rings, expected):
    result = normalize_spaces({"point": POINT, "land_cover": land(rings=rings)})
    assert bool(result.materials) is expected
    if expected:
        assert result.materials[0].material == {"피복": "상업·업무시설"}
        assert result.materials[0].scope["distance_m"] == 0


@pytest.mark.parametrize("change", ["unknown_code", "bad_crs", "inconsistent_code", "open_ring"])
def test_unusable_land_does_not_remove_valid_park(change):
    source = land()
    feature = source["response"]["features"][0]
    if change == "unknown_code":
        feature["properties"]["l3_code"] = "999"
    elif change == "bad_crs":
        source["response"]["crs"]["properties"]["name"] = "EPSG:5179"
    elif change == "inconsistent_code":
        feature["properties"]["l2_code"] = "310"
    else:
        feature["geometry"]["coordinates"][0].pop()
    result = normalize_spaces(
        {"point": POINT, "land_cover": source, "park": area([park(1)], "park")}
    )
    assert count_sources(result) == {"park": 1}
    assert any(a["source"] == "land_cover" for a in result.audit)


def test_actual_mercator_response_shape_and_source_version_are_preserved():
    source = land()
    polygon = source["response"]["features"][0]["geometry"]
    polygon["type"] = "MultiPolygon"
    polygon["coordinates"] = [
        [
            [
                [
                    6378137 * math.radians(x),
                    6378137 * math.log(math.tan(math.pi / 4 + math.radians(y) / 2)),
                ]
                for x, y in polygon["coordinates"][0]
            ]
        ]
    ]
    source["response"]["crs"]["properties"]["name"] = "urn:ogc:def:crs:EPSG::3857"
    result = normalize_spaces({"point": POINT, "land_cover": source})
    assert cases(result) == {"land_cover:131"}
    assert result.materials[0].support["layer"] == "EGIS:lv3_2025y"
    polygon["coordinates"].clear()
    assert result.materials[0].scope["geometry"]["coordinates"]


def test_invalid_land_feature_does_not_discard_another_valid_feature():
    source = land()
    bad = deepcopy(source["response"]["features"][0])
    bad["id"] = "invalid-ring"
    bad["geometry"]["coordinates"][0].pop()
    source["response"]["features"].append(bad)
    result = normalize_spaces({"point": POINT, "land_cover": source})
    assert len(result.materials) == 1
    assert any(a.get("rejections") == {"invalid_land_ring": 1} for a in result.audit)


def test_sources_cannot_be_rebound_to_another_point_or_add_an_unapproved_provider():
    with pytest.raises(ValidationError, match="query differs"):
        normalize_spaces({"point": moved(100), "commerce": area([shop(1)])})
    with pytest.raises(ValidationError, match="Extra inputs"):
        normalize_spaces({"point": POINT, "river": {}})


def test_file_loader_uses_explicit_paths_and_keeps_api_payload_shape(tmp_path):
    response = page([shop(1)])
    (tmp_path / "commerce.json").write_text(json.dumps(response), encoding="utf-8")
    manifest = {
        "point": POINT,
        "commerce": {"query_point": POINT, "radius_m": 500, "pages": ["commerce.json"]},
    }
    (tmp_path / "input.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = normalize_spaces(load_input(tmp_path / "input.json"))
    assert cases(result) == {"commerce:sparse"}
