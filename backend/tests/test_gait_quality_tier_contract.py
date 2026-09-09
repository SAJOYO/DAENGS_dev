"""엔진 → DB `quality_tier` 어휘 계약 + 커밋 실패가 좀비를 만들지 않는지.

2026-09-09 에 실제로 난 사고: v4 가 `quality_tier="ok"` 를 냈는데 컬럼 CHECK 는
(good, low) 라 DONE 커밋이 CheckViolation 으로 죽고, 행이 **PROCESSING 으로 영원히**
남았습니다(47.mp4, 두 번 연속). 이 파일은 그 두 구멍을 각각 막습니다.
"""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from daengs_backend.services import gait as gait_service

REPO = Path(__file__).resolve().parents[2]


# ── 어휘 변환 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("good", "good"), ("low", "low"), ("ok", "low"), (None, None), ("weird", None)],
)
def test_db_quality_tier_maps_into_check_vocabulary(raw, expected) -> None:
    assert gait_service._db_quality_tier(raw) == expected


def test_every_tier_v4_can_emit_survives_the_db_check() -> None:
    """v4 `quality.py` 가 내는 tier 문자열 전부가 CHECK 를 지나야 합니다.

    두 파일을 **실제로 읽어** 대조합니다 — 누가 v4 에 tier 를 하나 더 붙이거나 SQL 의
    CHECK 를 바꾸면 여기서 빨간 줄이 납니다. 사람이 눈으로 맞추던 것을 기계가 봅니다.
    """
    sql = (REPO / "db" / "init" / "07_gait_records.sql").read_text(encoding="utf-8")
    m = re.search(r"quality_tier\s+VARCHAR\(\d+\)\s*CHECK\s*\(quality_tier IN \(([^)]*)\)\)", sql)
    assert m, "07_gait_records.sql 에서 quality_tier CHECK 를 못 찾음"
    allowed = {v.strip().strip("'") for v in m.group(1).split(",")}
    assert allowed == set(gait_service.DB_QUALITY_TIERS), "코드 상수와 SQL CHECK 가 어긋남"

    quality_py = REPO / "backend" / "gait_v4" / "gait_v4" / "quality.py"
    if not quality_py.exists():
        pytest.skip("backend/gait_v4 가 이 체크아웃에 없음")
    emitted = set(re.findall(r'tier\s*=\s*"([a-z]+)"', quality_py.read_text(encoding="utf-8")))
    assert emitted, "v4 quality.py 에서 tier 값을 못 읽음"
    for t in emitted:
        mapped = gait_service._db_quality_tier(t)
        assert mapped is None or mapped in allowed, (
            f"v4 tier {t!r} → {mapped!r} 는 CHECK 를 못 지남"
        )
    # 그리고 v4 의 세 단계가 실제로 이 매핑을 지납니다 (ok 가 핵심).
    assert {"good", "ok", "low"} <= emitted


# ── 커밋 실패 → FAILED (좀비 금지) ───────────────────────────────────────────
class _Record:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.pet_id = uuid.uuid4()
        self.status = "UPLOADED"
        self.deleted_at = None
        self.original_storage_key = "gait/x/original/a.mp4"
        self.overlay_storage_key = None
        self.failure_reason = None
        self.quality_status = self.quality_tier = None
        self.quality = self.summary_for_ui = self.internal_feature_vector = None
        self.gait_filter_version = self.video_meta = None


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        return self._v


class _Session:
    """DONE 커밋만 터뜨리는 세션. 그 뒤의 FAILED 커밋은 통과합니다.

    진짜 세션처럼 **커밋된 상태**를 기억합니다 — rollback 은 행을 마지막 커밋 시점으로
    되돌리고(`populate_existing` 재조회가 DB 값을 읽는 것과 같음), 실패한 커밋의 변경은
    버립니다. 이게 없으면 메모리의 `DONE` 이 남아 "PROCESSING 이 아니니 건드리지 않음"
    으로 빠져 실제와 다른 결과가 납니다.
    """

    _FIELDS = ("status", "failure_reason", "quality_tier", "overlay_storage_key")

    def __init__(self, record: _Record, fail_on_commit_no: int) -> None:
        self.record = record
        self.fail_on = fail_on_commit_no
        self.commits = 0
        self.rollbacks = 0
        self._committed = self._snapshot()

    def _snapshot(self) -> dict:
        return {f: getattr(self.record, f) for f in self._FIELDS}

    async def execute(self, stmt):
        return _Result(self.record)

    async def commit(self):
        self.commits += 1
        if self.commits == self.fail_on:
            raise RuntimeError('violates check constraint "gait_records_quality_tier_check"')
        self._committed = self._snapshot()

    async def rollback(self):
        self.rollbacks += 1
        for f, v in self._committed.items():
            setattr(self.record, f, v)


class _Storage:
    def __init__(self) -> None:
        self.written: list[str] = []
        self.deleted: list[str] = []

    def write(self, key, data):
        self.written.append(key)

    def delete(self, key):
        self.deleted.append(key)


async def test_commit_failure_marks_failed_instead_of_leaving_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _Record()
    # 커밋 순서: ① PROCESSING 전이 ② DONE(여기서 터짐) ③ FAILED 표시
    session = _Session(record, fail_on_commit_no=2)
    storage = _Storage()

    @asynccontextmanager
    async def worker_session():
        yield session

    from daengs_backend.core import database

    monkeypatch.setattr(database, "worker_session", worker_session)
    monkeypatch.setattr(gait_service, "get_storage", lambda: storage)
    monkeypatch.setattr(
        gait_service,
        "_analyze_from_storage",
        lambda key: {
            "quality": {"status": "ok", "quality_tier": "ok"},
            "features": {"summary_for_ui": {}, "internal_feature_vector": {}},
            "gait_filter_version": "v5",
            "video_meta": {},
            "_overlay_bytes": b"ov",
        },
    )

    with pytest.raises(RuntimeError, match="quality_tier_check"):
        await gait_service._run_analysis(record.id)

    assert record.status == "FAILED", "커밋 실패 뒤에도 PROCESSING 이면 좀비입니다"
    assert "결과 저장 실패" in (record.failure_reason or "")
    assert "quality_tier_check" in record.failure_reason
    # 올렸던 overlay 는 되걷고, FAILED 커밋(③)까지 이뤄졌습니다.
    assert storage.deleted == storage.written and len(storage.written) == 1
    assert session.commits == 3 and session.rollbacks == 1


async def test_ok_tier_is_stored_as_low_but_kept_raw_in_quality_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정상 경로: v4 의 `ok` 가 컬럼엔 `low`, JSONB 엔 원값으로 들어갑니다."""
    record = _Record()
    session = _Session(record, fail_on_commit_no=0)  # 아무 커밋도 안 터짐
    storage = _Storage()

    @asynccontextmanager
    async def worker_session():
        yield session

    from daengs_backend.core import database

    monkeypatch.setattr(database, "worker_session", worker_session)
    monkeypatch.setattr(gait_service, "get_storage", lambda: storage)
    monkeypatch.setattr(
        gait_service,
        "_analyze_from_storage",
        lambda key: {
            "quality": {"status": "ok", "quality_tier": "ok", "n_frames_gait_usable": 59},
            "features": {"summary_for_ui": {"L_Hip": {}}, "internal_feature_vector": {}},
            "gait_filter_version": "v5",
            "video_meta": {"resolution": "1080x1920"},
            "_overlay_bytes": None,
        },
    )

    await gait_service._run_analysis(record.id)

    assert record.status == "DONE"
    assert record.quality_tier == "low"
    assert record.quality["quality_tier"] == "ok"
