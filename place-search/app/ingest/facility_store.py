"""facility 기반층 적재 공용 — (source, source_ref) UPSERT + 미확인 행 정리.

**왜 DELETE-후-INSERT가 아닌가**: `facility.id`가 매 동기화마다 바뀌면 즐겨찾기·
추천 이력·딥링크가 전부 끊긴다. 원천 고유키로 UPSERT하면 id가 유지된다.
스냅샷 의미(원천에서 사라진 건 없어져야 함)는 `synced_at` 스탬프로 지킨다 —
이번 실행에서 안 건드려진 같은 원천 행만 지운다.

**보존 규칙**: 이번 실행이 값을 안 가져온 필드는 기존 값을 덮지 않는다.
단, 빈 `pet` 이 "이번 값은 없음"인지 "상세를 이번에 안 받음"인지는 원천이 정한다.
후자인 KTO 목록 적재만 `preserve_empty_pet=True` 로 기존 상세를 보존한다.

`pet` 이 실제로 바뀌면 그 봉투에서 만든 두 파생 묶음(pet 축·제약 술어)을 즉시
NULL 로 되돌린다. 다음 파생 배치가 새 원문만 다시 계산하게 해, 새 원문과 옛 판정이
한 응답에 섞이는 시간을 없앤다.
"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

COLUMNS = (
    "name", "kind", "category3", "sido", "sigungu", "address", "phone", "homepage",
    "hours_text", "closed_days", "parking", "indoor", "outdoor", "lat", "lng",
    "last_written", "pet", "raw",
)

_UPSERT = text("""
INSERT INTO facility (source, source_ref, name, kind, category3, sido, sigungu, address,
                      phone, homepage, hours_text, closed_days, parking, indoor, outdoor,
                      pet, location, last_written, snapshot, raw, synced_at)
VALUES (:source, :source_ref, :name, :kind, :category3, :sido, :sigungu, :address,
        :phone, :homepage, :hours_text, :closed_days, :parking, :indoor, :outdoor,
        CAST(:pet AS jsonb), ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        :last_written, :snapshot, CAST(:raw AS jsonb), :synced_at)
ON CONFLICT (source, source_ref) WHERE source_ref IS NOT NULL DO UPDATE SET
    name         = EXCLUDED.name,
    kind         = EXCLUDED.kind,
    category3    = EXCLUDED.category3,
    sido         = COALESCE(EXCLUDED.sido, facility.sido),
    sigungu      = COALESCE(EXCLUDED.sigungu, facility.sigungu),
    address      = COALESCE(EXCLUDED.address, facility.address),
    phone        = COALESCE(EXCLUDED.phone, facility.phone),
    homepage     = COALESCE(EXCLUDED.homepage, facility.homepage),
    hours_text   = COALESCE(EXCLUDED.hours_text, facility.hours_text),
    closed_days  = COALESCE(EXCLUDED.closed_days, facility.closed_days),
    parking      = COALESCE(EXCLUDED.parking, facility.parking),
    indoor       = COALESCE(EXCLUDED.indoor, facility.indoor),
    outdoor      = COALESCE(EXCLUDED.outdoor, facility.outdoor),
    -- KTO 목록처럼 호출자가 명시한 경우에만 빈 상세를 "미수집"으로 취급한다.
    pet          = CASE
                     WHEN :preserve_empty_pet AND EXCLUDED.pet = '{}'::jsonb
                     THEN facility.pet ELSE EXCLUDED.pet
                   END,
    -- 아래 열은 모두 `pet` 의 함수다. effective pet 이 실제로 달라질 때만 무효화한다.
    pet_allowed  = CASE
                     WHEN NOT COALESCE(:preserve_empty_pet AND EXCLUDED.pet = '{}'::jsonb, false)
                          AND EXCLUDED.pet IS DISTINCT FROM facility.pet
                     THEN NULL ELSE facility.pet_allowed
                   END,
    pet_exclusive = CASE
                     WHEN NOT COALESCE(:preserve_empty_pet AND EXCLUDED.pet = '{}'::jsonb, false)
                          AND EXCLUDED.pet IS DISTINCT FROM facility.pet
                     THEN NULL ELSE facility.pet_exclusive
                   END,
    pet_dog_ok   = CASE
                     WHEN NOT COALESCE(:preserve_empty_pet AND EXCLUDED.pet = '{}'::jsonb, false)
                          AND EXCLUDED.pet IS DISTINCT FROM facility.pet
                     THEN NULL ELSE facility.pet_dog_ok
                   END,
    pet_size_class = CASE
                     WHEN NOT COALESCE(:preserve_empty_pet AND EXCLUDED.pet = '{}'::jsonb, false)
                          AND EXCLUDED.pet IS DISTINCT FROM facility.pet
                     THEN NULL ELSE facility.pet_size_class
                   END,
    pet_max_kg   = CASE
                     WHEN NOT COALESCE(:preserve_empty_pet AND EXCLUDED.pet = '{}'::jsonb, false)
                          AND EXCLUDED.pet IS DISTINCT FROM facility.pet
                     THEN NULL ELSE facility.pet_max_kg
                   END,
    restriction_state = CASE
                     WHEN NOT COALESCE(:preserve_empty_pet AND EXCLUDED.pet = '{}'::jsonb, false)
                          AND EXCLUDED.pet IS DISTINCT FROM facility.pet
                     THEN NULL ELSE facility.restriction_state
                   END,
    restriction_parse_state = CASE
                     WHEN NOT COALESCE(:preserve_empty_pet AND EXCLUDED.pet = '{}'::jsonb, false)
                          AND EXCLUDED.pet IS DISTINCT FROM facility.pet
                     THEN NULL ELSE facility.restriction_parse_state
                   END,
    restriction_predicates = CASE
                     WHEN NOT COALESCE(:preserve_empty_pet AND EXCLUDED.pet = '{}'::jsonb, false)
                          AND EXCLUDED.pet IS DISTINCT FROM facility.pet
                     THEN NULL ELSE facility.restriction_predicates
                   END,
    restriction_semantics_version = CASE
                     WHEN NOT COALESCE(:preserve_empty_pet AND EXCLUDED.pet = '{}'::jsonb, false)
                          AND EXCLUDED.pet IS DISTINCT FROM facility.pet
                     THEN NULL ELSE facility.restriction_semantics_version
                   END,
    location     = EXCLUDED.location,
    last_written = COALESCE(EXCLUDED.last_written, facility.last_written),
    snapshot     = EXCLUDED.snapshot,
    raw          = COALESCE(EXCLUDED.raw, facility.raw),
    synced_at    = EXCLUDED.synced_at
