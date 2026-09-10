"""Android 가 받는 JSON 을 바이트로 고정한다.

계약이 움직이면 다른 저장소가 아니라 여기서 깨져야 한다 —
`tests/fixtures/place_capability/` 가 같은 이유로 있다 (D-051).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from daengs_backend.orchestration.adapters.vet_contact import VetContactCapabilityAdapter
from daengs_backend.orchestration.contracts import CapabilityRequest, VetContactPayload

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vet_contact"


def _upstream() -> dict:
    return {
        "groups": [
            {
                "kind": "hospital",
                "limit": 5,
                "truncated": False,
                "results": [
                    {
                        "place": {
                            "key": {"source": "public:mois:animal_hospital", "ref": "1"},
                            "name": "가까운동물병원",
                            "lat": 37.5,
                            "lng": 127.0,
                            "distance_m": 320,
                            "match": {"kind": "hospital"},
                            "classifications": [{"source": {"source": "s", "ref": "r"}}],
                            "facts": {
                                "address": "서울시 중구",
                                "phone": "02-123-4567",
                                "medical": {
                                    "active": True,
                                    "license_status_name": "영업/정상",
                                    "open_now": None,
                                },
                            },
                        },
                        "evaluations": {},
                    }
                ],
            }
        ]
    }


async def _result(*, lat: float | None, lon: float | None) -> dict:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_upstream())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            CapabilityRequest(
                capability="vet_contact",
                payload=VetContactPayload(lat=lat, lon=lon, at_night=False),
            ),
            request_id="fixture",
        )
    finally:
        await client.aclose()
    return json.loads(result.model_dump_json(exclude={"elapsed_ms"}))


async def test_with_candidates_fixture_matches() -> None:
    actual = await _result(lat=37.5665, lon=126.978)
    expected = json.loads((FIXTURES / "with_candidates.json").read_text(encoding="utf-8"))
    assert actual == expected


async def test_location_required_fixture_matches() -> None:
    actual = await _result(lat=None, lon=None)
    expected = json.loads((FIXTURES / "location_required.json").read_text(encoding="utf-8"))
    assert actual == expected


async def test_no_quality_ranking_field_ever_reaches_the_client() -> None:
    """평점·후기·추천 이유가 계약에 들어올 자리가 없어야 한다 (설계 §1-1)."""
    blob = json.dumps(await _result(lat=37.5665, lon=126.978), ensure_ascii=False)
    for forbidden in ("rating", "review", "score", "평점", "후기", "추천 이유"):
        assert forbidden not in blob
