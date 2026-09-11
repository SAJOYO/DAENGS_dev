"""옛 legacy 추론 runtime 이 **정말로** 없어졌는가 + 옛 기록은 그대로인가 (D-063 6단계).

6단계가 지운 것은 **"새 legacy 분석을 실행하는 능력" 하나**입니다. 옛 legacy 기록의
조회·판별·비교는 그대로 살아 있어야 합니다 — 둘은 다른 문제입니다. 이 파일이 그 둘을
양쪽에서 잡습니다:

① **참조 0** — `pipeline` · `keypoint_infer` · `crop_assist` · `record_store` ·
   `engines.legacy` · `ultralytics` 가 `backend/src` 와 설정 파일 어디에도 **살아 있는
   참조로** 남아 있지 않다. 서술(주석·독스트링)은 세지 않습니다 — 없어진 경위를 적어 두는
   것은 오히려 필요한 일이라, 파이썬은 AST 로 읽고 설정 파일은 주석을 떼고 봅니다.
② **옛 기록 호환** — daengback DB 에 실제로 저장돼 있던 legacy 행(픽스처,
   `legacy_db_row.json`)이 여전히 legacy 로 판별되고, 계약을 만족하고, legacy↔legacy
   비교가 옛날과 같은 응답을 낸다.

서버 실DB 를 보지 않습니다 (지시). 실DB 의 실제 기록 조회는 머지·배포 뒤 실환경 검증입니다.
"""

from __future__ import annotations

import ast
import copy
import json
import re
from pathlib import Path

import pytest

from daengs_gait import contract
from daengs_gait.compare import compare_loaded_records

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
SRC = BACKEND / "src"
FIXTURES = Path(__file__).parent / "fixtures" / "gait"

#: 6단계에서 삭제된 모듈 (파일 경로 기준).
DELETED = [
    SRC / "daengs_gait" / "pipeline.py",
    SRC / "daengs_gait" / "keypoint_infer.py",
    SRC / "daengs_gait" / "crop_assist.py",
    SRC / "daengs_gait" / "record_store.py",
    SRC / "daengs_gait" / "engines" / "legacy.py",
]

#: 살아 있으면 안 되는 이름들. 문자열로도 잡습니다 — `monkeypatch.setitem(sys.modules,
#: "daengs_gait.pipeline", …)` 처럼 import 문이 아닌 자리로도 되살아날 수 있습니다.
FORBIDDEN = (
    "daengs_gait.pipeline",
    "daengs_gait.keypoint_infer",
    "daengs_gait.crop_assist",
    "daengs_gait.record_store",
    "daengs_gait.engines.legacy",
    "ultralytics",
)

#: 설정 파일은 AST 가 없으니 `#` 주석을 떼고 봅니다.
CONFIG_FILES = [
    REPO / "docker-compose.yml",
    REPO / ".env.example",
    BACKEND / ".env.example",
    BACKEND / "pyproject.toml",
    REPO / "nginx" / "default.conf",
]


def test_deleted_modules_are_gone() -> None:
    still_here = [str(p.relative_to(REPO)) for p in DELETED if p.exists()]
    assert still_here == []


def _live_strings_and_imports(path: Path) -> list[str]:
    """파이썬 파일에서 **주석·독스트링이 아닌** 이름과 문자열만 모읍니다."""
    # utf-8-sig — 저장소에 BOM 이 붙은 파일이 하나 있고, 그대로 읽으면 ast 가 첫 줄에서 죽습니다.
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(id(node.body[0].value))

    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            out.append(node.value)
    return out


