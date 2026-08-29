"""내부 의료·시설 resolver 결과를 공통 Place 계약으로 옮기는 순수 adapter.

canonical `/v2/places/search`가 이 함수들을 호출한다. 현재 facility_link는 검증된
identity가 아니므로 aliases로 자동 변환하지 않는다.
"""

from datetime import datetime

from app.geo.schemas import PlaceOut
from app.place.contracts import (
    FieldProvenance,
    MedicalFacts,
    PetAccessFacts,
    PlaceClassification,
    PlaceFacts,
    PlaceMatch,
    PlaceRef,
    PlaceResult,
    RestrictionChip,
    RestrictionFacts,
)
from app.place.facility_resolver import FacilityOut, RestrictionsOut
from app.place.restriction_map import LABELS, SUBJECT_LABELS, P, Subject
from app.place.source_catalog import (
    KCISA_KIND_MAPPING_VERSION as KCISA_MAPPING_VERSION,
)
from app.place.source_catalog import (
    KCISA_KINDS,
    KTO_KINDS,
)
from app.place.source_catalog import (
    KTO_KIND_MAPPING_VERSION as KTO_MAPPING_VERSION,
)
from app.place.source_catalog import (
    MOIS_KIND_MAPPING_VERSION as MOIS_MAPPING_VERSION,
)
from app.place.source_catalog import (
    MOIS_SOURCES as SOURCES,
)

