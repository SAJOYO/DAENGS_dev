import pytest

from daengs_place.place.contracts import PlaceRef
from daengs_place.place.source_facts.bundle import SourceFactKey
from daengs_place.place.source_facts.reader import (
    MAX_BUNDLE_CANDIDATES,
    load_candidate_fact_bundles,
    source_fact_key,
)


class _NeverExecuteSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("database must not be queried")


def test_place_ref_adapter_only_accepts_shadow_fact_sources() -> None:
    assert source_fact_key(PlaceRef(source="kcisa", ref="123")) == SourceFactKey(
        source="kcisa",
        source_ref="123",
    )
    assert source_fact_key(PlaceRef(source="public:mois:animal_hospital", ref="123")) is None


async def test_empty_candidate_read_does_not_touch_database() -> None:
    assert await load_candidate_fact_bundles(_NeverExecuteSession(), []) == []


async def test_candidate_read_has_an_explicit_bound() -> None:
    keys = [
        SourceFactKey(source="kto", source_ref=str(index))
        for index in range(MAX_BUNDLE_CANDIDATES + 1)
    ]

    with pytest.raises(ValueError, match="at most 1000"):
        await load_candidate_fact_bundles(_NeverExecuteSession(), keys)
