"""004_kcisa_facility.sql

원본 `migrations/004_kcisa_facility.sql` 을 한 글자도 바꾸지 않고 옮긴 것이다. 이 리비전은 **역사**다 —
다시 쓰면 이미 이 스키마로 돌아가는 DB 와 어긋난다.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

from app.core.migration_sql import run_legacy_sql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "004_kcisa_facility.sql"

SQL = """
-- KCISA 반려동물 동반 문화시설 기반층. 스냅샷 통째 교체가 설계 단위다 —
-- 행 단위 증분 없음(원천에 안정 ID가 없다). place(MOIS 인허가)는 의료 오버레이로 남는다.
CREATE TABLE IF NOT EXISTS facility (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,             -- 정규화 슬러그: cafe/travel/hospital/pharmacy/...
    category3    TEXT NOT NULL,             -- 원천 표기 그대로
    sido         TEXT,
    sigungu      TEXT,
    address      TEXT,
    phone        TEXT,
    homepage     TEXT,
    hours_text   TEXT,                      -- 원문 보존. place.hours(JSONB) 구조화는 별도 단계
    closed_days  TEXT,
    parking      BOOLEAN,
    indoor       BOOLEAN,
    outdoor      BOOLEAN,
    pet          JSONB,                     -- 동반가능·전용·크기·제한·추가요금
    location     geography(Point,4326) NOT NULL,
    last_written DATE,                      -- 원천 최종작성일
    snapshot     TEXT NOT NULL              -- 예: '2025-03-24'. 교체 단위이자 화면 표기용
);
CREATE INDEX IF NOT EXISTS facility_gix       ON facility USING gist (location);
CREATE INDEX IF NOT EXISTS facility_kind_idx  ON facility (kind);

-- 기반층 ↔ 인허가 오버레이. 매칭 방법과 거리를 남긴다 — "왜 이 둘이 같은 가게인가"에 답하는 자리.
CREATE TABLE IF NOT EXISTS facility_place_link (
    facility_id BIGINT NOT NULL REFERENCES facility (id) ON DELETE CASCADE,
    place_id    BIGINT NOT NULL REFERENCES place (id) ON DELETE CASCADE,
    method      TEXT NOT NULL,
    distance_m  REAL,
    matched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (facility_id, place_id)
);
CREATE INDEX IF NOT EXISTS facility_place_link_place_idx ON facility_place_link (place_id);
"""


def upgrade() -> None:
    run_legacy_sql(SOURCE, SQL)


def downgrade() -> None:
    raise NotImplementedError("alembic 도입 전 스키마로는 되돌리지 않는다")
