"""저장된 `facility.pet` → 축 컬럼 재파생. 원천 재호출 없음.

**왜 적재 파서 안이 아니라 별도 단계인가**: 축은 *저장된* `pet` 의 함수여야 한다.
적재 UPSERT 는 빈 상세가 기존 상세를 덮지 않게 하므로(`facility_store.py`), 입력 dict 에서
축을 만들면 저장값과 어긋난다. 그리고 원천이 둘인데(KCISA CSV · KTO API) 파싱은 하나여야 한다.

문턱값(`app/geo/pet.py` 의 kg 등급 경계)이 바뀌면 이 단계만 다시 돌린다 — 원천을 다시 받지 않는다.

    python -m app.ingest pet-axes            # 축이 비어 있는 행만
    python -m app.ingest pet-axes --all      # 전부 다시 파생 (문턱값 변경 후)
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.geo.pet import derive_axes

BATCH = 2000

# **커서 페이지네이션인 이유**: OFFSET 으로 훑으면 축이 전부 NULL 로 파생되는 행
# (KTO 상세는 키 이름이 달라 KCISA 축이 하나도 안 나온다 — 246행)이 미채움 조건에서
# 영원히 안 빠져 같은 배치를 무한 반복한다. id 커서는 결과가 줄어도 안전하다.
# `:sources` 가 NULL 이면 전 원천. 한 원천만 다시 파생할 수 있어야 하는 이유는 둘이다 —
# 문턱값을 바꿨을 때 KCISA 만 돌리면 되고, 테스트가 실데이터 전체를 훑지 않아도 된다.
_SOURCE_FILTER = "AND (CAST(:sources AS text[]) IS NULL OR source = ANY(:sources))"

_SELECT_PENDING = text(f"""
SELECT id, pet FROM facility
WHERE pet IS NOT NULL AND pet <> '{{}}'::jsonb AND id > :after
  AND pet_allowed IS NULL AND pet_size_class IS NULL AND pet_dog_ok IS NULL
  {_SOURCE_FILTER}
ORDER BY id LIMIT :limit
""")

_SELECT_ALL = text(f"""
SELECT id, pet FROM facility
WHERE pet IS NOT NULL AND pet <> '{{}}'::jsonb AND id > :after
  {_SOURCE_FILTER}
ORDER BY id LIMIT :limit
""")

_UPDATE = text("""
UPDATE facility SET
    pet_allowed    = :pet_allowed,
    pet_exclusive  = :pet_exclusive,
    pet_dog_ok     = :pet_dog_ok,
    pet_size_class = :pet_size_class,
    pet_max_kg     = :pet_max_kg
WHERE id = :id
""")


@dataclass
class DeriveStats:
    scanned: int = 0
    updated: int = 0
    with_size_class: int = 0
    with_max_kg: int = 0
    dog_excluded: int = 0        # 종이 열거됐는데 개가 없는 행. 0 이면 파서를 의심할 것

    def to_dict(self) -> dict:
        return dict(vars(self))


async def derive_all(
    session: AsyncSession, *, redo: bool = False, sources: tuple[str, ...] | None = None
) -> DeriveStats:
    """`pet` 이 있는 행의 축을 채운다. `redo=False` 면 아직 비어 있는 행만."""
    stmt = _SELECT_ALL if redo else _SELECT_PENDING
    params = {"limit": BATCH, "sources": list(sources) if sources else None}
    stats = DeriveStats()
    after = 0
    while True:
        rows = (await session.execute(stmt, {**params, "after": after})).all()
        if not rows:
            break
        for row in rows:
            axes = derive_axes(row.pet)
            stats.scanned += 1
            if axes.size_class is not None:
                stats.with_size_class += 1
            if axes.max_kg is not None:
                stats.with_max_kg += 1
            if axes.dog_ok is False:
                stats.dog_excluded += 1
            await session.execute(_UPDATE, {"id": row.id, **axes.to_columns()})
            stats.updated += 1
        after = rows[-1].id
        await session.commit()
    return stats
