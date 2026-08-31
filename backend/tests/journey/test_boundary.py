"""Journey의 별도 런타임 경계를 정적으로 지킨다 (D-039)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[2] / "src" / "daengs_journey"
FORBIDDEN_PACKAGES = {
    "daengs_backend",
    "daengs_life",
    "daengs_place",
    "daengs_training",
}


def _violations() -> list[str]:
    found: list[str] = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.split(".", 1)[0] in FORBIDDEN_PACKAGES:
                    found.append(f"{path.relative_to(PACKAGE_DIR)}:{node.lineno} imports {name}")
    return found


def test_journey_does_not_import_other_product_packages() -> None:
    assert not _violations(), (
        "Journey는 별도 컨테이너에서 단독으로 떠야 한다. 다른 제품 패키지를 import하지 말 것:\n  "
        + "\n  ".join(_violations())
    )
