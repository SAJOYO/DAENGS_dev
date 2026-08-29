"""facility 기반층의 내부 resolver와 legacy-compatible 결과 모델.

의료(hospital/pharmacy)는 여기서 안 나온다 — 존재 권위가 place(인허가)에 있어
MedicalResolver가 담당한다. API는 이 resolver를 호출할 뿐 SQL이나 병합 규칙을 소유하지 않는다.

같은 시설이 두 원천에 있으면 노출 행은 하나지만 **필드는 병합한다** — 존재·이름·좌표는
최신 원천, 그 원천이 안 주는 운영시간·주차·홈페이지는 과거 원천에서 빌린다.
행 단위 승자독식으로 숨기면 KTO(목록만)가 KCISA(운영시간 보유)를 가려서 정보가 사라진다.
의료 쪽 `attach_facility_hours` 와 같은 철학이다: 존재는 한 원천이, 필드는 있는 쪽이.

빌린 필드에는 `field_sources` 로 어느 원천의 언제 값인지가 붙는다 —
2025-03 스냅샷이 낡았음을 숨기지 않는다.
"""

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.geo.icons import IconGroup, icon_group
from app.geo.pet import accepting_size_classes
from app.geo.ranking import (
    DISTANCE_BAND_M,
    band_boost_sorted,
    facility_preference_tags,
    prefer_boost,
)
from app.place.contracts import DogSize
from app.place.restriction_map import RESTRICTION_SEMANTICS_VERSION

MEDICAL = ("hospital", "pharmacy")
CANONICAL_SOURCES = ("kcisa", "kto")

# 서버가 세우는 자원 경계. `/anchor/search` 의 `MAX_LIMIT` 과 같은 값·같은 이유다 —
# 호출자가 카테고리로 나눠 부를 것이라는 기대는 경계가 아니다. 반경 상한(20km)만으로는
# 부족하다: 강남역 20km 는 kind 를 지정해도 4,966 곳(3MB)이라 `kind` 를 요구해봐야
# 최악이 거의 안 준다. 실제 지도 사용(반경 3km, kind 별)은 수백 곳이라 이 상한에 안 닿는다.
MAX_RESULTS = 3000


class FacilityParams(BaseModel):
    lat: float = Field(ge=32, le=40)
    lng: float = Field(ge=123, le=133)
    radius_m: int = Field(3000, ge=100, le=20000)
    kind: str | None = None            # cafe/travel/grooming/... 미지정 = 비의료 전체
    # 미지정 = 반경 안 전부(서버 상한까지). 지도는 반경 안을 다 그려야 하고, 잘린 목록을
    # "이 동네엔 이만큼뿐"으로 읽으면 "우리 개가 갈 곳이 없다"가 된다. 리스트 화면이 몇 개만
    # 원하면 그때 명시한다. 어느 쪽이든 잘리면 `truncated` 로 말한다.
    limit: int | None = Field(None, ge=1, le=MAX_RESULTS)
    # 개 크기. 시설의 상한이 아니라 **데려갈 개**의 크기다 — 서버가 받을 수 있는 등급으로 편다.
    # identity(dog_id)가 아니라 값이다 — 프로필 → 크기 projection 은 프로필 소유자의 일이고,
    # resolver 는 장소 후보만 안다.
    dog_size: DogSize | None = None
    # 종을 열거하면서 개를 뺀 시설을 제외한다. place 검색의 `only_dog_ok` 와 같은 뜻.
    only_dog_ok: bool = True
    # 아래 둘은 **필터가 아니라 선호**다. 결과를 빼지 않고 거리 밴드 안에서만 순서를 바꾼다
    # (결정 #20). 무엇이 이 불을 켜는지는 호출자가 정한다 — geo/ranking.py 의 경계와 같다.
    parking: bool = False
    dog_exclusive: bool = False

    @field_validator("kind")
    @classmethod
    def reject_legacy_goods(cls, value: str | None) -> str | None:
        if value == "goods":
            raise ValueError("goods was split into pet_shop and shopping")
        return value


class PetAxesOut(BaseModel):
    """`pet` 원문에서 뽑은 축. None 은 미상이지 '아님'이 아니다 (explorations/facility/pet-axes.md)."""

    allowed: bool | None = None
    exclusive: bool | None = None
    dog_ok: bool | None = None
    size_class: str | None = None
    max_kg: float | None = None


