"""응급 병원 연락의 HTTP 경계와 CapabilityResult 매핑."""

from __future__ import annotations

import json

import httpx

from daengs_backend.orchestration.adapters.vet_contact import VetContactCapabilityAdapter
from daengs_backend.orchestration.contracts import (
    CapabilityRequest,
    CapabilityStatus,
    VetContactPayload,
)


def _request(*, lat: float | None = 37.5665, lon: float | None = 126.978, night: bool = False):
    return CapabilityRequest(
        capability="vet_contact",
        payload=VetContactPayload(lat=lat, lon=lon, at_night=night),
    )


def _search_payload(count: int = 2) -> dict:
    return {
        "groups": [
            {
                "kind": "hospital",
                "limit": 5,
                "truncated": False,
                "results": [
                    {
                        "place": {
                            "key": {"source": "public:mois:animal_hospital", "ref": f"r{i}"},
                            "name": f"{i}번 동물병원",
                            "lat": 37.5,
                            "lng": 127.0,
                            "distance_m": 100 * (i + 1),
                            "match": {"kind": "hospital"},
                            "classifications": [{"source": {"source": "s", "ref": "r"}}],
                            "facts": {
                                "address": f"서울시 어딘가 {i}",
                                "phone": f"02-000-000{i}",
                                "medical": {
                                    "active": True,
                                    "license_status_name": "영업/정상",
                                    "open_now": None,
                                },
                            },
                        },
                        "evaluations": {},
                    }
                    for i in range(count)
                ],
            }
        ]
    }


async def test_adapter_asks_only_for_hospitals_around_the_trusted_point() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_search_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(
            client=client, base_url="http://place-search:8000"
        ).run(_request(), request_id="request-1")
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.OK
    [sent] = seen
    assert sent.url == "http://place-search:8000/v2/places/search"
    assert sent.headers["X-Request-ID"] == "request-1"
    assert json.loads(sent.content) == {
        "lat": 37.5665,
        "lng": 126.978,
        "radius_m": 10000,
        "kinds": ["hospital"],
        "limit_per_kind": 5,
    }


async def test_candidates_carry_phone_distance_and_nothing_that_ranks_quality() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search_payload(count=1))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            _request(), request_id="request-2"
        )
    finally:
        await client.aclose()

    [candidate] = result.data["candidates"]
    assert candidate == {
        "name": "0번 동물병원",
        "phone": "02-000-0000",
        "distance_m": 100,
        "address": "서울시 어딘가 0",
        "license_status_name": "영업/정상",
        "open_now": None,
    }


async def test_missing_coordinates_abstain_without_calling_place_search() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("좌표가 없으면 HTTP 를 부르지 않는다")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            _request(lat=None, lon=None), request_id="request-3"
        )
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.ABSTAINED
    assert result.abstention.code == "vet_contact.location_required"
    assert "응급 상황으로 보여요" in result.abstention.message
    assert "현재 위치를 알 수 없어" in result.abstention.message


async def test_zero_candidates_is_ok_not_an_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"groups": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            _request(), request_id="request-4"
        )
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.OK
    assert result.data["candidates"] == []


async def test_hours_unknown_notice_is_present_whether_or_not_there_are_candidates() -> None:
    async def empty(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"groups": []})

    async def full(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search_payload())

    for handler in (empty, full):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await VetContactCapabilityAdapter(client=client).run(
                _request(), request_id="request-5"
            )
        finally:
            await client.aclose()
        codes = [notice["code"] for notice in result.data["notices"]]
        assert "vet_contact.hours_unknown" in codes
        assert "place.searched_around_current_location" in codes


async def test_night_changes_only_what_to_ask_on_the_phone() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search_payload())

    answers = {}
    for night in (True, False):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await VetContactCapabilityAdapter(client=client).run(
                _request(night=night), request_id="request-6"
            )
        finally:
            await client.aclose()
        answers[night] = result.data["answer"]

    assert "야간 진료 여부를" in answers[True]
    assert "지금 진료 가능한지" in answers[False]
    for answer in answers.values():
        assert answer.startswith("응급 상황으로 보여요. 지금 바로 동물병원으로 가세요.")
        assert "확인해 드릴 수 없습니다" in answer


async def test_upstream_failure_is_an_error_result_not_an_exception() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            _request(), request_id="request-7"
        )
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.ERROR
    assert result.error.kind == "vet_contact_upstream_failure"


async def test_timeout_still_tells_the_user_to_go_to_a_vet_now() -> None:
    """병원 목록이 시간 초과여도 "응급이면 병원부터" 는 빠지면 안 된다 — ABSTAINED(위치
    없음)가 이미 지키는 것과 같은 약속이다."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            _request(), request_id="request-timeout"
        )
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.TIMEOUT
    assert result.error.detail.startswith("응급 상황으로 보여요")
    assert "병원 목록 응답 시간이 초과됐습니다." in result.error.detail


async def test_transport_error_still_tells_the_user_to_go_to_a_vet_now() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            _request(), request_id="request-transport-error"
        )
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.ERROR
    assert result.error.kind == "vet_contact_unavailable"
    assert result.error.detail.startswith("응급 상황으로 보여요")
    assert "병원 목록 기능에 연결할 수 없습니다." in result.error.detail


async def test_4xx_still_tells_the_user_to_go_to_a_vet_now() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "bad request"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            _request(), request_id="request-4xx"
        )
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.ERROR
    assert result.error.kind == "vet_contact_invalid_request"
    assert result.error.detail.startswith("응급 상황으로 보여요")
    assert "병원 목록을 가져오지 못했습니다." in result.error.detail


async def test_malformed_json_body_still_tells_the_user_to_go_to_a_vet_now() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            _request(), request_id="request-malformed"
        )
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.ERROR
    assert result.error.kind == "vet_contact_invalid_response"
    assert result.error.detail.startswith("응급 상황으로 보여요")
    assert "병원 목록을 해석할 수 없습니다." in result.error.detail
