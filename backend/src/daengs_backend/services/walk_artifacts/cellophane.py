"""Cellophane v1 codec and ORM adapter. No measurement analysis construction."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from daengs_backend.models.walk import WalkCellophaneSheet
from daengs_walk.cellophane import Cellophane

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


def build_cellophane_model(sheet: Cellophane) -> WalkCellophaneSheet:
    sheet_payload = encode_cellophane(sheet)
    return WalkCellophaneSheet(
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


def encode_cellophane(sheet: Cellophane) -> dict[str, Any]:
    """tuple-key 셀 맵을 q/r 순으로 고정한 compact JSONB v1으로 만든다."""

    cells = [
        [q, r, sheet.occupancy[(q, r)], sheet.peak[(q, r)]] for q, r in sorted(sheet.occupancy)
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