@pytest.mark.parametrize("name", FORBIDDEN)
def test_no_live_reference_in_backend_sources(name: str) -> None:
    """`backend/src` 전체를 AST 로 읽습니다. 주석·독스트링의 서술은 세지 않습니다."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        for value in _live_strings_and_imports(path):
            if name in value:
                offenders.append(f"{path.relative_to(REPO)}: {value!r}")
    assert offenders == [], f"{name} 이(가) 코드에 살아 있습니다: {offenders}"


@pytest.mark.parametrize("name", FORBIDDEN)
def test_no_live_reference_in_config_files(name: str) -> None:
    """compose · env 예시 · pyproject · nginx — `#` 주석을 뗀 나머지."""
    offenders = []
    for path in CONFIG_FILES:
        assert path.exists(), f"검사 대상이 사라졌습니다: {path}"
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if name in code:
                offenders.append(f"{path.relative_to(REPO)}:{lineno}")
    assert offenders == [], f"{name} 이(가) 설정에 살아 있습니다: {offenders}"


def test_compose_gait_engine_defaults_are_v4() -> None:
    """기본값을 안 바꾸면 env 없는 배포가 **없는 엔진**을 골라 분석이 전부 FAILED 입니다."""
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    defaults = re.findall(r"GAIT_ENGINE:\s*\$\{GAIT_ENGINE:-([^}]*)\}", text)
    assert defaults == ["v4", "v4"], defaults


# ── ② 옛 legacy 기록은 그대로 ────────────────────────────────────────────────
def _legacy_row() -> dict:
    """daengback DB 에 실제로 저장돼 있던 legacy 행 (2026-09-02 채취)."""
    return json.loads((FIXTURES / "legacy_db_row.json").read_text(encoding="utf-8"))


def _as_compare_input(row: dict, record_id: str) -> dict:
    """DB 행 → `/app/gait/compare` 가 비교 함수에 넘기는 모양 (`services/gait._run_compare`)."""
    return {
        "record_id": record_id,
        "date": "2026-09-02",
        "quality": row["quality"],
        "gait_filter_version": row["gait_filter_version"],
        "features": {
            "summary_for_ui": row["summary_for_ui"],
            "internal_feature_vector": row["internal_feature_vector"],
        },
    }


def test_stored_legacy_record_is_still_identified_as_legacy() -> None:
    """추론 runtime 이 없어져도 판별은 `contract` 가 합니다 — 관절 이름으로 가립니다."""
    row = _legacy_row()
    assert contract.classify_joint_keys(row["summary_for_ui"]) == contract.POSE_MODEL_LEGACY
    assert set(row["summary_for_ui"]) <= contract.LEGACY_12KP_JOINTS
    assert contract.POSE_MODEL_LEGACY in contract.POSE_MODELS


def test_legacy_to_legacy_compare_still_returns_the_same_shape() -> None:
    """옛 기록끼리의 비교는 6단계와 무관합니다 — 응답 키 10개가 그대로 나와야 합니다."""
    a = _as_compare_input(_legacy_row(), "aaaaaaaa")
    b = _as_compare_input(_legacy_row(), "bbbbbbbb")

    out = compare_loaded_records(a, b)

    public = {k for k in out if not k.startswith("_dev_only_")}
    assert public == {
        "status",
        "reason",
        "recommendation",
        "record_a",
        "record_b",
        "message_for_ui",
        "reliability_note",
        "version_warning",
        "diff_threshold_note",
        "joint_movement_range_comparison",
    }
    assert out["status"] == "ok"
    # 같은 행끼리라 모든 관절이 '비슷함' 이어야 합니다.
    assert "관찰되지 않았습니다" in out["message_for_ui"]
    assert set(out["joint_movement_range_comparison"]) == set(_legacy_row()["summary_for_ui"])


def test_legacy_compare_detects_a_change_in_an_old_record() -> None:
    """판정 계산이 살아 있는지 — 한쪽 관절 값을 키우면 '차이 관찰됨' 이 나와야 합니다."""
    a = _as_compare_input(_legacy_row(), "aaaaaaaa")
    b = copy.deepcopy(a)
    b["record_id"] = "bbbbbbbb"
    joint = next(iter(b["features"]["summary_for_ui"]))
    b["features"]["summary_for_ui"][joint]["x_range"] *= 100

    out = compare_loaded_records(a, b)

    assert out["status"] == "ok"
    assert out["joint_movement_range_comparison"][joint]["comparison_note"]["x"] == "차이 관찰됨"
    assert "차이가 관찰됩니다" in out["message_for_ui"]


def test_dev_only_fields_do_not_reach_the_app_response_model() -> None:
    """계산은 내되 앱에는 안 나갑니다 — 응답 모델이 걸러 냅니다 (extra='ignore')."""
    from daengs_backend.schemas.gait import GaitCompareResponse

    out = compare_loaded_records(
        _as_compare_input(_legacy_row(), "aaaaaaaa"),
        _as_compare_input(_legacy_row(), "bbbbbbbb"),
    )
    assert any(k.startswith("_dev_only_") for k in out)

    dumped = GaitCompareResponse(**out).model_dump()
    assert [k for k in dumped if k.startswith("_dev_only_")] == []
