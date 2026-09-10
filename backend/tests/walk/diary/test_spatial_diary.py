import json
import math
import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from daengs_walk.capsule import ContextStatus, TrailContextSnapshot
from daengs_walk.cellophane import CANONICAL_PAINT_SPEC, Cellophane, PaintSpec
from daengs_walk.spatial_diary import (
    CONTEXT_FACET_POLICY_VERSION,
    DIARY_CALENDAR_TIMEZONE,
    ContextFacetFilter,
    DuplicateWalkInViewError,
    MixedPaintGenerationError,
    SpatialDiaryViewReceipt,
    SpatialDiaryViewSpec,
    SpatialField,
    WalkSelector,
    aggregate_spatial_field,
    build_view_receipt,
    context_facets,
    context_is_known,
    matches_walk_selector,
    selector_fingerprint,
)
from tests.walk.support.paths import WALK_FIXTURES

FIXTURE = WALK_FIXTURES / "spatial-diary-view-promotion-v1.json"
T0 = datetime(2026, 9, 1, 9, tzinfo=UTC)
PET_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _context(
    walk_id: uuid.UUID,
    *,
    weather_code: int | None,
    is_day: bool | None,
    precipitation_kind: str | None = None,
) -> TrailContextSnapshot:
    observed = weather_code is not None or is_day is not None or precipitation_kind is not None
    return TrailContextSnapshot(
        walk_id=walk_id,
        status=ContextStatus.PARTIAL if observed else ContextStatus.UNKNOWN,
        walked_at=T0,
        captured_at=T0,
        provider="fixture" if observed else None,
        weather_code=weather_code,
        is_day=is_day,
        precipitation_kind=precipitation_kind,
    )


def _sheet(
    walk_id: uuid.UUID,
    cells: list[list[float]],
    *,
    at: datetime = T0,
    paint_spec: PaintSpec = CANONICAL_PAINT_SPEC,
) -> Cellophane:
    return Cellophane(
        walk_id=walk_id,
        at=at,
        radius_u=paint_spec.radius_u,
        profile=paint_spec.profile_name,
        occupancy={(int(q), int(r)): float(amount) for q, r, amount, _ in cells},
        peak={(int(q), int(r)): float(peak) for q, r, _, peak in cells},
        paint_version=paint_spec.paint_version,
        grid_version=paint_spec.grid_version,
        profile_fp=paint_spec.profile_fp,
        sample_step_m=paint_spec.sample_step_m,
        paint_fp=paint_spec.fingerprint,
    )


def _fixture_sheets() -> tuple[Cellophane, ...]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return tuple(
        _sheet(
            uuid.UUID(walk["id"]),
            walk["cells"],
            at=datetime.fromisoformat(walk["started_at"]),
        )
        for walk in payload["walks"]
    )


def _spec(*facets: ContextFacetFilter, metric: str = "visit_rate"):
    return SpatialDiaryViewSpec(
        walk_selector=WalkSelector(pet_id=PET_ID, context_facets=facets),
        field_metric=metric,
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "dry"),
        (48, "dry"),
        (51, "rain"),
        (56, "rain"),
        (67, "rain"),
        (82, "rain"),
        (99, "rain"),
        (71, "snow"),
        (86, "snow"),
        (4, "unknown"),
        (None, "unknown"),
    ],
)
def test_context_policy_preserves_wmo_weather_meaning(code, expected):
    facets = context_facets(_context(uuid.uuid4(), weather_code=code, is_day=True))

    assert facets.precipitation == expected
    assert facets.daylight == "day"


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("none", "dry"),
        ("rain", "rain"),
        ("snow", "snow"),
        ("mixed", "mixed"),
    ],
)
def test_kma_precipitation_kind_takes_priority_over_wmo(kind, expected):
    context = _context(
        uuid.uuid4(),
        weather_code=61 if kind == "none" else 0,
        is_day=True,
        precipitation_kind=kind,
    )

    assert context_facets(context).precipitation == expected


