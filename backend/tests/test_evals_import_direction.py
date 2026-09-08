"""방향 가드 — 운영 코드는 `daengs_evals` 를 import 하면 안 된다.

`daengs_evals` 는 다른 daengs_* 패키지를 써도 되지만(평가·벤치마크가 실물 코드를 부르는 것은
정상이다) 반대 방향은 금지다. 그 방향이 한 번이라도 뒤집히면 `daengs_evals` 가 배포 이미지에
끌려 들어가야 할 이유가 생기고, `[tool.uv.build-backend].module-name` 에 넣어 둔 의미
(오프라인 도구를 서빙 패키지와 나란히 두되 서로 모르게 하는 것)가 무너진다.

`test_import_direction.py`·`test_import_direction_packages.py` 와 같은 이유로 AST 로 정적
검사한다 — 실제 import 로 확인하면 함수 안에 숨은 import 를 놓치고 테스트 프로세스가 이미
올려 둔 모듈에 영향을 받는다.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
EVALS_DIR = SRC_DIR / "daengs_evals"


def _imports_evals(path: Path) -> list[tuple[int, str]]:
    # utf-8-sig — `daengs_training/service.py` 등 일부 파일이 BOM 을 달고 있어 순수 utf-8 로
    # 읽으면 ast.parse 가 `SyntaxError: invalid non-printable character U+FEFF` 로 죽는다.
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [
                (node.lineno, a.name) for a in node.names if a.name.split(".")[0] == "daengs_evals"
            ]
        elif isinstance(node, ast.ImportFrom):
            # level > 0 은 패키지 내부 상대 import 라 다른 daengs_* 패키지까지 못 나간다
            if node.level or not node.module:
                continue
            if node.module.split(".")[0] == "daengs_evals":
                found.append((node.lineno, node.module))
    return found


def _violations() -> list[str]:
    out: list[str] = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        if EVALS_DIR in path.parents or path == EVALS_DIR:
            continue                                   # daengs_evals 자신은 검사 대상이 아니다
        for lineno, target in _imports_evals(path):
            rel = path.relative_to(SRC_DIR.parent)
            out.append(f"{rel}:{lineno} imports {target!r}")
    return out


def test_operational_code_does_not_import_daengs_evals() -> None:
    found = _violations()
    assert not found, (
        "운영 코드가 daengs_evals 를 import했다 — 방향은 daengs_evals -> 다른 패키지 한쪽뿐이다:\n  "
        + "\n  ".join(found)
    )


def test_guard_actually_detects_a_violation(tmp_path: Path) -> None:
    """가드가 살아 있는지 — 위반 파일을 만들어 실제로 잡히는지 확인한다.

    이 확인이 없으면 검사기가 조용히 아무것도 안 보게 돼도 테스트는 계속 통과한다.
    """
    bad_import = tmp_path / "bad_import.py"
    bad_import.write_text("import daengs_evals\n", encoding="utf-8")
    assert _imports_evals(bad_import) == [(1, "daengs_evals")]

    bad_from = tmp_path / "bad_from.py"
    bad_from.write_text("from daengs_evals.answer_quality import questions\n", encoding="utf-8")
    assert _imports_evals(bad_from) == [(1, "daengs_evals.answer_quality")]
