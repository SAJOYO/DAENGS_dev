import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from daengs_walk import WalkEvidencePoint


@pytest.fixture
def walk_id() -> uuid.UUID:
    return uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def started_at() -> datetime:
    return datetime(2026, 9, 1, 9, tzinfo=UTC)


@pytest.fixture
def point(started_at: datetime):
    """서울의 한 위도에서 동쪽 거리(m)를 좌표로 바꾸는 점 생성기."""

    def make(
        seconds: float,
        east_m: float,
        *,
        seq: int | None = None,
        accuracy: float | None = 10,
        chain_index: int = 0,
        is_mock: bool = False,
    ) -> WalkEvidencePoint:
        lat = 37.5
        lng = 127 + east_m / (111_320 * math.cos(math.radians(lat)))
        return WalkEvidencePoint(
            client_seq=round(seconds * 1_000) if seq is None else seq,
            chain_index=chain_index,
            at=started_at + timedelta(seconds=seconds),
            lat=lat,
            lng=lng,
            accuracy_m=accuracy,
            is_mock=is_mock,
        )

    return make
