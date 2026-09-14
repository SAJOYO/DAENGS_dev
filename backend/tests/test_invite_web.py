"""routers/invite_web.py — 공동 돌봄 초대 링크의 웹 폴백.

인증도 DB 도 없는 정적 라우터라 다른 라우터 테스트처럼 fake DB 를 세울 필요가
없다 — 이 라우터만 얹은 얇은 앱으로 충분하다.
"""

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.config import Settings, settings
from daengs_backend.routers import invite_web

app = FastAPI()
app.include_router(invite_web.router)
client = TestClient(app)

# 모양만 맞는 값이다 — 실제 서명 지문이 아니다.
FINGERPRINT = "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99"


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
    monkeypatch.setattr(settings, "play_signing_sha256_fingerprints", [])

    resp = client.get("/.well-known/assetlinks.json")

    assert resp.status_code == 200
    assert resp.json() == []


def test_지문이_있으면_App_Links_문서를_만든다(monkeypatch):
    monkeypatch.setattr(
        settings,
        "play_signing_sha256_fingerprints",
        [FINGERPRINT],
    )

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
    pydantic-settings 가 그 JSON 배열을 `list[str]` 로 읽고, 응답까지 그대로 실린다."""
    monkeypatch.setenv("DAENGS_PLAY_SIGNING_SHA256_FINGERPRINTS", f'["{FINGERPRINT}"]')

    loaded = Settings(_env_file=None)

    assert loaded.play_signing_sha256_fingerprints == [FINGERPRINT]

    monkeypatch.setattr(settings, "play_signing_sha256_fingerprints", loaded.play_signing_sha256_fingerprints)
    resp = client.get("/.well-known/assetlinks.json")
    assert resp.json()[0]["target"]["sha256_cert_fingerprints"] == [FINGERPRINT]


def test_지문은_둘_이상_넣을_수_있다(monkeypatch):
    """Play 앱 서명 키와 업로드 키를 같이 — 스토어 설치본과 로컬 릴리스 빌드가 둘 다 검증된다."""
    second = FINGERPRINT.replace("AA", "01")
    monkeypatch.setenv("DAENGS_PLAY_SIGNING_SHA256_FINGERPRINTS", f'["{FINGERPRINT}","{second}"]')

    loaded = Settings(_env_file=None)

    assert loaded.play_signing_sha256_fingerprints == [FINGERPRINT, second]


def test_지문은_대문자로_맞추고_공백을_버린다(monkeypatch):
    monkeypatch.setenv("DAENGS_PLAY_SIGNING_SHA256_FINGERPRINTS", f'[" {FINGERPRINT.lower()} "]')

    loaded = Settings(_env_file=None)

    assert loaded.play_signing_sha256_fingerprints == [FINGERPRINT]


@pytest.mark.parametrize(
    "bad",
    [
        "AA:BB:CC:DD",  # 너무 짧다 (SHA-1 도 아니다)
        "73:B7:66:C2:FA:F8:24:29:FD:C5:2E:B1:99:99:EC:CE:6F:3F:D0:1C",  # SHA-1 (20쌍)
        FINGERPRINT.replace(":", ""),  # 콜론 없음
        "c7dmwvr4JCn9xS6xmZnszm8/0Bw=",  # 카카오 키 해시(base64) — 다른 값이다
    ],
)
def test_지문_모양이_틀리면_부팅에서_막는다(monkeypatch, bad):
    """오타 난 지문은 검증만 조용히 실패해 원인이 안 보인다 — 설정을 읽는 자리에서 막는다."""
    monkeypatch.setenv("DAENGS_PLAY_SIGNING_SHA256_FINGERPRINTS", f'["{bad}"]')

    with pytest.raises(Exception, match="SHA-256 인증서 지문"):
        Settings(_env_file=None)


def test_비워_두면_기본값은_빈_배열이다(monkeypatch):
    monkeypatch.delenv("DAENGS_PLAY_SIGNING_SHA256_FINGERPRINTS", raising=False)

    assert Settings(_env_file=None).play_signing_sha256_fingerprints == []


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
