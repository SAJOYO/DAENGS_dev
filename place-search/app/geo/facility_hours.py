"""의료 place에 기반층(facility)의 운영시간을 빌려 붙인다.

존재·상태는 place(인허가)가 결정하고, 여기서는 표시 필드만 보강한다.
빌려온 값에는 원천과 기준일이 같이 붙는다 — 스냅샷이 낡아도 시스템이
거짓말은 안 하게 하는 자리다. 같은 place에 원천이 여럿 걸리면
운영시간이 실제로 있는 쪽 → 더 최신 쪽 순으로 하나만 고른다.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.geo.schemas import PlaceOut

_HOURS = text("""
SELECT DISTINCT ON (l.source_ref)
       l.source_ref::bigint AS place_id,
       f.hours_text, f.closed_days, f.source, f.source_ref,
       COALESCE(f.last_written::text, f.snapshot) AS as_of
FROM facility_link l
JOIN facility f ON f.id = l.facility_id
WHERE l.source = 'mois:place' AND l.source_ref = ANY(:ids)
ORDER BY l.source_ref, (f.hours_text IS NULL), f.last_written DESC NULLS LAST
""")


async def attach_facility_hours(db: AsyncSession, places: list[PlaceOut]) -> None:
    if not places:
        return
    rows = await db.execute(_HOURS, {"ids": [str(p.id) for p in places]})
    by_id = {r.place_id: r for r in rows}
    for p in places:
        r = by_id.get(p.id)
        if r is None or r.hours_text is None:
            continue
        p.hours_text = r.hours_text
        p.closed_days = r.closed_days
        p.hours_source = {"name": r.source, "as_of": r.as_of}
        p.hours_source_ref = r.source_ref
