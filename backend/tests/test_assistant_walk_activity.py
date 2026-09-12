"""D-072 — 기록된 산책이 비서 프롬프트까지 가는 길, 그리고 **못 잴 때 무엇을 말하는가**.

DB 는 안 씁니다. `test_assistant_care_log.py` 와 같은 꼴로 리포지토리를 가짜로 바꿉니다.
여기서 보는 것은 **규칙**입니다 — 좌표가 한 칸도 안 넘어가는가, 측정 안 된 산책이 합계에
안 섞이는가, 기록이 없는 요청의 프롬프트가 이 카드 전과 글자까지 같은가.
"""

import pytest
from pydantic import ValidationError

from daengs_backend.orchestration.contracts import GeneralPayload, WalkActivityContext
from daengs_backend.orchestration.redirects import DISTANCE_FROM_RECORDED_WALKS_ONLY


def test_disclosure_names_the_record_and_refuses_the_described_route() -> None:
    """D-051 ⑤ 의 고지와 같은 성질 — 못 하는 사실과 **그 이유**를 같이 말한다."""
    assert "기록된 산책" in DISTANCE_FROM_RECORDED_WALKS_ONLY
    assert "말씀" in DISTANCE_FROM_RECORDED_WALKS_ONLY
    # 되묻는 문장으로 읽히면 안 된다 — `VET_CONTACT_LOCATION_UNKNOWN` 과 같은 규칙
    assert "?" not in DISTANCE_FROM_RECORDED_WALKS_ONLY


def test_walk_activity_context_carries_no_coordinate() -> None:
    """좌표는 이 카드에서 한 칸도 안 넘어간다 (Global Constraint 2)."""
    for forbidden in ("lat", "lon", "lng", "polyline", "points", "path"):
        assert forbidden not in WalkActivityContext.model_fields, forbidden


def test_measured_walks_never_exceed_recorded_walks() -> None:
    """측정된 산책이 기록된 산책보다 많을 수 없다 — 조인이 중복 합산하면 여기서 걸린다."""
    with pytest.raises(ValidationError):
        WalkActivityContext(
            day="2026-09-12", walk_count=1, measured_walk_count=2,
            distance_m=100, moving_s=60,
        )


def test_general_payload_takes_walk_activity_and_life_does_not() -> None:
    """`care_log` 와 같은 규칙 — 폴백에만 간다."""
    from daengs_backend.orchestration.contracts import LifePayload

    activity = WalkActivityContext(
        day="2026-09-12", walk_count=2, measured_walk_count=1,
        distance_m=1_200, moving_s=900, last_started_at="08:30",
    )
    assert GeneralPayload(question="q", walk_activity=activity).walk_activity == activity
    with pytest.raises(ValidationError):
        LifePayload(question="q", walk_activity=activity)
