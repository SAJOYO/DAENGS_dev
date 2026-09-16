"""`daengs_cardimage` 는 순수 생성 로직이다 — backend·웹·DB 를 import 하지 않는다 (D-076).

나중에 이 패키지만 별도 서비스로 뗄 수 있으려면 이 선이 지켜져야 한다. 설정은 인자로 받고,
설정을 읽어 엔진을 만드는 일은 `daengs_backend/services/ai_card_engine.py` 가 한다.
"""

import ast
import importlib.util
from pathlib import Path

FORBIDDEN = {"daengs_backend", "fastapi", "sqlalchemy", "starlette", "pydantic_settings"}


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def test_package_does_not_import_backend_web_or_db() -> None:
    import daengs_cardimage

    root = Path(daengs_cardimage.__file__).parent
    offenders = [
        f"{p.relative_to(root)}: {name}"
        for p in sorted(root.rglob("*.py"))
        for name in _imports(p)
        if name.split(".")[0] in FORBIDDEN
    ]
    assert offenders == []


def test_old_service_path_is_gone() -> None:
    assert importlib.util.find_spec("daengs_backend.services.cardimage") is None
