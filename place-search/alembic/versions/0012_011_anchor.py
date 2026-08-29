"""011_anchor.sql

원본 `migrations/011_anchor.sql` 을 한 글자도 바꾸지 않고 옮긴 것이다. 이 리비전은 **역사**다 —
다시 쓰면 이미 이 스키마로 돌아가는 DB 와 어긋난다.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

from app.core.migration_sql import run_legacy_sql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "011_anchor.sql"

SQL = """
-- 점령 앵커 — 산책 게임의 고정 지점. 원천은 공공데이터(보안등 등) 좌표다.
--
-- **왜 원본을 다 담지 않는가**: 보안등 원본은 도심 최근접 이웃 중앙값이 12m 라
-- 판정 원이 통째로 겹친다. 육각 격자로 셀당 1개만 남긴 결과를 적재한다 —
-- 선별은 결정론이라 같은 원천 스냅샷이면 같은 앵커가 나온다 (scripts/load_anchors.py).
--
-- 이름을 두지 않는 이유: 앵커의 정체성은 장소명이 아니라 **점령한 주인**에서 온다.
-- 원천 48만 중 100m 안에 이름 붙일 시설이 있는 건 3.5% 뿐이라, 이름을 요구하면
-- 나머지 96.5% 를 버려야 한다. 익명 앵커가 정상이다.
CREATE TABLE IF NOT EXISTS anchor (
    id         BIGSERIAL PRIMARY KEY,
    cell       TEXT NOT NULL,                       -- 'anchor-hex:115:q:r' 선별 격자 셀
    source     TEXT NOT NULL,                       -- 'lamp' (전국보안등정보표준데이터)
    kind       TEXT NOT NULL,                       -- 한전주 | 전용주 | 통신주 | 건축물 | unknown
    location   geography(Point, 4326) NOT NULL,
    instt      TEXT,                                -- 제공기관 (커버리지 구멍 추적용)
    as_of      DATE,                                -- 원천 데이터기준일자
    UNIQUE (source, cell)
);
CREATE INDEX IF NOT EXISTS anchor_gix      ON anchor USING gist (location);
CREATE INDEX IF NOT EXISTS anchor_kind_idx ON anchor (kind);
"""


def upgrade() -> None:
    run_legacy_sql(SOURCE, SQL)


def downgrade() -> None:
    raise NotImplementedError("alembic 도입 전 스키마로는 되돌리지 않는다")
