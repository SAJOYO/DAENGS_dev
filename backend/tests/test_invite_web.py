"""routers/invite_web.py — 공동 돌봄 초대 링크의 웹 폴백.

인증도 DB 도 없는 정적 라우터라 다른 라우터 테스트처럼 fake DB 를 세울 필요가
없다 — 이 라우터만 얹은 얇은 앱으로 충분하다.
"""

import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend import app_links
from daengs_backend.config import Settings, settings
from daengs_backend.routers import invite_web

app = FastAPI()
app.include_router(invite_web.router)
client = TestClient(app)

# 모양만 맞는 값이다 — 실제 서명 지문이 아니다.
FINGERPRINT = "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99"
ENV = "DAENGS_PLAY_SIGNING_SHA256_FINGERPRINTS"


def test_초대_페이지는_html을_돌려준다():
    resp = client.get("/invite")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "앱 설치하기" in resp.text
    assert "이미 설치했나요? 앱에서 초대 열기" in resp.text
    assert "초대 링크 복사" in resp.text
    assert "복사가 안 되나요? 링크 직접 보기" in resp.text


def test_초대_페이지는_캐시하지_않는다():
    resp = client.get("/invite")

    assert resp.headers["cache-control"] == "no-store"


def test_초대_페이지는_토큰을_모른다():
    """토큰은 프래그먼트에만 있고 서버로 안 온다 — 쿼리로 흉내 내 봐도 응답에 안 보여야 한다."""
    resp = client.get("/invite?token=should-not-appear-server-side")

    assert "should-not-appear-server-side" not in resp.text


def test_지문이_없으면_빈_배열을_준다(monkeypatch):
    """가짜 지문을 채워 넣지 않는다 — 검증이 그냥 실패하는 쪽을 고른다."""
    monkeypatch.setattr(settings, "play_signing_sha256_fingerprints_raw", None)

    resp = client.get("/.well-known/assetlinks.json")

    assert resp.status_code == 200
    assert resp.json() == []


def test_지문이_있으면_App_Links_문서를_만든다(monkeypatch):
    monkeypatch.setattr(settings, "play_signing_sha256_fingerprints_raw", f'["{FINGERPRINT}"]')

    resp = client.get("/.well-known/assetlinks.json")

    body = resp.json()
    assert len(body) == 1
    target = body[0]["target"]
    assert target["namespace"] == "android_app"
    assert target["package_name"] == "com.daengs.app"
    assert target["sha256_cert_fingerprints"] == [FINGERPRINT]
    assert body[0]["relation"] == ["delegate_permission/common.handle_all_urls"]


# -- 환경 변수에서 설정으로 ---------------------------------------------------


def test_지문은_환경_변수의_JSON_배열로_읽힌다(monkeypatch):
    """운영은 `backend/.env` 의 `DAENGS_PLAY_SIGNING_SHA256_FINGERPRINTS=[…]` 로 준다 —
    원문 문자열로 받아 `app_links.py` 가 읽고, 응답까지 그대로 실린다."""
    monkeypatch.setenv(ENV, f'["{FINGERPRINT}"]')

    loaded = Settings(_env_file=None)

    assert loaded.play_signing_sha256_fingerprints == [FINGERPRINT]
    assert loaded.play_signing_config_error is None

    monkeypatch.setattr(settings, "play_signing_sha256_fingerprints_raw", loaded.play_signing_sha256_fingerprints_raw)
    resp = client.get("/.well-known/assetlinks.json")
    assert resp.json()[0]["target"]["sha256_cert_fingerprints"] == [FINGERPRINT]


def test_지문은_둘_이상_넣을_수_있다(monkeypatch):
    """스토어 앱 서명 키와 업로드 키가 다르면 둘 다 — 스토어 설치본과 로컬 릴리스 빌드가 둘 다 검증된다."""
    second = FINGERPRINT.replace("AA", "01")
    monkeypatch.setenv(ENV, f'["{FINGERPRINT}","{second}"]')

    assert Settings(_env_file=None).play_signing_sha256_fingerprints == [FINGERPRINT, second]


def test_지문은_대문자로_맞추고_공백을_버린다(monkeypatch):
    monkeypatch.setenv(ENV, f'[" {FINGERPRINT.lower()} "]')

    loaded = Settings(_env_file=None)

    assert loaded.play_signing_sha256_fingerprints == [FINGERPRINT]
    assert loaded.play_signing_config_error is None


@pytest.mark.parametrize("raw", [None, "", "   ", "[]"])
def test_비워_두면_빈_배열이고_오류도_아니다(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, raw)

    loaded = Settings(_env_file=None)

    assert loaded.play_signing_sha256_fingerprints == []
    assert loaded.play_signing_config_error is None


# -- 틀린 설정 — 부팅은 막지 않고, 전부 버리고, 사유를 남긴다 ----------------------


BAD_VALUES = [
    pytest.param("not-json", id="JSON 아님"),
    pytest.param("[AA:BB", id="깨진 JSON"),
    pytest.param(f'{{"a": "{FINGERPRINT}"}}', id="객체"),
    pytest.param(f'"{FINGERPRINT}"', id="배열 아닌 문자열"),
    pytest.param("42", id="숫자"),
    pytest.param("[42]", id="숫자 원소"),
    pytest.param(f'["{FINGERPRINT}", null]', id="null 원소"),
    pytest.param(f'[["{FINGERPRINT}"]]', id="중첩 배열"),
    pytest.param('["AA:BB:CC:DD"]', id="너무 짧음"),
    pytest.param('["73:B7:66:C2:FA:F8:24:29:FD:C5:2E:B1:99:99:EC:CE:6F:3F:D0:1C"]', id="SHA-1 20쌍"),
    pytest.param(f'["{FINGERPRINT.replace(":", "")}"]', id="콜론 없음"),
    pytest.param('["c7dmwvr4JCn9xS6xmZnszm8/0Bw="]', id="카카오 키 해시 base64"),
    pytest.param(f'["{FINGERPRINT}", "AA:BB"]', id="정상과 비정상 혼합"),
]