def test_unknown_context_does_not_invert_missing_atoms():
    context = _context(uuid.uuid4(), weather_code=None, is_day=None)

    assert context_facets(context).model_dump() == {
        "precipitation": "unknown",
        "daylight": "unknown",
    }


def test_context_known_count_uses_filtered_axes_or_all_axes_without_filters():
    context = _context(uuid.uuid4(), weather_code=61, is_day=None)
    precipitation_only = WalkSelector(
        pet_id=PET_ID,
        context_facets=(ContextFacetFilter(axis="precipitation", values=("rain",)),),
    )

    assert context_is_known(context, precipitation_only)
    assert not context_is_known(context, WalkSelector(pet_id=PET_ID))


def test_selector_uses_korean_calendar_and_all_facets():
    walk_id = uuid.uuid4()
    selector = WalkSelector(
        pet_id=PET_ID,
        since=date(2026, 9, 2),
        until=date(2026, 9, 2),
        context_facets=(
            ContextFacetFilter(axis="precipitation", values=("rain",)),
            ContextFacetFilter(axis="daylight", values=("night",)),
        ),
    )
    # UTC 9월 1일이지만 KST로는 9월 2일이다.
    started_at = datetime(2026, 9, 1, 15, 30, tzinfo=UTC)
    context = _context(walk_id, weather_code=61, is_day=False)

    assert matches_walk_selector(selector, started_at=started_at, context=context)
    assert DIARY_CALENDAR_TIMEZONE == "Asia/Seoul"
    assert context_is_known(context, selector)


def test_selector_rejects_duplicate_axes_unknown_values_and_long_ranges():
    rain = ContextFacetFilter(axis="precipitation", values=("rain",))
    with pytest.raises(ValidationError, match="facet axes must be unique"):
        WalkSelector(pet_id=PET_ID, context_facets=(rain, rain))
    with pytest.raises(ValidationError, match="unsupported precipitation"):
        ContextFacetFilter(axis="precipitation", values=("cloudy",))
    with pytest.raises(ValidationError, match="cannot exceed 366 days"):
        WalkSelector(
            pet_id=PET_ID,
            since=date(2025, 1, 1),
            until=date(2026, 1, 2),
        )
    with pytest.raises(ValidationError, match="field_metric"):
        _spec(metric="total_time")


def test_selector_fingerprint_is_stable_and_includes_hidden_policy():
    first = _spec()
    second = _spec(
        ContextFacetFilter(axis="daylight", values=("night",)),
    )

    assert selector_fingerprint(first) == selector_fingerprint(first)
    assert selector_fingerprint(first) != selector_fingerprint(second)
    assert CONTEXT_FACET_POLICY_VERSION == 2


def test_selector_fingerprint_canonicalizes_semantic_set_order():
    first = _spec(
        ContextFacetFilter(axis="precipitation", values=("rain", "snow")),
        ContextFacetFilter(axis="daylight", values=("night", "day")),
    )
    second = _spec(
        ContextFacetFilter(axis="daylight", values=("day", "night")),
        ContextFacetFilter(axis="precipitation", values=("snow", "rain")),
    )

    assert first == second
    assert selector_fingerprint(first) == selector_fingerprint(second)


@pytest.mark.parametrize("metric", ["visit_rate", "walk_utilization"])
def test_geo_promotion_fixture_preserves_field_values_and_denominators(metric):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected = payload["expected"][metric]

    field = aggregate_spatial_field(_fixture_sheets(), metric)
    rows = [[q, r, field.values[(q, r)], field.numerators[(q, r)]] for q, r in field.values]

    assert field.denominator == expected["denominator"]
    assert field.contributing == expected["contributing"]
    assert [(row[0], row[1]) for row in rows] == [(row[0], row[1]) for row in expected["cells"]]
    for actual, wanted in zip(rows, expected["cells"], strict=True):
        assert actual[2:] == pytest.approx(wanted[2:])
    if metric == "walk_utilization":
        assert math.fsum(field.values.values()) == pytest.approx(1.0)


