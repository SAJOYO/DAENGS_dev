"""반대 방향 가드 — `rag`·`realtime` 이 `crawler` 를 **어디까지** 참조하는가 (RT-001 ①-2).

`test_import_direction.py` 는 `crawler/` 만 훑어서 "crawler 가 위를 부르지 않는다"만 본다.
방향은 그것으로 지켜지지만 **범위**는 아무도 안 본다. RAG-018 의 "`rag` 가 `crawler` 를 참조하는
것은 경로 탐색 하나뿐"은 지금까지 문서에만 있는 규칙이었다.

범위가 왜 방향만큼 중요한가 — realtime 이 `Fetcher`·`store`·`seed_sources` 를 끌어오는 순간
**"실시간은 저장하지 않는다"**(`data/README.md` §5, RAG-012)가 흐려진다. realtime 은 크롤 대상이
아니고 `.meta.json` 도 만들지 않는다. 캐시하는 것은 측정소 목록 같은 정적 메타뿐이다.

**허용 목록은 패키지마다 다르다.** 하나로 뭉뚱그려 넓히면 realtime 을 좁힌 이유가 같이 사라진다.

파일을 나눈 이유 — `feat/rag` 와 `feat/realtime` 이 병렬이라 `test_import_direction.py` 를
양쪽이 고치면 충돌한다. 저기서는 `FORBIDDEN` 한 줄만 늘리고 새 검사는 여기로 뺐다.

검사는 `rglob` 로 훑으므로 **패키지 내부 배치와 무관하다** — RAG-023 이 `rag/` 를
`core/`+`stages/` 로 재배치해도 여기는 안 고친다.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# 이관하면서 다섯 형제가 `daengs_life/` 한 겹 안으로 들어갔다 (D-018). 스캔 루트가 그 한 겹을
# 가리키고 `_strip()` 이 import 이름에서 접두사를 벗기므로, **아래 허용/금지 목록은 형제
# 이름 그대로 읽힌다** — 목록에 `daengs_life.` 를 붙여 적으면 이 파일이 읽기 어려워진다.
PKG = "daengs_life"
PKG_ROOT = Path(__file__).resolve().parents[1] / "src" / PKG


def _strip(name: str) -> str:
    """import 이름에서 `daengs_life.` 한 겹을 벗긴다. 접두사가 없으면 그대로 둔다."""
    return name[len(PKG) + 1:] if name.startswith(PKG + ".") else name

# 패키지 → `crawler` 안에서 import 해도 되는 모듈. 여기 없으면 실패한다.
ALLOWED: dict[str, set[str]] = {
    # 경로 탐색(RAG-018·RAG-014) + 「법령」 조항 인용 파싱. 후자는 의도된 확장이다 —
    # `cites()` 에 허위 인용 19건을 잡아낸 규칙이 들어 있어 복사하면 그 지식이 갈라진다
    # (`rag/README.md` 설계 메모).
    # `jsobject` 도 같은 종류의 확장이다 — 손해보험협회 공시가 JSON 이 아니라 JS 객체
    # 리터럴로 오는데, 그 리더를 파서 쪽에 복사하면 **따옴표·이스케이프 처리가 두 벌**이 되고
    # 한쪽만 고쳐지는 날 수집과 파싱이 서로 다른 것을 읽는다.
    # `nextpayload` 도 `jsobject` 와 **같은 이유**로 들어왔다 (RAG-046). 에어프레미아는
    # Next.js 라 요금·케이지 규격이 DOM 에 없고 RSC 페이로드 안에 있는데, 그 리더를 파서 쪽에
    # 복사하면 이스케이프 처리가 두 벌이 되어 **크롤러는 요금을 보는데 파서는 못 보는** 날이 온다.
    "rag": {"crawler.core.config", "crawler.core.textutil", "crawler.core.jsobject",
            "crawler.core.nextpayload"},
    # 경로 탐색 하나뿐 (RT-001 ①-2). `textutil` 은 한국 법령 문서용이라 realtime 이 쓸 일이
    # 없고, 목록이 갈려 있어야 그게 새로 들어오는 날 여기서 잡힌다.
    "realtime": {"crawler.core.config"},
    # Celery 워커·Beat (RAG-009 의 형제 패키지).
    #
    # **RAG-044 에서 셋을 넓혔다.** 크롤러 Beat 가 들어오면서 워커가 실제로 크롤을 부르게
    # 됐고, 그때 필요한 것이 딱 이 셋이다:
    #   · `crawler.run`          — 수집 진입점. **CLI 와 같은 함수**다. 전용 경로를 태스크에
    #                              새로 만들면 새 소스가 CLI 에서만 되고 Beat 에서는 조용히
    #                              안 되는 날이 온다
    #   · `crawler.core.cadence` — due 판정. 판정 자체는 crawler 안에 있어야 `python -m crawler`
    #                              만으로도 "무엇이 밀렸나"를 볼 수 있다 (RAG-001 원칙 1)
    #   · `crawler.core.registry`— 시드 목록과 구현 여부. cadence 가 registry 를 부르지 않고
    #                              인자로 받게 해 둬서, 그 조립을 태스크가 한다
    #
    # **RAG-054 에서 하나 더** — `crawler.core.revision`. 개정 판정이다. cadence 와 같은 종류라
    # 같은 근거다: 판정은 crawler 안에 있어야 `python -m crawler revisions` 만으로도 "무엇이
    # 개정됐나"가 보이고, 태스크는 그 결과로 무엇을 보낼지만 정한다. 원본을 받지 않으므로
    # `run` 을 우회하는 수집 경로가 아니다.
    #
    # **`store`·`fetch` 는 여전히 밖이다.** 워커가 그것을 직접 잡으면 `run` 을 우회하는 두 번째
    # 수집 경로가 생기고, 위의 "같은 함수" 가 그날로 무너진다. 넓힐 일이 또 생기면 그 줄이
    # `run` 안으로 들어갈 수 있는지 먼저 볼 것.
    "tasks": {"crawler.core.config", "crawler.core.cadence", "crawler.core.registry",
              "crawler.core.revision", "crawler.run"},
    # `jobs` (D-062) — Cloud Run Job 이 부르는 조립층. `tasks` 와 같은 자리에서 같은 셋을 쓴다.
    # `tasks` 와 달리 파싱~적재까지 이어 붙이므로 `rag` 도 부르지만, `rag` 는 crawler 가 아니라
    # 이 검사의 대상이 아니다. `store`·`fetch` 는 여전히 밖이다 — `run` 을 우회하는 두 번째
    # 수집 경로를 만들지 않는다.
    #
    # **RAG-087 에서 하나 더** — `crawler.core.revision`. `tasks` 와 같은 근거다: `discover()` 만
    # 부르므로 원본을 받는 두 번째 수집 경로가 아니다 (RAG-054).
    "jobs": {"crawler.core.config", "crawler.core.cadence", "crawler.core.registry",
             "crawler.core.revision", "crawler.run"},
    # 서빙 (RAG-027). `app` 은 도메인 패키지를 통해 설정에 닿으므로 직접 쓸 일이 거의 없다.
    # 그래도 목록에 두는 이유는 **새로 들어오는 날 잡히게** 하기 위해서다.
    "app": {"crawler.core.config"},
}


def _crawler_imports(path: Path) -> list[tuple[int, str]]:
    """(줄번호, 가리키는 crawler 모듈). `crawler` 밖은 관심 없다."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, _strip(a.name)) for a in node.names
                      if _strip(a.name).split(".")[0] == "crawler"]
        elif isinstance(node, ast.ImportFrom):
            # level > 0 은 패키지 내부 상대 import 라 crawler 까지 못 나간다
            if node.level or not node.module:
                continue
            module = _strip(node.module)
            if module.split(".")[0] != "crawler":
                continue
            # `from crawler.core import textutil` 은 module 이 아니라 이름 쪽이 모듈이다.
            # 두 형태를 다 봐야 한다 — 한쪽만 보면 우회로가 생긴다.
            found += [(node.lineno, f"{module}.{a.name}") for a in node.names]
    return found