class RestrictionPredicateOut(BaseModel):
    """술어 하나. `applies_to` 가 있어야 소형견에게 없는 제한을 안 보여준다."""

    code: str
    applies_to: str = "all"
    params: dict[str, str] = Field(default_factory=dict)
    certainty: str = "firm"


class RestrictionsOut(BaseModel):
    """`pet.restrictions` 문장의 판독 결과 (결정 #70).

    `state` 와 `parse_state` 는 다른 것을 말한다 — 원천이 무엇을 말했나 / 우리가 읽었나.
    술어가 0개인 이유가 셋(모름·제한없음·못읽음)이라 둘을 함께 봐야 구분된다.
    """

    state: str = "unknown"
    parse_state: str | None = None
    predicates: list[RestrictionPredicateOut] = Field(default_factory=list)
    # 술어가 원문을 다 담지 못한 행은 원문을 함께 보여야 한다. 칩만 보이면 완결로 읽힌다.
    raw: str | None = None


class FacilitySourceOut(BaseModel):
    name: str                          # kcisa | kto
    as_of: str                         # 스냅샷 날짜 또는 원천 수정일
    # Place adapter 전용 내부 identity. resolver 모델의 직렬화 표면에는 노출하지 않는다.
    ref: str | None = Field(None, exclude=True, repr=False)


class FacilityOut(BaseModel):
    id: int                            # 내부 PK. 외부가 잡을 식별자는 (source, source_ref)
    source_ref: str | None
    name: str
    kind: str
    icon_group: IconGroup      # 지도 마커 그룹. kind 가 늘어도 앱은 이 값만 본다
    category3: str
    lat: float
    lng: float
    distance_m: int
    address: str | None
    phone: str | None
    homepage: str | None
    hours_text: str | None
    closed_days: str | None
    parking: bool | None
    pet: dict                          # 원문 봉투. 술어가 못 담은 문장은 여기 원문으로 남는다
    pet_axes: PetAxesOut               # 위 봉투에서 뽑은 축 — 필터·정렬이 쓰는 것은 이쪽
    restrictions: RestrictionsOut      # 같은 봉투의 `restrictions` 문장에서 파생한 술어
    source: FacilitySourceOut
    field_sources: dict[str, FacilitySourceOut] = Field(default_factory=dict)
    prefer_hit: list[str] = Field(default_factory=list)  # 선호와 이 행의 교집합 — 부스트 근거
    boost: int = 0                     # 거리 밴드 안에서만 순서를 바꾼다
    # 실제 kind 매핑 입력. KTO의 legacy category3는 상세분류라 raw.contenttypeid를 따로 쓴다.
    classification_category: str | None = Field(None, exclude=True, repr=False)
    indoor: bool | None = Field(None, exclude=True, repr=False)
    outdoor: bool | None = Field(None, exclude=True, repr=False)
    place_field_sources: dict[str, FacilitySourceOut] = Field(
        default_factory=dict, exclude=True, repr=False,
    )


class FacilitySearchOut(BaseModel):
    params: FacilityParams
    # 상한에 걸렸나. `/anchor/search` 와 같은 이유로 있다 — 조용히 자르면 "이 반경엔
    # 이만큼뿐"으로 읽힌다. 지도 표면에서는 그 오독이 "우리 개가 갈 곳이 없다"가 된다.
    truncated: bool = False
    results: list[FacilityOut]


