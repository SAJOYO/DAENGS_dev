"""Records/photo ownership, legacy public identities and independent import boundaries."""

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


def imports(path):
    module = path.relative_to(SOURCE).with_suffix("").as_posix().replace("/", ".")
    package = module.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            yield from (n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if node.level:
                name = resolve_name("." * node.level + name, package)
            yield name
            yield from (name + "." + n.name for n in node.names)


def test_records_and_photos_do_not_own_diary_or_each_other():
    for package, forbidden in [
        ("walk_records", (PREFIX + "walk_photos", PREFIX + "walk_photo")),
        (
            "walk_photos",
            (PREFIX + "walk_records", PREFIX + "walk_entry", PREFIX + "walk_background"),
        ),
    ]:
        for path in (SOURCE / "daengs_backend/services" / package).rglob("*.py"):
            denied = (
                *forbidden,
                PREFIX + "walk_diary",
                PREFIX + "walk_legacy",
                "daengs_walk.diary",
            )
            assert not any(name.startswith(denied) for name in imports(path)), path


@pytest.mark.parametrize(
    "module", ["errors", "profile", "policy", "pins", "v1", "v2", "context", "backfill", "photos"]
)
def test_entrypoints_import_without_unrelated_features(module):
    target = PREFIX + ("walk_photos.api" if module == "photos" else "walk_records." + module)
    blocked = [PREFIX + "walk_diary", PREFIX + "walk_legacy", "daengs_walk.diary"]
    if module == "photos":
        blocked += [PREFIX + "walk_records", PREFIX + "walk_background"]
    else:
        blocked += [PREFIX + "walk_photos"]
    if module in {"errors", "profile", "policy", "pins"}:
        blocked += [PREFIX + "walk_records." + n for n in ("v1", "v2", "context", "backfill")]
        blocked += [PREFIX + "walk_background"]
    script = """import importlib,json,sys
import tests.conftest
blocked=tuple(json.loads(sys.argv[2]))
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(blocked): raise AssertionError(fullname)
sys.meta_path.insert(0,Guard())
importlib.import_module(sys.argv[1])
"""
    result = subprocess.run(
        [sys.executable, "-c", script, target, json.dumps(blocked)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_nine_historical_modules_preserve_public_object_identity():
    manifest = json.loads((ROOT / "docs/walk/records-package.json").read_text(encoding="utf-8"))
    assert len(manifest["compatibility"]) == 9
    for old, names in manifest["compatibility"].items():
        compat = importlib.import_module(PREFIX + old)
        assert set(compat.__all__) == set(names)
        for name, owner in names.items():
            assert getattr(compat, name) is getattr(importlib.import_module(owner), name)


def test_production_imports_actual_owners():
    manifest = json.loads((ROOT / "docs/walk/records-package.json").read_text(encoding="utf-8"))
    old = {PREFIX + name for name in manifest["compatibility"]}
    for path in SOURCE.rglob("*.py"):
        assert not old.intersection(imports(path)), path


def test_default_collector_and_legacy_policy_exports_keep_identity():
    from daengs_backend.services.walk_background.collection import collect
    from daengs_backend.services.walk_records import context, errors, policy, v2

    assert context.process.__kwdefaults__["collector"] is collect
    assert v2.capabilities is policy.capabilities
    assert v2.guard_v1 is policy.guard_v1
    assert v2.EntryUpgradeRequired is errors.EntryUpgradeRequired
