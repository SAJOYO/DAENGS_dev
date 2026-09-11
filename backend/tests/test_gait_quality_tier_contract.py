"""엔진 → DB `quality_tier` 어휘 계약 + 커밋 실패가 좀비를 만들지 않는지.

2026-09-09 에 실제로 난 사고: 엔진은 `good/ok/low` 세 단계를 내는데 컬럼 CHECK 는
(good, low) 라 `ok`(유효 프레임 20~80) 인 영상의 DONE 커밋이 CheckViolation 으로 죽고,
행이 **PROCESSING 으로 영원히** 남았습니다(47.mp4, 두 번 연속). CHECK 는 D-043 때 엔진
코드가 아니라 짐작으로 적혔고, verify 도 같은 짐작을 베껴 "검증됨" 처럼 보였습니다.

그래서 이 파일은 **사람이 눈으로 맞추던 계약을 기계가 읽어 대조**합니다 — 두 엔진의
실제 소스, 실제 SQL, ORM 상수, 서비스 상수를 전부 읽습니다. 누가 어느 한쪽에 등급을
더하거나 빼면 여기서 빨간 줄이 납니다.
"""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from daengs_backend.models.gait_record import GaitRecord
from daengs_backend.services import gait as gait_service

REPO = Path(__file__).resolve().parents[2]
INIT_SQL = REPO / "db" / "init" / "07_gait_records.sql"
MIGRATION_SQL = REPO / "db" / "migrations" / "2026-09-09_gait_quality_tier_ok.sql"
# 5B 부터 두 엔진 다 `daengs_gait.quality_gate.check_quality` 하나를 씁니다 (5C 공통 계산).
# 그래서 tier 어휘의 정본은 파일 하나이고, 아래 `test_both_engines_call_the_shared_quality_gate`
# 가 "두 엔진의 분석 경로가 실제로 그 함수를 부른다" 는 사실을 소스에서 잽니다.
QUALITY_GATE = REPO / "backend" / "src" / "daengs_gait" / "quality_gate.py"
ENGINE_SOURCES = {"shared": QUALITY_GATE}
ENGINE_ANALYZE_SOURCES = {
    "legacy": REPO / "backend" / "src" / "daengs_gait" / "pipeline.py",
    "v4": REPO / "backend" / "src" / "daengs_gait" / "inference" / "analyze.py",
}

_CHECK_RE = re.compile(r"CHECK\s*\(\s*quality_tier\s+IN\s*\(([^)]*)\)\s*\)")
_TIER_ASSIGN_RE = re.compile(r'\btier\s*=\s*"([a-z]+)"')


def _check_values(sql_text: str) -> set[str]:
    m = _CHECK_RE.search(sql_text)
    assert m, "quality_tier CHECK 를 못 찾음"
    return {v.strip().strip("'") for v in m.group(1).split(",")}


def _emitted_tiers(source: Path) -> set[str]:
    """엔진 소스에서 `tier = "..."` 대입 전부. 문서/주석이 아니라 코드를 읽습니다."""
    text = source.read_text(encoding="utf-8")
    found = set(_TIER_ASSIGN_RE.findall(text))
    assert found, f"{source.name} 에서 tier 대입을 못 읽음 — 정규식이 낡았을 수 있음"
    return found


# ── 계약: 엔진이 낼 수 있는 값 전부가 DB 에 들어간다 ─────────────────────────────
@pytest.mark.parametrize("engine", sorted(ENGINE_SOURCES))
def test_every_tier_the_engine_can_emit_is_allowed_by_the_check(engine: str) -> None:
    source = ENGINE_SOURCES[engine]
    if not source.exists():
        pytest.skip(f"{engine} 엔진 소스가 이 체크아웃에 없음: {source}")
    emitted = _emitted_tiers(source)
    allowed = _check_values(INIT_SQL.read_text(encoding="utf-8"))
    assert emitted <= allowed, (
        f"{engine} 엔진이 내는 {sorted(emitted - allowed)} 가 CHECK {sorted(allowed)} 밖 — "
        "그 구간 영상의 DONE 커밋이 죽고 행이 PROCESSING 으로 남습니다"
    )


