"""Enforce service ownership and usable import boundaries in fresh interpreters."""

import ast
import importlib.util
import subprocess
import sys

import pytest

from tests.walk.support.paths import REPO

PREFIX = "daengs_backend.services.walk_diary"
SOURCE = REPO / "backend/src"
PACKAGE = SOURCE / "daengs_backend/services/walk_diary"


def imports(path):
    package = ".".join(path.relative_to(SOURCE).parts[:-1])
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = (
                importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                if node.level
                else node.module
            )
            yield module
            yield from (module + "." + n.name for n in node.names)


@pytest.mark.parametrize(
    ("area", "forbidden"),
    [
        (
            "preparation",
            ("api", "runtime", "lifecycle", "writing", "storage", "collection", "legacy"),
        ),
        ("collection", ("api", "runtime", "lifecycle", "writing", "storage", "legacy")),
        (
            "writing",
            ("api", "runtime", "lifecycle", "preparation", "collection", "storage", "legacy"),
        ),
        ("legacy", ("api", "runtime", "lifecycle", "writing")),
    ],
)
def test_service_dependencies_follow_ownership(area, forbidden):
    denied = tuple(PREFIX + "." + name for name in forbidden)
    violations = {
        str(path.relative_to(PACKAGE)): [
            name
            for name in imports(path)
            if any(name == prefix or name.startswith(prefix + ".") for prefix in denied)
        ]
        for path in (PACKAGE / area).rglob("*.py")
    }
    assert not {path: names for path, names in violations.items() if names}


def test_production_callers_enter_through_api():
    # The diary graph intentionally consumes task contracts and pure writing helpers.
    backend = SOURCE / "daengs_backend"
    violations = {}
    for folder in (backend / "routers", backend / "services"):
        for path in folder.rglob("*.py"):
            if path.is_relative_to(PACKAGE):
                continue
            bad = [
                n
                for n in imports(path)
                if n.startswith(PREFIX + ".") and not n.startswith(PREFIX + ".api")
            ]
            if bad:
                violations[str(path.relative_to(backend))] = bad
    assert not violations


@pytest.mark.parametrize(
    ("statement", "forbidden"),
    [
        (
            "from daengs_backend.services.walk_diary import api",
            ("daengs_backend.config", "langgraph", "google.genai"),
        ),
        (
            "from daengs_backend.services.walk_diary.runtime import write_board",
            (PREFIX + ".legacy", "daengs_backend.config", "google.genai"),
        ),
        (
            "from daengs_backend.orchestration.execution import JobExecutor",
            (
                "daengs_backend.orchestration.graph",
                "daengs_backend.orchestration.runtime",
                "daengs_backend.orchestration.service",
                "daengs_backend.config",
                "langgraph",
            ),
        ),
    ],
)
def test_lightweight_entries_do_not_import_unrelated_runtime(statement, forbidden):
    script = f"""
import importlib.abc
import sys

class Denied(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in {forbidden!r}):
            raise AssertionError('unrelated runtime imported: ' + fullname)

sys.meta_path.insert(0, Denied())
{statement}
"""
    result = subprocess.run(
        [sys.executable, "-c", script], text=True, capture_output=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr


def test_existing_orchestration_exports_keep_the_same_objects():
    import daengs_backend.orchestration as package
    from daengs_backend.orchestration.graph import OrchestrationEngine
    from daengs_backend.orchestration.runtime import Orchestrator, build_orchestrator
    from daengs_backend.orchestration.service import AssistantOrchestrationService

    assert package.OrchestrationEngine is OrchestrationEngine
    assert package.Orchestrator is Orchestrator
    assert package.build_orchestrator is build_orchestrator
    assert package.AssistantOrchestrationService is AssistantOrchestrationService
