"""Session suppliers do not import sealing, and public compatibility shares owners."""

import ast
import importlib
import json
import subprocess
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[4]
SOURCE = ROOT / "backend/src"
PREFIX = "daengs_backend.services."
PACKAGE = SOURCE / "daengs_backend/services/walk_session"
PURE = {"errors", "chunk", "finalize", "motion_contract", "precision_contract"}


def imports(path):
    module = path.relative_to(SOURCE).with_suffix("").as_posix().replace("/", ".")
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            yield from (n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if node.level:
                name = resolve_name("." * node.level + name, module.rpartition(".")[0])
            yield name
            yield from (name + "." + n.name for n in node.names)


def test_session_dependency_direction():
    for path in PACKAGE.rglob("*.py"):
        denied = [PREFIX + n for n in ("walk_records", "walk_photos", "walk_diary", "walk_legacy")]
        denied += ["daengs_walk.diary"]
        if path.stem != "lifecycle":
            denied += [
                PREFIX + "walk_session.lifecycle",
                "daengs_backend.orchestration",
                PREFIX + "walk_artifacts",
            ]
        if path.stem in PURE:
            denied += [
                "sqlalchemy",
                "daengs_backend.repositories",
                "daengs_backend.models",
                "daengs_backend.config",
            ]
        assert not any(name.startswith(tuple(denied)) for name in imports(path)), path


@pytest.mark.parametrize(
    "module",
    [
        "errors",
        "chunk",
        "finalize",
        "motion_contract",
        "precision_contract",
        "recording",
        "motion",
        "precision",
        "upload_receipt",
    ],
)
def test_suppliers_import_without_sealing_or_diary(module):
    script = """import importlib,sys
import tests.conftest
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('daengs_backend.services.walk_session.lifecycle','daengs_backend.services.walk_diary','daengs_walk.diary','daengs_backend.orchestration','daengs_backend.services.walk_artifacts')):
            raise AssertionError(fullname)
sys.meta_path.insert(0,Guard())
importlib.import_module(sys.argv[1])
"""
    result = subprocess.run(
        [sys.executable, "-c", script, PREFIX + "walk_session." + module],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_error_contract_does_not_load_storage_or_configuration():
    script = """import sys
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('sqlalchemy','httpx','daengs_backend.config','daengs_backend.models','daengs_backend.services.walk_session.lifecycle')):
            raise AssertionError(fullname)
sys.meta_path.insert(0,Guard())
from daengs_backend.services.walk_session.errors import WalkNotFoundError,WalkStateConflictError
assert issubclass(WalkNotFoundError,Exception)
error=WalkStateConflictError('changed','retry')
assert (error.code,error.detail,str(error)) == ('changed','retry','retry')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr


def test_nine_compatibility_modules_preserve_public_identity():
    manifest = json.loads((ROOT / "docs/walk/session-package.json").read_text(encoding="utf-8"))
    assert len(manifest["compatibility"]) == 9
    for name, exports in manifest["compatibility"].items():
        compat = importlib.import_module(PREFIX + name)
        assert set(compat.__all__) == set(exports)
        for symbol, owner in exports.items():
            assert getattr(compat, symbol) is getattr(importlib.import_module(owner), symbol)


def test_product_callers_use_owner_modules():
    manifest = json.loads((ROOT / "docs/walk/session-package.json").read_text(encoding="utf-8"))
    old = {PREFIX + name for name in manifest["compatibility"]}
    for path in SOURCE.rglob("*.py"):
        assert not old.intersection(imports(path)), path
