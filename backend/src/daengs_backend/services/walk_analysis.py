"""버전된 산책 계산 결과를 DB 모델과 canonical JSONB로 옮긴다.

트랜잭션과 상태 변경은 후속 finalize application service의 책임이다. 이 모듈은 검증된
입력과 순수 계산 결과가 어떤 불변 DB 행이 되는지만 결정한다.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from daengs_backend.models.walk import WalkAnalysis, WalkCellophaneSheet
from daengs_backend.services.walk_finalize import PreparedWalkEvidence
from daengs_walk import Cellophane, WalkEvidenceBundle
from daengs_walk.contracts import (
    MICRO_OBSERVATION_VERSION,
    CanonicalWalkFacts,
    MeasurementReceipt,
    MicroObservation,
    MotionEventOccurrence,
)

CELLOPHANE_SHEET_SCHEMA_VERSION = 1
CELLOPHANE_CELL_COLUMNS = ("q", "r", "occupancy_s", "peak")
_SHEET_KEYS = {"v", "walk_id", "at", "paint", "cols", "cell_count", "cells"}
_PAINT_KEYS = {
    "paint_version",
    "grid_version",
    "radius_u",
    "profile",
    "profile_fp",
    "sample_step_m",
    "paint_fp",
}


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


def build_analysis_models(
    prepared: PreparedWalkEvidence,
    evidence: WalkEvidenceBundle,
    sheet: Cellophane,
) -> WalkAnalysis:
    """한 finalize 계산 결과를 아직 session에 붙이지 않은 ORM graph로 만든다."""

    facts = evidence.facts
    if evidence.receipt.walk_id != facts.walk_id or sheet.walk_id != facts.walk_id:
        raise ValueError("analysis 결과의 walk_id가 서로 다릅니다.")
    if evidence.receipt.received_fix_count != prepared.point_count:
        raise ValueError("measurement receipt가 finalize 입력 점 개수와 다릅니다.")
    if sheet.at != facts.started_at:
        raise ValueError("Cellophane 시각이 canonical 산책 시작 시각과 다릅니다.")

    payloads = analysis_payloads(evidence)
    sheet_payload = encode_cellophane(sheet)
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
    analysis.cellophane_sheets.append(
        WalkCellophaneSheet(
            paint_fp=sheet.paint_fp,
            sheet_schema_version=CELLOPHANE_SHEET_SCHEMA_VERSION,
            paint_version=sheet.paint_version,
            grid_version=sheet.grid_version,
            radius_u=sheet.radius_u,
            profile=sheet.profile,
            profile_fp=sheet.profile_fp,
            sample_step_m=sheet.sample_step_m,
            cell_count=len(sheet.occupancy),
            sheet_fingerprint=cellophane_sheet_fingerprint(sheet_payload),
            payload=sheet_payload,
        )
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


def encode_cellophane(sheet: Cellophane) -> dict[str, Any]:
    """tuple-key 셀 맵을 q/r 순으로 고정한 compact JSONB v1으로 만든다."""

    cells = [
        [q, r, sheet.occupancy[(q, r)], sheet.peak[(q, r)]]
        for q, r in sorted(sheet.occupancy)
    ]
    return {
        "v": CELLOPHANE_SHEET_SCHEMA_VERSION,
        "walk_id": str(sheet.walk_id),
        "at": _utc_text(sheet.at),
        "paint": {
            "paint_version": sheet.paint_version,
            "grid_version": sheet.grid_version,
            "radius_u": sheet.radius_u,
            "profile": sheet.profile,
            "profile_fp": sheet.profile_fp,
            "sample_step_m": sheet.sample_step_m,
            "paint_fp": sheet.paint_fp,
        },
        "cols": list(CELLOPHANE_CELL_COLUMNS),
        "cell_count": len(cells),
        "cells": cells,
    }


def decode_cellophane(
    payload: Mapping[str, Any],
    *,
    expected_fingerprint: str | None = None,
) -> Cellophane:
    """저장 JSONB를 검증해 계산 계약으로 되돌린다."""

    if set(payload) != _SHEET_KEYS:
        raise ValueError("Cellophane sheet key 계약이 다릅니다.")
    if payload["v"] != CELLOPHANE_SHEET_SCHEMA_VERSION:
        raise ValueError(f"모르는 Cellophane sheet 형식입니다: v={payload['v']!r}")
    if payload["cols"] != list(CELLOPHANE_CELL_COLUMNS):
        raise ValueError("Cellophane cell column 계약이 다릅니다.")
    if expected_fingerprint is not None:
        actual = cellophane_sheet_fingerprint(payload)
        if actual != expected_fingerprint:
            raise ValueError("Cellophane sheet fingerprint가 payload와 다릅니다.")

    paint = payload["paint"]
    if not isinstance(paint, Mapping) or set(paint) != _PAINT_KEYS:
        raise ValueError("Cellophane paint 계약이 다릅니다.")
    raw_cells = payload["cells"]
    if not isinstance(raw_cells, list):
        raise TypeError("Cellophane cells는 배열이어야 합니다.")

    occupancy: dict[tuple[int, int], float] = {}
    peak: dict[tuple[int, int], float] = {}
    canonical_rows: list[list[int | float]] = []
    for raw in raw_cells:
        if not isinstance(raw, list) or len(raw) != len(CELLOPHANE_CELL_COLUMNS):
            raise ValueError("Cellophane cell 행의 길이가 다릅니다.")
        q, r, amount, strength = raw
        if (
            isinstance(q, bool)
            or isinstance(r, bool)
            or not isinstance(q, int)
            or not isinstance(r, int)
        ):
            raise TypeError("Cellophane q/r은 정수여야 합니다.")
        cell = (q, r)
        if cell in occupancy:
            raise ValueError("Cellophane에 중복 cell이 있습니다.")
        occupancy[cell] = float(amount)
        peak[cell] = float(strength)
        canonical_rows.append([q, r, float(amount), float(strength)])

    if canonical_rows != sorted(canonical_rows, key=lambda row: (row[0], row[1])):
        raise ValueError("Cellophane cells는 q/r 순이어야 합니다.")
    if payload["cell_count"] != len(canonical_rows):
        raise ValueError("Cellophane cell_count가 payload 길이와 다릅니다.")

    return Cellophane(
        walk_id=uuid.UUID(str(payload["walk_id"])),
        at=datetime.fromisoformat(str(payload["at"])),
        radius_u=float(paint["radius_u"]),
        profile=str(paint["profile"]),
        occupancy=occupancy,
        peak=peak,
        paint_version=int(paint["paint_version"]),
        grid_version=str(paint["grid_version"]),
        profile_fp=str(paint["profile_fp"]),
        sample_step_m=float(paint["sample_step_m"]),
        paint_fp=str(paint["paint_fp"]),
    )


def decode_stored_cellophane(stored: WalkCellophaneSheet) -> Cellophane:
    """검색용 metadata 컬럼과 sheet payload가 같은 계산물을 가리키는지 확인한다."""

    sheet = decode_cellophane(
        stored.payload,
        expected_fingerprint=stored.sheet_fingerprint,
    )
    if stored.sheet_schema_version != CELLOPHANE_SHEET_SCHEMA_VERSION:
        raise ValueError("저장된 Cellophane sheet schema version을 읽을 수 없습니다.")
    if (
        stored.paint_version != sheet.paint_version
        or stored.grid_version != sheet.grid_version
        or stored.radius_u != sheet.radius_u
        or stored.profile != sheet.profile
        or stored.profile_fp != sheet.profile_fp
        or stored.sample_step_m != sheet.sample_step_m
        or stored.paint_fp != sheet.paint_fp
        or stored.cell_count != len(sheet.occupancy)
    ):
        raise ValueError("저장된 Cellophane metadata 컬럼과 payload가 다릅니다.")
    return sheet


def cellophane_sheet_fingerprint(payload: Mapping[str, Any]) -> str:
    """정렬 key·공백 없는 JSON으로 compact sheet 전체의 SHA-256을 계산한다."""

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Cellophane 시각에는 timezone이 필요합니다.")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
