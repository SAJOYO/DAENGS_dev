"""Approved orchestration contract invariants."""

from typing import get_args

import pytest
from pydantic import ValidationError

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityStatus,
    ClarifyRequest,
    Handoff,
    LifePayload,
    PlacePayload,
    RoutePlan,
    RouterKind,
    ScreeningContext,
    SkinPayload,
    TrainingPayload,
)
from daengs_backend.orchestration.semantic import ExecuteName
from daengs_backend.schemas.chat import AgentCategory


def test_abstained_and_refused_are_distinct() -> None:
    assert CapabilityStatus.ABSTAINED != CapabilityStatus.REFUSED


def test_error_and_timeout_are_distinct() -> None:
    assert CapabilityStatus.ERROR != CapabilityStatus.TIMEOUT


def test_requests_and_handoffs_can_coexist() -> None:
    plan = RoutePlan(
        requests=[
            CapabilityRequest(
                capability="life",
                payload=LifePayload(question="동물등록 변경 기한은?"),
            )
        ],
        handoffs=[Handoff(target="skin", reason="image_required")],
        router=RouterKind.DETERMINISTIC,
    )
    assert len(plan.requests) == 1
    assert plan.handoffs[0].target == "skin"


def test_clarify_is_exclusive_at_the_contract_boundary() -> None:
    with pytest.raises(ValidationError, match="clarify is exclusive"):
        RoutePlan(
            requests=[
                CapabilityRequest(
                    capability="training",
                    payload=TrainingPayload(question="입질 교육"),
                )
            ],
            clarify=ClarifyRequest(question="어느 상황인가요?", missing=["situation"]),
            router=RouterKind.DETERMINISTIC,
        )


def test_route_plan_has_no_scalar_mode() -> None:
    assert "mode" not in RoutePlan.model_fields
    with pytest.raises(ValidationError):
        RoutePlan(mode="EXECUTE", router=RouterKind.DETERMINISTIC)


def test_unsupported_capability_fails_predictably() -> None:
    with pytest.raises(ValidationError):
        CapabilityRequest(capability="gait", payload={"question": "걸음이 이상해"})


def test_capability_payload_must_match_its_identity() -> None:
    with pytest.raises(ValidationError):
        CapabilityRequest(
            capability="training",
            payload=LifePayload(question="훈련 질문"),
        )


