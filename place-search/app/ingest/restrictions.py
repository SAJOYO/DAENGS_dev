"""저장된 `facility.pet->>'restrictions'` → 두 축 + 술어. 원천 재호출 없음.

`pet_axes` 와 같은 성격·같은 구조다. 판독표(`place.restriction_map`)가 바뀌면 이 단계만
다시 돌린다 — 공공데이터를 다시 받지 않는다.

    python -m app.ingest restrictions             # 아직 파생 안 된 행만
    python -m app.ingest restrictions --all       # 전량 재파생 (표를 고친 뒤)
    python -m app.ingest restrictions --source kcisa

**`--all` 없이도 버전이 다른 행은 다시 판다.** `pet_axes` 는 "축이 비어 있는 행" 만 봤지만
여기는 `restriction_semantics_version` 이 현재 값과 다른 행도 미처리로 센다. 표를 고치고
`--all` 을 깜빡하면 옛 규칙으로 파생된 행이 남는데, 그 행은 스스로 낡았다고 말하지 못한다.

`pet` 봉투가 없는 행(KTO 9,692행)도 **처리 대상이다.** `restriction_state=unknown` 을
명시적으로 기록해야 "아직 안 돌렸다" 와 "돌렸는데 원문이 없다" 가 구분된다.
"""

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.place.restriction_map import (
    RESTRICTION_SEMANTICS_VERSION,
    ParseState,
    RestrictionState,
    derive,
)

BATCH = 2000

# **커서 페이지네이션인 이유는 `pet_axes` 와 같다** — OFFSET 으로 훑으면 미처리 조건에서
# 안 빠지는 행이 같은 배치를 무한 반복하게 만든다. id 커서는 결과가 줄어도 안전하다.
_SOURCE_FILTER = "AND (CAST(:sources AS text[]) IS NULL OR source = ANY(:sources))"

# 미처리 = 파생된 적 없거나(NULL) 옛 규칙으로 파생된 행.
_SELECT_PENDING = text(f"""
SELECT id, pet->>'restrictions' AS restrictions FROM facility
WHERE id > :after
  AND (restriction_state IS NULL
       OR restriction_semantics_version IS DISTINCT FROM :version)
  {_SOURCE_FILTER}
ORDER BY id LIMIT :limit
""")

_SELECT_ALL = text(f"""
SELECT id, pet->>'restrictions' AS restrictions FROM facility
WHERE id > :after
  {_SOURCE_FILTER}
ORDER BY id LIMIT :limit
""")

_UPDATE = text("""
UPDATE facility SET
    restriction_state             = :restriction_state,
    restriction_parse_state       = :restriction_parse_state,
    restriction_predicates        = CAST(:restriction_predicates AS jsonb),
    restriction_semantics_version = :restriction_semantics_version
WHERE id = :id
""")


@dataclass
class DeriveStats:
    """무엇이 얼마나 나왔는지. `raw_only` 가 갑자기 늘면 표가 낡은 것이다."""

    scanned: int = 0
    updated: int = 0
    state: dict[str, int] = field(default_factory=dict)
    parse_state: dict[str, int] = field(default_factory=dict)
    with_predicates: int = 0
    conditional: int = 0  # applies_to 가 all 이 아닌 술어를 가진 행

    def to_dict(self) -> dict:
        return dict(vars(self))


async def derive_all(
    session: AsyncSession,
    *,
    redo: bool = False,
    sources: tuple[str, ...] | None = None,
) -> DeriveStats:
    """미처리 행(또는 `redo=True` 면 전량)의 제약 축을 채운다."""
    import json

    stmt = _SELECT_ALL if redo else _SELECT_PENDING
    params = {
        "limit": BATCH,
        "sources": list(sources) if sources else None,
        "version": RESTRICTION_SEMANTICS_VERSION,
    }
    stats = DeriveStats()
    after = 0
    while True:
        rows = (await session.execute(stmt, {**params, "after": after})).all()
        if not rows:
            break
        for row in rows:
            derivation = derive(row.restrictions)
            columns = derivation.to_columns()
            columns["restriction_predicates"] = json.dumps(
                columns["restriction_predicates"], ensure_ascii=False
            )
            await session.execute(_UPDATE, {"id": row.id, **columns})
            stats.scanned += 1
            stats.updated += 1
            stats.state[derivation.state.value] = stats.state.get(derivation.state.value, 0) + 1
            if derivation.parse_state is not None:
                key = derivation.parse_state.value
                stats.parse_state[key] = stats.parse_state.get(key, 0) + 1
            if derivation.predicates:
                stats.with_predicates += 1
            if any(p.applies_to.value != "all" for p in derivation.predicates):
                stats.conditional += 1
        after = rows[-1].id
        await session.commit()
    return stats


async def coverage(session: AsyncSession) -> dict:
    """현재 저장된 분포. 배치를 돌리지 않고 상태만 본다."""
    result = await session.execute(
        text("""
        SELECT COALESCE(restriction_state, 'not_derived') AS state,
               COALESCE(restriction_parse_state, '-') AS parse_state,
               count(*) AS rows
        FROM facility GROUP BY 1, 2 ORDER BY 3 DESC
        """)
    )
    return {
        "version": RESTRICTION_SEMANTICS_VERSION,
        "known_states": [state.value for state in RestrictionState],
        "known_parse_states": [state.value for state in ParseState],
        "rows": [
            {"state": row.state, "parse_state": row.parse_state, "rows": row.rows}
            for row in result
        ],
    }
