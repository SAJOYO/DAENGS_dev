"""회귀 가드 — 배포되는 API 프로세스가 무거운 것을 끌어오지 않는다 (#23).

`daengs_backend` 가 `daengs_life` 를 부르는 자리는 `main.py` 의 `/walk` 등록 하나뿐이고,
그 선을 그을 수 있었던 이유는 **`walk` 라우터가 임베딩 모델을 안 쓰기 때문**이다 (D-018).
지금은 사실이지만 말로 두면 지켜지지 않는다 — 누군가 `deps.py` 나 `services/walk.py` 에
`get_encoder` 를 한 줄 넣는 순간, 배포되는 API 프로세스가 조용히 torch 를 요구하게 된다.
그 시점에 backend 컨테이너는 `ml` 그룹이 없어서 **뜨지도 않는다.**

**별도 프로세스에서 검사한다.** 같은 프로세스의 `sys.modules` 를 보면 `test_embed.py` 같은
이웃 테스트가 이미 올려 둔 torch 가 잡혀 항상 실패한다 — 그러면 가드를 지우게 되고, 그게
가드가 죽는 가장 흔한 경로다.

`test_import_direction*.py` 와 파일을 나눈 이유는 **경계가 다르기** 때문이다. 저 둘은
`daengs_life` **안**의 형제 패키지끼리를 AST 로 보고, 여기는 `daengs_backend` → `daengs_life`
경계를 **실제 import 결과**로 본다. 함수 안에 숨은 import 는 AST 가 못 잡는데, 여기서는 잡힌다.
"""
from __future__ import annotations

import json
import subprocess
import sys

from daengs_life.app.deps import get_cache

# 하나라도 올라오면 컨테이너가 못 뜨거나 메모리를 GB 단위로 먹는다.
# `transformers` 를 같이 세는 이유 — `sentence_transformers` 없이 저것만 들어오는 경로가 있다.
HEAVY = ("torch", "sentence_transformers", "transformers")

_PROBE = (
    "import json, sys;"
    " import daengs_backend.main;"
    ' print("MODULES=" + json.dumps(sorted({m.split(".")[0] for m in sys.modules})))'
)


def _modules_after_importing_the_app() -> set[str]:
    """새 프로세스에서 앱을 import 하고 최상위 모듈 이름을 받아 온다.

    환경 변수는 그대로 물려준다 — `conftest.py` 가 넣어 둔 테스트용 키가 없으면
    `config.Settings` 가 뜨지 않아 import 자체가 실패한다.
    """
    done = subprocess.run([sys.executable, "-c", _PROBE],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, (
        f"앱을 import 하지 못했다 (returncode={done.returncode}).\n{done.stderr[-2000:]}")
    line = next(ln for ln in done.stdout.splitlines() if ln.startswith("MODULES="))
    return set(json.loads(line[len("MODULES="):]))


def test_importing_the_app_does_not_pull_in_the_ml_stack() -> None:
    loaded = _modules_after_importing_the_app()
    found = sorted(set(HEAVY) & loaded)
    assert not found, (
        f"`daengs_backend.main` 을 import 했더니 {found} 가 올라왔다.\n"
        "  배포되는 API 프로세스는 `ml` 그룹(torch) 없이 떠야 한다 — 이대로 두면 "
        "backend 컨테이너가 기동에 실패한다.\n"
        "  `/ask`(파트①)를 붙이려는 것이라면 이 가드를 지우지 말고, 임베딩 모델을 "
        "어느 프로세스에 둘지부터 `docs/decisions.md` 에 남길 것.")


def test_the_guard_would_notice_the_ml_stack() -> None:
    """가드가 살아 있는지 — 실제로 무거운 것이 올라오면 이름이 잡히는지.

    이 확인이 없으면 `_PROBE` 가 조용히 아무것도 안 보게 돼도 테스트는 계속 통과한다
    (`test_import_direction.py` 가 자기검사 픽스처를 두는 것과 같은 이유다).
    """
    probe = "import json, sys;" ' print("MODULES=" + json.dumps(sorted({m.split(".")[0] for m in sys.modules})))'
    done = subprocess.run([sys.executable, "-c", "import json, sys; sys.modules['torch'] = sys; " + probe],
                          capture_output=True, text=True, check=False)
    line = next(ln for ln in done.stdout.splitlines() if ln.startswith("MODULES="))
    assert "torch" in set(json.loads(line[len("MODULES="):]))


def test_walk_is_registered() -> None:
    """라우터가 실제로 붙었는지. 등록 한 줄이 사라져도 위 가드는 통과한다.

    `app.routes` 를 보지 않는 이유 — 지금 FastAPI 는 `include_router` 를 `_IncludedRouter`
    로 감싸 두었다가 나중에 펼친다. 그래서 저기에는 `/walk` 가 안 보인다. OpenAPI 스키마는
    실제로 노출되는 경로 목록이라 그 구현 변화에 안 흔들린다.
    """
    from daengs_backend.main import app

    assert "/walk" in app.openapi()["paths"]


def test_lifespan_opens_the_cache_up_front() -> None:
    """시작할 때 `Cache` 를 미리 만들고, 끝날 때 놓는지 (RT-001 ④-c).

    미리 안 열면 Redis 연결 비용이 **첫 요청 하나**에 통째로 붙는다. 안 놓으면 리로드가
    잦은 개발 모드에서 죽은 워커의 커넥션 풀이 남는다 — `engine.dispose()` 와 같은 이유다.

    Redis 가 없어도 이 테스트는 돈다. `open_store()` 가 연결 실패를 예외가 아니라 저하로
    다뤄 메모리 저장소로 떨어지므로, 어느 쪽이든 캐시는 하나 만들어진다 (④-c).
    """
    from fastapi.testclient import TestClient

    from daengs_backend.main import app

    get_cache.cache_clear()
    with TestClient(app):
        assert get_cache.cache_info().currsize == 1, "lifespan 이 캐시를 미리 열지 않았다"
    assert get_cache.cache_info().currsize == 0, "lifespan 이 캐시를 놓지 않았다"