def _violations(allowed: set[str], root: Path) -> list[str]:
    out: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for lineno, target in _crawler_imports(path):
            # `from crawler.core.config import DATA_DIR` 는 target 이
            # `crawler.core.config.DATA_DIR` 로 잡힌다 — 접두사가 허용되면 통과다.
            if any(target == ok or target.startswith(ok + ".") for ok in allowed):
                continue
            rel = path.relative_to(root.parent)
            out.append(f"{rel}:{lineno} imports {target!r}")
    return out


def test_packages_reach_only_the_allowed_crawler_modules() -> None:
    checked = 0
    for package, allowed in ALLOWED.items():
        root = PKG_ROOT / package
        if not root.is_dir():
            continue                       # 아직 없는 패키지는 건너뛴다
        checked += 1
        found = _violations(allowed, root)
        assert not found, (
            f"`{package}` 가 허용 밖의 crawler 모듈을 import 했다 (RT-001 ①-2).\n"
            f"  허용: {', '.join(sorted(allowed))}\n  "
            + "\n  ".join(found)
            + f"\n\n정말 필요하면 ALLOWED['{package}'] 에 추가하고 **왜 넓혔는지**를 남길 것 — "
              "`rag` 의 textutil 이 그 선례다."
        )
    assert checked, "검사한 패키지가 하나도 없다 — ALLOWED 의 이름이 실제 디렉터리와 어긋났다"