@pytest.mark.parametrize("raw", BAD_VALUES)
def test_틀린_지문_설정은_부팅을_막지_않고_전부_버린다(monkeypatch, raw):
    """선택 기능의 설정 오류가 백엔드 전체를 세우면 안 된다 — 예전에는 여기서 부팅이 죽었다
    (JSON 이 아니면 pydantic-settings 의 `SettingsError`, 모양이 틀리면 `ValidationError`)."""
    monkeypatch.setenv(ENV, raw)

    loaded = Settings(_env_file=None)

    # 정상 지문이 섞여 있어도 일부만 채택하지 않는다.
    assert loaded.play_signing_sha256_fingerprints == []
    assert loaded.play_signing_config_error is not None
    assert ENV in loaded.play_signing_config_error


@pytest.mark.parametrize("raw", BAD_VALUES)
def test_틀린_지문_설정이면_assetlinks_는_빈_배열이다(monkeypatch, raw):
    monkeypatch.setattr(settings, "play_signing_sha256_fingerprints_raw", raw)

    resp = client.get("/.well-known/assetlinks.json")

    assert resp.status_code == 200
    assert resp.json() == []


def test_사유에는_원문_값을_싣지_않는다():
    secret_like = "this-is-not-a-fingerprint-but-looks-secret"

    result = app_links.parse_play_signing_fingerprints(f'["{FINGERPRINT}", "{secret_like}"]')

    assert result.fingerprints == ()
    assert "1번째 원소" in result.error
    assert secret_like not in result.error
    assert FINGERPRINT not in result.error


def test_설정을_읽는_자리는_틀린_지문에도_던지지_않고_오류를_로그로_남긴다(monkeypatch, caplog):
    """부팅이 부르는 `_load_settings` 그대로 — 예외 없이 설정을 돌려주고 ERROR 로그를 한 줄 남긴다."""
    from daengs_backend import config

    monkeypatch.setenv(ENV, '["AA:BB", "should-not-be-logged"]')

    with caplog.at_level(logging.ERROR, logger="daengs_backend.config"):
        loaded = config._load_settings()

    assert loaded.play_signing_sha256_fingerprints == []
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and ENV in r.getMessage()]
    assert len(errors) == 1
    assert "should-not-be-logged" not in errors[0].getMessage()


def test_필수_보안_설정은_여전히_부팅에서_막는다(monkeypatch):
    """선택 설정만 느슨해졌다 — 카카오 앱 키가 비면 예전처럼 설정 로딩이 실패한다."""
    from daengs_backend import config

    monkeypatch.setenv(ENV, "not-json")
    monkeypatch.setenv("DAENGS_KAKAO_APP_KEYS", "[]")

    with pytest.raises(RuntimeError, match="KAKAO_APP_KEYS"):
        config._load_settings()


def test_틀린_지문_설정으로도_본_앱의_다른_라우트와_health_는_멀쩡하다(monkeypatch):
    """lifespan 은 돌리지 않는다(Redis·예열). DB 세션은 갈아끼운다 — 팀 DB 를 읽으러 가면 안 된다."""
    from daengs_backend.core.database import get_session
    from daengs_backend.main import app as main_app

    class _OkSession:
        async def execute(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(settings, "play_signing_sha256_fingerprints_raw", "{broken")
    main_app.dependency_overrides[get_session] = lambda: _OkSession()
    try:
        main = TestClient(main_app)
        health = main.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "db": "ok"}
        assert main.get("/invite").status_code == 200
        assert main.get("/.well-known/assetlinks.json").json() == []
    finally:
        main_app.dependency_overrides.clear()


# -- 「앱에서 초대 열기」 ------------------------------------------------------


def test_앱_열기_버튼은_토큰을_URL이_아니라_extra에_싣는다():
    """`intent://…/invite#Intent;…;S.<extra>=<token>;…;end` — 데이터 URI 에 토큰이 없다.
    extra 이름은 앱 쪽 `InviteLink.WEB_FALLBACK_EXTRA` 와 같아야 한다."""
    html = client.get("/invite").text

    assert "intent://daengapi.weareithero.cloud/invite#Intent;scheme=https;package=com.daengs.app;" in html
    assert "S.com.daengs.app.extra.INVITE_TOKEN=" in html
    # 예전 쿼리 통로가 남아 있으면 토큰이 URL 의 일부가 된다.
    assert "/invite?t=" not in html
    assert "?t=" not in html


def test_앱_열기_폴백_URL에는_토큰이_없다():
    """앱이 없을 때 브라우저가 진짜로 여는 주소(스토어)에 토큰이 붙으면 안 된다."""
    html = client.get("/invite").text

    fallback = re.search(r"browser_fallback_url=\" \+ fallback", html)
    assert fallback, "폴백은 스토어 주소 하나뿐이어야 한다"
    store = re.search(r'encodeURIComponent\("(https://play\.google\.com/[^"]+)"\)', html)
    assert store and "token" not in store.group(1)


def test_앱_열기를_무조건_열린다고_말하지_않는다():
    html = client.get("/invite").text

    assert "무조건" not in html
    # 카카오톡 안에서 안 열릴 때의 길을 같이 말한다.
    assert "앱이 열리지 않나요?" in html
    assert "받은 초대 링크 넣기" in html
