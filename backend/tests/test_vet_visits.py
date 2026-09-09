"""진료비 기록 (#353). SQL 이 원본이고 모델이 따라간다."""

import re
from pathlib import Path

from daengs_backend.models import VET_REASON_CODES

_SQL = Path(__file__).resolve().parents[2] / "db" / "init" / "25_vet_visits.sql"


def _codes_in_check(constraint: str) -> tuple[str, ...]:
    """`db/init` 의 CHECK 정의문에서 코드 목록만 뽑는다."""
    text = _SQL.read_text(encoding="utf-8")
    start = text.index(constraint)
    body = text[start : text.index("))", start)]
    return tuple(re.findall(r"'([a-z_]+)'", body))


def test_reason_codes_match_sql():
    """**이 테스트가 이 파일의 존재 이유다.** 모델과 SQL 이 갈리면 앱이 보내는 코드가
    DB CHECK 에서 터지는데, 그때 나오는 것은 500 이고 무엇이 어긋났는지 안 보인다."""
    assert VET_REASON_CODES == _codes_in_check("vet_visits_reason_code_check")


def test_suggested_reason_codes_match_sql():
    """제안 칸의 목록도 같아야 한다 — 둘이 갈리면 제안이 확정에서 떨어진다."""
    assert _codes_in_check("vet_visits_suggested_reason_code_check") == VET_REASON_CODES


def test_reason_codes_have_no_pathology_or_acuity():
    """축이 하나여야 한다 (verify ⑦ 과 같은 단언, 코드 쪽에서 한 번 더)."""
    for banned in ("tumor", "injury", "parasite", "emergency"):
        assert banned not in VET_REASON_CODES
