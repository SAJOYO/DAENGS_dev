import pytest

from daengs_place.place.planning.contract import GateOrigin, PlaceKind
from daengs_place.place.planning.purpose import (
    PURPOSE_CATALOG,
    PurposeId,
    PurposePolicyError,
    build_purpose_search_plan,
    resolve_purposes,
)


def test_purpose_catalog_partitions_every_executable_kind_except_fallback() -> None:
    catalog_kinds = [kind for spec in PURPOSE_CATALOG for kind in spec.kinds]

    assert len(catalog_kinds) == len(set(catalog_kinds))
    assert set(catalog_kinds) == set(PlaceKind) - {PlaceKind.ETC}


def test_purpose_resolution_is_stable_and_does_not_silently_truncate() -> None:
    first = resolve_purposes([PurposeId.OUTING, PurposeId.DINING])
    reversed_input = resolve_purposes([PurposeId.DINING, PurposeId.OUTING])

    assert first == reversed_input
    assert first.purpose_ids == (PurposeId.DINING, PurposeId.OUTING)
    assert first.kinds == (
        PlaceKind.CAFE,
        PlaceKind.RESTAURANT,
        PlaceKind.TRAVEL,
        PlaceKind.LEISURE,
    )

    with pytest.raises(PurposePolicyError, match="purposes must be unique"):
        resolve_purposes([PurposeId.DINING, PurposeId.DINING])
    with pytest.raises(PurposePolicyError, match="unknown purpose id"):
        resolve_purposes(["quiet_place"])
    with pytest.raises(PurposePolicyError, match="6-kind request boundary"):
        resolve_purposes([PurposeId.CULTURE, PurposeId.LODGING])


@pytest.mark.parametrize(
    ("origin", "locked", "relaxable"),
    [
        (GateOrigin.USER_EXPLICIT, True, False),
        (GateOrigin.SYSTEM, True, False),
        (GateOrigin.INFERRED, False, True),
    ],
)
def test_purpose_origin_owns_lock_and_relaxation_policy(
    origin: GateOrigin,
    locked: bool,
    relaxable: bool,
) -> None:
    plan = build_purpose_search_plan(
        lat=37.5,
        lng=127.0,
        radius_m=3000,
        purpose_ids=[PurposeId.OUTING],
        origin=origin,
        limit_per_kind=20,
    )

    gate = plan.gates[0]
    assert gate.value == (PlaceKind.TRAVEL, PlaceKind.LEISURE)
    assert gate.origin is origin
    assert gate.locked is locked
    assert gate.relaxable is relaxable
    assert plan.trace.entries[0].reason == "deterministic purpose policy selected: outing"


def test_purpose_policy_rejects_context_as_kind_authority() -> None:
    with pytest.raises(PurposePolicyError, match="does not accept origin=context"):
        build_purpose_search_plan(
            lat=37.5,
            lng=127.0,
            radius_m=3000,
            purpose_ids=[PurposeId.DINING],
            origin=GateOrigin.CONTEXT,
            limit_per_kind=20,
        )
