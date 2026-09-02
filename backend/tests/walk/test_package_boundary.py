import ast
import tomllib
from pathlib import Path

PURE_MODULES = {
    "cellophane.py",
    "contracts.py",
    "evidence.py",
    "facts.py",
    "hex_grid.py",
    "measurement.py",
    "observation.py",
}
FORBIDDEN_ROOTS = {
    "daengs_backend",
    "daengs_journey",
    "daengs_place",
    "fastapi",
    "sqlalchemy",
}


def _import_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_calculation_core_does_not_bypass_product_capabilities() -> None:
    package = Path(__file__).parents[2] / "src" / "daengs_walk"
    violations = {
        path.name: sorted(_import_roots(path) & FORBIDDEN_ROOTS)
        for path in package.glob("*.py")
        if path.name in PURE_MODULES and _import_roots(path) & FORBIDDEN_ROOTS
    }

    assert not violations, violations


def test_calculation_core_is_configured_for_the_backend_wheel() -> None:
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    packaged_modules = config["tool"]["uv"]["build-backend"]["module-name"]

    assert "daengs_walk" in packaged_modules
