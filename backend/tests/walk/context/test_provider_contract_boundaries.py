"""Providers and photo transport must work without importing diary implementation."""

import ast
import json
import subprocess
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest


def test_all_owned_photo_and_background_sources_have_no_diary_imports():
    root = Path(__file__).resolve().parents[4]
    manifest = json.loads((root / "docs/walk/ownership/inventory.json").read_text(encoding="utf-8"))
    violations = []
    for record in manifest["files"]:
        path = record["path"]
        if record["owner"] not in {"photos", "background", "shared_contracts"}:
            continue
        if not path.startswith("backend/src/") or not path.endswith(".py"):
            continue
        for node in ast.walk(ast.parse((root / path).read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                name = node.module or ""
                if node.level:
                    module = path.removeprefix("backend/src/").removesuffix(".py").replace("/", ".")
                    package = module.rpartition(".")[0]
                    name = resolve_name("." * node.level + name, package)
                names = [name, *(name + "." + alias.name for alias in node.names)]
            if any("diary" in name.split(".") or "walk_diary" in name.split(".") for name in names):
                violations.append((path, node.lineno, names))
    assert not violations


@pytest.mark.parametrize(
    "module",
    [
        "daengs_walk.value_contracts",
        "daengs_walk.weather",
        "daengs_backend.schemas.walk_photo",
        "daengs_backend.services.walk_photo",
        "daengs_backend.services.walk_weather_context",
        "daengs_backend.services.walk_space_catalog_input",
        "daengs_backend.services.walk_area_catalog",
        "daengs_backend.services.walk_park_catalog",
        "daengs_backend.services.walk_commerce_catalog",
        "daengs_backend.services.walk_river_catalog",
    ],
)
def test_provider_imports_without_diary(module):
    script = """
import importlib
import importlib.abc
import sys

class NoDiary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('daengs_walk.diary', 'daengs_backend.services.walk_diary')):
            raise AssertionError('provider imported diary: ' + fullname)

sys.meta_path.insert(0, NoDiary())
importlib.import_module(sys.argv[1])
"""
    result = subprocess.run(
        [sys.executable, "-c", script, module],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
