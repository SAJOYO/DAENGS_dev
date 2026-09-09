from daengs_place.place.contracts import PlaceResult
from daengs_place.place.planning.capabilities import CAPABILITIES
from daengs_place.place.planning.execution import EXECUTORS
from daengs_place.place.source_catalog import MOIS_SOURCES
from daengs_place.place.source_facts.contract import SourceFactProjection
from daengs_place.place.source_facts.kcisa import project_kcisa
from daengs_place.place.source_facts.kto import project_kto


def _model_path_exists(model, path: str) -> bool:
    section, field = path.split(".", maxsplit=1)
    section_model = model.model_fields[section].annotation
    return field in section_model.model_fields


def test_every_declared_capability_has_a_fact_path_and_executor() -> None:
    assert len({spec.capability_id for spec in CAPABILITIES}) == len(CAPABILITIES)
    assert {spec.executor_id for spec in CAPABILITIES} == set(EXECUTORS)
    assert all(
        _model_path_exists(SourceFactProjection, path)
        for spec in CAPABILITIES
        for path in spec.projection_paths
    )
    assert all(
        _model_path_exists(PlaceResult, path)
        for spec in CAPABILITIES
        for path in spec.execution_paths
    )


def test_projection_and_execution_sources_are_declared_separately() -> None:
    projections = {
        "kcisa": project_kcisa({"카테고리3": "카페"}),
        "kto": project_kto({"contenttypeid": "12"}),
    }
    actual_execution_sources = {
        "kcisa",
        "kto",
        *(source.source for source in MOIS_SOURCES.values()),
    }
    for spec in CAPABILITIES:
        assert set(spec.projection_sources) <= projections.keys()
        assert set(spec.execution_sources) <= actual_execution_sources
        assert all(
            path in projections[source].evidence
            for source in spec.projection_sources
            for path in spec.projection_paths
        )


def test_capability_metadata_does_not_pretend_static_coverage_is_current_data() -> None:
    for spec in CAPABILITIES:
        dumped = spec.model_dump()
        assert "coverage" not in dumped
        assert "freshness" not in dumped
        assert "raw_fields" not in dumped