""")


async def upsert_rows(
    session: AsyncSession,
    source: str,
    rows: list[dict],
    snapshot: str,
    synced_at: datetime,
    *,
    preserve_empty_pet: bool = False,
) -> int:
    """행 목록을 UPSERT. rows 는 COLUMNS + source_ref 키를 가진 dict.

    `preserve_empty_pet` 은 상세 API를 별도로 호출하는 원천의 목록 적재에만 쓴다.
    """
    for row in rows:
        row.setdefault("raw", None)
        row.setdefault("pet", "{}")
        row.update(
            source=source,
            snapshot=snapshot,
            synced_at=synced_at,
            preserve_empty_pet=preserve_empty_pet,
        )
    for start in range(0, len(rows), 1000):
        await session.execute(_UPSERT, rows[start : start + 1000])
    return len(rows)


_UPDATE_PET_DETAIL = text("""
UPDATE facility SET
    pet = CAST(:pet AS jsonb),
    pet_allowed = NULL,
    pet_exclusive = NULL,
    pet_dog_ok = NULL,
    pet_size_class = NULL,
    pet_max_kg = NULL,
    restriction_state = NULL,
    restriction_parse_state = NULL,
    restriction_predicates = NULL,
    restriction_semantics_version = NULL
WHERE source = :source AND source_ref = :source_ref
  AND pet IS DISTINCT FROM CAST(:pet AS jsonb)
""")


async def update_pet_detail(
    session: AsyncSession, source: str, source_ref: str, pet_json: str,
) -> int:
    """별도 상세 API가 준 `pet` 을 저장하고, 바뀐 경우에만 파생값을 무효화한다."""
    result = await session.execute(
        _UPDATE_PET_DETAIL,
        {"source": source, "source_ref": source_ref, "pet": pet_json},
    )
    return result.rowcount


async def prune_unseen(session: AsyncSession, source: str, synced_at: datetime) -> int:
    """이번 실행에서 안 건드려진 같은 원천 행 삭제 = 스냅샷 의미.

    증분 적용(변경분만 UPSERT)에서는 부르면 안 된다 — 안 바뀐 행이 전부 지워진다.
    """
    result = await session.execute(
        text("""DELETE FROM facility
                WHERE source = :source
                  AND (synced_at IS NULL OR synced_at < :synced_at)"""),
        {"source": source, "synced_at": synced_at},
    )
    return result.rowcount
