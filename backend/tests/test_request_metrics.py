"""요청 지표 — services/request_metrics.py (콘솔 로드맵 B2 · #297).

**여기서 지키려는 것 다섯:**

  ① 지표가 답변을 죽이지 않는다 — 쓰기가 터져도 응답은 그대로 나간다
  ② 원문이 안 들어간다 — 남기는 값이 D-037 의 허용 칸을 안 넘는다
  ③ 사유는 **코드**지 문구가 아니다 — 문구는 바뀌고, 바뀌면 집계가 끊긴다
  ④ 능력 0개가 정상이다 — 사교적 응답과 라우터 실패를 세려면 그래야 한다
  ⑤ 계약된 클라이언트 오류는 안 남긴다 — 이 표는 오케스트레이션을 센다

`conftest.py` 의 `recorded_metrics` 가 `_write` 를 가로채므로 **이 파일은 DB 를 안 탄다.**
진짜 SQL(집계 질의)이 맞는지는 `db/migrations/verify_2026-09-07_request_metrics.sql` 과
실제 DB 로 본다.
"""

import uuid

import pytest
from fastapi import HTTPException

from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityName,
    CapabilityResult,
    CapabilityStatus,
    OutcomeDetail,
    RouterKind,
    RouteTrace,
)
from daengs_backend.services import request_metrics as service

#: **수집 시점에 잡아 둔 진짜 `_write`.** `conftest.py` 의 autouse fixture 가
#: 테스트마다 이것을 가짜로 갈아 끼우므로, 진짜 쓰기 경로를 봐야 하는 테스트는
#: 이 참조로 되돌린다. 모듈 import 는 fixture 보다 먼저라 여기 담기는 것은 원본이다.
_REAL_WRITE = service._write


def _response(**overrides) -> AssistantResponse:
    fields = {
        "request_id": str(uuid.uuid4()),
        "status": AssistantStatus.ANSWERED,
        "message": "네, 그렇습니다.",
        "results": [],
    }
    fields.update(overrides)
    return AssistantResponse(**fields)


def _ok(capability: CapabilityName = CapabilityName.LIFE) -> CapabilityResult:
    return CapabilityResult(
        capability=capability,
        status=CapabilityStatus.OK,
        data={"answer": "..."},
        elapsed_ms=12,
    )


class TestMeasured:
    async def test_한_행을_남기고_응답은_그대로_돌려준다(self, recorded_metrics) -> None:
        response = _response(results=[_ok()])

        returned = await service.measured(
            None, principal_kind="APP_USER", run=lambda: _same(response)
        )

        assert returned is response
        (row,) = recorded_metrics
        assert row["status"] == "ANSWERED"
        assert row["principal_kind"] == "APP_USER"
        assert row["capabilities"] == ["life"]
        assert row["elapsed_ms"] >= 0

    async def test_남기는_값이_허용_칸을_안_넘는다(self, recorded_metrics) -> None:
        """**② 원문 금지.** 질문도 좌표도 회원 식별자도 값으로 안 들어간다 (D-037 · D-054).

        열 이름을 여기서 못박아 두면, 나중에 "디버깅에 편하니까" 로 하나가 늘 때
        이 테스트가 먼저 걸린다. DB 쪽 같은 그물은 verify ⑤ 다.
        """
        await service.measured(
            None, principal_kind="ADMIN", run=lambda: _same(_response(results=[_ok()]))
        )

        # **부분집합이 맞는 단언이다.** 여기서 묻는 것은 "빠진 게 있나"가 아니라
        # **"허용 밖의 것이 있나"** 이고, 성공 경로는 `error_category` 를 아예 안 넘긴다.
        allowed = {
            "request_id",
            "principal_kind",
            "status",
            "elapsed_ms",
            "router_kind",
            "capabilities",
            "reason_code",
            "error_category",
        }
        (row,) = recorded_metrics
        assert set(row) <= allowed, set(row) - allowed

    async def test_능력_0개가_정상이다(self, recorded_metrics) -> None:
        """**④** 사교적 응답과 라우터 실패는 아무 능력도 안 부른다. 그 둘을 세는 것이
        이 열의 값이라, 빈 배열을 "빠뜨린 것" 으로 다루면 안 된다."""
        await service.measured(
            None,
            principal_kind="APP_USER",
            run=lambda: _same(_response(status=AssistantStatus.FAILED, results=[])),
        )

        (row,) = recorded_metrics
        assert row["capabilities"] == []
        assert row["status"] == "FAILED"

    async def test_라우터_종류를_남긴다(self, recorded_metrics) -> None:
        await service.measured(
            None,
            principal_kind="ADMIN",
            run=lambda: _same(
                _response(route=RouteTrace(router=RouterKind.LLM), results=[_ok()])
            ),
        )

        (row,) = recorded_metrics
        assert row["router_kind"] == "llm"

    async def test_라우트가_없으면_None_이다(self, recorded_metrics) -> None:
        """앱 회원 요청에는 `route` 가 안 실린다 (#238). 그때 NULL 이 맞다."""
        await service.measured(
            None, principal_kind="APP_USER", run=lambda: _same(_response(results=[_ok()]))
        )

        assert recorded_metrics[0]["router_kind"] is None