def test_shared_quality_gate_emits_exactly_the_three_tiers() -> None:
    assert _emitted_tiers(QUALITY_GATE) == {"good", "ok", "low"}


@pytest.mark.parametrize("engine", sorted(ENGINE_ANALYZE_SOURCES))
def test_both_engines_call_the_shared_quality_gate(engine: str) -> None:
    """legacy 와 v4 가 같은 어휘를 쓰는 이유는 같은 함수를 부르기 때문입니다 (5B). 한쪽이
    자기 판정을 다시 만들면 여기서 잡힙니다."""
    text = ENGINE_ANALYZE_SOURCES[engine].read_text(encoding="utf-8")
    assert re.search(r"from daengs_gait\.quality_gate import .*\bcheck_quality\b", text), (
        f"{engine} 분석 경로가 daengs_gait.quality_gate.check_quality 를 import 하지 않음"
    )
    assert not _TIER_ASSIGN_RE.search(text), f"{engine} 분석 경로가 tier 를 직접 정함"


def test_init_sql_migration_orm_and_service_constant_all_agree() -> None:
    """같은 사실이 네 곳에 적혀 있습니다 — 전부 같아야 합니다."""
    init_set = _check_values(INIT_SQL.read_text(encoding="utf-8"))
    mig_set = _check_values(MIGRATION_SQL.read_text(encoding="utf-8"))
    orm = next(
        c for c in GaitRecord.__table__.constraints if c.name == "gait_records_quality_tier_check"
    )
    orm_set = _check_values(f"CHECK ({orm.sqltext.text})")
    assert init_set == mig_set == orm_set == set(gait_service.DB_QUALITY_TIERS), {
        "db/init": sorted(init_set),
        "migration": sorted(mig_set),
        "orm": sorted(orm_set),
        "service": sorted(gait_service.DB_QUALITY_TIERS),
    }
    assert init_set == {"good", "ok", "low"}


# ── 어댑터: 변환 없이 통과, CHECK 밖만 None ────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("good", "good"), ("ok", "ok"), ("low", "low"), (None, None), ("weird", None)],
)
def test_db_quality_tier_passes_engine_values_through(raw, expected) -> None:
    assert gait_service._db_quality_tier(raw) == expected


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
    """지정한 n번째 커밋만 터뜨리는 세션.

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


def _wire(monkeypatch: pytest.MonkeyPatch, session: _Session, storage: _Storage, result: dict):
    @asynccontextmanager
    async def worker_session():
        yield session

    from daengs_backend.core import database

    monkeypatch.setattr(database, "worker_session", worker_session)
    monkeypatch.setattr(gait_service, "get_storage", lambda: storage)
    monkeypatch.setattr(gait_service, "_analyze_from_storage", lambda key: result)


async def test_commit_failure_marks_failed_instead_of_leaving_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _Record()
    # 커밋 순서: ① PROCESSING 전이 ② DONE(여기서 터짐) ③ FAILED 표시
    session = _Session(record, fail_on_commit_no=2)
    storage = _Storage()
    _wire(
        monkeypatch,
        session,
        storage,
        {
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


async def test_ok_tier_is_stored_verbatim_in_column_and_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정상 경로: `ok` 가 컬럼에도 JSONB 에도 그대로 — 앱이 '보통' 문장을 그릴 수 있게."""
    record = _Record()
    session = _Session(record, fail_on_commit_no=0)  # 아무 커밋도 안 터짐
    storage = _Storage()
    _wire(
        monkeypatch,
        session,
        storage,
        {
            "quality": {"status": "ok", "quality_tier": "ok", "n_frames_gait_usable": 59},
            "features": {"summary_for_ui": {"L_Hip": {}}, "internal_feature_vector": {}},
            "gait_filter_version": "v5",
            "video_meta": {"resolution": "1080x1920"},
            "_overlay_bytes": None,
        },
    )

    await gait_service._run_analysis(record.id)

    assert record.status == "DONE"
    assert record.quality_tier == "ok"
    assert record.quality["quality_tier"] == "ok"
