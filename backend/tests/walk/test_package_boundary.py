import ast
import tomllib
from pathlib import Path

PURE_MODULES = {
    "capsule.py",
    "cellophane.py",
    "contracts.py",
    "evidence.py",
    "facts.py",
    "hex_grid.py",
    "measurement.py",
    "observation.py",
    "trajectory.py",
    "trajectory_selection.py",
    "trajectory_view.py",
}
PRODUCT_PACKAGES = {
    "daengs_walk",
    "daengs_place",
    "daengs_journey",
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


def test_product_packages_do_not_import_each_others_implementations() -> None:
    """제품 패키지의 조립점은 daengs_backend다. 서로를 Python import로 우회하지 않는다."""

    source = Path(__file__).parents[2] / "src"
    violations: dict[str, list[str]] = {}
    for package_name in PRODUCT_PACKAGES:
        forbidden = PRODUCT_PACKAGES - {package_name}
        for path in (source / package_name).rglob("*.py"):
            imported = sorted(_import_roots(path) & forbidden)
            if imported:
                violations[str(path.relative_to(source))] = imported

    assert not violations, violations


def test_calculation_core_is_configured_for_the_backend_wheel() -> None:
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    packaged_modules = config["tool"]["uv"]["build-backend"]["module-name"]

    assert "daengs_walk" in packaged_modules
