"""Package direction, public compatibility and background dispatch contracts."""

import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[4]
PACKAGE = ROOT / "backend/src/daengs_backend/services/walk_background"


def test_provider_and_contract_modules_do_not_import_coordinators():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports.extend([module, *(module + "." + n.name for n in node.names)])
            elif isinstance(node, ast.Import):
                imports.extend(n.name for n in node.names)
        assert not any(
            "walk_diary" in n
            or n.startswith(
                (
                    "daengs_walk.diary",
                    "daengs_backend.services.walk_records",
                    "daengs_backend.services.walk_entry",
                )
            )
            for n in imports
        ), path
        if path.parent.name == "providers" or path.name == "contracts.py":
            assert not any(
                n.startswith(
                    (
                        "daengs_backend.services.walk_background.collection",
                        "daengs_backend.services.walk_background.catalogs.refresh",
                        "daengs_backend.repositories",
                    )
                )
                for n in imports
            ), path


@pytest.mark.parametrize(
    "module",
    [
        "contracts",
        "providers.facility",
        "providers.weather",
        "providers.public",
        "providers.area",
        "catalogs.park",
        "catalogs.commerce",
        "catalogs.river",
    ],
)
def test_supplier_imports_work_with_coordinators_blocked(module):
    script = """import sys, importlib
import tests.conftest
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('daengs_backend.services.walk_background.collection','daengs_backend.services.walk_records','daengs_backend.services.walk_entry_context','daengs_backend.services.walk_diary','daengs_walk.diary')):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Guard())
importlib.import_module(sys.argv[1])
"""
    result = subprocess.run(
        [sys.executable, "-c", script, "daengs_backend.services.walk_background." + module],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_all_thirteen_compatibility_modules_export_the_same_objects():
    manifest = json.loads((ROOT / "docs/walk/background-package.json").read_text(encoding="utf-8"))
    assert len(manifest["compatibility"]) == 13
    for name, names in manifest["compatibility"].items():
        compat = importlib.import_module("daengs_backend.services." + name)
        assert set(compat.__all__) == set(names)
        for symbol, owner in names.items():
            assert getattr(compat, symbol) is getattr(importlib.import_module(owner), symbol)


def test_production_uses_no_old_background_imports():
    manifest = json.loads((ROOT / "docs/walk/background-package.json").read_text(encoding="utf-8"))
    old = {"daengs_backend.services." + name for name in manifest["compatibility"]}
    for directory in [ROOT / "backend/src", ROOT / "backend/tools"]:
        for path in directory.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names = [node.module, *((node.module or "") + "." + a.name for a in node.names)]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                else:
                    continue
                assert not (old & set(names)), path


def test_contract_import_needs_no_config_http_or_database():
    script = """import sys
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('httpx','sqlalchemy','daengs_backend.config','daengs_backend.services.walk_background.providers')):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Guard())
from daengs_backend.services.walk_background.contracts import Collected,digest
assert Collected('empty').payload is None
# Frozen from dev 70a07371 before extraction; preserve Unicode and float serialization.
assert digest({'나':1,'a':[1.0,None]}) == '02d57b38de41af64be9dcc7f0f3e579bfdebf15ca53c665020cd8f9cba120da0'
assert digest({'a':[1.0,None],'나':1}) == '02d57b38de41af64be9dcc7f0f3e579bfdebf15ca53c665020cd8f9cba120da0'
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False, timeout=30
    )
    assert result.returncode == 0, result.stderr