# 노출 행: 교차 링크의 ref 로 잡힌 쪽(과거 원천)은 빼고, 그 행을 LATERAL 로 끌어와
# 빈 필드를 채운다. 링크가 없으면 b.* 는 전부 NULL 이고 결과는 원래 행 그대로다.
#
# pet 은 원문과 파생 축이 한 묶음이다. 먼저 `merged` 에서 실제로 노출할 봉투/축을 한 번 정한 뒤
# 바깥 WHERE 도 그 effective 축을 본다. 그래야 KTO 행이 KCISA 의 "5kg 이하"를 빌린 경우
# 대형견 필터를 NULL(미상)로 통과한 뒤 small 을 표시하는 모순이 생기지 않는다.
_SEARCH = text("""
WITH merged AS (
    SELECT f.id, f.source_ref, f.name, f.kind, f.category3,
           CASE WHEN f.source = 'kto'
                THEN COALESCE(f.raw->>'contenttypeid', f.category3)
                ELSE f.category3 END AS classification_category,
           ST_Y(f.location::geometry) AS lat, ST_X(f.location::geometry) AS lng,
           ST_Distance(f.location, o.geom) AS distance_m,
           f.address, f.phone, f.homepage, f.hours_text, f.closed_days,
           -- parking 도 순위에 쓰이므로 pet 과 같이 SQL 에서 effective 를 정한다. 파이썬
           -- `_merge` 뒤에야 정해지면 SQL 이 순위 키를 만들 수 없다.
           CASE WHEN f.parking IS NULL AND b.parking IS NOT NULL
                THEN b.parking ELSE f.parking END AS parking,
           (f.parking IS NULL AND b.parking IS NOT NULL) AS parking_borrowed,
           CASE WHEN f.indoor IS NULL AND b.indoor IS NOT NULL
                THEN b.indoor ELSE f.indoor END AS indoor,
           (f.indoor IS NULL AND b.indoor IS NOT NULL) AS indoor_borrowed,
           CASE WHEN f.outdoor IS NULL AND b.outdoor IS NOT NULL
                THEN b.outdoor ELSE f.outdoor END AS outdoor,
           (f.outdoor IS NULL AND b.outdoor IS NOT NULL) AS outdoor_borrowed,
           CASE WHEN (f.pet IS NULL OR f.pet = '{}'::jsonb)
                     AND b.pet IS NOT NULL AND b.pet <> '{}'::jsonb
                THEN b.pet ELSE f.pet END AS pet,
           CASE WHEN (f.pet IS NULL OR f.pet = '{}'::jsonb)
                     AND b.pet IS NOT NULL AND b.pet <> '{}'::jsonb
                THEN b.pet_allowed ELSE f.pet_allowed END AS pet_allowed,
           CASE WHEN (f.pet IS NULL OR f.pet = '{}'::jsonb)
                     AND b.pet IS NOT NULL AND b.pet <> '{}'::jsonb
                THEN b.pet_exclusive ELSE f.pet_exclusive END AS pet_exclusive,
           CASE WHEN (f.pet IS NULL OR f.pet = '{}'::jsonb)
                     AND b.pet IS NOT NULL AND b.pet <> '{}'::jsonb
                THEN b.pet_dog_ok ELSE f.pet_dog_ok END AS pet_dog_ok,
           CASE WHEN (f.pet IS NULL OR f.pet = '{}'::jsonb)
                     AND b.pet IS NOT NULL AND b.pet <> '{}'::jsonb
                THEN b.pet_size_class ELSE f.pet_size_class END AS pet_size_class,
           CASE WHEN (f.pet IS NULL OR f.pet = '{}'::jsonb)
                     AND b.pet IS NOT NULL AND b.pet <> '{}'::jsonb
                THEN b.pet_max_kg ELSE f.pet_max_kg END AS pet_max_kg,
           ((f.pet IS NULL OR f.pet = '{}'::jsonb)
             AND b.pet IS NOT NULL AND b.pet <> '{}'::jsonb) AS pet_borrowed,
           -- 제약 술어는 `pet` 봉투의 함수다. 봉투를 빌리면 술어도 같이 빌려야
           -- "빌린 원문 + 자기 술어" 라는 어긋난 짝이 안 생긴다.
           CASE WHEN (f.pet IS NULL OR f.pet = '{}'::jsonb)
                     AND b.pet IS NOT NULL AND b.pet <> '{}'::jsonb
                THEN b.restriction_state ELSE f.restriction_state END AS restriction_state,
           CASE WHEN (f.pet IS NULL OR f.pet = '{}'::jsonb)
                     AND b.pet IS NOT NULL AND b.pet <> '{}'::jsonb
                THEN b.restriction_parse_state
                ELSE f.restriction_parse_state END AS restriction_parse_state,
           CASE WHEN (f.pet IS NULL OR f.pet = '{}'::jsonb)
                     AND b.pet IS NOT NULL AND b.pet <> '{}'::jsonb
                THEN b.restriction_predicates
                ELSE f.restriction_predicates END AS restriction_predicates,
           CASE WHEN (f.pet IS NULL OR f.pet = '{}'::jsonb)
                     AND b.pet IS NOT NULL AND b.pet <> '{}'::jsonb
                THEN b.restriction_semantics_version
                ELSE f.restriction_semantics_version END AS restriction_semantics_version,
           f.source, COALESCE(f.last_written::text, f.snapshot) AS as_of,
           b.homepage AS b_homepage, b.hours_text AS b_hours_text,
           b.closed_days AS b_closed_days,
           b.source AS b_source, b.source_ref AS b_source_ref,
           COALESCE(b.last_written::text, b.snapshot) AS b_as_of
    FROM facility f
    CROSS JOIN (SELECT ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography AS geom) o
    LEFT JOIN LATERAL (
        SELECT f2.homepage, f2.hours_text, f2.closed_days,
               f2.parking, f2.indoor, f2.outdoor, f2.pet,
               f2.pet_allowed, f2.pet_exclusive, f2.pet_dog_ok, f2.pet_size_class, f2.pet_max_kg,
               f2.restriction_state, f2.restriction_parse_state, f2.restriction_predicates,
               f2.restriction_semantics_version,
               f2.source, f2.source_ref, f2.last_written, f2.snapshot
        FROM facility_link l
        JOIN facility f2 ON f2.id = l.source_ref::bigint
        WHERE l.source = 'facility' AND l.facility_id = f.id
          AND (:require_canonical_identity IS NOT TRUE
               OR (f2.source = ANY(:canonical_sources) AND f2.source_ref IS NOT NULL))
          -- cross-kind link는 동일 장소의 복수 분류 후보일 수 있다. scalar kind 응답에서는
          -- 다른 후보군의 값을 빌리지 않는다.
          AND f2.kind = f.kind
        ORDER BY (f2.hours_text IS NULL), f2.last_written DESC NULLS LAST
        LIMIT 1
    ) b ON true
    WHERE f.kind <> ALL(:medical)
      AND (:require_canonical_identity IS NOT TRUE
           OR (f.source = ANY(:canonical_sources) AND f.source_ref IS NOT NULL))
      AND (CAST(:kind AS text) IS NULL OR f.kind = :kind)
      AND ST_DWithin(f.location, o.geom, :radius_m)
      AND NOT EXISTS (
          SELECT 1
          FROM facility_link l
          JOIN facility winner ON winner.id = l.facility_id
          WHERE l.source = 'facility' AND l.source_ref = f.id::text
            -- 같은 후보군에서만 중복을 접는다. shopping winner 때문에 pet_shop 후보가
            -- 사라지면 canonical kind → 후보군 순서가 깨진다.
            AND winner.kind = f.kind
      )
)
SELECT *,
       -- 선호 적중 수. 부스트 점수(`prefer_boost`)가 적중 수에 단조라 순서가 같다 —
       -- 점수식을 SQL 에 복제하지 않으려고 수를 센다. 부스트에 다른 재료가 더해지면
       -- (병원은 근거 수를 더한다) 이 단조성이 깨지므로 여기도 같이 고쳐야 한다.
       (CASE WHEN :want_parking AND parking IS TRUE THEN 1 ELSE 0 END
        + CASE WHEN :want_exclusive AND pet_exclusive IS TRUE THEN 1 ELSE 0 END) AS prefer_hits
FROM merged
WHERE
  -- 종을 열거하면서 개를 뺀 곳만 제외한다. 종 표기가 없는 곳(NULL)은 개 전제라 남는다.
  (:only_dog_ok IS NOT TRUE OR pet_dog_ok IS NOT FALSE)
  -- **미상은 빼지 않는다.** 크기 등급이 NULL 인 곳은 제약을 모르는 것이지 못 가는 곳이 아니다.
  AND (CAST(:dog_size AS text) IS NULL
       OR pet_size_class IS NULL
       OR pet_size_class = ANY(:size_accepts))
-- 순위 키를 여기서 만드는 이유: 거리로만 자르고 파이썬에서 부스트를 매기면, 밴드가 빽빽할 때
-- 선호 시설이 자른 창 밖에 남는다. `limit` 20 인데 0~400m 에 40곳이 있으면 450m 의 주차
-- 가능 시설은 같은 밴드인데도 후보에 못 들어와 부스트가 아예 작동하지 않는다.
-- 이건 태그 우선 정렬(`geo/search.py` 가 금지한 것)이 아니라 결정 #20 의 rank key 그대로다.
-- 같은 rank key가 LIMIT 경계에 걸려도 후보 집합이 실행계획에 따라 흔들리지 않아야 한다.
-- canonical 행은 (source, source_ref)가 unique이고, ref가 없는 legacy 행은 마지막 id가 닫는다.
ORDER BY floor(distance_m / :band_m), prefer_hits DESC, distance_m,
         source, source_ref NULLS LAST, id
LIMIT :limit
""")

