"""`insurance` 를 `policy` 에서 가른다 (RAG-067 / A7 · #271).

**이 카드의 위험은 값이 아니라 경로다.** `category` 는 크롤 시점에 `.meta.json` 으로 박히고
parsed → 청크 → DB 로 내려온다. 그래서 세 자리에서 어긋날 수 있고, 셋 다 조용하다:

  ① `CHECK` 에 값이 없으면 **적재가 죽는다** (이건 시끄럽다 — 그래서 여기서 먼저 잡는다)
  ② 재분류 표가 **대상별 오버라이드를 덮으면** 다른 카드가 한 일이 되돌아간다
  ③ 소스 클래스와 표가 갈리면 **새로 받는 문서와 이미 받은 문서가 다른 값**을 갖는다

네트워크도 DB 도 `data/` 도 쓰지 않는다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from daengs_life.crawler.sources.insurance.insurer_terms_pdfs import InsurerTermsPdfs
from daengs_life.crawler.sources.insurance.knia_disclosure import KniaDisclosure

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "db" / "init" / "01_schema.sql"
MIGRATION = REPO / "db" / "migrations" / "2026-09-06_documents_category_insurance.sql"


def _schema_categories() -> set[str]:
    """`db/init/01_schema.sql` 의 `CHECK (category IN (...))` 값 집합."""
    text = SCHEMA.read_text(encoding="utf-8")
    m = re.search(r"CHECK\s*\(category IN \(([^)]*)\)\)", text)
    assert m, "CHECK (category IN (...)) 를 못 찾았다 — 스키마 모양이 바뀌었다"
    return set(re.findall(r"'([^']+)'", m.group(1)))


# ------------------------------------------------------------------ ① CHECK

def test_the_schema_accepts_insurance() -> None:
    assert "insurance" in _schema_categories()


def test_the_schema_keeps_the_three_it_had() -> None:
    """값이 **늘어나는** 변경이다. 하나라도 빠지면 기존 9,000여 행이 CHECK 위반이 된다."""
    assert {"policy", "travel", "food"} <= _schema_categories()


def test_the_migration_and_the_schema_agree() -> None:
    """**두 파일이 갈리면 볼륨을 새로 만든 DB 와 이미 도는 DB 가 다른 제약을 갖는다.**
    `db/init/` 은 볼륨이 빌 때만 실행되므로(CLAUDE.md) 그 어긋남은 한참 뒤에 드러난다.
    """
    text = MIGRATION.read_text(encoding="utf-8")
    m = re.search(r"CHECK \(category IN \(([^)]*)\)\)", text)
    assert m, "마이그레이션에서 CHECK 를 못 찾았다"
    assert set(re.findall(r"'([^']+)'", m.group(1))) == _schema_categories()


def test_the_migration_is_idempotent_by_construction() -> None:
    """버전 테이블이 없어 **여러 번 돌 수 있다** (CLAUDE.md). 두 장치가 그것을 받친다."""
    text = MIGRATION.read_text(encoding="utf-8")
    assert "DROP CONSTRAINT IF EXISTS" in text
    assert "IS DISTINCT FROM" in text


def test_the_migration_moves_rows_by_subcategory_not_by_a_baked_list() -> None:
    """`org` 백필(#262)은 doc_id 245개를 SQL 에 구워야 했는데, 이 카드는 그럴 필요가 없다 —
    `subcategory` 가 이미 'insurance' 라 한 줄로 잡힌다. 목록을 구우면 코퍼스가 늘 때마다 낡는다.
    """
    text = MIGRATION.read_text(encoding="utf-8")
    assert "subcategory = 'insurance'" in text
    assert "VALUES" not in text.upper().split("COMMIT")[0], "doc_id 목록을 굽지 않는다"


# ------------------------------------------------------------------ ③ 소스 클래스

def test_both_insurance_sources_declare_the_new_category() -> None:
    """**새로 받는 문서의 원천은 여기다.** 클래스가 안 바뀌면 다음 크롤이 `policy` 로 되돌린다."""
    assert InsurerTermsPdfs.category == "insurance"
    assert KniaDisclosure.category == "insurance"


def test_the_declared_category_is_one_the_schema_accepts() -> None:
    """오타 하나가 파이프라인 끝(적재)에서야 CHECK 위반으로 터지는 것을 여기서 막는다."""
    allowed = _schema_categories()
    assert {InsurerTermsPdfs.category, KniaDisclosure.category} <= allowed


def test_the_subcategory_is_untouched() -> None:
    """`subcategory` 는 안 건드린다 — 마이그레이션이 그것으로 행을 고른다."""
    assert InsurerTermsPdfs.subcategory == "insurance"
    assert KniaDisclosure.subcategory == "insurance"


# ------------------------------------------------------------------ ② 재분류 표

def _retag() -> dict:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "retag_meta_category", REPO / "backend" / "tools" / "retag_meta_category.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.RETAG


def test_the_retag_table_says_both_the_old_and_the_new_value() -> None:
    """**옛 값이 안전장치다.** 값이 이미 다르면(사람이 손댔거나 오버라이드가 있거나) 건너뛴다."""
    table = _retag()
    assert table == {
        ("insurer-terms-pdfs", "policy"): "insurance",
        ("knia-disclosure", "policy"): "insurance",
    }


def test_the_retag_table_never_touches_a_per_target_override() -> None:
    """**첫 판이 여기서 틀렸다.** "소스 클래스와 동기화" 로 짰더니 `nias-pet` 과 `law-drf-api` 의
    `food` 6건을 `policy` 로 **되돌리려** 했다 — 그 값은 `Target.meta["category"]` 오버라이드로
    #268 이 의도해서 준 것이다(RAG-065 ⑥). 미리보기에서 잡혔고, 전제를 바꿔 표를 명시했다.

    그 두 소스가 표에 들어오면 같은 사고가 다시 난다.
    """
    touched = {source_id for source_id, _ in _retag()}
    assert "nias-pet" not in touched
    assert "law-drf-api" not in touched


def test_the_retag_table_agrees_with_the_source_classes() -> None:
    """표가 가는 목적지와 클래스가 갈리면, 이미 받은 문서와 새로 받을 문서가 다른 값을 갖는다."""
    table = _retag()
    assert table[("insurer-terms-pdfs", "policy")] == InsurerTermsPdfs.category
    assert table[("knia-disclosure", "policy")] == KniaDisclosure.category


def test_the_retag_targets_are_values_the_schema_accepts() -> None:
    assert set(_retag().values()) <= _schema_categories()


# ------------------------------------------------------------------ 실물 (있을 때만)

def test_no_meta_file_is_left_on_the_old_value() -> None:
    """`data/` 가 있는 PC 에서만 돈다 — 없으면 skip 이다 (`test_parse.py` 와 같은 규약).

    `--write` 를 안 돌리고 머지하면 **코드는 `insurance` 인데 코퍼스는 `policy`** 인 상태가 된다.
    그 상태에서 `rag load` 는 옛 값을 넣고, 마이그레이션이 채운 값을 **덮어쓴다** (RAG-066 ①).
    """
    from daengs_life.crawler.core import config
    if config.RAW_DIR is None or not config.RAW_DIR.exists():
        pytest.skip("data/raw 가 없다 — 크롤러를 먼저 돌려라")
    stale = [
        p.name for p in config.RAW_DIR.rglob("*.meta.json")
        if (m := json.loads(p.read_text(encoding="utf-8")))
        and (m.get("source_id", ""), m.get("category", "")) in _retag()
    ]
    assert not stale, f"retag 를 안 돌린 meta 가 있다 (tools/retag_meta_category.py --write): {stale[:5]}"
