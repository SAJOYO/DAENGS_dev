"""Assert the actual provider boundary and invocation-local citation restoration."""

import gzip
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from daengs_backend.services.walk_diary import runtime as writing
from daengs_backend.services.walk_diary.model_input import normalize
from daengs_backend.services.walk_diary.model_materials import location, material
from daengs_backend.services.walk_diary.storage.card_receipt import StoredCardWriting
from daengs_backend.services.walk_diary.writing import policy as activity_policy
from tests.walk.diary.test_diary_card_writing import collect_with_sgis, prepared, prose


def public_result():
    path = (
        Path(__file__).resolve().parents[3] / "evals/diary_route_scenario/public-02/result.json.gz"
    )
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def test_real_public_materials_keep_meaning_and_drop_all_provenance():
    job = next(j for j in public_result()["jobs"] if j["stage"] == "space")
    before = deepcopy(job)
    model = normalize("space", job["request"])
    values = model.payload["materials"]
    assert len(values) == 5
    assert [m["id"] for m in values] == ["m1", "m2", "m3", "m4", "m5"]
    text = json.dumps(model.payload, ensure_ascii=False)
    for key in (
        "anchor",
        "source_ref",
        "source_version",
        "grid",
        "provider",
        "request_revision",
        "scene_structure",
        "rank",
        "schema_version",
        "client_seq",
        "coordinates",
    ):
        assert key not in text
    assert "24.2" in text and "격자" in text and "배경 유형" in text and "근처에 공원" in text
    for source, sent in zip(job["request"]["materials"], values, strict=True):
        if "material" in source["facts"]:
            if sent["role"] == "area_statistics":
                assert sent["material"]["업종구성"] == source["facts"]["material"]["업종구성"]
                assert "구간" not in str(sent["material"])
                assert "조회영역_등록분포" in sent["material"]
            elif (
                source["facts"].get("relation", {}).get("kind") == "registered_park_point_distance"
            ):
                assert sent["material"] == {"배경": "공원"}
            else:
                assert sent["material"] == source["facts"]["material"]
    assert job == before
    assert len(text.encode()) < len(json.dumps(job["request"], ensure_ascii=False).encode()) / 2


@pytest.mark.parametrize("legacy", [False, True])
def test_park_name_type_and_distance_stay_internal_but_citation_survives(legacy):
    facts = (
        {
            "name": "긴고유명공원",
            "park_kind": "근린공원",
            "distance_m": 42,
            "reference": "registered_park_point",
        }
        if legacy
        else {
            "material": {"공원명": "긴고유명공원", "공원종류": "근린공원"},
            "relation": {"kind": "registered_park_point_distance", "distance_m": 42},
        }
    )
    request = {
        "card_id": "card",
        "request_revision": "r",
        "materials": [
            {
                "id": "original-park-evidence",
                "role": "scene_registered_point_distance",
                "facts": facts,
            }
        ],
    }
    before = deepcopy(request)
    model = normalize("space", request)
    sent = model.payload["materials"][0]
    assert sent["material"] == {"배경": "공원"}
    assert "근처" in sent["relation"] and "방문 여부는 미확인" in sent["relation"]
    text = json.dumps(model.payload, ensure_ascii=False)
    assert all(value not in text for value in ("긴고유명", "근린공원", "42", "distance_m"))
    assert model.restore({"text": "근처에 공원이 있었다.", "evidence_ids": ["m1"]})[
        "evidence_ids"
    ] == ["original-park-evidence"]
    assert request == before


def test_future_metadata_is_not_implicitly_promoted_to_prose():
    job = next(j for j in public_result()["jobs"] if j["stage"] == "space")
    clean = normalize("space", job["request"]).payload
    dirty = deepcopy(job["request"])
    dirty["private_metadata"] = "must stay internal"
    for item in dirty["materials"]:
        item["facts"]["private_metadata"] = {"secret": "must stay internal"}
        for key in ("material", "relation"):
            if isinstance(item["facts"].get(key), dict):
                item["facts"][key]["private_metadata"] = "must stay internal"
    assert normalize("space", dirty).payload == clean


def test_space_and_title_location_send_only_dong():
    facts = {
        "dong": "양재1동",
        "sido": "서울특별시",
        "sigungu": "서초구",
        "address": "서울특별시 서초구 양재1동",
        "address_type": "administrative_dong",
    }
    assert location(facts) == {"dong": "양재1동"}
    value = material({"role": "scene_address_reference", "facts": facts})
    assert value["material"] == {"dong": "양재1동"}
    for job in public_result()["jobs"]:
        if job["stage"] == "space":
            request = normalize("space", job["request"]).payload
            assert "서울특별시" not in json.dumps(request, ensure_ascii=False)
            assert "서초구" not in json.dumps(request, ensure_ascii=False)
        elif job["stage"] == "title":
            for card in normalize("title", job["request"]).payload["cards"]:
                assert all(set(place) == {"dong"} for place in card["location"])


@pytest.mark.parametrize("dong", [None, "", "   ", 1])
def test_missing_dong_never_falls_back_to_full_address(dong):
    item = {
        "id": "address-1",
        "role": "scene_address_reference",
        "facts": {"dong": dong, "address": "서울특별시 서초구 양재1동", "sido": "서울특별시"},
    }
    assert material(item) is None
    model = normalize("space", {"materials": [item]})
    assert model.payload == {"materials": []} and not model.references