def test_place_payload_contains_original_query_and_location_but_no_identity() -> None:
    payload = PlacePayload(query="  조용한 곳  ", lat=37.5563, lon=126.9236)
    assert payload.query == "  조용한 곳  "
    assert set(payload.model_dump()) == {"query", "lat", "lon"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PlacePayload.model_validate(
            {
                "query": "조용한 곳",
                "lat": 37.5563,
                "lon": 126.9236,
                "active_dog_id": "dog-1",
            }
        )


def test_screening_context_carries_no_lesion_identity_and_no_control_copy() -> None:
    """#307 — 좁힘이 계약에 박혀 있다.

    D-023 의 방어는 "병변 이름을 말하는 코드 경로가 없다" 이지 "말하지 말자는 합의" 가
    아니다. 그 성질은 필드가 하나 늘면 조용히 사라지므로(예외도 실패도 안 난다) 계약이
    직접 거절해야 한다. 통제 문구도 같다 — 사용자에게 무수정으로 갈 것이지 payload 나
    프롬프트를 지날 것이 아니다 (PR #79).
    """
    assert set(ScreeningContext.model_fields) == {"verdict", "days_ago"}
    for 금지 in ("distribution", "group", "top1", "headline", "body", "action",
               "disclaimer", "stage1", "stage2", "photo_url"):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ScreeningContext.model_validate(
                {"verdict": "abnormal", "days_ago": 1, 금지: "x"}
            )


def test_screening_verdict_stays_the_three_the_capability_owns() -> None:
    """상류가 내는 세 값뿐이다. 모르는 판정을 아는 척 통과시키지 않는다."""
    for verdict in ("normal", "abnormal", "retake"):
        assert ScreeningContext(verdict=verdict, days_ago=0).verdict == verdict
    with pytest.raises(ValidationError):
        ScreeningContext(verdict="inconclusive", days_ago=0)


def test_skin_executes_only_as_an_explainer_of_the_narrow_verdict() -> None:
    """D-079 이 D-036 의 `Skin EXECUTE = NO` 를 좁게 열었다 — 이미 끝난 판정을 **해설**하는
    실행 하나다. #307 의 좁힘은 그대로다: 해설 payload 의 판정은 `ScreeningContext` 자체라서
    병변 이름 · 확률 · 통제 문구가 들어갈 칸이 없다. 판정을 새로 내는 실행은 여전히 없다 —
영상을 새로 분석하는 실행은
    여전히 없다 — `gait` 도 D-080 으로 **이미 계산된 비교를 해설하는** 실행 하나만 열렸다."""
    assert set(SkinPayload.model_fields) == {"question", "screening", "history"}
    assert SkinPayload.model_fields["screening"].annotation is ScreeningContext
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SkinPayload.model_validate(
            {"question": "q", "screening": {"verdict": "abnormal", "days_ago": 0}, "top1": "A6"}
        )


def test_gait_executes_only_as_an_explainer_of_an_already_computed_comparison() -> None:
    """D-080 이 D-036 의 `Gait EXECUTE = NO` 를 좁게 열었다 — **이미 계산된 비교**를
    해설하는 실행 하나다. 영상·관절 좌표·수치가 들어갈 칸이 없고(D-058), 방향(좋아졌다·
    나빠졌다)도 계약에 없다. 분석을 새로 내는 실행은 여전히 HANDOFF 다."""
    from daengs_backend.orchestration.contracts import GaitCompareContext, GaitComparePayload

    assert set(GaitComparePayload.model_fields) == {"question", "compare", "unavailable"}
    assert GaitComparePayload.model_fields["compare"].annotation == GaitCompareContext | None
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GaitCompareContext.model_validate(
            {
                "change_kind": "no_change",
                "flagged_sides": [],
                "left_measured": 3,
                "left_joints": 3,
                "right_measured": 3,
                "right_joints": 3,
                "days_between": 1,
                "reliability": "ok",
                "version_mismatch": False,
                "summary_for_ui": {"L_Hip": {"x_range": 1.0}},
            }
        )


# ---------------------------------------------- 능력 이름의 사본 (#269)


def test_capability_names_have_exactly_three_copies_and_they_agree() -> None:
    """능력 이름 목록이 저장소에 세 벌 있고, 서로를 참조하지 않는다.

    #196 이 `CapabilityName` 에 `place` 를 넣었고, #204 가 `ExecuteName` 을 따라 넓혔고,
    `AgentCategory` 는 아무도 못 봤다. 사본끼리 대조하는 테스트가 없어서 한쪽만 넓혀도
    아무것도 깨지지 않았고, 결국 저장된 `place` 행을 읽을 때 응답 검증이 터져 `/app/chats`
    가 500 을 냈다 (#269). 값 하나가 빠졌다는 것보다 **대조하는 사람이 없었다**는 것이
    사고의 본질이라, 셋을 한자리에서 묶는다.
    """
    names = {capability.value for capability in CapabilityName}
    # `general` (D-057) 은 v9 부터 라우터 목적지이기도 하다.
    # `vet_contact` 는 라우터가 고를 수 없는 능력이라 세 사본이 더는 완전히 같지 않다 —
    # 결정론적 어휘 게이트와 명시 신호로만 들어온다.
    # `care_log` (D-075) 도 라우터 밖이고, 한 겹 더 좁다 — 명시 신호로도 못 부르고,
    # 사용자가 앞 턴의 제안에 승낙했을 때만 들어온다.
    # `skin` (D-079) 도 라우터 밖이다 — 판정 기록이 붙은 `skin` 명시 신호로만 들어온다.
    # `gait` (D-080) 도 같다 — 비교 참조가 붙은 `gait` 명시 신호로만 들어온다.
    router_reachable = names - {"vet_contact", "care_log", "skin", "gait"}
    assert set(get_args(ExecuteName)) == router_reachable, "라우터가 고를 수 있는 목적지가 어긋났다"
    # **꼬리표는 안 좁힌다.** 라우터가 못 고르는 능력이라도 결과가 OK 면 `categories_of()` 가
    # 이름을 그대로 넣고, 그 행을 읽을 때 `AgentCategory` 가 좁으면 500 이 난다 — 그것이
    # 위 독스트링의 #269 사고다.
    assert names == set(get_args(AgentCategory)), "저장된 대화를 읽어 줄 꼬리표가 어긋났다"
