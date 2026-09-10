"""응급 문구는 제품 문장이다 — 모델이 짓지 않고, 경로가 달라도 같은 말이 나간다."""

from __future__ import annotations

from daengs_backend.orchestration.redirects import (
    SCOPED_REDIRECT_MESSAGES,
    VET_CONTACT_CALL_FIRST,
    VET_CONTACT_CURRENT_LOCATION_FRAME,
    VET_CONTACT_HOURS_UNKNOWN,
    VET_CONTACT_LOCATION_UNKNOWN,
)


def test_night_and_day_differ_only_in_what_to_ask_on_the_phone() -> None:
    assert VET_CONTACT_CALL_FIRST[True] == "전화로 야간 진료 여부를 먼저 확인하세요."
    assert VET_CONTACT_CALL_FIRST[False] == "전화로 지금 진료 가능한지 먼저 확인하세요."


def test_hours_unknown_sentence_admits_what_we_do_not_have() -> None:
    assert VET_CONTACT_HOURS_UNKNOWN == (
        "진료 시간과 응급 진료 여부는 공공 데이터에 없어서 확인해 드릴 수 없습니다."
    )


def test_emergency_opener_is_reused_not_rewritten() -> None:
    """같은 상황이 경로에 따라 다른 문장으로 나오면 안 된다."""
    assert SCOPED_REDIRECT_MESSAGES["emergency"] == "응급 상황으로 보여요. 지금 바로 동물병원으로 가세요."


def test_location_unknown_does_not_ask_a_question() -> None:
    """응급에 되묻지 않는다 — 물음표가 있으면 CLARIFY 처럼 읽힌다."""
    assert "?" not in VET_CONTACT_LOCATION_UNKNOWN
    assert VET_CONTACT_LOCATION_UNKNOWN == "현재 위치를 알 수 없어 가까운 병원을 찾지 못했습니다."


def test_location_frame_matches_place_wording() -> None:
    assert VET_CONTACT_CURRENT_LOCATION_FRAME == "현재 기기 위치를 기준으로"
