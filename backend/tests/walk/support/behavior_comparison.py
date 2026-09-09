"""행동 비교 API와 앱 계약 예제에 쓰는 봉인 원판·현재 행동 기록."""

import uuid
from datetime import UTC, datetime

from daengs_backend.models import WalkCellophaneSheet
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_v2 import WalkEntryPin
from daengs_backend.repositories.walk_behavior_comparison import BehaviorEntryRow
from daengs_backend.repositories.walk_spatial_diary import SpatialDiaryIndexRow
from daengs_backend.services.walk_analysis import cellophane_sheet_fingerprint, encode_cellophane
from daengs_walk.capsule import ContextStatus, TrailContextSnapshot
from daengs_walk.cellophane import CANONICAL_PAINT_SPEC, Cellophane

OWNER = uuid.UUID(int=1)
PET = uuid.UUID(int=2)
AT = datetime(2026, 9, 1, 15, 30, tzinfo=UTC)  # KST 9/2


def capsule(number: int, occupancy=None, *, at=AT, weather_code=61):
    walk_id = uuid.UUID(int=number)
    analysis_id = uuid.UUID(int=100 + number)
    paint = CANONICAL_PAINT_SPEC
    context = TrailContextSnapshot(
        walk_id=walk_id,
        status=ContextStatus.PARTIAL,
        walked_at=at,
        captured_at=at,
        provider="fixture",
        weather_code=weather_code,
    )
    sheet = Cellophane(
        walk_id=walk_id,
        at=at,
        radius_u=paint.radius_u,
        profile=paint.profile_name,
        occupancy={} if occupancy is None else occupancy,
        peak={} if occupancy is None else {cell: 1.0 for cell in occupancy},
        paint_version=paint.paint_version,
        grid_version=paint.grid_version,
        profile_fp=paint.profile_fp,
        sample_step_m=paint.sample_step_m,
        paint_fp=paint.fingerprint,
    )
    payload = encode_cellophane(sheet)
    stored = WalkCellophaneSheet(
        analysis_id=analysis_id,
        paint_fp=paint.fingerprint,
        sheet_schema_version=1,
        paint_version=paint.paint_version,
        grid_version=paint.grid_version,
        radius_u=paint.radius_u,
        profile=paint.profile_name,
        profile_fp=paint.profile_fp,
        sample_step_m=paint.sample_step_m,
        cell_count=len(sheet.occupancy),
        sheet_fingerprint=cellophane_sheet_fingerprint(payload),
        payload=payload,
    )
    index = SpatialDiaryIndexRow(
        analysis_id=analysis_id,
        walk_id=walk_id,
        started_at=at,
        capsule_version=1,
        context_version=context.context_version,
        trail_context=context.model_dump(mode="json"),
        sheet_schema_version=1,
        paint_version=paint.paint_version,
        grid_version=paint.grid_version,
        radius_u=paint.radius_u,
        profile=paint.profile_name,
        profile_fp=paint.profile_fp,
        sample_step_m=paint.sample_step_m,
        paint_fp=paint.fingerprint,
        cell_count=len(sheet.occupancy),
    )
    return index, stored


def entry(number: int, walk_id: uuid.UUID, *, pet_id=PET, located=True, at=AT):
    row = WalkEntry(
        walk_id=walk_id,
        id=uuid.UUID(int=200 + number),
        revision=1,
        mutation_id=uuid.UUID(int=300 + number),
        payload={
            "kind": "behavior",
            "behavior_code": "sniffing",
            "pet_id": str(pet_id) if pet_id else None,
            "recorded_at": at.isoformat(),
            "location": {
                "lat": 37.5,
                "lng": 127.0,
                "captured_at": at.isoformat(),
                "accuracy_m": 5.0,
            }
            if located
            else None,
        },
    )
    sidecar = (
        None
        if located
        else WalkEntryPin(
            walk_id=walk_id,
            entry_id=row.id,
            pin_revision=2,
            payload={
                "resolution_id": str(uuid.UUID(int=400 + number)),
                "state": "unlocated",
                "method": "none",
                "target_at": at.isoformat(),
                "point": None,
                "computed_at": at.isoformat(),
                "resolve_by": at.isoformat(),
                "policy_version": "action-pin-v1",
                "algorithm_version": "fixture",
                "source_refs": [],
                "uncertainty_m": None,
                "uncertainty_basis": "unknown",
                "reason": "no_evidence",
            },
        )
    )
    return BehaviorEntryRow(row, sidecar, uuid.UUID(int=500 + walk_id.int))


def example():
    pairs = [capsule(10, {(0, 0): 3.0, (1, 0): 1.0}), capsule(11, {(1, 0): 40.0}), capsule(12)]
    rows = [entry(1, pairs[0][0].walk_id), entry(2, pairs[0][0].walk_id, located=False)]
    return pairs, rows
