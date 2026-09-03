"""경계의 기계적 집행 — geo `tests/test_search_closure.py` 의 번안 (UPSTREAM.md).

geo 에서는 provider/profile 이 같은 트리에 살아서 "closure 에 그들이 없다"를 쟀다.
여기는 그 코드가 아예 없으므로 같은 검사는 공허하게 통과한다. 이 저장소에서 실제로
막아야 하는 침범은 둘이다:

  1. daengs_place 최상위가 다시 자라는 것 — profile·usage·전역 provider 류가 돌아오는 것
  2. place-search 가 daengs_backend 를 아는 것 — 두 서비스의 경계는 HTTP 뿐이다 (D-026)

그래서 금지 목록이 아니라 **화이트리스트**로 잰다: 진입점 closure 의 하위 패키지는
api / core / geo / place / territory뿐이어야 한다 (ingest는 배치 전용이라 서버 closure 밖).
PR5의 Gemini adapter는 범용 provider 계층이 아니라 `place/providers` 아래 Place 소유다.
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
DISCOVERY_DIR = PACKAGE_DIR / "place" / "discovery"
INTENT_DIR = PACKAGE_DIR / "place" / "intent"
PRESENTATION_DIR = PACKAGE_DIR / "place" / "presentation"
FORBIDDEN_PACKAGES = {
    "daengs_backend",
    "daengs_journey",
    "daengs_life",
    "daengs_training",
}
FORBIDDEN_INTENT_IMPORTS = {
    "fastapi",
    "google",
    "httpx",
    "sqlalchemy",
}
FORBIDDEN_PRESENTATION_IMPORTS = FORBIDDEN_INTENT_IMPORTS | {
    "daengs_place.api",
    "daengs_place.ingest",
    "daengs_place.place.intent",
}
FORBIDDEN_DISCOVERY_IMPORTS = {
    "fastapi",
    "google",
    "httpx",
    "daengs_place.api",
    "daengs_place.ingest",
    "daengs_place.main",
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
        "다시 들여왔는지, ingest를 서버 경로에 물렸는지 보라 (D-026)."
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


def test_place_intent_core_stays_provider_transport_and_presentation_free():
    """PR2 코어는 provider·HTTP·DB와 아직 승격되지 않은 presentation을 모른다."""
    found = []
    for path in sorted(INTENT_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.split(".", 1)[0] in FORBIDDEN_INTENT_IMPORTS or name.startswith(
                    "daengs_place.place.presentation"
                ):
                    found.append(
                        f"{path.relative_to(PACKAGE_DIR)}:{node.lineno} imports {name}"
                    )
    assert not found, "Place intent 코어의 조기 runtime 결합:\n  " + "\n  ".join(found)


def test_place_presentation_stays_provider_transport_and_intent_free():
    """PR3 표시는 사실·검색 계약만 소비하고 provider·transport·intent 구현을 모른다."""
    found = []
    for path in sorted(PRESENTATION_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if any(
                    name == forbidden or name.startswith(f"{forbidden}.")
                    for forbidden in FORBIDDEN_PRESENTATION_IMPORTS
                ):
                    found.append(
                        f"{path.relative_to(PACKAGE_DIR)}:{node.lineno} imports {name}"
                    )
    assert not found, "Place presentation의 조기 runtime 결합:\n  " + "\n  ".join(found)


def test_place_discovery_stays_provider_transport_and_global_orchestration_free():
    """PR4 조립은 Place 안에서 닫고 HTTP·provider·전역 오케스트레이터를 모른다."""
    found = []
    for path in sorted(DISCOVERY_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.split(".", 1)[0] in FORBIDDEN_PACKAGES or any(
                    name == forbidden or name.startswith(f"{forbidden}.")
                    for forbidden in FORBIDDEN_DISCOVERY_IMPORTS
                ):
                    found.append(
                        f"{path.relative_to(PACKAGE_DIR)}:{node.lineno} imports {name}"
                    )
    assert not found, "Place discovery의 runtime 경계 침범:\n  " + "\n  ".join(found)


async def _no_db():
    yield None


def test_search_app_serves_public_health_and_validation_without_db_or_any_key():
    """DB·키 없이도 뜨고, 기존 공개 검증 경로가 동작한다."""
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
    public = {path for path in exposed if not path.startswith("/internal/")}
    assert public == {
        "/health",
        "/health/ready",
        "/territory/sites/nearby",
        "/v2/places/search",
    }, (
        f"검색 서버의 공개 표면이 계약과 다르다: {sorted(public)}"
    )
    assert exposed - public == {"/internal/place/discovery"}


def test_nginx_does_not_publish_the_internal_discovery_prefix():
    nginx = (PACKAGE_DIR.parents[2] / "nginx" / "default.conf").read_text(encoding="utf-8")

    assert "location /internal" not in nginx
    assert "location /v2/places/" in nginx
    assert "location /territory/sites/" in nginx
