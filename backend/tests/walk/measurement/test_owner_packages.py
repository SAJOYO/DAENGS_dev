"""Owner imports preserve calculation isolation and historical public object identity."""

import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.walk.api.test_session_package import imports

ROOT = Path(__file__).parents[4]
SOURCE = ROOT / "backend/src"
PREFIX = "daengs_backend.services."
MANIFEST = json.loads((ROOT / "docs/walk/remaining-packages.json").read_text(encoding="utf-8"))


def test_product_callers_use_owners_and_old_paths_only_forward():
    old = {PREFIX + name for name in MANIFEST["moves"]}
    for path in SOURCE.rglob("*.py"):
        assert not old.intersection(imports(path)), path
    for name in MANIFEST["moves"]:
        path = SOURCE / "daengs_backend/services" / (name + ".py")
        definitions = [
            n.name
            for n in ast.parse(path.read_text()).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        assert set(definitions) == {"__getattr__", "__dir__"}, path


def test_measurement_projection_has_no_query_or_sealing_dependencies():
    for name in ("motion_engine", "trajectory_shadow", "measurement_projection"):
        path = SOURCE / "daengs_backend/services/walk_metrics" / (name + ".py")
        denied = (
            PREFIX + "walk_metrics.trajectory",
            PREFIX + "walk_metrics.measurement",
            PREFIX + "walk_session.lifecycle",
            PREFIX + "walk_artifacts",
            PREFIX + "walk_views",
            "daengs_backend.repositories",
            "daengs_backend.models",
            "daengs_backend.config",
        )
        # Exact module-prefix matching keeps trajectory_shadow distinct from trajectory.
        assert not [
            n for n in imports(path) if any(n == p or n.startswith(p + ".") for p in denied)
        ], path


@pytest.mark.parametrize(
    "owner",
    [
        "walk_metrics",
        "walk_views",
        "walk_metrics.motion_engine",
        "walk_metrics.measurement_projection",
        "walk_metrics.measurement",
        "walk_metrics.analysis",
        "walk_views.spatial_diary",
    ],
)
def test_owners_import_without_diary_or_sealing(owner):
    script = """import importlib,sys
import tests.conftest
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('daengs_backend.services.walk_diary','daengs_walk.diary','daengs_backend.services.walk_legacy','daengs_backend.services.walk_session.lifecycle','daengs_backend.orchestration')):
            raise AssertionError(fullname)
sys.meta_path.insert(0,Guard())
importlib.import_module(sys.argv[1])
"""
    result = subprocess.run(
        [sys.executable, "-c", script, PREFIX + owner],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", list(MANIFEST["moves"]))
def test_compatibility_preserves_owner_objects(name):
    compat = importlib.import_module(PREFIX + name)
    exports = MANIFEST["compatibility"][name]
    namespace = {}
    exec(f"from {PREFIX + name} import *", namespace)  # noqa: S102 - fixed compatibility manifest
    assert set(compat.__all__) == set(exports)
    for symbol, owner in exports.items():
        expected = getattr(importlib.import_module(owner), symbol)
        assert getattr(compat, symbol) is expected
        assert namespace[symbol] is expected
        assert symbol in dir(compat)
    with pytest.raises(AttributeError):
        _ = compat.not_an_export