# 파이썬이 빌리는 필드. 값이 비어 있을 때만 뒤 원천에서 가져온다.
_BORROWABLE = ("homepage", "hours_text", "closed_days")

# SQL `merged` 가 이미 effective 를 정한 필드. 순위에 쓰이는 것은 전부 여기 있어야 한다 —
# 파이썬 병합을 기다리면 SQL 이 순위 키를 만들 수 없다. 여기서는 출처 라벨만 붙인다.
_SQL_MERGED = (
    "parking", "indoor", "outdoor",
    "pet", "pet_allowed", "pet_exclusive", "pet_dog_ok", "pet_size_class", "pet_max_kg",
    # 제약 축은 `pet` 봉투와 한 묶음이다 — 봉투를 빌리면 같이 빌려온다 (SQL `merged`).
    "restriction_state", "restriction_parse_state", "restriction_predicates",
    "restriction_semantics_version",
)


def _merge(row) -> tuple[dict, dict]:
    """(필드값, 필드별 출처). SQL 이 병합한 것은 라벨만, 나머지는 여기서 빈 값을 빌린다."""
    values = {name: getattr(row, name) for name in (*_BORROWABLE, *_SQL_MERGED)}
    borrowed: dict[str, FacilitySourceOut] = {}
    if row.b_source is None:
        return values, borrowed
    source = FacilitySourceOut(name=row.b_source, ref=row.b_source_ref, as_of=row.b_as_of)
    for name in _BORROWABLE:
        own, other = values[name], getattr(row, f"b_{name}")
        if own in (None, {}, "") and other not in (None, {}, ""):
            values[name] = other
            borrowed[name] = source
    if row.pet_borrowed:
        borrowed["pet"] = source
        # 제약 술어도 같은 pet 봉투에서 함께 빌린 값이다. Place 계층이 이 provenance를
        # 보고 미검증 facility_link를 자동 불가 판정의 근거로 쓰지 않는다.
        borrowed["restrictions"] = source
    for name in ("parking", "indoor", "outdoor"):
        if getattr(row, f"{name}_borrowed"):
            borrowed[name] = source
    return values, borrowed


