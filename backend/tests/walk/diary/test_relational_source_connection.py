"""Provider response -> full facts -> comparison/current action, without the old writer."""

import json
import time
from dataclasses import replace

import httpx
import pytest

from daengs_backend.services.walk_background.providers.sgis import SgisSource
from daengs_backend.services.walk_diary.collection import relational as acquisition
from daengs_backend.services.walk_diary.preparation.board import assemble_saved_base_board
from daengs_backend.services.walk_diary.preparation.relational import prepare_relational_diary
from daengs_backend.services.walk_diary.writing.relational import validate_prepared
from daengs_walk.diary.contracts.slots import SlotPolicy
from tests.walk.diary.test_diary_card_writing import sgis_response
from tests.walk.diary.test_diary_space_integration import public_collector  # noqa: F401
from tests.walk.support.base_board import policy, saved_case


async def test_live_adapter_to_full_snapshot_without_old_planning(monkeypatch, public_collector):  # noqa: F811
    from daengs_walk.diary.relational import planning
    from daengs_walk.diary.slots import service

    source, _, _ = saved_case()
    base = assemble_saved_base_board(source, policy(3))
    base = replace(
        base,
        slots=base.slots.model_copy(
            update={
                "policy": SlotPolicy(space_slots=0, environment_slots=0, total_slots=0),
            }
        ),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("old capacity selection/planning must not run")

    monkeypatch.setattr(service, "admit", forbidden)
    monkeypatch.setattr(planning, "collect_relations", forbidden)
    # Public source normalization still runs; only transport/catalog are supplied fixtures.
    roads = []

    def response(request):
        if request.url.params.get("addr_type") == "10":
            roads.append(request)
            return httpx.Response(
                200,
                json={
                    "errCd": 0,
                    "result": [
                        {"road_nm": "양재천로3길", "bd_main_nm": "123", "full_addr": "주소 123"},
                    ],
                },
            )
        return sgis_response(request)

    monkeypatch.setattr(acquisition, "sgis", SgisSource())
    result = await acquisition.collect_and_prepare_relational(
        base,
        transport=httpx.MockTransport(response),
        sgis_key="test",
        sgis_secret="test",
        commerce_key="test",
    )
    validate_prepared(result)
    assert roads
    assert all(
        p["planning_contract"] == "scene-comparison-plan-v1" for p in result["snapshot"]["plans"]
    )
    for frame in result["snapshot"]["frames"]:
        assert {f["family"] for f in frame["scene_snapshot"]["facts"]} == {
            "road",
            "land_cover",
            "surrounding_object",
            "area_context",
        }
        assert frame["card_header"]["dong"]
        address = frame["card_header"]["administrative_address"]
        assert address["sido"] and address["sigungu"]
        assert address["dong"] == frame["card_header"]["dong"]
        plan = next(p for p in result["snapshot"]["plans"] if p["scene_id"] == frame["scene_id"])
        serialized = json.dumps(plan["space_task"], ensure_ascii=False)
        assert "bd_main_nm" not in serialized and "주소 123" not in serialized
        assert frame["card_header"]["dong"] not in serialized
        assert "administrative_address" not in serialized


async def test_address_types_share_transform_not_response_cache():
    source, calls = SgisSource(), []
    point = {"lat": 37.5, "lng": 127.0}

    def response(request):
        calls.append(request.url.path)
        if request.url.path.endswith("authentication.json"):
            result = {"accessToken": "test-token", "accessTimeout": time.time() + 3600}
        elif request.url.path.endswith("transcoord.json"):
            result = {"posX": 1, "posY": 2}
        elif request.url.params["addr_type"] == "10":
            result = [{"road_nm": "양재천로3길", "bd_main_nm": "123"}]
        else:
            return sgis_response(request)
        return httpx.Response(200, json={"errCd": 0, "result": result})

    transport = httpx.MockTransport(response)
    dong, _ = await source.address(transport, "test", "test", point)
    road, at = await source.address(transport, "test", "test", point, addr_type=10)
    assert "emdong_nm" in dong and road == {"road_nm": "양재천로3길"}
    road["road_nm"] = "mutated"
    assert await source.address(transport, "test", "test", point, addr_type=10) == (
        {"road_nm": "양재천로3길"},
        at,
    )
    assert len(calls) == 4


@pytest.mark.parametrize("pin,note", [(True, False), (False, False), (True, True)])
def test_current_action_survives_zero_writing_capacity_without_old_context(pin, note):
    from tests.walk.diary.test_diary_activity import prepared

    base, _ = prepared(pin=pin, note=note)
    base = replace(
        base,
        slots=base.slots.model_copy(
            update={
                "policy": base.slots.policy.model_copy(update={"total_slots": 0}),
            }
        ),
    )
    result = prepare_relational_diary(base)
    validate_prepared(result)
    actions = [p["action_task"] for p in result["snapshot"]["plans"] if p["action_task"]]
    assert bool(actions) == (pin and not note)
    if actions:
        assert actions[0]["payload"]["current_gait"]
        assert "earlier" not in actions[0]["payload"]
        assert "물을 마시고" not in json.dumps(actions, ensure_ascii=False)


@pytest.mark.parametrize(
    "result,state", [([], "empty"), ([{"road_nm": "강남대로 123"}], "unknown")]
)
async def test_empty_or_invalid_road_is_not_a_spatial_exit(monkeypatch, result, state):
    source, _, _ = saved_case()
    base = assemble_saved_base_board(source, policy(3))

    def response(request):
        if request.url.path.endswith("rgeocode.json"):
            return httpx.Response(200, json={"errCd": 0, "result": result})
        return sgis_response(request)

    monkeypatch.setattr(acquisition, "sgis", SgisSource())
    snapshots = await acquisition.collect_road_snapshots(
        base.board,
        transport=httpx.MockTransport(response),
        key="test",
        secret="test",
    )
    prepared = prepare_relational_diary(base, road_snapshots=snapshots)
    for frame in prepared["snapshot"]["frames"]:
        assert frame["scene_snapshot"]["collection"]["road"] == state
        assert not any(f["family"] == "road" for f in frame["scene_snapshot"]["facts"])
