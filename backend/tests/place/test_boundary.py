"""경계의 기계적 집행 — geo `tests/test_search_closure.py` 의 번안 (UPSTREAM.md).

geo 에서는 provider/profile 이 같은 트리에 살아서 "closure 에 그들이 없다"를 쟀다.
여기는 그 코드가 아예 없으므로 같은 검사는 공허하게 통과한다. 이 저장소에서 실제로
막아야 하는 침범은 둘이다:

  1. daengs_place 트리가 다시 자라는 것 — provider·profile·usage 류가 돌아오는 것
  2. place-search 가 daengs_backend 를 아는 것 — 두 서비스의 경계는 HTTP 뿐이다 (D-026)

그래서 금지 목록이 아니라 **화이트리스트**로 잰다: 진입점 closure 의 하위 패키지는
api / core / geo / place 넷뿐이어야 한다 (ingest 는 배치 전용이라 서버 closure 밖).
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from daengs_place.core.db import get_session

ALLOWED_PLACE_SUBPACKAGES = {"api", "core", "geo", "main", "place", "territory"}
PACKAGE_DIR = Path(__file__).resolve().parents[2] / "src" / "daengs_place"
FORBIDDEN_PACKAGES = {
    "daengs_backend",
    "daengs_journey",
    "daengs_life",
    "daengs_training",
}

_PROBE = """
import json, sys
import daengs_place.main
print(json.dumps(sorted(m for m in sys.modules if m.startswith("daengs_place."))))
"""


def test_entrypoint_closure_is_whitelisted_and_blind_to_the_backend():
    """같은 프로세스에서 재면 다른 테스트가 이미 로드한 모듈이 섞인다 — 새 인터프리터로 잰다."""
    probe = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, check=True,
    )
    loaded = json.loads(probe.stdout)
    subpackages = {m.split(".")[1] for m in loaded if "." in m}
    assert subpackages <= ALLOWED_PLACE_SUBPACKAGES, (
        f"검색 진입점 closure 에 새 하위 패키지가 생겼다: "
        f"{sorted(subpackages - ALLOWED_PLACE_SUBPACKAGES)}. provider/profile/usage 류를 "
        "다시 들여왔는지, ingest 를 서버 경로에 물렸는지 보라 (D-026)."
    )


def test_place_search_does_not_import_the_backend():
    """두 서비스의 경계는 HTTP 뿐이다. import 가 생기면 별도 컨테이너의 의미가 없다."""
    script = (
        "import sys, daengs_place.main; import daengs_place.ingest.__main__;"
        "print(any(m.startswith('daengs_backend') for m in sys.modules))"
    )
    probe = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "False"


def test_place_does_not_import_other_product_packages():
    """같은 src 아래에 있어도 런타임 경계는 Python import로 넘지 않는다 (D-039)."""
    found = []
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
    assert not found, "Place의 별도 런타임 경계를 넘는 import:\n  " + "\n  ".join(found)


async def _no_db():
    yield None


def test_search_app_serves_health_and_validation_without_db_or_any_key():
    """DB 없이도 뜨고, 검증 경로가 동작하고, place 밖의 표면은 노출하지 않는다."""
    from daengs_place.main import app as search_app

    search_app.dependency_overrides[get_session] = _no_db
    try:
        with TestClient(search_app) as client:
            assert client.get("/health").json() == {"ok": True}
            rejected = client.post("/v2/places/search", json={
                "lat": 37.5, "lng": 127.0, "kinds": ["cafe"], "conditions": {},
            })
            assert rejected.status_code == 422
    finally:
        search_app.dependency_overrides.pop(get_session, None)

    exposed = set(search_app.openapi()["paths"])
    assert exposed == {
        "/health",
        "/health/ready",
        "/territory/sites/nearby",
        "/v2/places/search",
    }, (
        f"검색 서버의 표면이 계약과 다르다: {sorted(exposed)}"
    )
