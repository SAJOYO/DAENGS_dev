"""의존 방향 가드 — `crawler` 는 백엔드 앱 쪽을 import 하지 않는다 (RAG-009, RAG-014).

RAG-001 원칙 1 이 "FastAPI 없이 `python -m crawler` 단독 실행" 을 요구한다. 방향이 한 번 뒤집히면
크롤러가 앱 설정·DB 세션을 끌고 들어와 CLI 도 Celery 워커도 앱 전체를 세워야 돌아간다.
지금까지는 사람이 지키는 규칙이었고, 이 테스트가 그걸 기계가 지키게 만든다.

import 문을 **정적으로(AST)** 본다. 모듈을 실제로 import 해서 sys.modules 를 보는 방식은
함수 안에 숨은 import 를 놓치고, 테스트 프로세스가 이미 올려 둔 모듈에 영향을 받는다.
"""
from __future__ import annotations

import ast
from pathlib import Path

# 이관하면서 다섯 형제가 `daengs_life/` 한 겹 안으로 들어갔다 (D-018). 검사 대상 경로와
# 아래 `_root()` 의 접두사 벗기기가 그 한 겹을 흡수하고, **가드의 뜻은 그대로다.**
PKG = "daengs_life"
CRAWLER_DIR = Path(__file__).resolve().parents[1] / "src" / PKG / "crawler"

# crawler 가 import 하면 안 되는 형제 패키지. `app/` 이 서빙 실물이다 (RAG-027 — 옛 `main.py`
# 2줄 shim 은 이관하며 없앴다).
# `rag` 는 파싱~검색 엔진(RAG-018). 방향은 app -> rag -> crawler 한쪽뿐이라 crawler 가 rag 를
# 끌어다 쓰면 안 된다 — 뒤집히면 `python -m crawler` 가 torch 까지 세워야 도는 물건이 된다.
# `realtime` 은 파트② 실시간 엔진(RT-001 ①-2). 같은 이유로 반대 방향만 허용한다.
FORBIDDEN = {"app", "tasks", "main", "rag", "realtime"}


def _root(name: str) -> str:
    """import 이름 → 형제 패키지 이름. `daengs_life.` 한 겹을 벗긴다.

    접두사가 없는 옛 표기(`app.db`)도 그대로 잡는다 — 이관 후엔 import 조차 안 되는
    이름이라 걸러 두는 편이 낫고, 아래 자기검사 픽스처가 그 형태를 쓴다.
    """
    parts = name.split(".")
    if parts[0] == PKG:
        parts = parts[1:]
    return parts[0] if parts else ""


def _violations() -> list[str]:
    found: list[str] = []
    for path in sorted(CRAWLER_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [(_root(a.name), a.name) for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # level > 0 은 패키지 내부 상대 import 라 crawler 밖으로 못 나간다
                if node.level or not node.module:
                    continue
                roots = [(_root(node.module), node.module)]
            else:
                continue
            for root, full in roots:
                if root in FORBIDDEN:
                    rel = path.relative_to(CRAWLER_DIR.parent)
                    found.append(f"{rel}:{node.lineno} imports {full!r}")
    return found


def test_crawler_does_not_import_backend_app() -> None:
    found = _violations()
    assert not found, (
        "crawler 가 앱 쪽을 import 했다. 의존 방향은 app → crawler 한쪽뿐이다 (RAG-009).\n  "
        + "\n  ".join(found)
    )


def test_guard_actually_detects_a_violation(tmp_path: Path) -> None:
    """가드가 살아 있는지 — 위반 파일을 만들어 실제로 잡히는지 확인한다.

    이 확인이 없으면 검사기가 조용히 아무것도 안 보게 돼도 테스트는 계속 통과한다.
    """
    global CRAWLER_DIR
    fake = tmp_path / "crawler"
    (fake / "core").mkdir(parents=True)
    (fake / "core" / "bad.py").write_text(
        "def go():\n    from app.db import session\n    return session\n", encoding="utf-8")

    original, CRAWLER_DIR = CRAWLER_DIR, fake
    try:
        found = _violations()
    finally:
        CRAWLER_DIR = original

    assert len(found) == 1 and "app.db" in found[0], found