@pytest.mark.parametrize("refs", [["m999"], ["m1", "m1"], ["material:foreign"]])
def test_unknown_or_duplicate_model_citations_are_rejected(refs):
    job = next(j for j in public_result()["jobs"] if j["stage"] == "space")
    with pytest.raises(ValueError):
        normalize("space", job["request"]).restore(
            {"text": "근처에 공원이 있다.", "evidence_ids": refs}
        )


def test_short_references_are_local_to_each_invocation():
    jobs = [j for j in public_result()["jobs"] if j["stage"] == "space"][:2]
    for job in jobs:
        restored = normalize("space", job["request"]).restore(
            {"text": "주변 기록", "evidence_ids": ["m1"]}
        )
        assert restored["card_id"] == job["request"]["card_id"]
        assert restored["request_revision"] == job["request_revision"]
        assert restored["evidence_ids"] == [job["request"]["materials"][0]["id"]]


@pytest.mark.parametrize(
    ("role", "facts", "meaning"),
    [
        ("scene_registered_point_distance", {"name": "공원", "distance_m": 42}, "등록 지점"),
        ("scene_geometry_distance", {"name": "하천", "distance_m": 42}, "지도 형상"),
        (
            "scene_area_context",
            {
                "radius_m": 1000,
                "registered_count": 3,
                "categories": [{"name": "음식점", "count": 3, "code": "private"}],
            },
            "조회 원",
        ),
        (
            "regional_observation",
            {"temperature_c": 21, "precipitation_mm": 2, "area_center": {"lat": 37, "lng": 127}},
            "지역 관측",
        ),
    ],
)
def test_legacy_relations_are_kept_without_provider_objects(role, facts, meaning):
    sent = material({"role": role, "facts": facts})
    assert meaning in sent["relation"]
    assert "area_center" not in sent
    assert "private" not in json.dumps(sent)
    if "precipitation_mm" in sent:
        assert "누적 구간 미확정" in sent["precipitation_meaning"]


async def test_sdk_gets_the_recorded_normalized_request(monkeypatch):
    from google import genai
    from google.genai import types

    from daengs_backend.config import settings

    sent = []

    async def generate_content(*, model, contents, config):
        payload = json.loads(contents if isinstance(contents, str) else contents[0].parts[0].text)
        stage = (
            "title"
            if "cards" in payload
            else "action"
            if {"actor", "recorded_action"} & payload.keys()
            else "space"
        )
        sent.append(payload)
        assert not {"card_id", "request_revision", "anchor", "evidence"} & payload.keys()
        assert "request_revision" not in json.dumps(config.response_json_schema)
        text = json.dumps(await prose(stage, payload, {}))
        if stage == "space":
            return types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(role="model", parts=[types.Part(text=text)])
                    )
                ]
            )
        return SimpleNamespace(text=text)

    class Client:
        def __init__(self, **_):
            self.aio = self
            self.models = SimpleNamespace(generate_content=generate_content)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr(genai, "Client", Client)
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("unit-test-only"))
    base = prepared()
    result = await writing.write_cards(base.input.source, base, collector=collect_with_sgis)
    assert all(j.failure_code is None for j in result.jobs)
    assert sorted(map(str, sent)) == sorted(
        str(j.tool_trace["initial_input"] if j.tool_trace else j.llm_request) for j in result.jobs
    )
    for job in result.jobs:
        if job.stage == "space" and job.accepted["evidence_ids"]:
            assert all(key in job.evidence for key in job.accepted["evidence_ids"])


def test_historical_receipt_remains_readable():
    result = public_result()
    path = Path(__file__).resolve().parents[3] / "evals/diary_route_scenario/public-02/run.json"
    writer = json.loads(path.read_text(encoding="utf-8"))["writer"]
    receipt = StoredCardWriting(generation_revision="a" * 64, writer=writer, result=result)
    assert receipt.result.model_dump(mode="json") == result


async def test_model_request_receipt_is_bound_to_internal_dependencies():
    base = prepared()
    result = await writing.write_cards(
        base.input.source, base, generate=AsyncMock(side_effect=prose)
    )
    receipt = StoredCardWriting(
        generation_revision="a" * 64, writer=activity_policy.writing_version(), result=result
    )
    raw = receipt.model_dump(mode="json")
    raw["result"]["jobs"][0]["llm_request"]["extra"] = "not actually sent"
    with pytest.raises(ValueError, match="stored model input changed"):
        StoredCardWriting.model_validate(raw)


def test_final_titles_send_all_bodies_with_short_ids_and_restore_versions_locally():
    request = {
        "input_revision": "a" * 64,
        "scenes": [
            {
                "id": "stamp:long-original-id",
                "order": 1,
                "event_at": "2026-09-14T01:00:00Z",
                "body": "보리와 산책을 시작했다.",
                "boundary": "start",
            },
            {
                "id": "stamp:other-original-id",
                "order": 2,
                "event_at": "2026-09-14T01:30:00Z",
                "body": "물을 마시고 산책을 마쳤다.",
                "boundary": "end",
            },
        ],
    }
    model = normalize("scene_titles", request)
    assert "input_revision" not in model.payload
    assert [s["id"] for s in model.payload["scenes"]] == ["c1", "c2"]
    answer = model.restore(
        {"titles": [{"id": "c1", "text": "산책 시작"}, {"id": "c2", "text": "마무리"}]}
    )
    assert answer["input_revision"] == request["input_revision"]
    assert [t["scene_id"] for t in answer["titles"]] == [s["id"] for s in request["scenes"]]
    with pytest.raises(ValueError):
        model.restore({"titles": [{"id": "c1", "text": "빠진 장면"}]})
