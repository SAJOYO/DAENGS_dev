"""`facility.pet` 봉투가 실제로 축이 될 수 있는지 **재기 위한** 측정.

병원에서 배운 순서를 그대로 따른다: 태그를 필터로 쓸지 말지는 커버리지를 재고 나서
정했다 (활성 5,457곳 중 night 1 · emergency 2 → `WHERE` 금지, 부스트만).
`pet` 도 같은 질문을 받아야 한다 — **필터로 쓸 수 있나, 부스트로만 써야 하나.**

재는 것 넷:
  1. 키 채움률      원천별로 어떤 키가 몇 % 행에 있나 (원천 간 스키마가 같은가)
  2. 값 분포        자유 텍스트인가, 사실상 열거형인가
  3. 교차 일관성    allowed 와 size 가 서로 모순되지 않나
  4. 파싱 가능률    구체 제약이 kg 숫자 / 크기 라벨로 떨어지는 비율

측정은 DB 를 읽기만 한다. 외부 호출 0 이라 Usage Gate 와 무관하다.

    uv run python scripts/facility_pet_coverage.py
    uv run python scripts/facility_pet_coverage.py --csv out/pet_size_values.csv
"""

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

# 원천이 준 "제약 없음 / 미상" 표기. 구체 제약과 가르는 기준선이라 한 곳에 둔다.
SIZE_OPEN = "모두 가능"
SIZE_UNKNOWN = "해당없음"

KCISA_KEYS = ("allowed", "exclusive", "size", "restrictions", "extra_fee")

# 개발 콘솔이 Windows 면 기본 cp949 라 한글·기호 출력에서 UnicodeEncodeError 로 죽는다.
# 측정이 인코딩 때문에 멈추면 안 되므로 표준출력을 UTF-8 로 고정한다.
sys.stdout.reconfigure(encoding="utf-8")


async def main() -> None:
    ap = argparse.ArgumentParser(description="facility.pet 축 커버리지 측정")
    ap.add_argument("--csv", type=Path, help="size 고유값 전체를 CSV 로 저장")
    args = ap.parse_args()

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as db:
            await _key_fill(db)
            await _foreign_keys(db)
            await _value_spread(db)
            await _cross(db)
            await _parseable(db)
            if args.csv:
                await _dump_sizes(db, args.csv)
    finally:
        await engine.dispose()


def _table(title: str, rows, headers: tuple[str, ...]) -> None:
    print(f"\n## {title}")
    print(" | ".join(headers))
    for r in rows:
        print(" | ".join("" if v is None else str(v) for v in r))


async def _key_fill(db) -> None:
    """1. 키 채움률. 원천별로 같은 키를 주는지가 첫 질문이다."""
    filters = ",\n".join(
        f"count(*) FILTER (WHERE pet ? '{k}') AS {k}" for k in KCISA_KEYS
    )
    rows = (await db.execute(text(f"""
        SELECT source, count(*) AS rows, {filters}
        FROM facility GROUP BY source ORDER BY source
    """))).all()
    _table("키 채움률 (KCISA 스키마 기준)", rows, ("source", "rows", *KCISA_KEYS))


async def _foreign_keys(db) -> None:
    """1-b. KCISA 스키마 밖의 키. 있으면 `pet` 은 원천별로 다른 봉투라는 뜻이다."""
    rows = (await db.execute(text("""
        SELECT source, k, count(*)
        FROM facility, LATERAL jsonb_object_keys(pet) k
        WHERE NOT (k = ANY(:known))
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20
    """), {"known": list(KCISA_KEYS)})).all()
    _table("KCISA 스키마 밖의 키", rows, ("source", "key", "rows"))


async def _value_spread(db) -> None:
    """2. 값 분포. 고유값이 적으면 열거형이고, 그러면 파싱 없이 축이 된다."""
    for field in ("allowed", "exclusive"):
        rows = (await db.execute(text(f"""
            SELECT pet->>'{field}', count(*) FROM facility
            WHERE pet ? '{field}' GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """))).all()
        _table(f"값 분포 — {field}", rows, (field, "rows"))

    rows = (await db.execute(text("""
        SELECT count(DISTINCT pet->>'size') AS distinct_values,
               count(*) FILTER (WHERE pet->>'size' = :open)    AS open_all,
               count(*) FILTER (WHERE pet->>'size' = :unknown) AS unknown,
               count(*) FILTER (WHERE pet->>'size' NOT IN (:open, :unknown)) AS concrete
        FROM facility WHERE pet ? 'size'
    """), {"open": SIZE_OPEN, "unknown": SIZE_UNKNOWN})).all()
    _table("값 분포 — size", rows, ("distinct", SIZE_OPEN, SIZE_UNKNOWN, "구체 제약"))


async def _cross(db) -> None:
    """3. 교차 일관성. allowed=N 인데 크기 제약이 적힌 행은 원천 노이즈다."""
    rows = (await db.execute(text("""
        SELECT pet->>'allowed' AS allowed,
               CASE WHEN pet->>'size' IN (:open, :unknown)
                    THEN pet->>'size' ELSE '(구체 제약)' END AS size_bucket,
               count(*)
        FROM facility WHERE pet ? 'allowed' AND pet ? 'size'
        GROUP BY 1, 2 ORDER BY 1, 3 DESC
    """), {"open": SIZE_OPEN, "unknown": SIZE_UNKNOWN})).all()
    _table("교차 — allowed × size", rows, ("allowed", "size", "rows"))


async def _parseable(db) -> None:
    """4. 파싱 가능률. 구체 제약이 축(kg·라벨)으로 떨어지는 비율.

    떨어지지 않는 값도 세는 것이 요점이다 — 그 잔여가 무엇인지가 다음 설계를 정한다.
    """
    rows = (await db.execute(text("""
        WITH c AS (
            SELECT pet->>'size' AS s FROM facility
            WHERE pet ? 'size' AND pet->>'size' NOT IN (:open, :unknown)
        )
        SELECT count(*) AS concrete,
               count(*) FILTER (WHERE s ~ '[0-9]+\\s*kg')            AS kg,
               count(*) FILTER (WHERE s ~ '소형|중형|대형')           AS label,
               count(*) FILTER (WHERE s !~ '[0-9]+\\s*kg'
                                  AND s !~ '소형|중형|대형')          AS neither
        FROM c
    """), {"open": SIZE_OPEN, "unknown": SIZE_UNKNOWN})).all()
    _table("파싱 가능률 — 구체 제약", rows, ("구체 제약", "kg 표기", "라벨 표기", "둘 다 없음"))

    rows = (await db.execute(text("""
        SELECT pet->>'size', count(*) FROM facility
        WHERE pet ? 'size' AND pet->>'size' NOT IN (:open, :unknown)
          AND pet->>'size' !~ '[0-9]+\\s*kg' AND pet->>'size' !~ '소형|중형|대형'
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
    """), {"open": SIZE_OPEN, "unknown": SIZE_UNKNOWN})).all()
    _table("잔여 — kg 도 라벨도 아닌 값", rows, ("size", "rows"))


async def _dump_sizes(db, path: Path) -> None:
    rows = (await db.execute(text("""
        SELECT pet->>'size' AS size, count(*) AS rows FROM facility
        WHERE pet ? 'size' GROUP BY 1 ORDER BY 2 DESC
    """))).all()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(("size", "rows"))
        w.writerows(rows)
    print(f"\n{path} — {len(rows)} 행")


if __name__ == "__main__":
    asyncio.run(main())