class TestReasonCode:
    """**③ 코드지 문구가 아니다.**"""

    @pytest.mark.parametrize("field", ["abstention", "refusal"])
    async def test_코드를_남기고_문구는_안_남긴다(self, recorded_metrics, field) -> None:
        detail = OutcomeDetail(code="no_evidence", message="근거를 찾지 못했습니다.")
        result = CapabilityResult(
            capability=CapabilityName.LIFE,
            status=(
                CapabilityStatus.ABSTAINED
                if field == "abstention"
                else CapabilityStatus.REFUSED
            ),
            elapsed_ms=8,
            **{field: detail},
        )

        await service.measured(
            None,
            principal_kind="APP_USER",
            run=lambda: _same(
                _response(status=AssistantStatus.UNCERTAIN, results=[result])
            ),
        )

        (row,) = recorded_metrics
        assert row["reason_code"] == "no_evidence"
        assert "근거를" not in str(row)

    async def test_사유가_없으면_None_이다(self, recorded_metrics) -> None:
        """거절이 아니었던 요청은 "사유 없음" 이 아니라 **분포에 안 들어간다.**"""
        await service.measured(
            None, principal_kind="APP_USER", run=lambda: _same(_response(results=[_ok()]))
        )

        assert recorded_metrics[0]["reason_code"] is None


class TestFailures:
    async def test_계약된_클라이언트_오류는_안_남긴다(self, recorded_metrics) -> None:
        """**⑤** 없는 대화 · 중복 message id 는 오케스트레이션이 어땠나가 아니다."""

        async def _raise() -> AssistantResponse:
            raise HTTPException(404, "대화를 찾을 수 없습니다.")

        with pytest.raises(HTTPException):
            await service.measured(None, principal_kind="APP_USER", run=_raise)

        assert recorded_metrics == []

    async def test_예상_못_한_예외는_범주로_남기고_다시_던진다(
        self, recorded_metrics
    ) -> None:
        """삼키면 500 이 200 이 된다. 남기되 던진다."""

        async def _raise() -> AssistantResponse:
            raise RuntimeError("provider down: 우리집 근처 병원 알려줘")

        with pytest.raises(RuntimeError):
            await service.measured(None, principal_kind="APP_USER", run=_raise)

        (row,) = recorded_metrics
        assert row["error_category"] == "RuntimeError"
        assert row["status"] == "FAILED"
        # **메시지를 안 담는다** — 예외 메시지에는 질문이나 좌표가 섞여 들어온다.
        assert "병원" not in str(row)

    async def test_지표가_터져도_답변은_나간다(self, monkeypatch) -> None:
        """**① 이 파일의 핵심.**

        **진짜 `_write` 로 되돌려 놓고 본다.** autouse fixture 가 걸려 있는 채로 재면
        가짜가 예외를 안 내니 아무것도 증명하지 못한다 — 통과하지만 통과할 이유가 없는
        테스트가 된다.

        세션 공장이 터지게 만들고도 응답이 그대로 나오는지 본다. 이것이 무너지면 지표
        한 행 때문에 사용자가 500 을 받는다.
        """
        monkeypatch.setattr(service, "_write", _REAL_WRITE)

        def _explode():
            raise RuntimeError("DB 없음")

        response = _response(results=[_ok()])
        returned = await service.measured(
            _explode, principal_kind="APP_USER", run=lambda: _same(response)
        )

        assert returned is response

    async def test_request_id_가_UUID_가_아니면_안_남긴다(self, recorded_metrics) -> None:
        """새 id 를 지어내지 않는다 — 아무것도 안 가리키는 id 는 있는 것보다 나쁘다."""
        await service.measured(
            None,
            principal_kind="APP_USER",
            run=lambda: _same(_response(request_id="not-a-uuid", results=[_ok()])),
        )

        assert recorded_metrics == []


async def _same(response: AssistantResponse) -> AssistantResponse:
    """`run` 자리에 넣을 코루틴. `lambda` 가 코루틴을 돌려주게 한다."""
    return response
