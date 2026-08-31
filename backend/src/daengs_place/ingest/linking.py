"""원천 간 동일 시설 매칭 — facility_link 재구축.

어느 원천이 교체되든 여기 두 함수를 다시 부르면 링크가 재구축된다.
매칭 규칙(이름 정규화 + 150m)은 잠정이다: 전화 정답지 채점 결과 재현율 70%/
전화 불일치 8.8% (2026-08-24 실측). 캘리브레이션은 보류 항목 — 규칙을 바꾸면
여기 한 곳만 바꾸면 된다.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_NORM = (
    r"'\s|[()\[\]·.,‐-]|동물병원|동물의료센터|동물메디컬센터|의료센터|메디컬|센타|약국'"
)

# 의료: facility(전 원천) ↔ place(MOIS 인허가). 존재 권위는 place에 있다.
_LINK_MEDICAL = text(f"""
WITH f AS (
    SELECT id, location, kind,
           regexp_replace(lower(name), {_NORM}, '', 'g') AS norm
    FROM facility WHERE kind IN ('hospital', 'pharmacy')
), p AS (
    SELECT id, location, kind,
           regexp_replace(lower(name), {_NORM}, '', 'g') AS norm
    FROM place WHERE kind IN ('hospital', 'pharmacy')
)
INSERT INTO facility_link (facility_id, source, source_ref, method, distance_m)
SELECT DISTINCT ON (f.id) f.id, 'mois:place', p.id::text, 'norm-name+150m',
       ST_Distance(f.location, p.location)
FROM f
JOIN p ON p.kind = f.kind
      AND ST_DWithin(f.location, p.location, 150)
      AND length(f.norm) >= 2 AND length(p.norm) >= 2
      AND (f.norm = p.norm OR f.norm LIKE '%' || p.norm || '%'
           OR p.norm LIKE '%' || f.norm || '%')
ORDER BY f.id, ST_Distance(f.location, p.location)
ON CONFLICT DO NOTHING
""")

# 원천 간: 서로 다른 source의 facility 행이 같은 물리 시설일 때.
# 방향: kto 행(facility_id) → kcisa 행(source_ref). 검색은 ref로 잡힌 쪽을 숨긴다.
# 'o.source < n.source'는 원천 2개(kcisa<kto)에서만 최신→과거와 일치한다 —
# 세 번째 원천부터는 명시적 우선순위 표가 필요하다 (결정 문서 참조).
_LINK_CROSS = text(f"""
WITH a AS (
    SELECT id, source, kind, location,
           regexp_replace(lower(name), {_NORM}, '', 'g') AS norm
    FROM facility
)
INSERT INTO facility_link (facility_id, source, source_ref, method, distance_m)
SELECT DISTINCT ON (n.id) n.id, 'facility', o.id::text, 'norm-name+150m',
       ST_Distance(n.location, o.location)
FROM a n
JOIN a o ON o.source < n.source
        -- scalar kind만 있는 legacy 소비자는 서로 다른 분류를 한 행으로 안전하게 표현할 수
        -- 없다. 복수 classification은 Place 계약에서 다루고, 여기서는 같은 후보군만 접는다.
        AND o.kind = n.kind
        AND ST_DWithin(n.location, o.location, 150)
        AND length(n.norm) >= 2 AND length(o.norm) >= 2
        AND (n.norm = o.norm OR n.norm LIKE '%' || o.norm || '%'
             OR o.norm LIKE '%' || n.norm || '%')
ORDER BY n.id, ST_Distance(n.location, o.location)
ON CONFLICT DO NOTHING
""")


async def rebuild_links(session: AsyncSession) -> dict:
    await session.execute(text("DELETE FROM facility_link"))
    medical = (await session.execute(_LINK_MEDICAL)).rowcount
    cross = (await session.execute(_LINK_CROSS)).rowcount
    return {"medical": medical, "cross": cross}
