"""반경 검색 — 결정론. 병원/약국 공용, 나중에 산책 랜드마크도 이 함수를 쓴다.

여기는 **순수 DB 검색**만 산다. 정적 지도 preview·deeplink 조립은 `search_surface.py` 다 —
canonical Place resolver 가 쓰는 것은 `find_authoritative_places` 뿐이라, provider 가
이 파일에 있으면 Place 검색 import closure 에 지도 제공사 전체가 딸려 온다.
"""

from geoalchemy2 import Geography, Geometry
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.geo.contract import SearchPlan
from app.geo.facility_hours import attach_facility_hours
from app.geo.hours import is_open_at, today_ranges
from app.geo.models import Place
from app.geo.ranking import band_boost_sorted, prefer_boost
from app.geo.schemas import PlaceOut
from app.geo.tagging import dog_ok


def _point(lat: float, lng: float):
    return cast(func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326), Geography)


async def _find_places(
    db: AsyncSession,
    plan: SearchPlan,
    *,
    only_dog_ok: bool,
    authoritative_source: str | None,
    require_source_ref: bool,
) -> list[PlaceOut]:
    must = plan.must
    origin = _point(must.lat, must.lng)
    dist = func.ST_Distance(Place.location, origin).label("distance_m")
    geom = cast(Place.location, Geometry)

    stmt = (
        select(Place, dist, func.ST_Y(geom).label("lat"), func.ST_X(geom).label("lng"))
        .where(Place.active.is_(True))
        .where(func.ST_DWithin(Place.location, origin, must.radius_m))
    )
    if must.kind:
        stmt = stmt.where(Place.kind == must.kind)
    if authoritative_source is not None:
        stmt = stmt.where(Place.source == authoritative_source)
    if require_source_ref:
        stmt = stmt.where(Place.source_id.is_not(None))
    # **야간·응급은 거르지 않는다.** 인허가 원천엔 진료 능력이 없어서 이 태그들은 간판 이름
    # 정규식이 전부다 (geo/tagging.py). 실측 2026-08-20, 활성 병원 5,457곳 중
    # night 1곳 · emergency 2곳 — WHERE 로 쓰면 반경 안 결과가 통째로 사라진다.
    # '미상은 제외하지 않는다'(open_now)와 같은 판단이다: 모르는 걸 없는 것으로 취급하지 않는다.
    # 진짜 요구('24시만 보여줘')는 require_tags 로 온다 — 그건 사용자가 명시했으니 거른다.
    if must.require_tags:
        stmt = stmt.where(Place.tags.contains(list(must.require_tags)))
    if must.exclude_ids:
        stmt = stmt.where(Place.id.notin_(list(must.exclude_ids)))
    prefer = set(plan.prefer.tags)
    fetch = must.limit * 4 if must.open_now else must.limit * 2
    # prefer 를 SQL 정렬로 앞당기지 않는다. 태그 우선 + [:limit] 절단은 사실상 필터가 된다 —
    # 실측(강남역 5km): 태그 10곳이 전부 들어오면 근접 병원 10곳이 집합에서 밀려난다.
    # '빼지 않는다'는 약속은 집합 단위로 지켜야 한다. 희귀 태그가 fetch 창 밖인 문제
    # (예은, top-12 밖)는 기본 모음/추천 모음 분리에서 두 번째 조회로 푼다 (backlog).
    stmt = stmt.order_by(dist).limit(fetch)

    rows = (await db.execute(stmt)).all()
    out: list[PlaceOut] = []
    for place, distance_m, plat, plng in rows:
        tags = list(place.tags or [])
        if only_dog_ok and not dog_ok(tags):
            continue
        open_flag = is_open_at(place.hours, must.judge_at, is_24h=place.is_24h)
        # **미상은 제외하지 않는다** (condition-schema.md). 확정 '영업종료'만 뺀다.
        # 인허가 원천에 영업시간이 없어 실데이터는 대부분 미상이다 - 여기서 빼면 결과가 통째로 사라진다.
        if must.open_now and open_flag is False:
            continue
        out.append(PlaceOut(
            id=place.id, kind=place.kind, name=place.name, lat=plat, lng=plng,
            distance_m=int(distance_m), address=place.address, phone=place.phone,
            is_night=place.is_night, is_24h=place.is_24h, open_now=open_flag,
            hours_today=today_ranges(place.hours, must.judge_at), tags=tags,
            area_m2=float(place.area_m2) if place.area_m2 is not None else None,
            staff_count=place.staff_count,
            prefer_hit=sorted(set(tags) & prefer),
            boost=prefer_boost(sorted(set(tags) & prefer)),
            source=place.source, source_ref=place.source_id,
            source_updated_at=place.source_updated_at, active=place.active,
            license_status_code=place.license_status_code,
            license_status_name=place.license_status_name,
        ))
    # 선호 부스트는 거리 밴드(500m) 안에서만 순서를 바꾼다 — 결정 #20, geo/ranking.py.
    # 이전에는 prefer_hit 을 계산해 놓고 정렬에 쓰지 않아 `night=true` 가 순서를 안 바꿨다 (#24).
    out = band_boost_sorted(out, distance_of=lambda p: p.distance_m, boost_of=lambda p: p.boost)
    if must.open_now:
        # 확정 영업중을 앞으로, 미상은 뒤로 - 빼지는 않는다. 위 밴드 순서는 각 묶음 안에서 유지.
        out.sort(key=lambda p: (p.open_now is not True,))
    out = out[:must.limit]
    # 표시 보강만 - 존재·정렬·open_now 판정엔 관여하지 않는다 (facility_hours.py).
    await attach_facility_hours(db, out)
    return out


async def find_places(
    db: AsyncSession,
    plan: SearchPlan,
    *,
    only_dog_ok: bool = True,
) -> list[PlaceOut]:
    """Legacy 의료 검색. 개발 seed를 포함한 기존 source 범위를 그대로 유지한다."""
    return await _find_places(
        db,
        plan,
        only_dog_ok=only_dog_ok,
        authoritative_source=None,
        require_source_ref=False,
    )


async def find_authoritative_places(
    db: AsyncSession,
    plan: SearchPlan,
    *,
    source: str,
) -> list[PlaceOut]:
    """Canonical resolver용. 지정 원천과 외부 ref가 모두 있는 의료 행만 반환한다."""
    return await _find_places(
        db,
        plan,
        only_dog_ok=False,
        authoritative_source=source,
        require_source_ref=True,
    )