def test_visit_rate_keeps_empty_walk_in_the_denominator():
    field = aggregate_spatial_field(_fixture_sheets(), "visit_rate")

    assert field.selected == 3
    assert field.contributing == 3
    assert field.denominator == 3
    assert field.values[(0, 0)] == pytest.approx(2 / 3)


def test_walk_utilization_weights_contributing_walks_equally():
    field = aggregate_spatial_field(_fixture_sheets(), "walk_utilization")

    assert field.selected == 3
    assert field.contributing == 2
    assert field.denominator == 2
    assert field.values == pytest.approx({(0, 0): 0.4, (0, 1): 0.4, (1, 0): 0.2})


def test_field_rejects_duplicate_walks_and_other_paint_generations():
    sheet = _fixture_sheets()[0]
    with pytest.raises(DuplicateWalkInViewError):
        aggregate_spatial_field((sheet, sheet), "visit_rate")

    other = PaintSpec(
        paint_version=sheet.paint_version + 1,
        grid_version=sheet.grid_version,
        radius_u=sheet.radius_u,
        profile_name=sheet.profile,
        profile_fp=sheet.profile_fp,
        sample_step_m=sheet.sample_step_m,
    )
    with pytest.raises(MixedPaintGenerationError):
        aggregate_spatial_field((_sheet(uuid.uuid4(), [], paint_spec=other),), "visit_rate")


def test_field_maps_are_defensively_copied_and_read_only():
    values = {(0, 0): 1.0}
    numerators = {(0, 0): 1.0}
    field = SpatialField(
        metric="visit_rate",
        values=values,
        numerators=numerators,
        denominator=1.0,
        selected=1,
        contributing=1,
        paint_spec=CANONICAL_PAINT_SPEC,
        unit="ratio",
        normalization="selected_walks",
    )

    values[(0, 0)] = 99.0
    numerators[(0, 0)] = 99.0
    assert field.values[(0, 0)] == 1.0
    assert field.numerators[(0, 0)] == 1.0
    with pytest.raises(TypeError):
        field.values[(0, 0)] = 99.0  # type: ignore[index]
    with pytest.raises(TypeError):
        field.numerators[(0, 0)] = 99.0  # type: ignore[index]


def test_receipt_is_frozen_and_covers_every_selected_context():
    spec = _spec()
    field = aggregate_spatial_field(_fixture_sheets(), "visit_rate")
    receipt = build_view_receipt(
        spec,
        field,
        view_as_of=T0,
        total_capsules=4,
        context_known_count=2,
    )

    assert receipt == SpatialDiaryViewReceipt(
        selector_fingerprint=selector_fingerprint(spec),
        view_as_of=T0,
        total_capsules=4,
        selected_capsules=3,
        contributing_capsules=3,
        context_known_count=2,
        context_unknown_count=1,
        paint_fp=CANONICAL_PAINT_SPEC.fingerprint,
        field_metric="visit_rate",
        normalization="selected_walks",
    )
    with pytest.raises(ValidationError):
        receipt.selected_capsules = 0


def test_receipt_rejects_impossible_denominators_and_naive_time():
    values = {
        "selector_fingerprint": "12345678",
        "view_as_of": T0,
        "total_capsules": 1,
        "selected_capsules": 1,
        "contributing_capsules": 1,
        "context_known_count": 1,
        "context_unknown_count": 0,
        "paint_fp": CANONICAL_PAINT_SPEC.fingerprint,
        "field_metric": "visit_rate",
        "normalization": "selected_walks",
    }
    with pytest.raises(ValidationError, match="must cover selected capsules"):
        SpatialDiaryViewReceipt(**(values | {"context_known_count": 0}))
    with pytest.raises(ValidationError, match="must include a timezone"):
        SpatialDiaryViewReceipt(
            **(values | {"view_as_of": datetime.fromisoformat("2026-09-01T00:00:00")})
        )