_FACILITY_MAPPING_VERSIONS = {
    "kcisa": KCISA_MAPPING_VERSION,
    "kto": KTO_MAPPING_VERSION,
}
_FACILITY_KIND_MAPPINGS = {
    "kcisa": KCISA_KINDS,
    "kto": KTO_KINDS,
}
_MEDICAL_SOURCES = {
    definition.source: definition for definition in SOURCES.values()
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _required_ref(source: str | None, ref: str | None) -> PlaceRef:
    if not source or not ref:
        raise ValueError("PlaceResult requires an external (source, ref) key")
    return PlaceRef(source=source, ref=ref)


def _restriction_facts(value: RestrictionsOut) -> RestrictionFacts:
    """resolver 술어 → 라벨이 붙은 계약. **알 수 없는 코드는 조용히 버리지 않는다.**

    코드가 라벨 사전에 없다는 것은 판독표와 라벨이 어긋났다는 뜻이다. 그 칩을 빼고
    내보내면 사용자는 조건이 없는 줄 알게 되고, 우리는 그 사실을 영영 모른다 —
    `mapping_version` 불일치를 adapter 가 오류로 거부하는 것과 같은 이유다.
    """
    chips: list[RestrictionChip] = []
    for predicate in value.predicates:
        if predicate.code not in LABELS:
            raise ValueError(f"restriction code has no label: {predicate.code}")
        subject = Subject(predicate.applies_to)
        chips.append(RestrictionChip(
            code=predicate.code,
            label=_chip_label(P(predicate.code, subject), predicate.params),
            applies_to=subject.value,
            params=dict(predicate.params),
            certainty=predicate.certainty,
        ))
    return RestrictionFacts(
        state=value.state,
        parse_state=value.parse_state,
        chips=chips,
        raw=value.raw,
    )


# 수치가 있는 술어는 값이 라벨에 보여야 한다 — `마리 수 제한` 만으로는
# 1마리와 5마리가 같은 칩이 된다.
_PARAM_LABELS = {
    ("limit:max_dogs", "max"): "최대 {}마리",
    ("deny:age", "max_months"): "{}개월 미만",
    ("deny:age", "min_years"): "{}살 이상",
    ("deny:size", "min_kg"): "{}kg 이상",
    ("fee:deposit", "amount"): "예치금 {}원",
}


def _chip_label(predicate: P, params: dict[str, str] | None = None) -> str:
    """`입마개·대형견` · `최대 2마리`. 한정어나 수치가 사라지면 칩이 거짓이 된다."""
    for key, value in (params or {}).items():
        template = _PARAM_LABELS.get((predicate.code, key))
        if template is not None:
            return template.format(value)
    base = LABELS[predicate.code]
    qualifier = SUBJECT_LABELS[predicate.applies_to]
    return f"{base}·{qualifier}" if qualifier else base


def medical_place_result(value: PlaceOut) -> PlaceResult:
    """MOIS `PlaceOut` → 공통 계약. ORM 내부 id는 결과에 복사하지 않는다."""
    key = _required_ref(value.source, value.source_ref)
    try:
        source_definition = _MEDICAL_SOURCES[key.source]
    except KeyError as exc:
        raise ValueError(f"unknown medical classification source: {key.source}") from exc
    if value.kind != source_definition.kind:
        raise ValueError(
            f"stale medical kind for {key.source}:{key.ref}: "
            f"expected {source_definition.kind}, got {value.kind}"
        )

    field_sources: dict[str, FieldProvenance] = {}
    if value.hours_source:
        if not value.hours_source_ref:
            raise ValueError("borrowed medical hours require an external source ref")
        borrowed = FieldProvenance(
            source=PlaceRef(
                source=str(value.hours_source["name"]), ref=value.hours_source_ref,
            ),
            as_of=value.hours_source.get("as_of"),
        )
        if value.hours_text is not None:
            field_sources["facts.hours_text"] = borrowed
        if value.closed_days is not None:
            field_sources["facts.closed_days"] = borrowed

    return PlaceResult(
        key=key,
        name=value.name,
        lat=value.lat,
        lng=value.lng,
        distance_m=value.distance_m,
        match=PlaceMatch(source=key, kind=value.kind),
        classifications=[PlaceClassification(
            source=key,
            source_category=source_definition.slug,
            kind=value.kind,
            mapping_version=MOIS_MAPPING_VERSION,
            as_of=_iso(value.source_updated_at),
        )],
        facts=PlaceFacts(
            address=value.address,
            phone=value.phone,
            hours_text=value.hours_text,
            closed_days=value.closed_days,
            medical=MedicalFacts(
                active=value.active,
                license_status_code=value.license_status_code,
                license_status_name=value.license_status_name,
                open_now=value.open_now,
                hours_today=value.hours_today,
                area_m2=value.area_m2,
                staff_count=value.staff_count,
            ),
        ),
        field_sources=field_sources,
    )


def facility_place_result(value: FacilityOut) -> PlaceResult:
    """KCISA/KTO 내부 시설 결과 → 공통 계약. legacy category3 표시는 그대로 둔다."""
    key = _required_ref(value.source.name, value.source_ref)
    try:
        mapping_version = _FACILITY_MAPPING_VERSIONS[key.source]
        kind_mapping = _FACILITY_KIND_MAPPINGS[key.source]
    except KeyError as exc:
        raise ValueError(f"unknown facility classification source: {key.source}") from exc
    if not value.classification_category:
        raise ValueError(f"missing mapping input category for {key.source}:{key.ref}")
    expected_kind = kind_mapping.get(value.classification_category, "etc")
    if value.kind != expected_kind:
        raise ValueError(
            f"stale facility kind for {key.source}:{key.ref}: "
            f"{value.classification_category} maps to {expected_kind}, got {value.kind}"
        )

    field_sources: dict[str, FieldProvenance] = {}
    borrowed_fields = value.place_field_sources or value.field_sources
    for field, source in borrowed_fields.items():
        borrowed_key = _required_ref(source.name, source.ref)
        path = "facts.pet_access" if field == "pet" else f"facts.{field}"
        field_sources[path] = FieldProvenance(source=borrowed_key, as_of=source.as_of)

    return PlaceResult(
        key=key,
        name=value.name,
        lat=value.lat,
        lng=value.lng,
        distance_m=value.distance_m,
        match=PlaceMatch(source=key, kind=value.kind),
        classifications=[PlaceClassification(
            source=key,
            source_category=value.classification_category,
            kind=value.kind,
            mapping_version=mapping_version,
            as_of=value.source.as_of,
        )],
        facts=PlaceFacts(
            address=value.address,
            phone=value.phone,
            homepage=value.homepage,
            hours_text=value.hours_text,
            closed_days=value.closed_days,
            parking=value.parking,
            indoor=value.indoor,
            outdoor=value.outdoor,
            pet_access=PetAccessFacts(
                raw=value.pet,
                allowed=value.pet_axes.allowed,
                exclusive=value.pet_axes.exclusive,
                dog_ok=value.pet_axes.dog_ok,
                size_class=value.pet_axes.size_class,
                max_kg=value.pet_axes.max_kg,
            ),
            restrictions=_restriction_facts(value.restrictions),
        ),
        field_sources=field_sources,
    )
