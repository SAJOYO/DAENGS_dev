"""`daengs_cardgen` 은 GPU 이미지에 혼자 들어간다 (D-078).

- backend·cardimage·life·DB 를 import 하지 않는다 — 이미지에는 이 패키지만 복사된다.
- torch·diffusers 같은 무거운 것은 **함수 안에서만** import 한다 — 개발 PC 의 pytest 가
  이 패키지를 import 하는 것만으로 CUDA 스택을 끌어오지 않게.
- 반대로 backend·cardimage 도 이 패키지를 import 하지 않는다 — backend 이미지에 torch 가
  새어 들어가는 길을 막는 것이 패키지를 나눈 이유다.
"""

import ast
import tomllib
from pathlib import Path

BACKEND = Path(__file__).parents[1]
SRC = BACKEND / "src"
FORBIDDEN = {"daengs_backend", "daengs_cardimage", "daengs_life", "sqlalchemy"}
HEAVY = {"torch", "diffusers", "transformers", "bitsandbytes", "accelerate"}


def _names(nodes):
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def _all_imports(path: Path):
    return _names(ast.walk(ast.parse(path.read_text(encoding="utf-8"))))


def _module_level_imports(path: Path):
    return _names(ast.parse(path.read_text(encoding="utf-8")).body)


def test_cardgen_does_not_import_backend_cardimage_or_db() -> None:
    offenders = [
        f"{p.name}: {name}"
        for p in sorted((SRC / "daengs_cardgen").rglob("*.py"))
        for name in _all_imports(p)
        if name.split(".")[0] in FORBIDDEN
    ]
    assert offenders == []


def test_heavy_libraries_are_imported_only_inside_functions() -> None:
    offenders = [
        f"{p.name}: {name}"
        for p in sorted((SRC / "daengs_cardgen").rglob("*.py"))
        for name in _module_level_imports(p)
        if name.split(".")[0] in HEAVY
    ]
    assert offenders == []


def test_backend_side_never_imports_cardgen() -> None:
    offenders = [
        f"{p.relative_to(SRC)}: {name}"
        for pkg in ("daengs_backend", "daengs_cardimage")
        for p in sorted((SRC / pkg).rglob("*.py"))
        for name in _all_imports(p)
        if name.split(".")[0] == "daengs_cardgen"
    ]
    assert offenders == []


def test_cardgen_is_packaged_and_has_its_own_group() -> None:
    cfg = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    assert "daengs_cardgen" in cfg["tool"]["uv"]["build-backend"]["module-name"]
    names = {
        spec.split(";")[0].split(">=")[0].split("==")[0].split("<")[0].split("[")[0].strip()
        for spec in cfg["dependency-groups"]["cardgen"]
    }
    assert {"fastapi", "pillow", "diffusers", "transformers", "accelerate", "bitsandbytes", "torch", "huggingface-hub"} <= names
