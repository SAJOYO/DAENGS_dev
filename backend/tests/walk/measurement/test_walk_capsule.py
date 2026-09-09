import uuid
from datetime import UTC, datetime, timedelta

import pytest

from daengs_backend.models.walk import WalkAnalysis
from daengs_backend.services.walk_capsule import (
    build_capsule_model,
    decode_capsule_model,
)
from daengs_walk.capsule import (
    ContextStatus,
    TrailContextSnapshot,
    build_walk_capsule,
    select_context_anchor,
)
from daengs_walk.contracts import WalkEvidencePoint
from tests.walk.support.paths import REPO as REPO_ROOT

REPO = REPO_ROOT
WALK_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
STARTED_AT = datetime(2026, 9, 3, 9, tzinfo=UTC)
SEALED_AT = STARTED_AT + timedelta(minutes=30)


def analysis(*, walk_id: uuid.UUID = WALK_ID) -> WalkAnalysis:
    return WalkAnalysis(
        walk_id=walk_id,
        input_fingerprint="sha256:" + "1" * 64,
        point_count=0,
        terminal_client_seq=None,
        facts_record_version=1,
        calculation_version=4,
        receipt_version=1,
        observation_version=1,
        moving_distance_m=0,
        moving_s=0,
        stop_count=0,
        facts={},
        measurement_receipt={},
        motion_events=[],
        micro_observations=[],
    )


def capsule(*, walk_id: uuid.UUID = WALK_ID, with_weather: bool = True):
    return build_walk_capsule(
        walk_id=walk_id,
        facts_record_version=1,
        calculation_version=4,
        receipt_version=1,
        observation_version=1,
        walked_at=STARTED_AT,
        sealed_at=SEALED_AT,
        weather_code=61 if with_weather else None,
        is_day=True if with_weather else None,
        temperature_c=18.5 if with_weather else None,
    )


def test_capsule은_기존_날씨_원자와_관측_능력만_봉인한다() -> None:
    artifacts = capsule()

    assert artifacts.trail_context.status is ContextStatus.PARTIAL
    assert artifacts.trail_context.context_version == 2
    assert artifacts.trail_context.provider == "android_walk_upload_v1"
    assert artifacts.trail_context.weather_code == 61
    assert artifacts.trail_context.temperature_c == 18.5
    assert [(item.name, item.generation) for item in artifacts.manifest.capabilities] == [
        ("low_motion", 1),
        ("gap", 1),
    ]


def test_context_v1은_새_필드가_없는_기존_payload도_계속_읽는다() -> None:
    payload = capsule().trail_context.model_dump(mode="json")
    payload["context_version"] = 1
    payload.pop("precipitation_kind")

    restored = TrailContextSnapshot.model_validate(payload)

    assert restored.context_version == 1
    assert restored.precipitation_kind is None


def test_context_v1은_새_강수_종류를_거짓으로_싣지_않는다() -> None:
    payload = capsule().trail_context.model_dump(mode="json") | {
        "context_version": 1,
        "precipitation_kind": "rain",
    }

    with pytest.raises(ValueError, match="v1"):
        TrailContextSnapshot.model_validate(payload)


def test_context_anchor는_시간상_중앙에_가깝고_순번이_앞선_실제_좌표다() -> None:
    points = (
        WalkEvidencePoint(
            client_seq=0,
            at=STARTED_AT + timedelta(minutes=10),
            lat=37.1,
            lng=127.1,
        ),
        WalkEvidencePoint(
            client_seq=2,
            at=STARTED_AT + timedelta(minutes=20),
            lat=37.3,
            lng=127.3,
        ),
        WalkEvidencePoint(
            client_seq=1,
            at=STARTED_AT + timedelta(minutes=20),
            lat=37.2,
            lng=127.2,
        ),
        WalkEvidencePoint(
            client_seq=3,
            at=STARTED_AT + timedelta(minutes=15),
            lat=38.0,
            lng=128.0,
            is_mock=True,
        ),
    )

    anchor = select_context_anchor(
        points,
        started_at=STARTED_AT,
        ended_at=STARTED_AT + timedelta(minutes=30),
    )

    assert anchor is not None
    assert anchor.client_seq == 0


def test_context_anchor는_실제_좌표가_없으면_조회하지_않는다() -> None:
    point = WalkEvidencePoint(
        client_seq=0,
        at=STARTED_AT,
        lat=37.1,
        lng=127.1,
        is_mock=True,
    )

    assert (
        select_context_anchor(
            (point,),
            started_at=STARTED_AT,
            ended_at=SEALED_AT,
        )
        is None
    )


def test_날씨가_없으면_현재값을_보충하지_않고_unknown으로_남긴다() -> None:
    artifacts = capsule(with_weather=False)

    assert artifacts.trail_context.status is ContextStatus.UNKNOWN
    assert artifacts.trail_context.provider is None
    assert artifacts.trail_context.weather_code is None
    assert artifacts.trail_context.temperature_c is None


def test_capsule_storage_roundtrip은_analysis를_복제하지_않는다() -> None:
    stored_analysis = analysis()
    artifacts = capsule()

    stored_analysis.capsule = build_capsule_model(stored_analysis, artifacts)

    assert stored_analysis.capsule.analysis is stored_analysis
    assert stored_analysis.capsule.trail_context["walk_id"] == str(WALK_ID)
    assert "facts" not in stored_analysis.capsule.trail_context
    assert decode_capsule_model(stored_analysis) == artifacts


def test_다른_analysis의_capsule은_저장하지_않는다() -> None:
    with pytest.raises(ValueError, match="identity"):
        build_capsule_model(analysis(), capsule(walk_id=uuid.uuid4()))


def test_capsule_migration은_기존_분석을_출처와_함께_backfill한다() -> None:
    migration = (REPO / "db/migrations/2026-09-03_walk_capsules.sql").read_text(encoding="utf-8")
    verify = (REPO / "db/migrations/verify_2026-09-03_walk_capsules.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS walk_capsules" in migration
    assert "ON CONFLICT (analysis_id) DO NOTHING" in migration
    assert "legacy_walk_metadata_v1" in migration
    assert "JOIN walks AS walk ON walk.id = analysis.walk_id" in migration
    assert "walk.weather_code BETWEEN 0 AND 99" in migration
    assert "walk.temperature_c BETWEEN -100 AND 100" in migration
    assert "walks_weather_code_range" in migration
    assert "walks_temperature_c_range" in migration
    assert "SET weather_code = NULL" in migration
    assert "SET temperature_c = NULL" in migration
    assert "derived_without_capsule" in verify
    assert "mismatched_context_identity" in verify
    assert "IS DISTINCT FROM" in verify
    assert "pg_input_is_valid" in verify
