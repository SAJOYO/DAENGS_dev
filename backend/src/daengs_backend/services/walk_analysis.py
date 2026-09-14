"""Measurement analysis JSONB and ORM adapter, independent of sealed spatial outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from daengs_backend.models.walk import WalkAnalysis
from daengs_backend.services.walk_session.finalize import PreparedWalkEvidence
from daengs_walk.contracts import (
    MICRO_OBSERVATION_VERSION,
    CanonicalWalkFacts,
    MeasurementReceipt,
    MicroObservation,
    MotionEventOccurrence,
)
from daengs_walk.evidence import WalkEvidenceBundle


@dataclass(frozen=True)
class WalkAnalysisPayloads:
    facts: dict[str, Any]
    measurement_receipt: dict[str, Any]
    motion_events: list[dict[str, Any]]
    micro_observations: list[dict[str, Any]]


@dataclass(frozen=True)
class DecodedWalkAnalysis:
    facts: CanonicalWalkFacts
    measurement_receipt: MeasurementReceipt
    motion_events: tuple[MotionEventOccurrence, ...]
    micro_observations: tuple[MicroObservation, ...]


def build_analysis_model(
    prepared: PreparedWalkEvidence,
    evidence: WalkEvidenceBundle,
) -> WalkAnalysis:
    """한 finalize 계산 결과를 아직 session에 붙이지 않은 ORM graph로 만든다."""

    facts = evidence.facts
    if evidence.receipt.walk_id != facts.walk_id:
        raise ValueError("analysis 결과의 walk_id가 서로 다릅니다.")
    if evidence.receipt.received_fix_count != prepared.point_count:
        raise ValueError("measurement receipt가 finalize 입력 점 개수와 다릅니다.")

    payloads = analysis_payloads(evidence)
    analysis = WalkAnalysis(
        walk_id=facts.walk_id,
        input_fingerprint=prepared.input_fingerprint,
        point_count=prepared.point_count,
        terminal_client_seq=prepared.terminal_client_seq,
        facts_record_version=facts.record_version,
        calculation_version=facts.calculation_version,
        receipt_version=evidence.receipt.receipt_version,
        observation_version=MICRO_OBSERVATION_VERSION,
        moving_distance_m=facts.moving_distance_m,
        moving_s=facts.moving_s,
        stop_count=facts.stop_count,
        facts=payloads.facts,
        measurement_receipt=payloads.measurement_receipt,
        motion_events=payloads.motion_events,
        micro_observations=payloads.micro_observations,
    )
    return analysis


def analysis_payloads(evidence: WalkEvidenceBundle) -> WalkAnalysisPayloads:
    """Pydantic 계약을 JSONB가 조용히 필드를 잃지 않는 JSON 값으로 바꾼다."""

    return WalkAnalysisPayloads(
        facts=evidence.facts.model_dump(mode="json"),
        measurement_receipt=evidence.receipt.model_dump(mode="json"),
        motion_events=[event.model_dump(mode="json") for event in evidence.events],
        micro_observations=[item.model_dump(mode="json") for item in evidence.observations],
    )


def decode_analysis_model(analysis: WalkAnalysis) -> DecodedWalkAnalysis:
    """중복 저장한 version·summary 컬럼과 canonical JSONB가 같은지 다시 확인한다."""

    facts = CanonicalWalkFacts.model_validate(analysis.facts)
    receipt = MeasurementReceipt.model_validate(analysis.measurement_receipt)
    events = tuple(MotionEventOccurrence.model_validate(item) for item in analysis.motion_events)
    observations = tuple(
        MicroObservation.model_validate(item) for item in analysis.micro_observations
    )
    walk_ids = {
        facts.walk_id,
        receipt.walk_id,
        *(event.walk_id for event in events),
        *(item.walk_id for item in observations),
    }
    if walk_ids != {analysis.walk_id}:
        raise ValueError("저장된 산책 분석 payload의 walk_id가 다릅니다.")
    if (
        facts.record_version != analysis.facts_record_version
        or facts.calculation_version != analysis.calculation_version
        or receipt.receipt_version != analysis.receipt_version
        or analysis.observation_version != MICRO_OBSERVATION_VERSION
        or any(item.generation != analysis.observation_version for item in observations)
    ):
        raise ValueError("저장된 산책 분석 version 컬럼과 payload가 다릅니다.")
    if (
        facts.moving_distance_m != analysis.moving_distance_m
        or facts.moving_s != analysis.moving_s
        or facts.stop_count != analysis.stop_count
    ):
        raise ValueError("저장된 산책 분석 summary 컬럼과 Facts가 다릅니다.")
    if receipt.received_fix_count != analysis.point_count:
        raise ValueError("저장된 point_count와 MeasurementReceipt가 다릅니다.")
    if analysis.terminal_client_seq != (analysis.point_count - 1 if analysis.point_count else None):
        raise ValueError("저장된 finalize manifest가 0-based sequence 계약과 다릅니다.")
    return DecodedWalkAnalysis(
        facts=facts,
        measurement_receipt=receipt,
        motion_events=events,
        micro_observations=observations,
    )


# Historical imports remain available without loading artifact code for analysis readers.
_COMPAT = {
    "CELLOPHANE_SHEET_SCHEMA_VERSION": "cellophane",
    "CELLOPHANE_CELL_COLUMNS": "cellophane",
    "encode_cellophane": "cellophane",
    "decode_cellophane": "cellophane",
    "decode_stored_cellophane": "cellophane",
    "cellophane_sheet_fingerprint": "cellophane",
    "build_analysis_models": "api",
}


def __getattr__(name):
    if name not in _COMPAT:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f"daengs_backend.services.walk_artifacts.{_COMPAT[name]}"), name)
    globals()[name] = value
    return value
