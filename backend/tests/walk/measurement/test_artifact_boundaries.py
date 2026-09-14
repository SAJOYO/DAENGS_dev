"""Sealed output ownership, historical exports and pre-extraction payload contracts."""

import ast
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import timedelta
from importlib import import_module
from pathlib import Path

import pytest

from daengs_backend.services.walk_artifacts import api, capsule, cellophane
from daengs_backend.services.walk_metrics import analysis as walk_analysis
from tests.walk.measurement.test_walk_analysis_storage import calculation
from tests.walk.measurement.test_walk_capsule import capsule as make_capsule

FROZEN = json.loads((Path(__file__).parents[1] / "fixtures/sealed-artifacts-v1.json").read_text())
EXPORTS = {
    "Cellophane": "cellophane",
    "build_cellophane": "cellophane",
    "WalkEvidencePoint": "contracts",
    "WalkEvidenceBundle": "evidence",
    "analyze_walk": "evidence",
    "WalkCapsuleArtifacts": "capsule",
    "build_walk_capsule": "capsule",
    "select_context_anchor": "capsule",
    "SpatialDiaryViewSpec": "spatial_diary",
    "aggregate_spatial_field": "spatial_diary",
    "context_facets": "spatial_diary",
}


def test_sealed_payloads_match_pre_extraction_baseline():
    prepared, evidence, sheet = calculation()
    row = api.build_analysis_models(prepared, evidence, sheet)
    values = {
        "analysis": asdict(walk_analysis.analysis_payloads(evidence)),
        "sheet": row.cellophane_sheets[0].payload,
    }
    for key, weather in [("capsule_weather", True), ("capsule_unknown", False)]:
        artifact = make_capsule(with_weather=weather)
        stored = capsule.build_capsule_model(row, artifact)
        row.capsule = stored
        decoded = capsule.decode_capsule_model(row)
        values[key] = {
            "manifest": decoded.manifest.model_dump(mode="json"),
            "context": decoded.trail_context.model_dump(mode="json"),
        }
    assert {
        k: hashlib.sha256(
            json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for k, v in values.items()
    } == FROZEN["hashes"]
    assert row.cellophane_sheets[0].sheet_fingerprint == FROZEN["sheet_fingerprint"]


def test_measurement_builder_does_not_create_spatial_outputs():
    prepared, evidence, _ = calculation()
    row = walk_analysis.build_analysis_model(prepared, evidence)
    assert row.cellophane_sheets == []
    assert row.capsule is None
    assert walk_analysis.decode_analysis_model(row).facts == evidence.facts


@pytest.mark.parametrize("change", ["walk_id", "at"])
def test_assembler_rejects_sheet_from_another_analysis(change):
    import uuid

    prepared, evidence, sheet = calculation()
    sheet = replace(
        sheet, **{change: uuid.uuid4() if change == "walk_id" else sheet.at + timedelta(seconds=1)}
    )
    with pytest.raises(ValueError):
        api.build_analysis_models(prepared, evidence, sheet)


def test_historical_exports_are_identical_objects():
    import daengs_walk
    from daengs_backend.services import walk_analysis, walk_capsule

    assert set(daengs_walk.__all__) == set(EXPORTS)
    namespace = {}
    exec("from daengs_walk import *", namespace)  # noqa: S102 - fixed legacy import contract
    for name, module in EXPORTS.items():
        owner = getattr(import_module("daengs_walk." + module), name)
        assert getattr(daengs_walk, name) is owner
        assert namespace[name] is owner
        assert name in dir(daengs_walk)
    assert walk_analysis.build_analysis_models is api.build_analysis_models
    for name in [
        "encode_cellophane",
        "decode_cellophane",
        "decode_stored_cellophane",
        "cellophane_sheet_fingerprint",
    ]:
        assert getattr(walk_analysis, name) is getattr(cellophane, name)
    assert walk_capsule.build_capsule_model is capsule.build_capsule_model
    assert walk_capsule.decode_capsule_model is capsule.decode_capsule_model
    with pytest.raises(AttributeError):
        _ = daengs_walk.not_an_export


@pytest.mark.parametrize(
    "statement, blocked",
    [
        (
            "import daengs_walk",
            [
                "daengs_walk.evidence",
                "daengs_walk.cellophane",
                "daengs_walk.capsule",
                "daengs_walk.spatial_diary",
            ],
        ),
        (
            "from daengs_walk import analyze_walk",
            ["daengs_walk.cellophane", "daengs_walk.capsule", "daengs_walk.spatial_diary"],
        ),
        (
            "from daengs_backend.services.walk_metrics.analysis import decode_analysis_model, build_analysis_model",
            [
                "daengs_walk.cellophane",
                "daengs_walk.capsule",
                "daengs_walk.spatial_diary",
                "daengs_backend.services.walk_artifacts",
            ],
        ),
        (
            "from daengs_backend.services.walk_artifacts.cellophane import decode_stored_cellophane",
            [
                "daengs_backend.services.walk_metrics.analysis",
                "daengs_walk.capsule",
                "daengs_walk.spatial_diary",
            ],
        ),
    ],
)
def test_owner_imports_do_not_load_unrelated_products(statement, blocked):
    program = f"""import sys
import tests.conftest
blocked = {blocked!r}
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in blocked):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Guard())
{statement}
"""
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_production_callers_use_owner_modules_and_not_compatibility_facades():
    root = Path(__file__).parents[3] / "src"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module == "daengs_walk":
                assert not (set(EXPORTS) & {n.name for n in node.names}), path
            if node.module == "daengs_backend.services.walk_metrics.analysis":
                assert not (
                    {
                        "build_analysis_models",
                        "encode_cellophane",
                        "decode_stored_cellophane",
                        "decode_cellophane",
                    }
                    & {n.name for n in node.names}
                ), path
