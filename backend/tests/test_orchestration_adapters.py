"""Thin adapter mappings for Training, Life, and Walk."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from daengs_backend.orchestration.adapters.life import LifeCapabilityAdapter
from daengs_backend.orchestration.adapters.training import TrainingCapabilityAdapter
from daengs_backend.orchestration.adapters.walk import WalkCapabilityAdapter
from daengs_backend.orchestration.contracts import (
    CapabilityRequest,
    CapabilityStatus,
    LifePayload,
    TrainingPayload,
    WalkPayload,
)
from daengs_backend.schemas.training import TrainingCitation
from daengs_backend.services.training_rag import (
    TrainingRagResult,
    TrainingRagTimeoutError,
    TrainingRagUnavailableError,
)
from daengs_life.app.dto.ask import AskOut, HitOut
from daengs_life.app.dto.walk import WalkOut


class FakeTrainingService:
    def __init__(self, result: TrainingRagResult | Exception) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def ask(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def training_result(decision: str, reason: str) -> TrainingRagResult:
    return TrainingRagResult(
        decision=decision,  # type: ignore[arg-type]
        reason=reason,
        answer="상류 문구",
        citations=[TrainingCitation(rank=1, label="기초")],
    )


def training_request() -> CapabilityRequest:
    return CapabilityRequest(
        capability="training",
        payload=TrainingPayload(question="질문"),
    )


@pytest.mark.parametrize(
    ("decision", "reason", "status"),
    [
        ("ANSWER", "grounded_generation", CapabilityStatus.OK),
        ("UNCERTAIN", "low_top_score", CapabilityStatus.ABSTAINED),
        ("SAFETY_REFUSAL", "safety_boundary_training_harm", CapabilityStatus.REFUSED),
        ("MEDICAL_REFUSAL", "medical_input_guardrail", CapabilityStatus.REFUSED),
        ("SAFETY_REFUSAL", "output_safety_guardrail", CapabilityStatus.REFUSED),
    ],
)
async def test_training_decisions_preserve_domain_meaning(
    decision: str, reason: str, status: CapabilityStatus
) -> None:
    service = FakeTrainingService(training_result(decision, reason))
    result = await TrainingCapabilityAdapter(service).run(training_request(), request_id="trace")
    assert result.status == status
    if result.abstention:
        assert result.abstention.code == reason
    if result.refusal:
        assert result.refusal.code == reason


async def test_training_timeout_is_not_error() -> None:
    service = FakeTrainingService(TrainingRagTimeoutError())
    result = await TrainingCapabilityAdapter(service).run(training_request(), request_id="trace")
    assert result.status == CapabilityStatus.TIMEOUT


async def test_training_runtime_failure_is_error() -> None:
    service = FakeTrainingService(TrainingRagUnavailableError())
    result = await TrainingCapabilityAdapter(service).run(training_request(), request_id="trace")
    assert result.status == CapabilityStatus.ERROR


async def test_training_citations_stay_at_public_granularity_and_top_k_cannot_leak() -> None:
    service = FakeTrainingService(training_result("ANSWER", "grounded_generation"))
    result = await TrainingCapabilityAdapter(service).run(training_request(), request_id="trace")
    assert result.data == {"answer": "상류 문구", "citations": [{"rank": 1, "label": "기초"}]}
    assert service.calls == [{"question": "질문", "trace_id": "trace"}]
    with pytest.raises(ValidationError):
        CapabilityRequest(
            capability="training",
            payload={"question": "질문", "top_k": 20},
        )


def life_output() -> AskOut:
    return AskOut(
        question="질문",
        answer="근거 있는 답변",
        hits=[
            HitOut(
                rank=1,
                score=0.91,
                chunk_id="secret-chunk",
                citation="제3조",
                citation_url="https://example.test/source",
                document_title="공식 문서",
                content="그래프 상태에 들어가면 안 되는 원문 청크",
            )
        ],
        cited=["제3조"],
        ungrounded=[],
        model="gemini",
        embedding_model="qwen",
    )


def life_request() -> CapabilityRequest:
    return CapabilityRequest(capability="life", payload=LifePayload(question="질문"))


async def test_life_normal_result_is_ok_without_raw_chunks() -> None:
    result = await LifeCapabilityAdapter(lambda _q, **_: life_output()).run(
        life_request(), request_id="trace"
    )
    assert result.status == CapabilityStatus.OK
    encoded = json.dumps(result.data, ensure_ascii=False)
    assert "근거 있는 답변" in encoded and "제3조" in encoded
    assert "원문 청크" not in encoded
    assert "secret-chunk" not in encoded
    assert "0.91" not in encoded


@pytest.mark.parametrize(
    ("status_code", "status"),
    [
        (404, CapabilityStatus.ABSTAINED),
        (504, CapabilityStatus.TIMEOUT),
        (502, CapabilityStatus.ERROR),
        (503, CapabilityStatus.ERROR),
    ],
)
async def test_life_machine_readable_errors_are_mapped(
    status_code: int, status: CapabilityStatus
) -> None:
    def fail(_: str, **_kw):
        raise HTTPException(status_code=status_code, detail="upstream")

    result = await LifeCapabilityAdapter(fail).run(life_request(), request_id="trace")
    assert result.status == status
    if status == CapabilityStatus.ABSTAINED:
        assert result.abstention and result.abstention.code == "no_evidence"


async def test_life_boundary_becomes_refused_with_its_code_and_wording() -> None:
    """422 -> REFUSED. `refusal.code` 는 보존되고 `message` 는 Life 가 준 문장 그대로다.

    Invariant 3 is the whole point: the adapter translates the status, never the sentence.
    """
    said = "지체 없이 가까운 동물병원에 방문해 수의사의 진료를 받으세요."

    def refuse(_: str, **_kw):
        raise HTTPException(status_code=422,
                            detail={"code": "emergency_boundary", "message": said})

    result = await LifeCapabilityAdapter(refuse).run(life_request(), request_id="trace")
    assert result.status == CapabilityStatus.REFUSED
    assert result.refusal and result.refusal.code == "emergency_boundary"
    assert result.refusal.message == said


async def test_a_mapping_detail_never_reaches_the_user_as_a_repr() -> None:
    """**`str()` over a mapping would hand the user a Python repr.** That is the lossy step
    invariant 3 forbids, and it is silent — the request still returns 200 at the top.
    """
    def abstain(_: str, **_kw):
        raise HTTPException(status_code=404,
                            detail={"code": "no_evidence", "message": "자료에 없습니다."})

    result = await LifeCapabilityAdapter(abstain).run(life_request(), request_id="trace")
    assert result.status == CapabilityStatus.ABSTAINED
    assert result.abstention and result.abstention.message == "자료에 없습니다."
    assert "{" not in result.abstention.message


async def test_the_older_string_detail_still_maps() -> None:
    """404 with a bare string is the pre-RAG-055 shape and still has to work — `services/ask`
    keeps it for the genuinely-zero-hit case.
    """
    def abstain(_: str, **_kw):
        raise HTTPException(status_code=404, detail="근거를 찾지 못했다")

    result = await LifeCapabilityAdapter(abstain).run(life_request(), request_id="trace")
    assert result.status == CapabilityStatus.ABSTAINED
    assert result.abstention.code == "no_evidence"
    assert result.abstention.message == "근거를 찾지 못했다"


async def test_life_runtime_failure_is_error() -> None:
    def fail(_: str, **_kw):
        raise RuntimeError("database unavailable")

    result = await LifeCapabilityAdapter(fail).run(life_request(), request_id="trace")
    assert result.status == CapabilityStatus.ERROR


def walk_output(grade: str) -> WalkOut:
    return WalkOut.model_validate(
        {
            "location": {
                "dong": "서초2동",
                "grid": [61, 125],
                "air_station": "강남대로",
                "label": "서초2동 (측정소: 강남대로) 기준",
            },
            "generated_at": datetime.fromisoformat("2026-08-31T10:00:00+09:00"),
            "now": {
                "at": datetime.fromisoformat("2026-08-31T10:00:00+09:00"),
                "grade": grade,
                "axes": {
                    "heat": {
                        "grade": grade,
                        "note": "체감온도 근거",
                        "basis": [
                            {
                                "quantity": "temperature",
                                "value": 29.0,
                                "source": "kma",
                                "spatial_ref": "61,125",
                                "valid_at": "2026-08-31T10:00:00+09:00",
                                "issued_at": "2026-08-31T09:00:00+09:00",
                            }
                        ],
                    }
                },
            },
            "sources": [{"provider": "kma", "ok": grade != "unknown", "reason": None}],
        }
    )


def walk_request() -> CapabilityRequest:
    return CapabilityRequest(capability="walk", payload=WalkPayload(lat=37.5, lon=127.0))


@pytest.mark.parametrize("grade", ["GOOD", "CAUTION", "UNSAFE"])
async def test_walk_successful_domain_verdicts_are_ok(grade: str) -> None:
    result = await WalkCapabilityAdapter(lambda _: walk_output(grade)).run(
        walk_request(), request_id="trace"
    )
    assert result.status == CapabilityStatus.OK
    assert result.data and result.data["now"]["grade"] == grade


async def test_walk_unknown_is_abstained() -> None:
    result = await WalkCapabilityAdapter(lambda _: walk_output("unknown")).run(
        walk_request(), request_id="trace"
    )
    assert result.status == CapabilityStatus.ABSTAINED


async def test_walk_runtime_failure_is_error() -> None:
    def fail(_: WalkPayload):
        raise RuntimeError("provider failure")

    result = await WalkCapabilityAdapter(fail).run(walk_request(), request_id="trace")
    assert result.status == CapabilityStatus.ERROR


async def test_walk_basis_source_time_and_location_are_preserved() -> None:
    result = await WalkCapabilityAdapter(lambda _: walk_output("UNSAFE")).run(
        walk_request(), request_id="trace"
    )
    assert result.data
    assert result.data["location"]["label"].startswith("서초2동")
    assert result.data["generated_at"]
    assert result.data["sources"][0]["provider"] == "kma"
    assert result.data["now"]["axes"]["heat"]["basis"][0]["source"] == "kma"


# ── Life → Walk 강수 어휘 경계 (RT-004) ──────────────────────────────────

def test_every_life_precipitation_kind_is_folded_at_the_walk_boundary() -> None:
    """**Life 에 값이 늘면 이 경계도 늘어야 한다.**

    `_precipitation_kind` 는 `.get()` 이라 빠뜨린 값이 조용히 `None` 이 되고, Walk 는 그것을
    "관측이 없다"로 읽는다 — KMA 가 값을 냈는데도 그렇다. 예외가 안 나서 안 보인다.
    실제로 RT-004 가 `PrecipKind` 에 셋(빗방울·빗방울눈날림·눈날림)을 늘렸을 때 여기가
    비어 있었고, 이 테스트는 **다음번에 같은 일이 나면 먼저 깨지라고** 있는 것이다.

    `PrecipKind` 를 직접 훑으므로 어휘가 늘면 자동으로 검사 범위가 는다.
    """
    from daengs_life.realtime.observation import PrecipKind

    from daengs_backend.orchestration.adapters.life import _precipitation_kind

    missing = [k.value for k in PrecipKind if _precipitation_kind(k.value) is None]
    assert not missing, f"Walk 경계에서 접히지 않는 Life 강수 어휘: {missing}"


def test_the_fold_keeps_the_form_and_drops_only_the_intensity() -> None:
    """접는 축은 **세기**이지 형태가 아니다 — 눈이 비가 되면 안 된다.

    `shower`(소나기)를 `rain` 으로 접어 온 규칙을 5·6·7 에 그대로 적용한 것이고,
    그 규칙이 지켜지는지를 형태별로 확인한다.
    """
    from daengs_backend.orchestration.adapters.life import _precipitation_kind

    assert _precipitation_kind("drizzle") == "rain"          # 5 빗방울 — 비 계열
    assert _precipitation_kind("drizzle_snow") == "mixed"    # 6 빗방울눈날림 — 혼합 계열
    assert _precipitation_kind("snow_flurry") == "snow"      # 7 눈날림 — 눈 계열
    assert _precipitation_kind("멋대로") is None              # 모르는 값은 그대로 없음