def _restrictions_out(values: dict) -> RestrictionsOut:
    """저장된 파생값 → 응답 모델. **원문은 술어가 다 담지 못했을 때만 싣는다.**

    `mapped` 행까지 원문을 실으면 응답이 두 배가 되고, 클라이언트가 술어 대신 문자열을
    파싱하는 우회로가 생긴다. `partial`·`raw_only` 는 반대로 원문이 없으면 사용자가
    빠진 조건을 알 방법이 없다 — 칩만 보이면 그 목록이 완결로 읽힌다.
    """
    if values.get("restriction_semantics_version") != RESTRICTION_SEMANTICS_VERSION:
        # 이전 규칙으로 만든 값이나 아직 파생하지 않은 값은 현재 사실로 내보내지 않는다.
        # 배치가 다시 계산할 때까지 fail closed: 제약을 모른다고만 말한다.
        return RestrictionsOut()
    state = values.get("restriction_state")
    if state is None:
        # 아직 파생 배치가 안 돈 행. 없는 사실을 지어내지 않고 미상으로 둔다.
        return RestrictionsOut()
    parse_state = values.get("restriction_parse_state")
    predicates = [
        RestrictionPredicateOut(**item) for item in (values.get("restriction_predicates") or [])
    ]
    raw = None
    if parse_state in ("partial", "raw_only"):
        raw = (values.get("pet") or {}).get("restrictions")
    return RestrictionsOut(
        state=state, parse_state=parse_state, predicates=predicates, raw=raw,
    )


