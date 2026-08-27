"""`scripts/` 의 모든 모듈이 실제로 import 되는가.

**왜 필요한가**: `ruff` 는 import 대상을 해석하지 않고, pytest 가 실제로 import 하는
스크립트는 `scripts.verify.walk_bundle` 하나뿐이다. `app/features/territory/paint.py` 의 함수 이름을
바꾸면 `spikes/territory_paint/paint.py` 의 import 가 조용히 깨진 채로 두 게이트를 다
통과하고, 몇 달 뒤 연구 문서의 `## 재현` 을 실제로 쳐 보는 순간에야 드러난다.
그때는 "왜 안 도는지"부터 파야 한다 (`scripts/README.md`).

`compileall` 로는 부족하다 — 바이트코드만 만들지 import 를 해석하지 않는다.

발견식이라 새 스파이크·새 갈래 폴더가 저절로 들어온다. 목록을 손으로 유지하면
빠뜨린 것이 곧 안 지켜지는 것이다.
"""

import importlib
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _modules() -> list[str]:
    out = []
    for path in sorted(_SCRIPTS.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "__init__.py":
            continue
        rel = path.relative_to(_SCRIPTS.parent).with_suffix("")
        out.append(".".join(rel.parts))
    return out


def test_discovers_every_script():
    """발견 자체가 깨지면(경로 오타 등) 아래 테스트가 0건으로 조용히 통과한다."""
    found = _modules()
    assert len(found) >= 15, found
    assert "scripts.spikes.territory_paint.storage_candidates" in found
    assert "scripts.verify.walk_fixture" in found


@pytest.mark.parametrize("module", _modules())
def test_script_imports(module: str):
    importlib.import_module(module)
