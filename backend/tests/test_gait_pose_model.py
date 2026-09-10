"""`pose_model` — 엔진 출력 계약과 기록의 관절 정의 식별 (D-063, 1단계).

세 가지를 지킵니다:

1. **계약** — 두 엔진의 실제 출력이 `daengs_gait.contract` 를 만족한다. v4 는 로컬 gait_v4
   venv 로 실제 영상을 돌린 record.json(픽스처), legacy 는 daengback DB 에 저장된 행 + 이
   엔진이 record 에 넣는 상수. 소스도 정규식으로 읽어 "키를 넣는다" 는 사실을 코드에서 잰다.
2. **판별 규칙** — 백필 SQL 의 CASE 와 `classify_joint_keys` 가 같은 규칙이고, 두 SQL 파일과
   레지스트리의 관절 집합이 한 글자도 안 다르다. 빈 집합은 어느 모델도 아니다.
3. **저장** — 워커가 DONE 이면 항상 `pose_model` 을 넣고(unavailable 이어도), 엔진 결과가
   없는 실패는 NULL 로 둔다.

SQL 백필 자체는 `tools/check_migration_verification.py sql`(CI 의 Postgres) 이 돕니다 —
여기서는 그 SQL 의 **텍스트**를 읽어 파이썬 쪽과 어긋나지 않는지만 봅니다.
"""

from __future__ import annotations

import json
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from daengs_backend.services import gait as gait_service
from daengs_gait import config as gait_config
from daengs_gait import contract

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "gait"
MIGRATION = REPO / "db" / "migrations" / "2026-09-09_gait_records_pose_model.sql"
VERIFY = REPO / "db" / "migrations" / "verify_2026-09-09_gait_records_pose_model.sql"
INIT_SQL = REPO / "db" / "init" / "07_gait_records.sql"
V4_CONFIG = REPO / "backend" / "gait_v4" / "gait_v4" / "config.py"
V4_ANALYZE = REPO / "backend" / "gait_v4" / "gait_v4" / "analyze.py"
LEGACY_PIPELINE = REPO / "backend" / "src" / "daengs_gait" / "pipeline.py"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _stored_row_as_engine_record(stored: dict, pose_model: str) -> dict:
    """DB 행(컬럼 모양) → 엔진 record 모양. `_run_analysis` 가 `features.summary_for_ui` ·
    `features.internal_feature_vector` 를 두 컬럼으로 펼치는 것의 역방향이다."""
    return {
        "pose_model": pose_model,
        "quality": stored["quality"],
        "gait_filter_version": stored["gait_filter_version"],
        "video_meta": stored["video_meta"],
        "features": {
            "summary_for_ui": stored["summary_for_ui"],
            "internal_feature_vector": stored["internal_feature_vector"],
        },
        "overlay_video": None,
    }


# ── 1. 계약 ───────────────────────────────────────────────────────────────────
def test_v4_engine_output_satisfies_contract() -> None:
    """실제 v4 출력(로컬 venv, 2026-09-09). `pose_model` 은 엔진이 스스로 넣은 값."""
    record = _fixture("v4_real_engine_output.json")
    assert contract.check_analysis_record(record) == []
    assert record["pose_model"] == contract.POSE_MODEL_V4
    assert set(record["features"]["summary_for_ui"]) <= contract.AP10K_17_JOINTS


def test_v4_unavailable_output_still_carries_pose_model() -> None:
    """품질 미달이어도 엔진은 돌았으니 모델을 안다 — 저장 규칙의 근거."""
    record = _fixture("v4_unavailable_engine_output.json")
    assert record["quality"]["status"] == "unavailable"
    assert "features" not in record
    assert record["pose_model"] == contract.POSE_MODEL_V4
    assert contract.check_analysis_record(record) == []


def test_legacy_stored_record_plus_engine_constant_satisfies_contract() -> None:
    """daengback 에 저장된 legacy 행(2026-09-02) + 이 엔진이 record 에 넣는 상수."""
    stored = _fixture("legacy_db_row.json")
    record = _stored_row_as_engine_record(stored, gait_config.POSE_MODEL_ID)
    assert contract.check_analysis_record(record) == []
    assert set(stored["summary_for_ui"]) <= contract.LEGACY_12KP_JOINTS