def _prefer_tags(values: dict) -> set[str]:
    """이 행이 실제로 갖고 있는 선호 축 → 태그.

    **`_merge` 뒤 병합된 값에서만 뽑는다.** `parking` 은 빌려올 수 있는 필드라 병합 전
    자기 컬럼만 보면 빌린 주차장을 못 세고, 그러면 표시(빌린 값)와 순위(자기 값)가 갈린다 —
    PR #51 이 필터에서 밟은 바로 그 함정이다.
    """
    tags = set()
    if values["parking"] is True:
        tags.add("parking")
    if values["pet_exclusive"] is True:
        tags.add("dog_exclusive")
    return tags


async def resolve_facilities(
    params: FacilityParams,
    db: AsyncSession,
    *,
    max_results: int = MAX_RESULTS,
    require_canonical_identity: bool = False,
) -> FacilitySearchOut:
    prefer = set(facility_preference_tags(
        parking=params.parking, dog_exclusive=params.dog_exclusive,
    ))
    # 미지정이어도 무한이 아니다 — 상한이 서버에 있고, 걸리면 `truncated` 로 알린다.
    effective_limit = params.limit or max_results
    rows = await db.execute(_SEARCH, {
        "lat": params.lat, "lng": params.lng, "radius_m": params.radius_m,
        # +1 은 절단 감지용 한 칸이다.
        "kind": params.kind, "medical": list(MEDICAL), "limit": effective_limit + 1,
        "require_canonical_identity": require_canonical_identity,
        "canonical_sources": list(CANONICAL_SOURCES),
        "only_dog_ok": params.only_dog_ok, "dog_size": params.dog_size,
        "size_accepts": list(accepting_size_classes(params.dog_size)),
        "band_m": DISTANCE_BAND_M,
        "want_parking": params.parking, "want_exclusive": params.dog_exclusive,
    })
    fetched = rows.all()
    truncated = len(fetched) > effective_limit
    if truncated:
        fetched = fetched[:effective_limit]
    results = []
    for r in fetched:
        values, borrowed = _merge(r)
        hit = sorted(_prefer_tags(values) & prefer)
        results.append(FacilityOut(
            id=r.id, source_ref=r.source_ref, name=r.name, kind=r.kind,
            icon_group=icon_group(r.kind), category3=r.category3,
            lat=r.lat, lng=r.lng, distance_m=int(r.distance_m),
            address=r.address, phone=r.phone,
            homepage=values["homepage"], hours_text=values["hours_text"],
            closed_days=values["closed_days"], parking=values["parking"],
            indoor=values["indoor"], outdoor=values["outdoor"],
            pet=values["pet"] or {},
            pet_axes=PetAxesOut(
                allowed=values["pet_allowed"], exclusive=values["pet_exclusive"],
                dog_ok=values["pet_dog_ok"], size_class=values["pet_size_class"],
                max_kg=values["pet_max_kg"],
            ),
            restrictions=_restrictions_out(values),
            source=FacilitySourceOut(name=r.source, ref=r.source_ref, as_of=r.as_of),
            # resolver 결과에는 표시하는 필드의 출처만 유지한다. Place adapter는 숨은
            # 실내외 사실까지 포함한 내부 맵을 쓴다.
            field_sources={
                name: source for name, source in borrowed.items()
                if name not in {"indoor", "outdoor"}
            },
            place_field_sources=borrowed,
            prefer_hit=hit, boost=prefer_boost(hit),
            classification_category=r.classification_category,
        ))
    # SQL 은 **어느 행을 후보로 삼을지**를 정하고, 최종 순서는 여기서 정의된다 —
    # 결정 #20 의 rank key 는 `geo/ranking.py` 한 곳에만 산다. 둘이 어긋나면 순서가 아니라
    # 후보 선택이 틀어지므로, 빽빽한 밴드에서 그걸 잡는 회귀 테스트가 붙어 있다.
    results = band_boost_sorted(
        results, distance_of=lambda f: f.distance_m, boost_of=lambda f: f.boost,
    )
    return FacilitySearchOut(params=params, truncated=truncated, results=results)
