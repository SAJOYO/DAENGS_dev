"""Offline stage-0 inventory check; does not import application code or run product tests.

Run: uv run --no-project --python 3.12 python tools/check_walk_ownership.py
Reports scope/import drift against a reviewed manifest, not architectural completion.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/walk/ownership/inventory.json"
EXCLUDE = {"tools/check_walk_ownership.py"}
SEAMS = {
    "backend/src/daengs_backend/config.py",
    "backend/src/daengs_backend/main.py",
    "docker-compose.yml",
    "nginx/default.conf",
    ".github/workflows/deploy.yml",
    ".github/workflows/walk-diary-runtime.yml",
}
WORD = re.compile(r"walk|diary|trajectory|motion|measurement", re.IGNORECASE)


def module_name(path):
    for prefix in ("backend/src/", "backend/"):
        if path.startswith(prefix):
            name = path[len(prefix) : -3].replace("/", ".")
            return name.removesuffix(".__init__")
    return path[:-3].replace("/", ".")


def imports(text, module, *, is_package=False):
    """Resolve static imports, including aliases and function-local/TYPE_CHECKING imports."""
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(text.lstrip("\ufeff"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if node.level:
                name = importlib.util.resolve_name("." * node.level + name, package)
            yield name
            yield from (name + "." + alias.name for alias in node.names)


def is_core(path):
    if path.startswith("backend/src/daengs_walk/") and path.endswith(".py"):
        return True
    if path.startswith("backend/src/daengs_backend/") and path.endswith(".py"):
        return (
            Path(path).stem == "walk"
            or Path(path).stem.startswith("walk_")
            or any(
                f"/services/{package}/" in path
                for package in (
                    "walk_diary",
                    "walk_generation",
                    "walk_legacy",
                    "walk_artifacts",
                    "walk_background",
                    "walk_records",
                    "walk_photos",
                )
            )
            or path.endswith("/orchestration/diary.py")
        )
    return False


def discover(root=ROOT):
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    tracked = sorted(p for p in tracked if p and p not in EXCLUDE)
    python = {module_name(p): p for p in tracked if p.endswith(".py")}
    core = {p for p in tracked if is_core(p)}
    all_edges = set()
    for module, path in python.items():
        text = (root / path).read_text(encoding="utf-8-sig")
        for imported in imports(text, module, is_package=path.endswith("/__init__.py")):
            target = imported
            while target and target not in python:
                target = target.rpartition(".")[0]
            if target and python[target] != path:
                all_edges.add((path, python[target]))
    edges = sorted((a, b) for a, b in all_edges if a in core or b in core)
    # One-hop local dependencies/callers are integration seams, not ownership of
    # every dependency they in turn import (which would absorb unrelated products).
    scope = core | {p for edge in edges for p in edge}
    reasons = {p: "core" if p in core else "direct-import-seam" for p in scope}
    for path in tracked:
        name = Path(path).name
        if path.startswith(("backend/tests/walk/", "backend/evals/walk", "backend/evals/diary")):
            scope.add(path)
            reasons.setdefault(path, "verification-assets")
        elif path.startswith(
            ("tools/", "backend/tools/", "backend/src/daengs_evals/")
        ) and WORD.search(name):
            scope.add(path)
            reasons.setdefault(path, "named-tool")
        elif path.startswith("db/") and path.endswith(".sql"):
            text = (root / path).read_text(encoding="utf-8-sig")
            if re.search(r"\bwalk(?:s|_[a-z_]+)?\b", text, re.IGNORECASE):
                scope.add(path)
                reasons.setdefault(path, "storage-seam")
        elif path.startswith(("frontend/", ".github/", "docs/ci/")) and path.endswith(
            (".ts", ".tsx", ".js", ".yml", ".yaml")
        ):
            text = (root / path).read_text(encoding="utf-8-sig")
            if re.search(r"/app/walks|walk-inspect|walk_runtime|walk-diary|tests/walk", text):
                scope.add(path)
                reasons.setdefault(path, "textual-integration-seam")
        if path in SEAMS:
            scope.add(path)
            reasons.setdefault(path, "declared-integration-seam")
    return {p: reasons[p] for p in sorted(scope)}, [list(e) for e in edges]


def problems(manifest, scope, edges):
    records = manifest["files"]
    listed = {r["path"] for r in records}
    actual = set(scope)
    issues = []
    if len(listed) != len(records):
        issues.append("duplicate file classifications")
    issues.extend("unclassified: " + p for p in sorted(actual - listed))
    issues.extend("out of scope or removed: " + p for p in sorted(listed - actual))
    owners = set(manifest["owners"])
    for record in records:
        if record.get("owner") not in owners or not record.get("rationale"):
            issues.append("missing owner/rationale: " + record["path"])
        if record.get("scope_reason") != scope.get(record["path"]):
            issues.append("scope reason changed: " + record["path"])
        if record.get("disposition") not in {
            "retain",
            "repackage",
            "split",
            "integration",
        }:
            issues.append("missing disposition: " + record["path"])
    recorded = {tuple(e) for e in manifest["imports"]}
    observed = {tuple(e) for e in edges}
    issues.extend("new import: " + " -> ".join(e) for e in sorted(observed - recorded))
    issues.extend("removed import: " + " -> ".join(e) for e in sorted(recorded - observed))
    by_path = {r["path"]: r for r in records}
    if "crossings" in manifest:
        wanted = {
            (a, b)
            for a, b in observed
            if a in by_path
            and b in by_path
            and a.startswith("backend/src/")
            and b.startswith("backend/src/")
            and (
                by_path[a]["owner"] != by_path[b]["owner"]
                or (
                    a.endswith("/services/walk_measurement.py")
                    and b.endswith("/services/walk_trajectory.py")
                )
            )
        }
        crossings = manifest["crossings"]
        classified = {(c["from"], c["to"]) for c in crossings}
        if classified != wanted or len(classified) != len(crossings):
            issues.append("crossing decisions do not cover the observed source boundaries exactly")
        for c in crossings:
            if c["decision"] not in {"change", "retain-interface"} or not c.get("reason"):
                issues.append("missing crossing decision: " + c["from"])
            if c["decision"] == "change" and not c.get("debt"):
                issues.append("untracked boundary debt: " + c["from"])
    return issues


def self_test():
    # In-memory mutations prove omitted files and changed imports fail; no repo
    # files, live DB, credentials, HTTP or application imports are involved.
    scope = {"a.py": "core", "b.py": "direct-import-seam"}
    manifest = {
        "owners": {"owner": "description"},
        "files": [
            {
                "path": p,
                "scope_reason": why,
                "owner": "owner",
                "rationale": "explicit",
                "disposition": "retain",
            }
            for p, why in scope.items()
        ],
        "imports": [["a.py", "b.py"]],
    }
    assert not problems(manifest, scope, manifest["imports"])
    assert problems(manifest, {**scope, "new.py": "core"}, manifest["imports"])
    assert problems(manifest, {"a.py": "core"}, manifest["imports"])
    assert problems(manifest, scope, [])
    assert problems(manifest, scope, [["b.py", "a.py"]])
    damaged = {**manifest, "files": [*manifest["files"], manifest["files"][0]]}
    assert problems(damaged, scope, manifest["imports"])
    damaged = {
        **manifest,
        "files": [{**r, "owner": "unknown"} for r in manifest["files"]],
    }
    assert problems(damaged, scope, manifest["imports"])
    broken = {
        **manifest,
        "crossings": [
            {
                "from": "a.py",
                "to": "b.py",
                "decision": "change",
                "debt": None,
                "reason": "missing debt",
            }
        ],
    }
    assert problems(broken, scope, manifest["imports"])
    parsed = set(
        imports(
            "from .source import thing as x\ndef f():\n from daengs_walk import facts\n",
            "demo.consumer",
        )
    )
    assert {"demo.source", "demo.source.thing", "daengs_walk.facts"} <= parsed
    print(
        "OK self-test: omission, deletion, import drift, duplicate, owner and alias/local imports"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scope, edges = discover()
    issues = problems(manifest, scope, edges)
    for issue in issues:
        print(issue)
    if issues:
        return 1
    print(f"OK classified {len(scope)} files; {len(edges)} direct local import edges unchanged")
    print("Stage-0 inventory consistency only; recorded debts are NOT resolved by this result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