def test_v4_stored_record_matches_engine_output_shape() -> None:
    """DB 에 저장된 v4 행(2026-09-07)도 같은 계약 — 저장하면서 모양이 바뀌지 않는다."""
    stored = _fixture("v4_db_row.json")
    record = _stored_row_as_engine_record(stored, contract.POSE_MODEL_V4)
    assert contract.check_analysis_record(record) == []


def test_engines_put_pose_model_into_the_record_in_source() -> None:
    """두 엔진 소스가 `pose_model` 키를 넣는다. v4 는 MODEL_ID, legacy 는 POSE_MODEL_ID."""
    v4 = V4_ANALYZE.read_text(encoding="utf-8")
    assert re.search(r'"pose_model"\s*:\s*MODEL_ID', v4), (
        "gait_v4/analyze.py 가 pose_model 을 안 넣음"
    )
    legacy = LEGACY_PIPELINE.read_text(encoding="utf-8")
    assert re.search(r'"pose_model"\s*:\s*POSE_MODEL_ID', legacy), (
        "pipeline.process_video 가 pose_model 을 안 넣음"
    )


def test_registry_ids_match_engine_sources() -> None:
    v4_cfg = V4_CONFIG.read_text(encoding="utf-8")
    m = re.search(r'^MODEL_ID\s*=\s*"([^"]+)"', v4_cfg, re.MULTILINE)
    assert m and m.group(1) == contract.POSE_MODEL_V4
    assert gait_config.POSE_MODEL_ID == contract.POSE_MODEL_LEGACY
    assert set(contract.POSE_MODELS) == {contract.POSE_MODEL_V4, contract.POSE_MODEL_LEGACY}


def test_registry_joint_sets_match_engine_configs() -> None:
    """레지스트리가 낡지 않게 — 관절 목록의 원본은 각 엔진 config 다."""
    assert set(gait_config.KEYPOINT_NAMES) == contract.LEGACY_12KP_JOINTS
    assert contract.POSE_MODELS[contract.POSE_MODEL_LEGACY]["joints"] == list(
        gait_config.KEYPOINT_NAMES
    )
    assert contract.POSE_MODELS[contract.POSE_MODEL_LEGACY]["priority"] == list(
        gait_config.PRIORITY_JOINTS
    )

    v4_cfg = V4_CONFIG.read_text(encoding="utf-8")
    block = re.search(r"AP10K_NAMES\s*=\s*\[(.*?)\]", v4_cfg, re.DOTALL)
    assert block, "gait_v4/config.py 의 AP10K_NAMES 를 못 읽음"
    names = re.findall(r'"([^"]+)"', block.group(1))
    assert set(names) == contract.AP10K_17_JOINTS and len(names) == 17
    assert contract.POSE_MODELS[contract.POSE_MODEL_V4]["joints"] == names
    prio = re.search(r'"priority"\s*:\s*\[(.*?)\]', v4_cfg, re.DOTALL)
    assert (
        prio
        and re.findall(r'"([^"]+)"', prio.group(1))
        == contract.POSE_MODELS[contract.POSE_MODEL_V4]["priority"]
    )


def test_the_two_joint_vocabularies_are_disjoint() -> None:
    """백필의 `n_keys = n_ap10k` 가 "전부 AP-10K" 를 뜻하려면 두 집합이 서로소여야 한다."""
    assert not (contract.AP10K_17_JOINTS & contract.LEGACY_12KP_JOINTS)


def test_check_reports_problems_instead_of_raising() -> None:
    assert contract.check_analysis_record("nope")
    problems = contract.check_analysis_record({"quality": {"status": "ok"}})
    assert any("pose_model" in p for p in problems)
    assert any("quality_tier" in p for p in problems)
    # 다른 모델의 관절이 요약에 섞이면 잡는다.
    bad = _fixture("v4_real_engine_output.json")
    bad["features"]["summary_for_ui"]["Iliac crest"] = {"x_range": 0.1, "y_range": 0.1}
    assert any("관절이 아닌 키" in p for p in contract.check_analysis_record(bad))


# ── 2. 판별 규칙 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (["L_Hip", "L_Knee", "R_B_Paw"], contract.POSE_MODEL_V4),
        (["Iliac crest", "Femorotibial joint"], contract.POSE_MODEL_LEGACY),
        (["L_Hip"], contract.POSE_MODEL_V4),
        ([], None),  # 빈 집합 — all() 함정
        (["L_Hip", "Iliac crest"], None),  # 두 체계가 섞임
        (["L_Hip", "Tail_tip"], None),  # 미지의 키
        (["Tail_tip"], None),
    ],
)
def test_classify_joint_keys(keys, expected) -> None:
    assert contract.classify_joint_keys(keys) == expected