def test_guard_actually_detects_a_violation(tmp_path: Path) -> None:
    """가드가 살아 있는지 — 위반 파일을 만들어 실제로 잡히는지 확인한다.

    이 확인이 없으면 검사기가 조용히 아무것도 안 보게 돼도 테스트는 계속 통과한다
    (`test_import_direction.py` 와 같은 이유로 붙인다).
    """
    fake = tmp_path / "realtime"
    (fake / "providers").mkdir(parents=True)
    (fake / "providers" / "bad.py").write_text(
        # 실제로 걱정하는 형태 그대로 — realtime 이 수집기를 끌어오는 순간이다
        "from crawler.core import store\n"
        "from crawler.core.config import DATA_DIR\n",
        encoding="utf-8")

    found = _violations(ALLOWED["realtime"], fake)
    assert len(found) == 1 and "crawler.core.store" in found[0], found


def test_the_allowed_prefix_rule_does_not_leak() -> None:
    """접두사 매칭이 `crawler.core.configX` 같은 이웃 모듈까지 열어주면 안 된다."""
    fake_ok = "crawler.core.config"
    for target, expected in [
        ("crawler.core.config", True),
        ("crawler.core.config.DATA_DIR", True),
        ("crawler.core.configuration", False),      # 접두사가 같지만 다른 모듈이다
        ("crawler.core.store", False),
    ]:
        got = target == fake_ok or target.startswith(fake_ok + ".")
        assert got is expected, target


# --------------------------------------------- 컨트롤러에 로직을 넣지 않는다 (RAG-027)

# 컨트롤러가 **불러도 되는** 도메인 모듈. 타입과 값 객체뿐이고, 판정·조회는 없다.
CONTROLLER_MAY_IMPORT = {"realtime.geo", "realtime.cache", "realtime.config"}

# 컨트롤러가 이걸 부르면 그 순간 로직이 컨트롤러로 넘어온 것이다.
CONTROLLER_MUST_NOT_IMPORT = {"realtime.collect", "realtime.rules", "realtime.observation",
                              "realtime.providers", "realtime.transport", "rag", "crawler"}


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, _strip(a.name)) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.append((node.lineno, _strip(node.module)))
    return found


def test_controllers_call_services_not_the_domain() -> None:
    """**RAG-027 의 유일한 강제 규칙**을 기계가 지킨다 — 컨트롤러에 로직 0줄.

    말로 두면 반드시 새어 나온다. "여기서 한 줄만" 이 쌓이면 서비스 층이 빈 껍데기가 되고,
    그 순간 이 구조는 이름만 MVC2 가 된다. 판정·조회 모듈을 import 하는 것을 금지하면
    로직을 넣으려 해도 넣을 재료가 없다.
    """
    controllers = PKG_ROOT / "app" / "controllers"
    if not controllers.is_dir():
        pytest.skip("아직 컨트롤러가 없다")

    violations: list[str] = []
    for path in sorted(controllers.rglob("*.py")):
        for lineno, target in _imports(path):
            head = target.split(".")[0]
            if head not in {"realtime", "rag", "crawler"}:
                continue                              # app.* · fastapi · 표준 라이브러리
            allowed = any(target == ok or target.startswith(ok + ".")
                          for ok in CONTROLLER_MAY_IMPORT)
            banned = any(target == no or target.startswith(no + ".")
                         for no in CONTROLLER_MUST_NOT_IMPORT)
            if banned or not allowed:
                violations.append(f"{path.relative_to(PKG_ROOT)}:{lineno} imports {target!r}")

    assert not violations, (
        "컨트롤러가 도메인을 직접 부른다 (RAG-027 — 로직은 `app/services/` 에): "
        + " · ".join(violations)
        + f"  |  허용: {', '.join(sorted(CONTROLLER_MAY_IMPORT))}")