def _in_lists(sql: str) -> list[set[str]]:
    """SQL 의 `key IN ('a','b',…)` 목록들을 집합으로."""
    out = []
    for m in re.finditer(r"joint_keys\.key\s+IN\s*\((.*?)\)\)", sql, re.DOTALL):
        out.append(set(re.findall(r"'([^']+)'", m.group(1))))
    return out


def test_backfill_sql_joint_lists_match_registry() -> None:
    """마이그레이션·verify 의 IN 목록 == 레지스트리. 한 곳만 고치면 여기서 빨간 줄."""
    for path in (MIGRATION, VERIFY):
        lists = _in_lists(path.read_text(encoding="utf-8"))
        assert lists, f"{path.name} 에서 IN 목록을 못 읽음"
        for joints in lists:
            assert joints in (contract.AP10K_17_JOINTS, contract.LEGACY_12KP_JOINTS), (
                f"{path.name} 의 IN 목록이 레지스트리와 다름: {sorted(joints)}"
            )
        assert contract.AP10K_17_JOINTS in lists, f"{path.name} 에 AP-10K 목록이 없음"
        assert contract.LEGACY_12KP_JOINTS in lists, f"{path.name} 에 legacy 목록이 없음"


def test_backfill_sql_requires_at_least_one_key_and_only_fills_null() -> None:
    """빈 객체가 특정 모델로 분류되지 않고, 워커가 쓴 값은 덮지 않는다."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS pose_model TEXT" in sql
    assert "WHERE g.pose_model IS NULL" in sql
    assert sql.count("n_keys >= 1") >= 4  # CASE 두 가지 + WHERE 두 가지
    # 적용 전 SELECT 는 컬럼을 참조하지 않는다 (첫 적용에는 컬럼이 없다) — BEGIN; 과
    # ALTER TABLE 문 사이의 실제 문장에 pose_model 이 없어야 한다. 주석은 뺀다.
    statements = sql.split("BEGIN;", 1)[1].split("ALTER TABLE gait_records ADD COLUMN", 1)[0]
    code_only = "\n".join(
        line for line in statements.splitlines() if not line.lstrip().startswith("--")
    )
    assert "pose_model" not in code_only
    assert "count(*) AS rows_before" in code_only
    for value in (contract.POSE_MODEL_V4, contract.POSE_MODEL_LEGACY):
        assert f"'{value}'" in sql


def test_verify_sql_does_not_force_null_on_records_without_joint_keys() -> None:
    """검증 규칙 ≠ 백필 규칙: 관절 키가 없는 행(새 분석의 unavailable)은 값이 있어도 통과."""
    sql = VERIFY.read_text(encoding="utf-8")
    assert "RAISE EXCEPTION" in sql
    for value in (contract.POSE_MODEL_V4, contract.POSE_MODEL_LEGACY):
        assert f"'{value}'" in sql
    # 값 단언은 전부 "관절 키가 1개 이상" 조건 아래에 있다 — 빈 집합 행에 NULL 을 강제하는 절이 없다.
    value_asserts = [
        m for m in re.finditer(r"WHERE n_keys >= 1 AND n_keys = n_(ap10k|legacy)", sql)
    ]
    assert len(value_asserts) == 2
    assert "n_keys = 0" not in sql and "n_keys < 1" not in sql
    # 허용 ID 밖의 값은 잡는다.
    assert "NOT IN ('rtmpose_ap10k_ssd', 'yolov8_12kp_best')" in sql


def test_init_sql_and_orm_have_the_column() -> None:
    from daengs_backend.models.gait_record import GaitRecord

    assert re.search(r"^\s*pose_model TEXT,", INIT_SQL.read_text(encoding="utf-8"), re.MULTILINE)
    column = GaitRecord.__table__.columns["pose_model"]
    assert column.nullable and str(column.type).upper() == "TEXT"


# ── 3. 저장 ────────────────────────────────────────────────────────────────────
class _Record:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.pet_id = uuid.uuid4()
        self.status = "UPLOADED"
        self.deleted_at = None
        self.original_storage_key = "gait/x/original/a.mp4"
        self.overlay_storage_key = None
        self.failure_reason = None
        self.quality_status = self.quality_tier = self.pose_model = None
        self.quality = self.summary_for_ui = self.internal_feature_vector = None
        self.gait_filter_version = self.video_meta = None


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        return self._v


class _Session:
    def __init__(self, record: _Record) -> None:
        self.record = record
        self.commits = 0

    async def execute(self, stmt):
        return _Result(self.record)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


class _Storage:
    def __init__(self, fail_write: bool = False) -> None:
        self.fail_write = fail_write
        self.deleted: list[str] = []

    def write(self, key, data):
        if self.fail_write:
            raise OSError("disk full")

    def delete(self, key):
        self.deleted.append(key)


def _wire(monkeypatch: pytest.MonkeyPatch, session: _Session, storage: _Storage, analyze):
    @asynccontextmanager
    async def worker_session():
        yield session

    from daengs_backend.core import database

    monkeypatch.setattr(database, "worker_session", worker_session)
    monkeypatch.setattr(gait_service, "get_storage", lambda: storage)
    monkeypatch.setattr(gait_service, "_analyze_from_storage", analyze)


async def test_done_record_stores_pose_model_from_engine_output(monkeypatch) -> None:
    record = _Record()
    session = _Session(record)
    result = _fixture("v4_real_engine_output.json")
    result["_overlay_bytes"] = None
    _wire(monkeypatch, session, _Storage(), lambda key: result)

    await gait_service._run_analysis(record.id)

    assert record.status == "DONE"
    assert record.pose_model == contract.POSE_MODEL_V4
    assert record.quality_tier == "good"


async def test_unavailable_record_still_stores_pose_model(monkeypatch) -> None:
    """엔진은 돌았다 — summary 가 비어도 어떤 모델이었는지는 저장한다."""
    record = _Record()
    session = _Session(record)
    result = _fixture("v4_unavailable_engine_output.json")
    result["_overlay_bytes"] = None
    _wire(monkeypatch, session, _Storage(), lambda key: result)

    await gait_service._run_analysis(record.id)

    assert record.status == "DONE"
    assert record.quality_status == "unavailable"
    assert record.summary_for_ui is None
    assert record.pose_model == contract.POSE_MODEL_V4


async def test_legacy_record_stores_legacy_id(monkeypatch) -> None:
    record = _Record()
    session = _Session(record)
    result = _stored_row_as_engine_record(_fixture("legacy_db_row.json"), gait_config.POSE_MODEL_ID)
    result["_overlay_bytes"] = None
    _wire(monkeypatch, session, _Storage(), lambda key: result)

    await gait_service._run_analysis(record.id)

    assert record.status == "DONE"
    assert record.pose_model == contract.POSE_MODEL_LEGACY


async def test_failure_before_engine_result_leaves_pose_model_null(monkeypatch) -> None:
    """엔진 결과가 없는 실패 — 무엇을 돌렸는지 모르니 NULL 이 맞다."""
    record = _Record()
    session = _Session(record)

    def boom(key):
        raise RuntimeError("gait_v4 분석 실패 (exit 1): traceback…")

    _wire(monkeypatch, session, _Storage(), boom)

    await gait_service._run_analysis(record.id)

    assert record.status == "FAILED"
    assert record.pose_model is None


async def test_overlay_upload_failure_keeps_pose_model(monkeypatch) -> None:
    """결과는 있는데 overlay 저장이 실패한 FAILED — 모델은 안다."""
    record = _Record()
    session = _Session(record)
    result = _fixture("v4_real_engine_output.json")
    result["_overlay_bytes"] = b"ov"
    _wire(monkeypatch, session, _Storage(fail_write=True), lambda key: result)

    await gait_service._run_analysis(record.id)

    assert record.status == "FAILED"
    assert record.pose_model == contract.POSE_MODEL_V4


async def test_contract_violation_only_warns(monkeypatch, caplog) -> None:
    """계약 위반은 로그 경고일 뿐 — DONE 을 막지 않는다 (막으면 좀비/FAILED 를 새로 만든다)."""
    record = _Record()
    session = _Session(record)
    result = _fixture("v4_real_engine_output.json")
    result["pose_model"] = "some_future_model"
    result["_overlay_bytes"] = None
    _wire(monkeypatch, session, _Storage(), lambda key: result)

    with caplog.at_level("WARNING", logger="daengs_backend.services.gait"):
        await gait_service._run_analysis(record.id)

    assert record.status == "DONE"
    assert record.pose_model == "some_future_model"  # 그대로 저장 — CHECK 없음
    assert any("계약과 어긋남" in m for m in caplog.messages)
